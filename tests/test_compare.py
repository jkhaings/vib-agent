"""Session HIST-2 — pdm_core/compare.py, the two-file Before/After comparison.

Every assertion here is about arithmetic and refusals: what the comparison
computes, and — at least as important — what it declines to compare. The module
is additive (no existing pdm_core file was opened), so nothing in this file
pins existing behaviour; it pins the new module's contract.

The Cases are built the way a tabular CSV/XLSX upload builds one — a single
`spectra` dict of `kind="velocity"` spectra, no `raw_spectra` — so the fixtures
exercise the shape the product's compare mode actually receives.
"""

from __future__ import annotations

import pytest

from vib_agent.models import (
    AnalysisResult,
    Case,
    Check,
    ComparisonResult,
    QualityGateResult,
    RcaResult,
    SensorData,
    Spectrum,
)
from vib_agent.pdm_core import compare as C
from vib_agent.pipeline import run_analysis
from vib_agent.synth.generator import make_spectrum

# 6206 BPFO at 1800 rpm (shaft 30 Hz). The same tone tests/test_spectrum_kind_
# routing.py uses, so a fault committed here is committed for the documented
# reason and not by coincidence.
_BPFO = 107.16
_1X = 30.0
_SD = dict(
    rpm=1800.0,
    x_velocity_mm_sec=1.0, y_velocity_mm_sec=3.0, z_velocity_mm_sec=3.0,
    x_rms_ACC_G=0.05, y_rms_ACC_G=0.10, z_rms_ACC_G=0.10,
)
#: Acceleration-only: no velocity anywhere, so no ISO zone and no severity_rms —
#: the CWRU/MFPT/unscaled-WAV shape, where only unit-relative answers exist.
_SD_ACCEL = dict(rpm=1800.0, x_rms_ACC_G=0.05, y_rms_ACC_G=0.10, z_rms_ACC_G=0.10)


def _case(machine, peaks, *, sd=None, kind="velocity", seed=7, fmax=500.0, lines=2000,
          name="cmp"):
    spectra = {
        axis: spectrum.model_copy(update={"kind": kind})
        for axis, spectrum in make_spectrum(peaks, seed=seed, fmax=fmax, lines=lines).items()
    }
    return Case(name=name, machine=machine, sensor_data=SensorData(**(sd or _SD)),
                spectra=spectra)


def _pair(machine, iso_table, thresholds, rules, before: Case, after: Case) -> ComparisonResult:
    rb = run_analysis(before, iso_table=iso_table, thresholds=thresholds, rules=rules)
    ra = run_analysis(after, iso_table=iso_table, thresholds=thresholds, rules=rules)
    return C.compare_readings(rb, ra, before_case=before, after_case=after,
                              cfg=thresholds.get("compare"))


def _band(comparison: ComparisonResult, axis: str, name: str):
    for spectrum in comparison.spectra:
        if spectrum.axis == axis:
            for band in spectrum.bands:
                if band.name == name:
                    return band
    raise AssertionError(f"no band {name} on axis {axis}")


def _axis(comparison: ComparisonResult, axis: str):
    for spectrum in comparison.spectra:
        if spectrum.axis == axis:
            return spectrum
    raise AssertionError(f"no comparison on axis {axis}")


# ─────────────────────────────────────────────────────────────────────────
# The constants are in-code defaults — the whole session's threshold story
# ─────────────────────────────────────────────────────────────────────────


class TestConstantsAreInCodeDefaults:
    def test_no_shipped_profile_carries_a_compare_block(self, thresholds):
        """`config/thresholds.json` was NOT edited by HIST-2. If a later session
        adds a `compare` block, this test fails and that session has to say so —
        which is the point: today every constant in compare.py is its own
        published default, and the report's significance note is the whole
        threshold story an analyst is owed."""
        assert "compare" not in thresholds

    def test_defaults_apply_when_the_block_is_absent(self):
        assert C._c(None, "significant_ratio", C.SIGNIFICANT_RATIO) == 1.25
        assert C._c({}, "significant_ratio", C.SIGNIFICANT_RATIO) == 1.25
        assert C._c({"significant_ratio": 2.0}, "significant_ratio", C.SIGNIFICANT_RATIO) == 2.0

    def test_significance_is_symmetric_in_db(self):
        """1.25 up and 0.80 down are the same 1.94 dB. A percent-based rule would
        judge a rise and an equal fall differently, which is why the constant is
        a ratio."""
        assert C._verdict(1.0, 1.25, C.SIGNIFICANT_RATIO) == "worsened"
        assert C._verdict(1.25, 1.0, C.SIGNIFICANT_RATIO) == "improved"
        assert C._verdict(1.0, 1.24, C.SIGNIFICANT_RATIO) == "no_significant_change"
        assert C._verdict(1.24, 1.0, C.SIGNIFICANT_RATIO) == "no_significant_change"

    def test_verdict_vocabulary_is_closed(self):
        """The honest idioms and nothing else. A fifth word would be a claim
        nobody ratified."""
        assert set(C._VERDICT_WORD) == {
            "worsened", "improved", "no_significant_change", "not_assessable"
        }

    def test_the_repair_vocabulary_never_says_repaired(self):
        """A spectrum cannot witness a repair, only the absence of the signature
        that justified one. Pinned on what the module EMITS rather than on its
        source text: the printable vocabulary and every word it can return."""
        assert set(C._REPAIR_WORD) == {
            "consistent_with_repair", "not_verified", "not_applicable"
        }
        assert set(C._REPAIR_WORD.values()) == {
            "consistent with repair", "not verified", "not applicable"
        }
        for word in C._REPAIR_WORD.values():
            assert "repaired" not in word


# ─────────────────────────────────────────────────────────────────────────
# Identical readings — the null case that proves nothing is invented
# ─────────────────────────────────────────────────────────────────────────


class TestRepairRegister:
    def test_no_produced_sentence_says_repaired(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """The evidence strings are authored in compare.py, so this is where the
        register is enforced — every sentence a repair check can produce, over
        the verified, the unverified and the unmeasurable case."""
        pairs = [
            # verified: the evidence peak is gone
            ({"y": [(_BPFO, 0.40)], "z": [(_BPFO, 0.36)]}, {"y": [], "z": []}),
            # not verified: the After screen committed it too
            ({"y": [(_BPFO, 0.40)], "z": [(_BPFO, 0.36)]},
             {"y": [(_BPFO, 0.30)], "z": [(_BPFO, 0.27)]}),
            # verified by amplitude: still measurable, far below the evidence floor
            ({"y": [(_BPFO, 0.40)], "z": [(_BPFO, 0.36)]},
             {"y": [(_BPFO, 0.005)], "z": [(_BPFO, 0.0045)]}),
        ]
        seen = set()
        for before_peaks, after_peaks in pairs:
            before = _case(comp_machine, before_peaks)
            after = _case(comp_machine, after_peaks,
                          sd={**_SD, "y_velocity_mm_sec": 1.0, "z_velocity_mm_sec": 1.0})
            result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
            for check in result.repair:
                seen.add(check.verdict)
                assert "repaired" not in check.evidence
            assert "repaired" not in result.headline
        assert seen == {"consistent_with_repair", "not_verified"}


class TestIdenticalReadings:
    def test_identical_files_produce_null_deltas(self, comp_machine, iso_table, thresholds, rules):
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)], "z": [(_BPFO, 0.36)]})
        after = _case(comp_machine, {"y": [(_BPFO, 0.40)], "z": [(_BPFO, 0.36)]})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)

        assert result.status == "ok"
        assert result.verdict == "no_significant_change"
        assert result.overall.delta_mms == 0.0
        assert result.overall.pct_change == 0.0
        assert result.overall.zone_movement == "same"
        assert result.overall.zone_note is None
        for spectrum in result.spectra:
            assert [b.verdict for b in spectrum.bands] == ["no_significant_change"] * len(spectrum.bands)
            assert spectrum.new_frequencies == []
            assert spectrum.gone_frequencies == []
            assert all(p.pct_change == 0.0 for p in spectrum.matched)

    def test_identical_files_do_not_claim_a_repair(self, comp_machine, iso_table, thresholds, rules):
        """The Before analysis committed a bearing fault and the After committed
        the same one. Nothing was fixed, and the comparison must not imply it
        was — the exact failure mode a Before/After feature invites."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)], "z": [(_BPFO, 0.36)]})
        after = _case(comp_machine, {"y": [(_BPFO, 0.40)], "z": [(_BPFO, 0.36)]})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)

        assert result.repair_verdict == "not_verified"
        assert [c.after_committed for c in result.repair] == [True]
        assert "committed this same fault" in result.repair[0].evidence
        assert "consistent with repair" not in result.headline


# ─────────────────────────────────────────────────────────────────────────
# Overall level
# ─────────────────────────────────────────────────────────────────────────


class TestOverallChange:
    def test_a_rise_past_the_ratio_is_worsened(self, comp_machine, iso_table, thresholds, rules):
        before = _case(comp_machine, {"y": [(_1X, 0.20)]})
        after = _case(comp_machine, {"y": [(_1X, 0.20)]},
                      sd={**_SD, "y_velocity_mm_sec": 6.0, "z_velocity_mm_sec": 6.0})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.overall.verdict == "worsened"
        assert result.verdict == "worsened"
        assert result.overall.pct_change == 100.0
        assert result.overall.zone_movement == "up"
        assert "worsened" in result.headline

    def test_a_fall_past_the_ratio_is_improved(self, comp_machine, iso_table, thresholds, rules):
        before = _case(comp_machine, {"y": [(_1X, 0.20)]})
        after = _case(comp_machine, {"y": [(_1X, 0.20)]},
                      sd={**_SD, "y_velocity_mm_sec": 1.0, "z_velocity_mm_sec": 1.0})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.overall.verdict == "improved"
        assert result.overall.zone_movement == "down"

    def test_a_small_move_is_no_significant_change(self, comp_machine, iso_table, thresholds, rules):
        before = _case(comp_machine, {"y": [(_1X, 0.20)]})
        after = _case(comp_machine, {"y": [(_1X, 0.20)]},
                      sd={**_SD, "y_velocity_mm_sec": 3.3, "z_velocity_mm_sec": 3.3})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.overall.verdict == "no_significant_change"
        assert result.overall.pct_change == 10.0

    def test_a_zone_crossed_by_noise_is_stated_and_not_called_a_change(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """group2/rigid puts the B/C boundary at 2.8 mm/s. 2.7 -> 2.9 crosses it
        on a 7.4% move: a fact about the boundary, not a change in the machine.
        The zone movement is REPORTED and the verdict stays honest."""
        before = _case(comp_machine, {"y": [(_1X, 0.20)]},
                       sd={**_SD, "y_velocity_mm_sec": 2.7, "z_velocity_mm_sec": 2.7})
        after = _case(comp_machine, {"y": [(_1X, 0.20)]},
                      sd={**_SD, "y_velocity_mm_sec": 2.9, "z_velocity_mm_sec": 2.9})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.overall.zone_before == "B"
        assert result.overall.zone_after == "C"
        assert result.overall.zone_movement == "up"
        assert result.overall.verdict == "no_significant_change"
        assert result.overall.zone_note is not None
        assert "did not clear the significance ratio" in result.overall.zone_note

    def test_acceleration_only_gets_bands_but_no_mm_s_claim(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """No velocity means no ISO zone and no severity_rms — subtracting them
        would be arithmetic on numbers the product has already refused to
        interpret. Band ratios are unit-free, so those still answer."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)]}, sd=_SD_ACCEL)
        after = _case(comp_machine, {"y": [(_BPFO, 0.10)]}, sd=_SD_ACCEL)
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)

        assert result.status == "ok"
        assert result.overall.verdict == "not_assessable"
        assert result.overall.reason is not None
        assert result.overall.delta_mms is None
        assert _band(result, "y", "harmonics").verdict == "improved"
        assert result.verdict == "improved"
        assert "unit-relative" in result.headline
        assert any("mm/s" in line for line in result.what_to_collect)

    def test_an_implausible_velocity_is_not_compared(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """`mark_not_assessable` PRESERVES severity_rms while withholding the
        zone. The number is still on the model, so a naive comparison would
        happily subtract two values the product refused to classify."""
        before = _case(comp_machine, {"y": [(_1X, 0.20)]},
                       sd={**_SD, "y_velocity_mm_sec": 3.0, "z_velocity_mm_sec": 3.0})
        after = _case(comp_machine, {"y": [(_1X, 0.20)]},
                      sd={**_SD, "y_velocity_mm_sec": 9000.0, "z_velocity_mm_sec": 9000.0})
        ra = run_analysis(after, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert ra.iso.iso_zone == "not_assessable"
        assert ra.iso.severity_rms is not None  # the trap this test exists for

        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.overall.verdict == "not_assessable"
        assert result.overall.delta_mms is None
        assert "After" in (result.overall.reason or "")


# ─────────────────────────────────────────────────────────────────────────
# Bands and peaks
# ─────────────────────────────────────────────────────────────────────────


class TestBands:
    def test_a_band_moves_only_where_the_energy_moved(
        self, comp_machine, iso_table, thresholds, rules
    ):
        before = _case(comp_machine, {"y": [(_1X, 0.20), (_BPFO, 0.05)]})
        after = _case(comp_machine, {"y": [(_1X, 0.20), (_BPFO, 0.50)]})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert _band(result, "y", "harmonics").verdict == "worsened"
        assert _band(result, "y", "one_x").verdict == "no_significant_change"
        assert _band(result, "y", "sub_synchronous").verdict == "no_significant_change"

    def test_bands_are_named_in_shaft_orders_and_hz(
        self, comp_machine, iso_table, thresholds, rules
    ):
        before = _case(comp_machine, {"y": [(_1X, 0.20)]})
        after = _case(comp_machine, {"y": [(_1X, 0.20)]})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        one_x = _band(result, "y", "one_x")
        assert (one_x.lo_order, one_x.hi_order) == (0.8, 1.2)
        assert one_x.lo_hz == pytest.approx(24.0, abs=0.01)   # 0.8 x 30 Hz
        assert one_x.hi_hz == pytest.approx(36.0, abs=0.01)   # 1.2 x 30 Hz


class TestTheHeadlineCannotHideABand:
    def test_a_band_moving_against_the_verdict_is_named(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """The failure a one-line summary invites: an overall velocity falls
        because imbalance was corrected, while a bearing tone grows underneath
        it. "Improved" alone would be true AND misleading, so the headline names
        what moved the other way."""
        before = _case(comp_machine, {"y": [(_1X, 0.60), (_BPFO, 0.02)]})
        after = _case(comp_machine, {"y": [(_1X, 0.60), (_BPFO, 0.50)]},
                      sd={**_SD, "y_velocity_mm_sec": 1.0, "z_velocity_mm_sec": 1.0})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)

        assert result.verdict == "improved"          # the overall level did fall
        assert _band(result, "y", "harmonics").verdict == "worsened"
        assert "Not every band moved with it" in result.headline
        assert "Shaft harmonics worsened" in result.headline

    def test_bands_moving_with_the_verdict_are_not_called_out(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """The note appears only when there is a disagreement to report. A
        headline that always hedged would be noise."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.50)]})
        after = _case(comp_machine, {"y": [(_BPFO, 0.05)]},
                      sd={**_SD, "y_velocity_mm_sec": 1.0, "z_velocity_mm_sec": 1.0})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.verdict == "improved"
        assert "Not every band moved with it" not in result.headline


class TestPeaks:
    def test_a_new_frequency_is_found(self, comp_machine, iso_table, thresholds, rules):
        before = _case(comp_machine, {"y": [(_1X, 0.20)]})
        after = _case(comp_machine, {"y": [(_1X, 0.20), (_BPFO, 0.40)]})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        new = [p.after_hz for p in _axis(result, "y").new_frequencies]
        assert any(abs(f - _BPFO) < 1.0 for f in new), new
        assert _axis(result, "y").gone_frequencies == []

    def test_a_gone_frequency_is_found(self, comp_machine, iso_table, thresholds, rules):
        before = _case(comp_machine, {"y": [(_1X, 0.20), (_BPFO, 0.40)]})
        after = _case(comp_machine, {"y": [(_1X, 0.20)]})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        gone = [p.before_hz for p in _axis(result, "y").gone_frequencies]
        assert any(abs(f - _BPFO) < 1.0 for f in gone), gone

    def test_a_matched_peak_carries_its_amplitude_change(
        self, comp_machine, iso_table, thresholds, rules
    ):
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)]})
        after = _case(comp_machine, {"y": [(_BPFO, 0.20)]})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        matched = [p for p in _axis(result, "y").matched if abs((p.before_hz or 0) - _BPFO) < 1.0]
        assert len(matched) == 1
        assert matched[0].pct_change == pytest.approx(-50.0, abs=1.0)
        assert matched[0].order == pytest.approx(3.57, abs=0.05)   # 107.16 / 30

    def test_a_peak_inside_the_window_still_pairs(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """2% off, tolerance 3% — one moved peak, not one gone plus one new."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)]})
        after = _case(comp_machine, {"y": [(_BPFO * 1.02, 0.40)]})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        spectrum = _axis(result, "y")
        assert any(abs((p.before_hz or 0) - _BPFO) < 1.0 for p in spectrum.matched)
        assert spectrum.gone_frequencies == []
        assert spectrum.new_frequencies == []

    def test_a_peak_outside_the_window_is_gone_and_new(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """8% off is a different tone, not the same one moved — pairing it would
        be the comparison calling two frequencies the diagnosis distinguishes
        'the same peak'."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)]})
        after = _case(comp_machine, {"y": [(_BPFO * 1.08, 0.40)]})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        spectrum = _axis(result, "y")
        assert [round(p.before_hz or 0) for p in spectrum.gone_frequencies] == [round(_BPFO)]
        assert len(spectrum.new_frequencies) == 1

    def test_broadband_noise_is_not_reported_as_peaks(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """Two spectra with no tones at all. The picker still returns its top-N
        of the noise floor; the floor keeps them out of the report, because a
        table of twelve rows each moving 0% reads as detail and is noise."""
        before = _case(comp_machine, {"y": []}, seed=3)
        after = _case(comp_machine, {"y": []}, seed=9)
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        spectrum = _axis(result, "y")
        assert spectrum.matched == []
        assert spectrum.new_frequencies == []
        assert spectrum.gone_frequencies == []


# ─────────────────────────────────────────────────────────────────────────
# Repair verification
# ─────────────────────────────────────────────────────────────────────────


class TestRepairVerification:
    def test_the_evidence_peak_gone_is_consistent_with_repair(
        self, comp_machine, iso_table, thresholds, rules
    ):
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)], "z": [(_BPFO, 0.36)]})
        after = _case(comp_machine, {"y": [], "z": []},
                      sd={**_SD, "y_velocity_mm_sec": 1.0, "z_velocity_mm_sec": 1.0})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)

        assert [c.fault for c in result.repair] == ["bearing_outer_race"]
        assert result.repair[0].verdict == "consistent_with_repair"
        assert result.repair_verdict == "consistent_with_repair"
        assert "consistent with repair" in result.headline
        assert "repaired" not in result.headline

    def test_a_halved_evidence_peak_is_consistent_with_repair(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """The peak is still MEASURED — it clears compare's own 3x-mean report
        floor — but has fallen under the RCA's 12.73x evidence floor, so the
        After screen no longer commits the fault. That is the case the amplitude
        rule exists for: a peak that is still there, and much smaller."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)], "z": [(_BPFO, 0.36)]})
        after = _case(comp_machine, {"y": [(_BPFO, 0.005)], "z": [(_BPFO, 0.0045)]},
                      sd={**_SD, "y_velocity_mm_sec": 1.0, "z_velocity_mm_sec": 1.0})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.repair[0].after_committed is False
        assert result.repair[0].verdict == "consistent_with_repair"
        assert result.repair[0].after_amp is not None
        assert result.repair[0].pct_change is not None
        assert "% of" in result.repair[0].evidence

    def test_a_still_committed_fault_is_never_verified(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """`after_committed` is decisive and comes BEFORE any amplitude
        arithmetic: if the After screen committed the fault on its own evidence,
        no ratio can overrule the screen that just ran."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)], "z": [(_BPFO, 0.36)]})
        after = _case(comp_machine, {"y": [(_BPFO, 0.30)], "z": [(_BPFO, 0.27)]})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.repair[0].after_committed is True
        assert result.repair[0].verdict == "not_verified"
        assert result.repair_verdict == "not_verified"
        assert "not verified" in result.headline

    def test_no_committed_fault_before_means_nothing_to_verify(
        self, comp_machine, iso_table, thresholds, rules
    ):
        before = _case(comp_machine, {"y": [(_1X, 0.20)]},
                       sd={**_SD, "y_velocity_mm_sec": 1.0, "z_velocity_mm_sec": 1.0})
        after = _case(comp_machine, {"y": [(_1X, 0.20)]},
                      sd={**_SD, "y_velocity_mm_sec": 1.0, "z_velocity_mm_sec": 1.0})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.repair == []
        assert result.repair_verdict == "not_applicable"
        assert "repair" not in result.headline.lower()

    def test_an_unmeasurable_evidence_peak_is_not_verified(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """The After capture stops below the fault frequency. Its absence from a
        span nobody measured is not evidence of anything, and the comparison says
        exactly that rather than counting silence as a repair."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)], "z": [(_BPFO, 0.36)]},
                       fmax=1000.0, lines=4000)
        after = _case(comp_machine, {"y": [(_1X, 0.05)], "z": [(_1X, 0.05)]},
                      sd={**_SD, "y_velocity_mm_sec": 1.0, "z_velocity_mm_sec": 1.0},
                      fmax=100.0, lines=400)
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.repair[0].verdict == "not_verified"
        assert "could not be re-measured" in result.repair[0].evidence
        assert any("Fmax" in line for line in result.what_to_collect)


# ─────────────────────────────────────────────────────────────────────────
# Refusals — the half of this module that says no
# ─────────────────────────────────────────────────────────────────────────


def _bare_result(*, gate="pass", rca: RcaResult | None = None) -> AnalysisResult:
    """An AnalysisResult built by hand, for the two states run_analysis cannot
    reach from a Case: a crashed RCA (`run_rca` swallows its own exception) and
    a machine-off RCA behind a PASSING gate (a stopped machine fails the gate)."""
    return AnalysisResult(
        machine_id="M", mac="MAC", ts="2026-09-01T00:00:00Z",
        quality_gate=QualityGateResult(
            overall=gate,
            checks=[Check(name="x", status="fail", reason="planted")] if gate == "fail" else [],
            train_baseline=False,
        ),
        rca=rca,
    )


class TestRefusals:
    def test_a_crashed_analysis_raises_rather_than_comparing(self, comp_machine):
        """S12FIX one layer out. `run_rca` swallows every exception and returns
        status="error", so a comparison would happily report "no new
        frequencies" about a spectrum nothing ever screened — the clean-bill
        hazard in different clothes. `analysis_to_expected` refuses the same way
        for the same reason."""
        crashed = _bare_result(rca=RcaResult(status="error", reason="missing weight key"))
        healthy = _bare_result(rca=RcaResult(status="ok"))
        case = Case(name="x", machine=comp_machine, sensor_data=SensorData(**_SD))
        with pytest.raises(ValueError, match="the Before analysis failed"):
            C.compare_readings(crashed, healthy, before_case=case, after_case=case)
        with pytest.raises(ValueError, match="the After analysis failed"):
            C.compare_readings(healthy, crashed, before_case=case, after_case=case)

    def test_machine_off_is_not_the_other_end_of_a_before_after(self, comp_machine):
        stopped = _bare_result(rca=RcaResult(status="machine_off"))
        running = _bare_result(rca=RcaResult(status="ok"))
        case = Case(name="x", machine=comp_machine, sensor_data=SensorData(**_SD))
        result = C.compare_readings(running, stopped, before_case=case, after_case=case)
        assert result.status == "not_comparable"
        assert result.reason_code == "machine_not_running"
        assert result.overall is None and result.spectra == []
        assert result.what_to_collect

    def test_different_running_speeds_are_not_comparable(
        self, comp_machine, iso_table, thresholds, rules
    ):
        before = _case(comp_machine, {"y": [(_1X, 0.20)]})
        after = _case(comp_machine, {"y": [(36.0, 0.20)]}, sd={**_SD, "rpm": 2160.0})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.status == "not_comparable"
        assert result.reason_code == "speed_mismatch"
        assert "1800" in result.reason and "2160" in result.reason

    def test_a_speed_drift_inside_the_window_still_compares(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """1% is a real machine on a real day, not two different measurements."""
        before = _case(comp_machine, {"y": [(_1X, 0.20)]})
        after = _case(comp_machine, {"y": [(_1X, 0.20)]}, sd={**_SD, "rpm": 1818.0})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.status == "ok"

    def test_a_gate_fail_blocks_the_comparison_and_says_what_to_recapture(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """NOT `not_comparable`: the files are a fine pair, the DATA is bad. The
        product already has a channel for that — the insufficient-data report —
        and CLAUDE.md makes it the only valid downstream output of a gate fail."""
        before = _case(comp_machine, {"y": [(_1X, 0.20)]})
        after = _case(comp_machine, {"y": [(_1X, 0.20)]},
                      sd={**_SD, "x_rms_ACC_G": 0.001, "y_rms_ACC_G": 0.001,
                          "z_rms_ACC_G": 0.001})
        rb = run_analysis(before, iso_table=iso_table, thresholds=thresholds, rules=rules)
        ra = run_analysis(after, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert ra.quality_gate.overall == "fail"

        result = C.compare_readings(rb, ra, before_case=before, after_case=after,
                                    cfg=thresholds.get("compare"))
        assert result.status == "gate_blocked"
        assert result.reason_code == "gate_fail"
        assert result.what_to_collect and result.what_to_collect[0].startswith("Re-capture the After")
        assert result.overall is None
        assert "data-quality gate" in result.headline

    def test_both_readings_failing_the_gate_names_both(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """Two bad captures are still not an error — they are two re-captures to
        ask for, and the reason has to name BOTH or the analyst repeats only one
        of them."""
        silent = {**_SD, "x_rms_ACC_G": 0.001, "y_rms_ACC_G": 0.001, "z_rms_ACC_G": 0.001}
        before = _case(comp_machine, {"y": [(_1X, 0.20)]}, sd=silent)
        after = _case(comp_machine, {"y": [(_1X, 0.20)]}, sd=silent)
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)

        assert result.status == "gate_blocked"
        assert result.reason_code == "gate_fail"
        assert "the Before reading" in result.reason
        assert "the After reading" in result.reason
        assert len(result.what_to_collect) == 2
        assert result.what_to_collect[0].startswith("Re-capture the Before")
        assert result.what_to_collect[1].startswith("Re-capture the After")
        # Still not an error, and still not a comparison: no verdict was reached.
        assert result.verdict == "not_assessable"
        assert result.repair_verdict == "not_applicable"

    def test_different_measurement_types_with_no_velocity_are_not_comparable(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """An envelope spectrum and a velocity spectrum measure different things.
        With no ISO-assessable velocity either, nothing at all can be compared —
        the one structural refusal left once every partial answer is given."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)]}, sd=_SD_ACCEL, kind="velocity")
        after = _case(comp_machine, {"y": [(_BPFO, 0.40)]}, sd=_SD_ACCEL, kind="envelope")
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.status == "not_comparable"
        assert result.reason_code == "no_common_evidence"
        assert "different measurement types" in result.reason

    def test_different_line_resolutions_are_not_compared_band_for_band(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """Band levels are sums over bins, so two spectra with different line
        resolutions would compare the export settings, not the machine.
        Resampling an amplitude spectrum is a DSP choice this module will not
        make silently."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)]}, sd=_SD_ACCEL, lines=2000)
        after = _case(comp_machine, {"y": [(_BPFO, 0.40)]}, sd=_SD_ACCEL, lines=500)
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.status == "not_comparable"
        assert result.reason_code == "no_common_evidence"
        assert "line resolutions" in result.reason

    def test_too_little_common_span_is_not_compared(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """Shaft is 30 Hz; a 50 Hz span is 1.7 orders — sub-synchronous plus the
        1x line and nothing above it, which is where the energy that changes
        lives. A 200 Hz capture (6.7 orders) is narrow but real and IS
        compared; that boundary is pinned by the test below."""
        before = _case(comp_machine, {"y": [(_1X, 0.40)]}, sd=_SD_ACCEL,
                       fmax=50.0, lines=200)
        after = _case(comp_machine, {"y": [(_1X, 0.40)]}, sd=_SD_ACCEL,
                      fmax=50.0, lines=200)
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.status == "not_comparable"
        assert result.reason_code == "no_common_evidence"
        assert "x shaft" in result.reason

    def test_the_products_own_narrow_capture_is_still_compared(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """200 Hz at 1800 rpm is 6.7 orders — the shape of this product's own
        example file and of tests/test_webapp_e2e.py's fixtures. It gets a
        smaller, truthful table, never a refusal."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)]}, fmax=200.0, lines=400)
        after = _case(comp_machine, {"y": [(_BPFO, 0.10)]}, fmax=200.0, lines=400,
                      sd={**_SD, "y_velocity_mm_sec": 1.0, "z_velocity_mm_sec": 1.0})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.status == "ok"
        harmonics = _band(result, "y", "harmonics")
        # The band is DEFINED as 1.2-10x and here covers 1.2-6.6x. It reports
        # what it covers, so the order span and the Hz span agree.
        assert harmonics.hi_order == pytest.approx(harmonics.hi_hz / 30.0, abs=0.02)
        assert harmonics.hi_order < 10.0
        assert [b.name for b in _axis(result, "y").bands] == [
            "sub_synchronous", "one_x", "harmonics"
        ]  # no high-frequency band: the capture never reached 10x

    def test_a_partial_refusal_still_answers_what_it_can(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """Spectra that cannot be compared do NOT sink a pair whose overall mm/s
        can be. Refusal is per-question, and the unanswered one says why."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)]}, lines=2000)
        after = _case(comp_machine, {"y": [(_BPFO, 0.40)]}, lines=500,
                      sd={**_SD, "y_velocity_mm_sec": 1.0, "z_velocity_mm_sec": 1.0})
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        assert result.status == "ok"
        assert result.spectra == []
        assert "line resolutions" in (result.spectra_note or "")
        assert result.overall.verdict == "improved"
        assert any("same Fmax" in line for line in result.what_to_collect)


# ─────────────────────────────────────────────────────────────────────────
# Common-span truncation
# ─────────────────────────────────────────────────────────────────────────


class TestCommonSpan:
    def test_the_wider_spectrum_is_truncated_to_the_common_span(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """0-1000 Hz against 0-500 Hz at the same bin width: compare the 0-500
        both actually measured, and say so. A band summed over a span only one
        reading covers would report the capture setting as a change."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)]}, fmax=1000.0, lines=4000)
        after = _case(comp_machine, {"y": [(_BPFO, 0.40)]}, fmax=500.0, lines=2000)
        result = _pair(comp_machine, iso_table, thresholds, rules, before, after)
        spectrum = _axis(result, "y")
        assert spectrum.truncated is True
        assert spectrum.fmax_hz == pytest.approx(499.75, abs=0.5)
        assert "common span" in (spectrum.truncation_note or "")
        assert _band(result, "y", "harmonics").verdict == "no_significant_change"

    def test_an_equal_span_is_not_reported_as_truncated(
        self, comp_machine, iso_table, thresholds, rules
    ):
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)]})
        after = _case(comp_machine, {"y": [(_BPFO, 0.40)]})
        assert _axis(_pair(comp_machine, iso_table, thresholds, rules, before, after),
                     "y").truncated is False


# ─────────────────────────────────────────────────────────────────────────
# Purity — the pdm_core contract
# ─────────────────────────────────────────────────────────────────────────


class TestPurity:
    def test_the_module_imports_nothing_it_should_not(self):
        source = open(C.__file__).read()
        for banned in ("import anthropic", "open(", "Path(", "requests", "logging"):
            assert banned not in source, banned

    def test_comparing_does_not_mutate_either_input(
        self, comp_machine, iso_table, thresholds, rules
    ):
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)]})
        after = _case(comp_machine, {"y": [(_BPFO, 0.10)]})
        rb = run_analysis(before, iso_table=iso_table, thresholds=thresholds, rules=rules)
        ra = run_analysis(after, iso_table=iso_table, thresholds=thresholds, rules=rules)
        snapshot = (rb.model_dump_json(), ra.model_dump_json(),
                    before.model_dump_json(), after.model_dump_json())
        C.compare_readings(rb, ra, before_case=before, after_case=after)
        assert (rb.model_dump_json(), ra.model_dump_json(),
                before.model_dump_json(), after.model_dump_json()) == snapshot

    def test_the_same_inputs_compare_identically_twice(
        self, comp_machine, iso_table, thresholds, rules
    ):
        """Determinism is this module's, not the pipeline's: `AnalysisResult.ts`
        is stamped per run, so re-running run_analysis is a different INPUT, not
        a different answer. The same two results must compare byte-identically."""
        before = _case(comp_machine, {"y": [(_BPFO, 0.40)]})
        after = _case(comp_machine, {"y": [(_BPFO, 0.10)]})
        rb = run_analysis(before, iso_table=iso_table, thresholds=thresholds, rules=rules)
        ra = run_analysis(after, iso_table=iso_table, thresholds=thresholds, rules=rules)
        first = C.compare_readings(rb, ra, before_case=before, after_case=after)
        second = C.compare_readings(rb, ra, before_case=before, after_case=after)
        assert first.model_dump_json() == second.model_dump_json()

    def test_a_spectrum_with_one_bin_is_declined_not_crashed(self, comp_machine):
        tiny = Spectrum(freq_hz=[0.0], amplitude=[1.0], fmax_hz=0.0, kind="velocity")
        case = Case(name="x", machine=comp_machine, sensor_data=SensorData(**_SD_ACCEL),
                    spectra={"y": tiny})
        result = C.compare_readings(_bare_result(rca=RcaResult(status="ok")),
                                    _bare_result(rca=RcaResult(status="ok")),
                                    before_case=case, after_case=case)
        assert result.status == "not_comparable"
        assert "too few bins" in result.reason
