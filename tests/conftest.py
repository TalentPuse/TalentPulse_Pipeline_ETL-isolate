"""Shared pytest fixtures.

The kill switch is a PROCESS-GLOBAL environment variable:

    src/utils/safety.py::trigger()  ->  os.environ["CRAWLER_KILL_SWITCH"] = "1"

Several crawler tests exercise the blocked-by-Cloudflare path, which calls
`safety.trigger()` for real, and nothing puts the variable back. Every crawler
that runs later in the same pytest process then sees a kill switch it never
armed and exits immediately.

That stayed invisible for a long time because the tests which trip it
(`tests/crawlers/itviec`, `tests/crawlers/linkedin`) happened to sort after the
tests that would notice. Adding `tests/crawlers/threads` — alphabetically
between them — produced nine failures that passed in isolation and failed in the
full suite, which is the most expensive kind of flake to debug.

Resetting it before every test costs nothing and removes the whole class of
ordering bug.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def reset_crawler_kill_switch():
    """Clear CRAWLER_KILL_SWITCH around every test.

    Cleared before so a test never inherits another's kill switch, and after so
    a test that arms it deliberately does not leak into the next one.
    """
    os.environ.pop("CRAWLER_KILL_SWITCH", None)
    yield
    os.environ.pop("CRAWLER_KILL_SWITCH", None)
