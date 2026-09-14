"""Session DOMINANT-RANK — "dominant" in the imbalance finding means loudest.

`detect_imbalance` told two different stories about the same peak. The SCORING
was rank-honest: the `rank_one` confidence factor is awarded only when
`radial_peak.rank == 1` (`bearing_rca.py:1230`). The SENTENCE was not — all
three `evidence=` branches opened with the same copy-pasted "dominant on radial
axis {axis}" whatever the rank. On the HIST-2 sample (`before.csv`, no bearing
model) that shipped a report committing to 30.0 Hz at amplitude 0.9 and calling
it dominant, while 107.0 Hz sat 1.56x louder on the same axis, claimed by
nothing (Session UNEXPLAINED-PEAK §3).

**These are the verbatim pins Session UNEXPLAINED-PEAK believed already
existed.** They did not. `test_agent_consistency.py:212` and
`test_agent_loop.py:257` do quote an imbalance-shaped sentence, but both are
hand-written *fabricated* LLM narratives fed against a healthy, no-findings
result: nothing there compares them to `bearing_rca`'s output, and both were
already stale ("axial axis (x) quiet" where the detector says "measured and
quiet"). They are fabricated prose by design and must not mirror the detector.
The real pin lives here.

One deliberate scoping note, so a later session does not tighten it into a
falsehood: the honest sentence is pinned on the phrase "dominant on radial
axis" — the CLAIM — never on the bare token "dominant". Branch (B)'s tail
legitimately contains "an axial-dominant misalignment or bent shaft", which is
a statement about what could NOT be ruled out, not a claim about this peak.
"""

from __future__ import annotations

import pytest

from vib_agent.agent.consistency import computed_numbers
from vib_agent.config import load_thresholds
from vib_agent.models import MachineMeta, Peak, PeakSet
from vib_agent.pdm_core.bearing_rca import run_rca

# The HIST-2 `before.csv` geometry, reused as the rank-2 fixture everywhere: the
# committed 1x line is rank 2 on its own axis and 107.0 Hz is rank 1.
from tests.test_unexplained_peak import (  # noqa: F401 -- `cfg` is used AS a fixture
    _BEFORE,
    _analyse,
    _documents,
    SHAFT_HZ,
    UNCLAIMED_HZ,
    cfg,
)

DOMINANCE_CLAIM = "dominant on radial axis"

_MACHINE = MachineMeta(
    mac="TEST-DOMRANK-01", name="Imbalance Rig", active=True, type="pump",
    iso_group="2", iso_support="rigid", axial_axis="x",
)

#: rank-2 peak list, `before.csv`'s exactly — 107.0 Hz is the loudest line on y.
_RANK_TWO_PEAKS = [
    Peak(axis="y", freq=UNCLAIMED_HZ, rank=1, amplitude=1.4),
    Peak(axis="y", freq=SHAFT_HZ, rank=2, amplitude=0.9),
    Peak(axis="y", freq=2 * UNCLAIMED_HZ, rank=3, amplitude=0.6),
]
#: the control: the same machine with the 1x line loudest on its axis.
_RANK_ONE_PEAKS = [Peak(axis="y", freq=SHAFT_HZ, rank=1, amplitude=1.0)]

#: radial dominance 5.0/0.4 = 12.5, clear of route's `imbalance_radial_dominance` 7.5.
_V = {"x": 0.4, "y": 5.0, "z": 0.5}


def _peak_set(peaks, velocities=None, *, measured_axes=None, amplitudes=True) -> PeakSet:
    kw = {} if measured_axes is None else {"measured_axes": measured_axes}
    if not amplitudes:
        peaks = [Peak(axis=p.axis, freq=p.freq, rank=p.rank) for p in peaks]
    return PeakSet(
        source="spectrum", kind="velocity", shaft_freq_hz=SHAFT_HZ, rpm=1800.0,
        velocities_mms=dict(velocities or _V), peaks=peaks, **kw,
    )


# The three evidence branches, each reachable from a PeakSet:
#   A `axial_quiet`      — axial axis measured, no 1x on it
#   B `not axial_measured` — the single-channel upload (`before.csv`)
#   C the fall-through   — axial measured AND carrying a 1x
_AXIAL_1X = Peak(axis="x", freq=SHAFT_HZ, rank=1, amplitude=0.1)


def _branch(name: str, peaks: list[Peak], **kw) -> PeakSet:
    if name == "A":
        return _peak_set(peaks, **kw)
    if name == "B":
        return _peak_set(peaks, {"y": 5.0}, measured_axes=["y"], **kw)
    if name == "C":
        return _peak_set(peaks + [_AXIAL_1X], **kw)
    raise AssertionError(name)


BRANCHES = ("A", "B", "C")


def _imbalance(peak_set: PeakSet, profile: str = "route"):
    result = run_rca(peak_set, _MACHINE, "warn", load_thresholds(profile))
    match = next((m for m in result.primary_findings if m.fault == "imbalance"), None)
    assert match is not None, (
        "fixture no longer commits imbalance to primary — the pin below would be vacuous; "
        f"primary={[m.fault for m in result.primary_findings]}"
    )
    return match


# ── the control: a rank-1 peak is still called dominant, byte for byte ───────


class TestRankOneIsUnchanged:
    """The whole point of the fix is that it is invisible where the word was
    already true. These are the pre-DOMINANT-RANK strings, verbatim."""

    EXPECTED = {
        "A": (
            "1× shaft frequency (30.0 Hz) dominant on radial axis y, axial axis (x) measured "
            "and quiet at shaft frequency. Characteristic signature of rotor imbalance."
        ),
        "B": (
            "1× shaft frequency (30.0 Hz) dominant on radial axis y, and the 1× line leads the "
            "higher radial harmonics. Consistent with rotor imbalance. The axial axis (x) was "
            "NOT measured, so the quiet-axial half of the imbalance signature is unevidenced "
            "and an axial-dominant misalignment or bent shaft cannot be ruled out from this "
            "channel alone."
        ),
        "C": (
            "1× shaft frequency (30.0 Hz) dominant on radial axis y; radial velocity dominates "
            "the axial axis (x) and the 1× line leads the higher radial harmonics. "
            "Characteristic signature of rotor imbalance."
        ),
    }

    @pytest.mark.parametrize("branch", BRANCHES)
    def test_the_sentence_is_byte_identical(self, branch):
        match = _imbalance(_branch(branch, _RANK_ONE_PEAKS))
        assert match.evidence == self.EXPECTED[branch]


# ── the defect: a rank-2 peak is no longer called dominant ───────────────────


class TestRankTwoIsHonest:
    @pytest.mark.parametrize("branch", BRANCHES)
    def test_no_branch_claims_dominance(self, branch):
        match = _imbalance(_branch(branch, _RANK_TWO_PEAKS))
        assert DOMINANCE_CLAIM not in match.evidence

    @pytest.mark.parametrize("branch", BRANCHES)
    def test_the_rank_is_stated(self, branch):
        match = _imbalance(_branch(branch, _RANK_TWO_PEAKS))
        assert "at rank 2 of that axis's peaks" in match.evidence

    @pytest.mark.parametrize("branch", BRANCHES)
    def test_the_louder_line_is_named_with_its_axis(self, branch):
        match = _imbalance(_branch(branch, _RANK_TWO_PEAKS))
        assert "The loudest line on y is 107.0 Hz, unexplained by this finding." in match.evidence

    def test_the_committed_peak_is_still_the_1x(self):
        """The wording moved; the diagnosis did not. Commit 1 changes prose only."""
        match = _imbalance(_branch("B", _RANK_TWO_PEAKS))
        assert match.freq_hz == pytest.approx(SHAFT_HZ)
        assert match.axis == "y"


# ── the invariant the fix restores: one predicate, two consumers ─────────────


class TestOnePredicateTwoConsumers:
    """`rank_one` (a confidence factor) and "dominant" (a word) are the same
    claim about the same peak. They may never disagree again."""

    @pytest.mark.parametrize("branch", BRANCHES)
    @pytest.mark.parametrize("peaks", [_RANK_ONE_PEAKS, _RANK_TWO_PEAKS], ids=["rank1", "rank2"])
    def test_the_word_tracks_the_factor(self, branch, peaks):
        match = _imbalance(_branch(branch, peaks))
        factor_awarded = any(f.name == "rank_one" for f in match.confidence_evidence)
        assert (DOMINANCE_CLAIM in match.evidence) is factor_awarded


class TestTheThreeBranchesShareOneClause:
    """The three `evidence=` strings repeated the same eleven words verbatim,
    which is how one of them could have been fixed and the other two left
    lying — the shape of UNEXPLAINED-PEAK's own defect (four templates, one
    wrong gate, all copies of each other). The clause is now built once."""

    @pytest.mark.parametrize("peaks", [_RANK_ONE_PEAKS, _RANK_TWO_PEAKS], ids=["rank1", "rank2"])
    def test_every_branch_opens_with_the_same_lead(self, peaks):
        leads = set()
        for branch in BRANCHES:
            evidence = _imbalance(_branch(branch, peaks)).evidence
            # the lead runs to the first clause break, which differs per branch
            leads.add(min(
                evidence.split(",")[0], evidence.split(";")[0], key=len
            ))
        assert len(leads) == 1, leads


# ── the sources that carry no amplitude, and the frozen profile ─────────────


class TestAmplitudeLessSources:
    """NCD triplets carry rank but no amplitude, so the rank predicate is the
    only one available there — and it works. No number moves on the frozen
    `streaming` profile; only the prose gains the honest clause."""

    def test_an_amplitude_less_rank_two_peak_gets_the_honest_sentence(self):
        match = _imbalance(_branch("A", _RANK_TWO_PEAKS, amplitudes=False))
        assert DOMINANCE_CLAIM not in match.evidence
        assert "The loudest line on y is 107.0 Hz, unexplained by this finding." in match.evidence

    def test_the_streaming_profile_is_honest_too(self):
        """`streaming` takes the strict pre-B5 gate (`has_1x_radial and not
        has_1x_axial`), so branch (A) is the reachable one. thresholds.json is
        untouched by this session; the wording is not a calibration constant."""
        match = _imbalance(_branch("A", _RANK_TWO_PEAKS, amplitudes=False), profile="streaming")
        assert DOMINANCE_CLAIM not in match.evidence
        assert "at rank 2 of that axis's peaks" in match.evidence

    def test_streaming_reports_the_same_numbers_as_before(self):
        """The freeze is on `profiles.streaming`'s constants and the numbers they
        produce. Both are untouched: same peak, same frequency, same axis."""
        match = _imbalance(_branch("A", _RANK_TWO_PEAKS, amplitudes=False), profile="streaming")
        assert (match.freq_hz, match.axis, match.expected_hz) == (SHAFT_HZ, "y", SHAFT_HZ)


# ── the product path: the report an analyst actually reads ──────────────────


class TestTheProductPath:
    """`before.csv` end to end — the case that made this session necessary.
    Minted through `parse_upload` -> `run_analysis`, reusing
    `test_unexplained_peak`'s fixture rather than re-describing it."""

    def test_the_report_no_longer_calls_the_quieter_line_dominant(self, tmp_path, cfg):
        _case, result = _analyse(tmp_path, cfg, _BEFORE)
        reason = result.findings[0].reason
        assert result.findings[0].fault == "imbalance"
        assert DOMINANCE_CLAIM not in reason
        assert "at rank 2 of that axis's peaks" in reason
        assert "The loudest line on y is 107.0 Hz, unexplained by this finding." in reason

    def test_both_documents_carry_the_honest_sentence(self, tmp_path, cfg):
        """PIN MOVED, Session REPORT-2-FIX (law #17): the sentence is unchanged in
        substance and gains its shaft order in the RENDERED document.

        REPORT-2 item 3 asked that every fault-frequency mention print both, and
        `report/generate.py` now passes `Finding.reason` through
        `charts.annotate_orders` before either twin renders it. 107.0 Hz is a bare
        mention, so it is glossed; the claim this pin protects -- that the honest
        sentence reaches BOTH documents, not just the detector's own string -- is
        exactly what it was. The report's own vocabulary agrees: the *Unexplained
        dominant peak* section two paragraphs below already writes
        "107.0 Hz (3.57x shaft, axis y)".

        The sibling pin above is deliberately NOT moved. It reads the raw
        `result.findings[0].reason` and proves pdm_core's string is untouched --
        the half of the contract that must not move, and what tells a later reader
        that the order is presentation and not a detector change."""
        case, result = _analyse(tmp_path, cfg, _BEFORE)
        markdown, html = _documents(case, result, cfg)
        for doc in (markdown, html):
            assert "The loudest line on y is 107.0 Hz (3.57×), unexplained by this finding." in doc

    def test_the_unexplained_peak_section_is_untouched(self, tmp_path, cfg):
        """Commit 1 is detector prose. The report layer's own section — added by
        UNEXPLAINED-PEAK, and the one place "dominant" is legitimately the
        report's word — must still be there, saying what it said."""
        case, result = _analyse(tmp_path, cfg, _BEFORE)
        markdown, html = _documents(case, result, cfg)
        for doc in (markdown, html):
            assert "Unexplained dominant peak" in doc
            assert "1.56" in doc

    def test_every_number_in_the_sentence_is_a_computed_number(self, tmp_path, cfg):
        """The new clause names 107.0 Hz, which widens what a drafted narrative
        may quote (`consistency.computed_numbers` walks pdm_core's own prose).
        That is correct only because the number IS a computed value — read off
        the same peak set the finding came from, not introduced by the wording."""
        _case, result = _analyse(tmp_path, cfg, _BEFORE)
        allowed = computed_numbers(result)
        for value in (SHAFT_HZ, UNCLAIMED_HZ, 2.0):  # 1x, the louder line, the rank
            assert any(abs(value - c) < 1e-9 for c in allowed), value
