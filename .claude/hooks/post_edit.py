"""PostToolUse hook: auto-format Python, validate dbt SQL, flag print() in library code.

Wired from .claude/settings.json as `python .claude/hooks/post_edit.py`.

Non-blocking by contract: this script ALWAYS exits 0. Feedback reaches Claude
through the documented `hookSpecificOutput.additionalContext` JSON channel, not
through exit code 2 — a formatting nit must never interrupt work in progress.

Why a Python script instead of an inline shell command: hook commands run
through whatever shell the platform hands us (cmd.exe on Windows, sh elsewhere),
so `$VAR` expansion and `&&` chaining are not portable. One interpreter, one
file, no shell syntax.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# .claude/hooks/post_edit.py -> repo root is two levels up from .claude/
REPO = Path(__file__).resolve().parents[2]
VENV_BIN = REPO / ".venv" / ("Scripts" if os.name == "nt" else "bin")

# print() belongs in scripts/ and CLI __main__ blocks; in library code it bypasses
# the logging config that Prefect and the GHA runners actually capture.
LOGGING_ONLY_DIRS = ("src", "orchestration")


def _tool(name: str) -> str | None:
    """Prefer the project venv's binary, fall back to PATH, else None."""
    for candidate in (VENV_BIN / f"{name}.exe", VENV_BIN / name):
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, OSError):
        return None


def _emit(messages: list[str]) -> None:
    """Hand feedback to Claude without failing the tool call."""
    if messages:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "\n".join(messages),
            }
        }))
    sys.exit(0)


def check_python(path: Path) -> list[str]:
    notes: list[str] = []
    ruff = _tool("ruff")
    if ruff:
        # `ruff check --fix` only applies fixes ruff considers safe (unused
        # imports and the like) — small, local, and worth doing automatically.
        _run([ruff, "check", "--fix", "--quiet", str(path)], timeout=30)
        # `ruff format` is deliberately OPT-IN. This codebase is not
        # ruff-formatted: 43 of 63 files would be rewritten, and the hand-aligned
        # collections (e.g. VIETNAM_LOCATION_MARKERS) would explode to one item
        # per line. Formatting on every edit would bury real diffs in churn.
        # Set TP_HOOK_RUFF_FORMAT=1 once the whole repo has been formatted in a
        # single dedicated commit.
        if os.getenv("TP_HOOK_RUFF_FORMAT") == "1":
            _run([ruff, "format", "--quiet", str(path)], timeout=30)
        lint = _run([ruff, "check", "--quiet", str(path)], timeout=30)
        if lint and lint.returncode != 0 and lint.stdout.strip():
            notes.append(
                "ruff still reports issues after --fix (these need a human decision):\n"
                + lint.stdout.strip()[:1500]
            )

    try:
        rel = path.relative_to(REPO)
    except ValueError:
        return notes
    if rel.parts and rel.parts[0] in LOGGING_ONLY_DIRS:
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return notes
        # Only the __main__ block legitimately prints; anything above it should log.
        head = body.split('if __name__ ==')[0]
        hits = [
            i for i, line in enumerate(head.splitlines(), 1)
            if line.lstrip().startswith("print(")
        ]
        if hits:
            notes.append(
                f"{rel}: print() at line(s) {', '.join(map(str, hits))} outside the "
                "__main__ block. Library code here runs under Prefect/GHA where only "
                "`logger` output is captured — use logging instead."
            )
    return notes


def check_dbt(path: Path) -> list[str]:
    dbt = _tool("dbt")
    if not dbt:
        return []
    proc = _run(
        [dbt, "parse", "--project-dir", "dbt_transform", "--profiles-dir", "dbt_transform"],
        timeout=180,
    )
    if proc is None:
        return []
    if proc.returncode != 0:
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-25:]
        return [
            "`dbt parse` FAILED after this edit — the project no longer compiles:\n"
            + "\n".join(tail)
        ]
    return []


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    raw = (payload.get("tool_input") or {}).get("file_path")
    if not raw:
        sys.exit(0)
    path = Path(raw)
    if not path.is_absolute():
        path = REPO / path
    if not path.exists():
        sys.exit(0)

    suffix = path.suffix.lower()
    in_dbt = "dbt_transform" in path.parts

    if suffix == ".py":
        _emit(check_python(path))
    if in_dbt and suffix in (".sql", ".yml", ".yaml"):
        _emit(check_dbt(path))
    sys.exit(0)


if __name__ == "__main__":
    main()
