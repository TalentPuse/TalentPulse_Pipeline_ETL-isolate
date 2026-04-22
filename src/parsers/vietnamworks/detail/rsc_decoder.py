"""Decode Next.js RSC payload from VietnamWorks detail HTML."""
import json
import re
from typing import Any

CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)', re.DOTALL)
# A ref header: hex id followed by colon. Ref boundaries aren't always newline-delimited
# because text frames (T<len>,<HTML>) contain arbitrary bytes. We parse forward instead.
REF_HEADER_RE = re.compile(r'([0-9a-f]+):')


class ParseError(Exception):
    pass


def _parse_value(payload: str, pos: int) -> tuple[Any, int]:
    """
    Parse one RSC ref value starting at `pos`. Returns (value, next_pos).
    Supports:
      - T<hex_len>,<raw text>      text frames (use byte length in UTF-8)
      - <JSON>                     objects/arrays/literals consumed greedily
      - I<JSON>                    I-prefixed (ignored metadata, consume like JSON)
      - HL<JSON>, H<JSON>          link/preload frames (consume like JSON)
      - other prefixes (M, S, etc) fallback: read until next ref header.
    """
    if pos >= len(payload):
        return None, pos

    ch = payload[pos]

    # Text frame: T<hex_len>,<body>
    if ch == "T":
        comma = payload.find(",", pos)
        if comma > 0:
            hex_len = payload[pos + 1:comma]
            try:
                byte_len = int(hex_len, 16)
            except ValueError:
                byte_len = 0
            text_start = comma + 1
            # Walk bytes in UTF-8 up to byte_len
            end = text_start
            consumed = 0
            while end < len(payload) and consumed < byte_len:
                consumed += len(payload[end].encode("utf-8"))
                end += 1
            return payload[text_start:end], end

    # JSON-like prefixes (I, HL, H, M, S) — skip the prefix, then raw_decode
    prefix_len = 0
    if ch == "I":
        prefix_len = 1
    elif payload.startswith("HL", pos):
        prefix_len = 2
    elif ch == "H":
        prefix_len = 1

    start = pos + prefix_len
    if start < len(payload) and payload[start] in '{["0123456789tfn-':
        try:
            obj, consumed = json.JSONDecoder().raw_decode(payload[start:])
            return (obj if prefix_len == 0 else None, start + consumed)
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: treat as opaque, stop at next line-start ref header
    next_nl = payload.find("\n", pos)
    if next_nl == -1:
        return payload[pos:], len(payload)
    return payload[pos:next_nl], next_nl + 1


def decode(html: str) -> dict[str, Any]:
    """
    Extract __next_f.push chunks, concatenate, then parse a sequence of
    "<hex_id>:<payload>" records where payloads can be JSON or T-prefixed text.
    """
    if not html:
        raise ParseError("empty html")

    chunks: list[str] = []
    for m in CHUNK_RE.finditer(html):
        try:
            chunks.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
    if not chunks:
        raise ParseError("no __next_f.push chunks found")

    full = ''.join(chunks)
    table: dict[str, Any] = {}

    pos = 0
    n = len(full)
    while pos < n:
        # Skip leading newlines
        while pos < n and full[pos] == "\n":
            pos += 1
        if pos >= n:
            break
        m = REF_HEADER_RE.match(full, pos)
        if not m:
            # Skip to next newline and try again
            nl = full.find("\n", pos)
            if nl == -1:
                break
            pos = nl + 1
            continue
        ref_id = m.group(1)
        pos = m.end()
        value, pos = _parse_value(full, pos)
        if value is not None:
            table[ref_id] = value
    return table


def find_main_job_ref(table: dict[str, Any]) -> dict:
    """Find ref dict containing both 'jobTitle' and 'jobId' (the main job object)."""
    for ref_id, val in table.items():
        if isinstance(val, dict) and 'jobTitle' in val and 'jobId' in val:
            return val
    raise ParseError("no main job ref containing jobTitle+jobId found")
