"""Shared fixture loader for parser tests."""
import gzip
import pathlib

FIXTURE_DIR = pathlib.Path(__file__).parent.parent.parent.parent / "fixtures" / "details"


def load_fixture_html(job_id: str) -> str:
    path = FIXTURE_DIR / f"{job_id}.html.gz"
    return gzip.decompress(path.read_bytes()).decode("utf-8", errors="replace")
