"""URL helpers for VietnamWorks job detail pages."""
import re

ALLOWED = re.compile(r"^https://www\.vietnamworks\.com/[a-z0-9\-]+-\d+-jv/?$")
BASE = "https://www.vietnamworks.com"


def build_detail_url(slug: str, job_id: int | str) -> str:
    """Build canonical detail URL from slug + jobId."""
    slug = (slug or "").strip("/").lower()
    if not slug:
        raise ValueError("slug cannot be empty")
    if str(job_id).strip() == "":
        raise ValueError("job_id cannot be empty")
    # If slug already contains the -{jobId}-jv suffix, use as-is
    if re.search(rf"-{job_id}-jv$", slug):
        return f"{BASE}/{slug}"
    return f"{BASE}/{slug}-{job_id}-jv"


def is_allowed(url: str) -> bool:
    """robots.txt-aligned guard: only public job detail pages."""
    return bool(ALLOWED.match(url or ""))
