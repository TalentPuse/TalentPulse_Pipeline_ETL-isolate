import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.loaders.job_detail_loader import JobDetailLoader


def _payload(jid="1", **extra):
    base = {
        "source": "vietnamworks",
        "source_job_id": str(jid),
        "parser_version": "v2",
        "parsed_at": "2026-04-22T15:00:00Z",
        "title": f"Job {jid}",
        "company_name": "ACME",
        "job_function": {
            "parentId": 5,
            "children": [{"id": 27, "name": "Data Engineer/Data Analyst/AI"}],
        },
    }
    base.update(extra)
    return base


def _make_loader(keys_with_bodies, last_mods=None, *, validate_payload=True):
    """keys_with_bodies = list of (key, body_dict_or_str). last_mods = dict key->datetime."""
    minio = MagicMock()
    repo = MagicMock()

    page = {"Contents": [
        {"Key": k, "LastModified": (last_mods or {}).get(k, datetime(2026, 4, 22, tzinfo=timezone.utc))}
        for k, _ in keys_with_bodies
    ]}
    paginator = MagicMock()
    paginator.paginate.return_value = [page]
    minio.s3_client.get_paginator.return_value = paginator

    bodies = {}
    for k, b in keys_with_bodies:
        if isinstance(b, dict):
            bodies[k] = json.dumps(b).encode()
        else:
            bodies[k] = b.encode() if isinstance(b, str) else b

    def get_object(Bucket, Key):
        body = MagicMock()
        body.read.return_value = bodies[Key]
        return {"Body": body}

    minio.s3_client.get_object.side_effect = get_object

    loader = JobDetailLoader(minio=minio, repo=repo, validate_payload=validate_payload)
    return loader, minio, repo


def _loaded_payloads(repo):
    """The list passed to the single upsert_many batch call (or [] if not called)."""
    if not repo.upsert_many.called:
        return []
    return list(repo.upsert_many.call_args.args[0])


def _reject_tuples(repo):
    if not repo.record_reject_many.called:
        return []
    return list(repo.record_reject_many.call_args.args[0])


def test_loader_loads_three_jsons():
    keys = [
        ("parsed/details/vietnamworks/1.json", _payload(1)),
        ("parsed/details/vietnamworks/2.json", _payload(2)),
        ("parsed/details/vietnamworks/3.json", _payload(3)),
    ]
    loader, _, repo = _make_loader(keys)
    counters = loader.run_batch()
    assert counters == {"loaded": 3, "rejected": 0, "skipped": 0, "failed": 0}
    # ONE batched upsert, not one-per-row
    assert repo.upsert_many.call_count == 1
    loaded = _loaded_payloads(repo)
    assert len(loaded) == 3
    assert {p["source_job_id"] for p in loaded} == {"1", "2", "3"}


def test_loader_skips_malformed_json():
    keys = [
        ("parsed/details/vietnamworks/1.json", _payload(1)),
        ("parsed/details/vietnamworks/bad.json", "not json {{{"),
        ("parsed/details/vietnamworks/3.json", _payload(3)),
    ]
    loader, _, repo = _make_loader(keys)
    counters = loader.run_batch()
    assert counters["loaded"] == 2
    assert counters["failed"] == 1
    assert len(_loaded_payloads(repo)) == 2


def test_loader_skips_missing_required():
    keys = [
        ("parsed/details/vietnamworks/1.json", {"foo": "bar"}),  # no source/source_job_id
        ("parsed/details/vietnamworks/2.json", _payload(2)),
    ]
    loader, _, repo = _make_loader(keys)
    counters = loader.run_batch()
    assert counters["loaded"] == 1
    assert counters["failed"] == 1
    assert len(_loaded_payloads(repo)) == 1


def test_loader_dry_run_no_writes():
    keys = [("parsed/details/vietnamworks/1.json", _payload(1))]
    loader, _, repo = _make_loader(keys)
    counters = loader.run_batch(dry_run=True)
    assert counters["loaded"] == 1
    repo.upsert_many.assert_not_called()
    repo.record_reject_many.assert_not_called()


def test_loader_since_filter():
    keys = [
        ("parsed/details/vietnamworks/old.json", _payload(1)),
        ("parsed/details/vietnamworks/new.json", _payload(2)),
    ]
    last_mods = {
        "parsed/details/vietnamworks/old.json": datetime(2026, 4, 1, tzinfo=timezone.utc),
        "parsed/details/vietnamworks/new.json": datetime(2026, 4, 22, tzinfo=timezone.utc),
    }
    loader, _, repo = _make_loader(keys, last_mods=last_mods)
    counters = loader.run_batch(since=datetime(2026, 4, 15, tzinfo=timezone.utc))
    assert counters["loaded"] == 1
    assert counters["skipped"] == 1
    assert len(_loaded_payloads(repo)) == 1


def test_loader_failed_upsert_counts_as_failed():
    keys = [("parsed/details/vietnamworks/1.json", _payload(1))]
    loader, _, repo = _make_loader(keys)
    repo.upsert_many.side_effect = RuntimeError("db down")
    counters = loader.run_batch()
    assert counters["loaded"] == 0
    assert counters["failed"] == 1


def test_loader_skips_non_json_keys():
    keys = [
        ("parsed/details/vietnamworks/1.json", _payload(1)),
        ("parsed/details/vietnamworks/junk.txt", "ignored"),
    ]
    loader, _, repo = _make_loader(keys)
    counters = loader.run_batch()
    assert counters["loaded"] == 1
    assert counters["failed"] == 0
    assert len(_loaded_payloads(repo)) == 1


# --- v2: validation integration ---

def test_loader_rejects_out_of_focus():
    p_noise = _payload(
        1, job_function={"children": [{"id": 9, "name": "Software Dev"}]}
    )
    keys = [
        ("parsed/details/vietnamworks/1.json", p_noise),
        ("parsed/details/vietnamworks/2.json", _payload(2)),
    ]
    loader, _, repo = _make_loader(keys)
    counters = loader.run_batch()
    assert counters["loaded"] == 1
    assert counters["rejected"] == 1
    assert len(_loaded_payloads(repo)) == 1
    rejects = _reject_tuples(repo)
    assert len(rejects) == 1
    payload, reason, detail, key = rejects[0]
    assert reason == "OUT_OF_FOCUS"
    assert key == "parsed/details/vietnamworks/1.json"


def test_loader_rejects_bad_salary_range():
    p = _payload(
        1,
        is_salary_visible=True,
        salary_min=50_000_000,
        salary_max=10_000_000,
    )
    keys = [("parsed/details/vietnamworks/1.json", p)]
    loader, _, repo = _make_loader(keys)
    counters = loader.run_batch()
    assert counters["rejected"] == 1
    assert counters["loaded"] == 0
    repo.upsert_many.assert_not_called()
    assert _reject_tuples(repo)[0][1] == "BAD_SALARY_RANGE"


def test_loader_rejects_missing_title():
    p = _payload(1, title=None)
    keys = [("parsed/details/vietnamworks/1.json", p)]
    loader, _, repo = _make_loader(keys)
    counters = loader.run_batch()
    assert counters["rejected"] == 1
    assert _reject_tuples(repo)[0][1] == "MISSING_TITLE"


def test_loader_validation_can_be_disabled():
    """Backward-compat: validate_payload=False bypasses everything."""
    p_noise = _payload(
        1, job_function={"children": [{"id": 9, "name": "Software Dev"}]}
    )
    keys = [("parsed/details/vietnamworks/1.json", p_noise)]
    loader, _, repo = _make_loader(keys, validate_payload=False)
    counters = loader.run_batch()
    assert counters["loaded"] == 1
    assert counters["rejected"] == 0
    repo.upsert_many.assert_called_once()
    repo.record_reject_many.assert_not_called()
