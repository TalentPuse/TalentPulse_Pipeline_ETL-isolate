"""Recursively resolve "$XX" string references against the RSC ref table."""
import re
from typing import Any

REF_RE = re.compile(r'^\$([0-9a-f]+)$')


def resolve(value: Any, table: dict[str, Any], _seen: frozenset[str] = frozenset()) -> Any:
    """
    Walk value (dict/list/str/scalar). For any string matching $hexid, replace with
    table[hexid] resolved recursively. Cycle-safe via _seen frozenset.
    """
    if isinstance(value, str):
        m = REF_RE.match(value)
        if not m:
            return value
        ref_id = m.group(1)
        if ref_id in _seen:
            return None  # break cycle
        if ref_id not in table:
            return value  # unknown → keep raw marker
        return resolve(table[ref_id], table, _seen | {ref_id})

    if isinstance(value, list):
        return [resolve(v, table, _seen) for v in value]

    if isinstance(value, dict):
        return {k: resolve(v, table, _seen) for k, v in value.items()}

    return value
