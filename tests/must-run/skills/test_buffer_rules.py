"""Unit tests for autosaxs.core.buffer_rules matcher SSOT."""

from __future__ import annotations

from pathlib import Path

import pytest

from autosaxs.core.buffer_rules import (
    DEFAULT_BUFFER_RULES,
    BufferMatchConflict,
    MatchKind,
    expand_pattern,
    match_all_samples,
    match_basename,
    match_sample_to_buffer,
    normalize_buffer_rules,
    pair_samples_with_rules,
    pattern_to_pool_glob,
    raise_on_conflicts,
    validate_rule_pair,
)


def test_capture_bind_unique_pair(tmp_path: Path):
    s = str(tmp_path / "ihs27_sample_001.tif")
    b = str(tmp_path / "ihs27_buffer_001.tif")
    r = match_sample_to_buffer(s, [b], DEFAULT_BUFFER_RULES)
    assert r.kind == MatchKind.PAIRED
    assert r.buffer == b
    assert r.captures.get("stem") == "ihs27"


def test_multi_capture():
    caps = match_basename("*run{run}_samp{id}.dat", "run03_sampA.dat")
    assert caps == {"run": "03", "id": "A"}
    assert expand_pattern("*run{run}_buf{id}.dat", caps) == "*run03_bufA.dat"


def test_literal_rhs(tmp_path: Path):
    s = str(tmp_path / "special_sample.dat")
    b = str(tmp_path / "my_buffer.dat")
    r = match_sample_to_buffer(s, [b, str(tmp_path / "other.dat")], [("special_sample.dat", "my_buffer.dat")])
    assert r.kind == MatchKind.PAIRED
    assert r.buffer == b


def test_empty_rhs_none(tmp_path: Path):
    s = str(tmp_path / "protein_run1.dat")
    r = match_sample_to_buffer(s, [], [("*protein{run}*", "")])
    assert r.kind == MatchKind.NONE
    assert r.buffer is None
    assert "run" in r.captures


def test_empty_rhs_none_whitespace_rhs(tmp_path: Path):
    s = str(tmp_path / "proteinA_frame.dat")
    r = match_sample_to_buffer(s, ["x.dat"], [("*protein{run}*", "   ")])
    assert r.kind == MatchKind.NONE
    assert r.buffer_expr == ""


def test_unmatched(tmp_path: Path):
    s = str(tmp_path / "noise.dat")
    r = match_sample_to_buffer(s, [], [("*_sample*", "*_buffer*")])
    assert r.kind == MatchKind.UNMATCHED


def test_multi_lhs_conflict(tmp_path: Path):
    s = str(tmp_path / "ab_sample.dat")
    r = match_sample_to_buffer(
        s,
        [str(tmp_path / "ab_buffer.dat")],
        [("*a*_sample*", "*a*_buffer*"), ("*b*_sample*", "*b*_buffer*")],
    )
    assert r.kind == MatchKind.CONFLICT
    assert len(r.conflicting_exprs) == 2
    assert "multiple sample expressions" in r.diagnostics


def test_zero_buffer_hits_conflict(tmp_path: Path):
    s = str(tmp_path / "ihs27_sample.dat")
    r = match_sample_to_buffer(s, [str(tmp_path / "other_buffer.dat")], DEFAULT_BUFFER_RULES)
    assert r.kind == MatchKind.CONFLICT
    assert r.buffer_hits == ()
    assert "matched 0 buffer" in r.diagnostics


def test_multi_buffer_hits_conflict(tmp_path: Path):
    s = str(tmp_path / "ihs27_sample.dat")
    b1 = str(tmp_path / "ihs27_buffer_a.dat")
    b2 = str(tmp_path / "ihs27_buffer_b.dat")
    r = match_sample_to_buffer(s, [b1, b2], DEFAULT_BUFFER_RULES)
    assert r.kind == MatchKind.CONFLICT
    assert set(r.buffer_hits) == {b1, b2}
    assert "matched 2 buffers" in r.diagnostics


def test_ordered_rules_multi_lhs_still_conflict(tmp_path: Path):
    """Order does not pick a winner when multiple LHS match."""
    s = str(tmp_path / "x_sample.dat")
    r = match_sample_to_buffer(
        s,
        [str(tmp_path / "x_buffer.dat")],
        [("*_sample*", "*_buffer*"), ("x_*", "x_buffer.dat")],
    )
    assert r.kind == MatchKind.CONFLICT


def test_normalize_allows_empty_rhs_rejects_empty_lhs():
    rules = normalize_buffer_rules({"*prot*": "", "*samp*": "*buf*"})
    assert rules[0] == ("*prot*", "")
    with pytest.raises(ValueError, match="non-empty"):
        normalize_buffer_rules({"": "*buf*"})
    with pytest.raises(ValueError, match="non-empty"):
        normalize_buffer_rules({})


def test_validate_rule_pair_captures():
    assert validate_rule_pair("*{a}*", "*{a}*buf*") is None
    assert validate_rule_pair("*{a}*", "") is None
    err = validate_rule_pair("*{a}*", "*{b}*")
    assert err is not None and "not present" in err
    assert validate_rule_pair("*{a*", "*b*") is not None


def test_pattern_to_pool_glob():
    assert pattern_to_pool_glob("*ihs{id}*") == "*ihs*"
    assert pattern_to_pool_glob("*ihs{id}*buffer*") == "*ihs*buffer*"


def test_pair_samples_and_raise(tmp_path: Path):
    s1 = str(tmp_path / "a_sample.dat")
    s2 = str(tmp_path / "solo_x.dat")
    s3 = str(tmp_path / "noise.dat")
    b1 = str(tmp_path / "a_buffer.dat")
    rules = [("*{stem}_sample*", "*{stem}_buffer*"), ("*solo*", "")]
    paired, none, unmatched = pair_samples_with_rules([s1, s2, s3], [b1], rules)
    assert paired == [(s1, b1)]
    assert none == [s2]
    assert unmatched == [s3]
    with pytest.raises(BufferMatchConflict) as ei:
        pair_samples_with_rules(
            [s1, str(tmp_path / "b_sample.dat")], [b1], DEFAULT_BUFFER_RULES
        )
    assert "b_sample" in str(ei.value)


def test_raise_on_conflicts_aggregates(tmp_path: Path):
    results = match_all_samples(
        [str(tmp_path / "a_sample.dat"), str(tmp_path / "b_sample.dat")],
        [],
        DEFAULT_BUFFER_RULES,
    )
    with pytest.raises(BufferMatchConflict) as ei:
        raise_on_conflicts(results)
    msg = str(ei.value)
    assert "a_sample" in msg and "b_sample" in msg


def test_default_rules_pair_int_prefixed(tmp_path: Path):
    s = str(tmp_path / "int_ihs27_sample.dat")
    b = str(tmp_path / "int_ihs27_buffer.dat")
    r = match_sample_to_buffer(s, [b], DEFAULT_BUFFER_RULES)
    assert r.kind == MatchKind.PAIRED
    assert r.buffer == b
