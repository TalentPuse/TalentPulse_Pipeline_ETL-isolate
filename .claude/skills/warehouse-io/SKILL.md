---
name: warehouse-io
description: Rules for talking to Postgres from this repo — batching, connection reuse, ON CONFLICT dedup, and the queue semantics of raw.crawl_log. Use when writing or editing anything in src/storage/, src/queue/, src/loaders/, or src/normalizer/, or when a pipeline stage is mysteriously slow or times out.
---

# Warehouse I/O

Every write path in this repo runs from a **GitHub Actions runner over a Tailscale
tailnet** to a Postgres on a 4 GB VPS (`max_connections=50`). One round-trip costs
1–3 s. That single fact explains almost every design choice below, and every one of
them was a real incident before it was a rule.

## Rule 1 — never one statement per row

Use `psycopg2.extras.execute_values`. Existing precedents:

| Function | Was | Now |
|---|---|---|
| `CrawlLog.enqueue_many` | per-row `enqueue()` — 236 URLs took ~13 min | one `execute_values` |
| `JobDetailRepo.upsert_many` | `executemany()` | `execute_values`, `page_size=200` |
| `JobDetailRepo.record_reject_many` | connection per reject (LinkedIn rejects ~800/run) | one `execute_values` |
| `NormalizerRunner._write_results` / `_write_drift` | `execute()` per row, blew the 600 s task timeout | one `execute_values`, `page_size=500` |

`JobDetailLoader.run_batch` follows the same shape at the object-storage layer:
read + validate all keys in a `ThreadPoolExecutor` (16 workers, I/O-bound R2 GETs),
then write the whole batch with **two** connections total.

## Rule 2 — `execute_values` + `ON CONFLICT` has two traps

**Trap A — duplicate conflict keys in one command.** A single
`INSERT ... ON CONFLICT DO UPDATE` cannot touch the same conflict key twice
(`CardinalityViolation`). Dedup in Python first:

```python
deduped = {job_id: url for job_id, url in items}                    # crawl_log
rows = list({(r["source"], r["source_job_id"]): r for r in rows}.values())  # job_detail
```

**Trap B — `cur.rowcount` lies.** `execute_values` splits VALUES into several
statements of `page_size` rows; `rowcount` reflects only the last one. 624 rows
would report 124. Return `len(rows)` instead when the count is user-facing.

## Rule 3 — reuse the connection

`CrawlLog` keeps one pooled connection (`_get_conn` / `_cursor` / `_discard`).
The detail crawlers make two calls per job (`claim_next` + `mark_success|failed`);
a fresh connect per call is what blew the LinkedIn GHA timeout. On
`OperationalError`/`InterfaceError` the connection is discarded so the next call
reconnects, and the exception still propagates so the Prefect retry covers it.
Call `log.close()` in a `finally` when you own the object.

## `raw.crawl_log` is a work queue, not just a log

PK is `(source, job_id)`. Status flow: `pending → in_progress → success|failed|expired`.

- `claim_next(source)` — `FOR UPDATE SKIP LOCKED`, one row, flips to `in_progress`
  and bumps `retry_count`. Safe under concurrency.
- `requeue_stale(source, older_than_minutes)` — **call this at the start of every
  detail crawl.** A run killed mid-flight (GHA timeout, OOM) leaves rows in
  `in_progress` that no later run can claim; those jobs are lost forever without
  the sweep. Budget comes from `config.CRAWL_STALE_CLAIM_MINUTES` (default 120).
- The `ON CONFLICT` in `enqueue`/`enqueue_many` keeps rows already in
  `success`/`in_progress` untouched — that is what replaces the per-row
  `is_fresh()` check without the round-trips.

## Upsert semantics for `raw.job_detail`

PK `(source, source_job_id)`. `_build_upsert_sql()` (single-row path) guards with
`WHERE raw.job_detail.parsed_at <= EXCLUDED.parsed_at` so an older re-parse cannot
overwrite newer data. **`upsert_many` currently omits that guard** — if you touch
that function, decide deliberately whether to add it rather than leaving the two
paths silently different.

JSON columns (`job_function`, `locations`, `industries`, `skills`, `benefits`,
`services`) must be wrapped in `psycopg2.extras.Json` — see `_coerce`.

## Rejects are the audit trail

Loader validation failures go to `raw.job_detail_rejects` with `reject_reason`,
`reject_detail`, and the full `payload` JSONB, so a rule change can be replayed by
re-running the loader. Never drop a row silently.

Note that `src/loaders/validators.py::validate` currently runs **only**
`validate_business_rules`. Focus/title/location filtering is intentionally
disabled (all platforms crawl the same `CRAWL_KEYWORDS`); the other `validate_*`
functions are kept for signature compatibility and are dead code. Do not
"restore" them without asking.
