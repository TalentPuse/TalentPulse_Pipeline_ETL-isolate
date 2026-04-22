"""Global kill-switch for crawlers. Reads env CRAWLER_KILL_SWITCH=1 to abort."""
import os


def is_killed() -> bool:
    """Return True if the crawler should stop immediately."""
    return os.getenv("CRAWLER_KILL_SWITCH", "0") == "1"


def trigger() -> None:
    """Set the in-process kill flag (env var); persists for child processes."""
    os.environ["CRAWLER_KILL_SWITCH"] = "1"
