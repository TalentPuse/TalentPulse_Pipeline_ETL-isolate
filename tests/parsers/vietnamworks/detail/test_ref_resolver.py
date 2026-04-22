from src.parsers.vietnamworks.detail.ref_resolver import resolve


def test_resolve_scalar_unchanged():
    assert resolve(42, {}) == 42
    assert resolve("plain string", {}) == "plain string"
    assert resolve(None, {}) is None


def test_resolve_simple_ref():
    table = {"2a": {"skillName": "Python"}}
    assert resolve("$2a", table) == {"skillName": "Python"}


def test_resolve_nested_array_of_refs():
    table = {
        "29": ["$2a", "$2b"],
        "2a": {"skillName": "Python"},
        "2b": {"skillName": "SQL"},
    }
    assert resolve("$29", table) == [{"skillName": "Python"}, {"skillName": "SQL"}]


def test_resolve_unknown_ref_kept_raw():
    assert resolve("$deadbeef", {}) == "$deadbeef"


def test_resolve_cycle_breaks():
    table = {"a": "$b", "b": "$a"}
    # Should not infinite-loop
    result = resolve("$a", table)
    assert result is None


def test_resolve_dict_with_nested_refs():
    table = {"a1": {"address": "HCM", "geo": "$b2"}, "b2": {"lat": 10.0}}
    assert resolve("$a1", table) == {"address": "HCM", "geo": {"lat": 10.0}}


def test_resolve_does_not_touch_dollar_prefix_non_hex():
    # "$USD 100" is not a ref (space present)
    assert resolve("$USD 100", {}) == "$USD 100"
