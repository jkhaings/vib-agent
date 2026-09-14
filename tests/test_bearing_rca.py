"""Layer 5 tests: bearing fault frequencies, the PeakSet adapters (Amendment
A3), all seven detectors via the reference's named fault fixtures, and the
detector-interaction rules (looseness downgrades misalignment; imbalance is
suppressed once bearing/looseness/misalignment already matched).
"""

from __future__ import annotations

import pytest

from tests.fixtures import REFERENCE_CASES
from vib_agent.config import load_thresholds
from vib_agent.models import Axis, BearingSpec, MachineMeta, Peak, PeakSet, SensorData, Spectrum
from vib_agent.pdm_core.bearing_rca import (
    bearing_frequencies,
    peaks_from_ncd,
    peaks_from_spectrum,
    run_rca,
)
from vib_agent.pdm_core.iso_classify import classify, resolve_thresholds


def _rca_for(case_name: str, machines: dict, iso_table: dict, thresholds: dict):
    case = REFERENCE_CASES[case_name]
    machine = machines[case["mac"]]
    sensor_data = SensorData(**case["sensor_data"])
    resolved = resolve_thresholds(machine, iso_table)
    reading = classify(sensor_data, machine, resolved)
    peak_set = peaks_from_ncd(sensor_data)
    result = run_rca(peak_set, machine, reading.iso_severity, thresholds)
    return reading, result


def _synthetic_spectrum(peaks: list[tuple[float, float]], fmax: float = 500.0, lines: int = 2000) -> Spectrum:
    freqs = [i * fmax / lines for i in range(lines)]
    amp = [0.001] * lines
    for target_freq, target_amp in peaks:
        idx = round(target_freq / fmax * lines)
        for offset in (-1, 0, 1):
            i = idx + offset
            if 0 <= i < lines:
                amp[i] = max(amp[i], target_amp if offset == 0 else target_amp * 0.3)
    return Spectrum(freq_hz=freqs, amplitude=amp, fmax_hz=fmax)


class TestBearingFrequencies:
    def test_6206_bpfo_matches_t12_fixture(self, comp_machine):
        # T12's BPFO peak is 107.16 Hz @ 1800 rpm (shaft=30Hz). The 6206
        # reconstruction should land within a fraction of a percent.
        freqs = bearing_frequencies(comp_machine.bearing, shaft_hz=30.0)
        assert freqs.BPFO == pytest.approx(107.16, abs=0.5)


class TestPeaksFromNcd:
    def test_extracts_nine_peaks_and_velocities(self):
        sensor_data = SensorData(**REFERENCE_CASES["T01_healthy_zone_a"]["sensor_data"])
        peak_set = peaks_from_ncd(sensor_data)
        assert peak_set.source == "ncd_triplet"
        assert len(peak_set.peaks) == 9
        assert peak_set.shaft_freq_hz == pytest.approx(30.0)  # 1800/60
        assert peak_set.velocities_mms["y"] == pytest.approx(0.3)


class TestDetectorsAgainstReferenceFixtures:
    """Each case reproduces the diagnosis its reference test name asserts."""

    def test_t07_imbalance(self, machines, iso_table, thresholds):
        _, result = _rca_for("T07_imbalance", machines, iso_table, thresholds)
        assert "imbalance" in {m.fault for m in result.primary_findings}

    def test_t08_bent_shaft_not_angular_misalignment(self, machines, iso_table, thresholds):
        # Diagnosis only reproduces as bent_shaft (not angular_misalignment)
        # because fan_machine.coupled == False.
        _, result = _rca_for("T08_bent_shaft_uncoupled", machines, iso_table, thresholds)
        faults = {m.fault for m in result.primary_findings}
        assert "bent_shaft" in faults
        assert "angular_misalignment" not in faults

    def test_t09_angular_misalignment(self, machines, iso_table, thresholds):
        _, result = _rca_for("T09_angular_misalignment", machines, iso_table, thresholds)
        assert "angular_misalignment" in {m.fault for m in result.primary_findings}

    def test_t10_parallel_misalignment(self, machines, iso_table, thresholds):
        _, result = _rca_for("T10_parallel_misalignment", machines, iso_table, thresholds)
        assert "parallel_misalignment" in {m.fault for m in result.primary_findings}

    def test_t11_looseness(self, machines, iso_table, thresholds):
        _, result = _rca_for("T11_looseness", machines, iso_table, thresholds)
        assert "mechanical_looseness" in {m.fault for m in result.primary_findings}

    def test_t12_bpfo(self, machines, iso_table, thresholds):
        _, result = _rca_for("T12_bpfo_bearing_fault", machines, iso_table, thresholds)
        bpfo_matches = [m for m in result.primary_findings if m.fault == "bearing_outer_race"]
        assert bpfo_matches
        assert bpfo_matches[0].confidence == "high"
        assert bpfo_matches[0].harmonic_present is True  # 2x BPFO also present in fixture
        assert bpfo_matches[0].bearing_model == "6206"

    def test_t13_severe_misalignment(self, machines, iso_table, thresholds):
        _, result = _rca_for("T13_severe_misalignment", machines, iso_table, thresholds)
        assert "severe_misalignment" in {m.fault for m in result.primary_findings}


class TestDetectorInteractionRules:
    def test_looseness_downgrades_misalignment_to_differential(self, machines, iso_table, thresholds):
        # T11 triggers both detectors: looseness (subharmonic + multiple
        # harmonics) AND the misalignment trigger condition. Under the
        # committed-differential contract, looseness is the committed primary
        # and the misalignment candidate is moved to the differential with an
        # adjudication reason (not dropped, not left low-confidence in primary).
        _, result = _rca_for("T11_looseness", machines, iso_table, thresholds)
        assert "mechanical_looseness" in {m.fault for m in result.primary_findings}

        misalignment_faults = {
            "angular_misalignment", "parallel_misalignment", "severe_misalignment",
            "misalignment_general", "bent_shaft",
        }
        downgraded = [d for d in result.differential if d.fault in misalignment_faults]
        assert downgraded, "expected a co-occurring misalignment candidate in the differential"
        for d in downgraded:
            assert "looseness" in d.adjudication.lower()
        # and it is NOT among the committed primary findings
        assert not (misalignment_faults & {m.fault for m in result.primary_findings})

    def test_imbalance_suppressed_to_differential_when_bearing_present(self, comp_machine, thresholds):
        # Hand-built PeakSet: a clean BPFO signature on y, PLUS a 1x radial
        # peak on z that would otherwise satisfy detect_imbalance's trigger
        # (has_1x_radial and not has_1x_axial). Bearing wins the committed
        # call; imbalance is surfaced in the differential with its adjudication.
        peak_set = PeakSet(
            source="ncd_triplet",
            shaft_freq_hz=30.0,
            rpm=1800.0,
            velocities_mms={"x": 0.5, "y": 5.2, "z": 1.0},
            peaks=[
                Peak(axis="x", freq=41.2, rank=1),
                Peak(axis="x", freq=53.7, rank=2),
                Peak(axis="x", freq=84.1, rank=3),
                Peak(axis="y", freq=107.16, rank=1),
                Peak(axis="y", freq=214.32, rank=2),
                Peak(axis="y", freq=264.4, rank=3),
                Peak(axis="z", freq=30.0, rank=1),
                Peak(axis="z", freq=184.6, rank=2),
                Peak(axis="z", freq=234.2, rank=3),
            ],
        )
        result = run_rca(peak_set, comp_machine, "warn", thresholds)
        assert "bearing_outer_race" in {m.fault for m in result.primary_findings}
        assert "imbalance" not in {m.fault for m in result.primary_findings}
        imbalance_diff = [d for d in result.differential if d.fault == "imbalance"]
        assert imbalance_diff
        assert "bearing" in imbalance_diff[0].adjudication.lower()


class TestSynchronousCollisionGuard:
    """Session B (B3): a matched bearing peak that lands within epsilon_sync of an
    integer shaft order is synchronous-ambiguous — routed to the differential at LOW
    (not primary), because a bearing defect there is indistinguishable from shaft-order
    content. The guard is a route-profile behavior (epsilon_sync present); streaming
    carries no epsilon_sync and is unaffected. Mirrors the MAFAULDA integer-order
    collision FPs (BPFO~3x, BPFI~5x) the guard clears from the committed diagnosis.
    """

    @staticmethod
    def _collision_machine() -> MachineMeta:
        # n=8, ball/pitch = 0.25 (angle 0) -> ratio 0.25 -> BPFO = (8/2)(1-0.25) = 3.0x
        # shaft exactly. Any radial peak at the computed BPFO therefore sits on 3x.
        return MachineMeta(
            mac="TEST-SYNC-01", name="Sync Collision Rig", active=True, type="pump",
            iso_group="2", iso_support="rigid", axial_axis="x",
            bearing=BearingSpec(
                n_balls=8, ball_dia_mm=10.0, pitch_dia_mm=40.0, contact_angle_deg=0.0, model="SYNC"
            ),
        )

    @staticmethod
    def _bpfo_peak_set() -> PeakSet:
        # shaft 30 Hz -> BPFO = 90 Hz = 3x. Radial axis y carries the loud BPFO peak.
        return PeakSet(
            source="spectrum", kind="envelope", shaft_freq_hz=30.0, rpm=1800.0,
            velocities_mms={"x": 0.4, "y": 3.0, "z": 0.4},
            peaks=[
                Peak(axis="y", freq=90.0, rank=1, amplitude=1.0),
                Peak(axis="y", freq=180.0, rank=2, amplitude=0.4),
                Peak(axis="z", freq=90.0, rank=1, amplitude=0.9),
            ],
        )

    def test_integer_order_bearing_demoted_to_differential(self):
        route = load_thresholds("route")
        machine = self._collision_machine()
        # sanity: the geometry really does put BPFO on 3x shaft
        assert bearing_frequencies(machine.bearing, shaft_hz=30.0).BPFO == pytest.approx(90.0, abs=0.1)

        result = run_rca(self._bpfo_peak_set(), machine, "warn", route)
        # NOT committed to primary...
        assert "bearing_outer_race" not in {m.fault for m in result.primary_findings}
        # ...routed to the differential at LOW with the synchronous adjudication.
        collided = [d for d in result.differential if d.fault == "bearing_outer_race"]
        assert collided
        assert collided[0].confidence == "low"
        assert collided[0].adjudication.startswith("Synchronous-ambiguous")

    def test_streaming_profile_leaves_guard_off(self):
        # No epsilon_sync in streaming -> the same integer-order match commits normally.
        streaming = load_thresholds()
        assert "epsilon_sync" not in streaming["rca"]
        result = run_rca(self._bpfo_peak_set(), self._collision_machine(), "warn", streaming)
        assert "bearing_outer_race" in {m.fault for m in result.primary_findings}
        assert not [d for d in result.differential if d.fault == "bearing_outer_race"]

    def test_noninteger_bearing_stays_committed_under_guard(self):
        # comp 6206 BPFO ~3.572x (10.7% off 4x) is well outside epsilon_sync -> committed.
        route = load_thresholds("route")
        machine = TestSynchronousCollisionGuard._noninteger_machine()
        ps = PeakSet(
            source="spectrum", kind="envelope", shaft_freq_hz=30.0, rpm=1800.0,
            velocities_mms={"x": 0.4, "y": 3.0, "z": 0.4},
            peaks=[Peak(axis="y", freq=107.16, rank=1, amplitude=1.0),
                   Peak(axis="z", freq=107.16, rank=1, amplitude=0.9)],
        )
        result = run_rca(ps, machine, "warn", route)
        assert "bearing_outer_race" in {m.fault for m in result.primary_findings}

    @staticmethod
    def _noninteger_machine() -> MachineMeta:
        return MachineMeta(
            mac="TEST-COMP-01", name="Comp", active=True, type="compressor",
            iso_group="2", iso_support="rigid", axial_axis="x",
            bearing=BearingSpec(
                n_balls=9, ball_dia_mm=9.53, pitch_dia_mm=46.0, contact_angle_deg=0.0, model="6206"
            ),
        )


class TestAmplitudeFloor:
    """Session B (B4): a committed bearing match whose peak sits below the axis
    amplitude floor (peak / axis-spectrum mean < floor_min) is routed to the
    differential at LOW — near-noise-floor tones cannot be distinguished from
    spectral noise. Route-profile behavior (floor_min present); streaming is
    unaffected. Mirrors the MFPT baseline_3 near-floor FP the floor clears.
    """

    _MACHINE = TestSynchronousCollisionGuard._noninteger_machine()  # 6206, BPFO ~3.572x (non-integer)

    @staticmethod
    def _peak_set(mean_amp: float) -> PeakSet:
        # BPFO ~107.16 Hz at shaft 30 (non-integer order -> the collision guard is
        # silent, isolating the floor behavior). Peak amplitude fixed at 1.0; the
        # peak-to-mean ratio is set purely by mean_amp.
        return PeakSet(
            source="spectrum", kind="envelope", shaft_freq_hz=30.0, rpm=1800.0,
            velocities_mms={"x": 0.4, "y": 3.0, "z": 0.4},
            axis_mean_amp={"x": mean_amp, "y": mean_amp, "z": mean_amp},
            peaks=[Peak(axis="y", freq=107.16, rank=1, amplitude=1.0),
                   Peak(axis="z", freq=107.16, rank=1, amplitude=0.9)],
        )

    def test_below_floor_demoted_to_differential(self):
        route = load_thresholds("route")
        floor = route["rca"]["floor_min"]
        # mean = 0.2 -> ratio 5.0, well below the floor.
        result = run_rca(self._peak_set(mean_amp=0.2), self._MACHINE, "warn", route)
        assert 1.0 / 0.2 < floor
        assert "bearing_outer_race" not in {m.fault for m in result.primary_findings}
        floored = [d for d in result.differential if d.fault == "bearing_outer_race"]
        assert floored and floored[0].confidence == "low"
        assert floored[0].adjudication.startswith("Below amplitude floor")

    def test_above_floor_stays_committed(self):
        route = load_thresholds("route")
        # mean = 0.02 -> ratio 50, comfortably above the floor.
        result = run_rca(self._peak_set(mean_amp=0.02), self._MACHINE, "warn", route)
        assert "bearing_outer_race" in {m.fault for m in result.primary_findings}
        assert not [d for d in result.differential if d.fault == "bearing_outer_race"]

    def test_streaming_profile_leaves_floor_off(self):
        streaming = load_thresholds()
        assert "floor_min" not in streaming["rca"]
        # Same near-floor peak commits normally when the profile has no floor.
        result = run_rca(self._peak_set(mean_amp=0.2), self._MACHINE, "warn", streaming)
        assert "bearing_outer_race" in {m.fault for m in result.primary_findings}


class TestSuppressionFloor:
    """Session B (B6): a bearing suppresses imbalance ONLY when committed to
    primary. A bearing demoted to the differential (by the B3 collision guard or
    the B4 floor) is itself uncertain and must not override a clear 1× radial
    imbalance signature — the imbalance commits instead.
    """

    @staticmethod
    def _machine() -> MachineMeta:
        # BPFO = 3.0x shaft (integer) -> the collision guard demotes it.
        return MachineMeta(
            mac="TEST-SYNC-02", name="Sync+Imbalance Rig", active=True, type="pump",
            iso_group="2", iso_support="rigid", axial_axis="x",
            bearing=BearingSpec(
                n_balls=8, ball_dia_mm=10.0, pitch_dia_mm=40.0, contact_angle_deg=0.0, model="S"
            ),
        )

    @staticmethod
    def _peak_set() -> PeakSet:
        # shaft 30: a BPFO peak at 90 Hz (=3x, synchronous -> differential) PLUS a
        # 1x radial-dominant imbalance signature at 30 Hz on y (axial x quiet).
        return PeakSet(
            source="spectrum", kind="envelope", shaft_freq_hz=30.0, rpm=1800.0,
            velocities_mms={"x": 0.4, "y": 5.0, "z": 0.5},
            axis_mean_amp={"x": 0.05, "y": 0.05, "z": 0.05},
            peaks=[Peak(axis="y", freq=90.0, rank=2, amplitude=1.0),
                   Peak(axis="y", freq=30.0, rank=1, amplitude=1.5)],
        )

    def test_differential_bearing_does_not_suppress_imbalance(self):
        route = load_thresholds("route")
        result = run_rca(self._peak_set(), self._machine(), "warn", route)
        # the collided bearing is in the differential (LOW)...
        assert [d for d in result.differential if d.fault == "bearing_outer_race"]
        # ...and no longer suppresses the imbalance, which now commits.
        assert "imbalance" in {m.fault for m in result.primary_findings}
        assert "imbalance" not in {d.fault for d in result.differential}

    def test_streaming_committed_bearing_still_suppresses(self):
        # Under streaming (no guard/floor) the BPFO commits to primary and DOES
        # suppress the imbalance — the B6 change is scoped to differential bearings.
        streaming = load_thresholds()
        result = run_rca(self._peak_set(), self._machine(), "warn", streaming)
        assert "bearing_outer_race" in {m.fault for m in result.primary_findings}
        assert "imbalance" not in {m.fault for m in result.primary_findings}
        assert "imbalance" in {d.fault for d in result.differential}


class TestImbalanceTwoConditionGate:
    """Session B (J3): the route imbalance gate requires BOTH radial-velocity
    dominance (>= r_dom) AND intra-radial 1× dominance (the 1× radial line leads
    the higher radial harmonics). The second condition is the imbalance-vs-
    misalignment separator — a 2×-dominant radial signature does NOT read as
    imbalance even when radial velocity dominates axial.
    """

    _MACHINE = MachineMeta(
        mac="TEST-PUMP-02", name="Imb", active=True, type="pump",
        iso_group="2", iso_support="rigid", axial_axis="x",
    )

    @staticmethod
    def _ps(peaks, v) -> PeakSet:
        return PeakSet(source="spectrum", kind="velocity", shaft_freq_hz=30.0, rpm=1800.0,
                       velocities_mms=v, peaks=peaks)

    def test_fires_on_radial_dominant_1x(self):
        route = load_thresholds("route")
        # radial dominance 5.0/0.4 = 12.5 (> 7.5); 1× radial peak, no 2× -> intra-radial ok.
        ps = self._ps(
            [Peak(axis="y", freq=30.0, rank=1, amplitude=1.0)],
            {"x": 0.4, "y": 5.0, "z": 0.5},
        )
        result = run_rca(ps, self._MACHINE, "warn", route)
        assert "imbalance" in {m.fault for m in result.primary_findings}

    def test_silent_when_2x_radial_dominates(self):
        # Misalignment-like: radial dominance high (12.5) BUT the 2× radial line
        # leads the 1× -> intra-radial fails -> imbalance does NOT fire.
        route = load_thresholds("route")
        ps = self._ps(
            [Peak(axis="y", freq=60.0, rank=1, amplitude=1.0),
             Peak(axis="y", freq=30.0, rank=2, amplitude=0.4)],
            {"x": 0.4, "y": 5.0, "z": 0.5},
        )
        result = run_rca(ps, self._MACHINE, "warn", route)
        assert "imbalance" not in {m.fault for m in result.primary_findings}
        assert "imbalance" not in {d.fault for d in result.differential}

    def test_silent_below_radial_dominance(self):
        # 1×-dominant radial but dominance 5.0/3.0 = 1.67 (< 7.5) -> silent.
        route = load_thresholds("route")
        ps = self._ps(
            [Peak(axis="y", freq=30.0, rank=1, amplitude=1.0)],
            {"x": 3.0, "y": 5.0, "z": 0.5},
        )
        result = run_rca(ps, self._MACHINE, "warn", route)
        assert "imbalance" not in {m.fault for m in result.primary_findings}


class TestPeakSetBoundary:
    """Amendment A3: an NCD triplet and an equivalent spectrum-derived
    PeakSet must drive the detectors to the same diagnosis — proving the
    normalized-boundary architecture holds across both source adapters.
    """

    def test_ncd_and_spectrum_sources_agree_on_bpfo(self, comp_machine, thresholds):
        case = REFERENCE_CASES["T12_bpfo_bearing_fault"]
        sensor_data = SensorData(**case["sensor_data"])

        ncd_peak_set = peaks_from_ncd(sensor_data)
        ncd_result = run_rca(ncd_peak_set, comp_machine, "warn", thresholds)

        spectra: dict[Axis, Spectrum] = {
            "x": _synthetic_spectrum([(41.2, 0.05), (53.7, 0.03), (84.1, 0.02)]),
            "y": _synthetic_spectrum([(107.16, 1.0), (214.32, 0.4), (264.4, 0.1)]),
            "z": _synthetic_spectrum([(107.16, 0.9), (214.32, 0.35), (234.2, 0.1)]),
        }
        spectrum_peak_set = peaks_from_spectrum(
            spectra, rpm=1800.0, velocities_mms={"x": 0.5, "y": 5.2, "z": 4.8}
        )
        spectrum_result = run_rca(spectrum_peak_set, comp_machine, "warn", thresholds)

        assert "bearing_outer_race" in {m.fault for m in ncd_result.primary_findings}
        assert "bearing_outer_race" in {m.fault for m in spectrum_result.primary_findings}


class TestConfidenceRubric:
    """The confidence rubric is a deterministic, config-weighted score over
    cited factors. Two anchors (from the reliability-engineer feedback): a
    clean bearing match scores high; a lone marginal peak scores low.
    """

    def test_clean_bpfo_scores_high_with_cited_factors(self, machines, iso_table, thresholds):
        _, result = _rca_for("T12_bpfo_bearing_fault", machines, iso_table, thresholds)
        bpfo = next(m for m in result.primary_findings if m.fault == "bearing_outer_race")
        assert bpfo.confidence == "high"
        # confidence is explained: factors are cited, and their signed deltas
        # sum into the banded score (nothing hard-coded).
        assert bpfo.confidence_evidence
        names = {f.name for f in bpfo.confidence_evidence}
        assert {"rank_one", "peak_match_tight"} <= names
        score = sum(f.delta for f in bpfo.confidence_evidence)
        assert score >= thresholds["confidence"]["bands"]["high"]

    def test_marginal_lone_peak_scores_low(self, comp_machine, thresholds):
        # A single BPFO-ish peak, off by ~2.5% (loose match, within tolerance:
        # past peak_match_tight_pct 1.5, inside route's tolerance_pct 3.0),
        # rank 1 but near the noise floor, no harmonic, no sidebands.
        # BPFO for the comp 6206 at 1800 rpm ≈ 107 Hz; place it at 109.7 (~2.5%).
        # (Order 3.66x is 8.6% off the nearest integer, so epsilon_sync is moot.)
        spectrum_amp_ceiling = 1.0
        marginal_amp = spectrum_amp_ceiling * 0.03  # below amplitude_marginal_ratio (0.05)
        peak_set = PeakSet(
            source="spectrum",
            shaft_freq_hz=30.0,
            rpm=1800.0,
            velocities_mms={"x": 0.5, "y": 3.0, "z": 2.9},
            peaks=[
                Peak(axis="y", freq=109.7, rank=1, amplitude=marginal_amp),
                Peak(axis="y", freq=250.0, rank=2, amplitude=spectrum_amp_ceiling),
            ],
        )
        result = run_rca(peak_set, comp_machine, "warn", thresholds)
        bearing = [m for m in result.primary_findings if m.fault.startswith("bearing_")]
        assert bearing, "expected the loose peak to still match a bearing frequency"
        assert bearing[0].confidence == "low"

    def test_gate_warnings_lower_confidence(self, machines, iso_table, thresholds):
        case = REFERENCE_CASES["T12_bpfo_bearing_fault"]
        machine = machines[case["mac"]]
        sensor_data = SensorData(**case["sensor_data"])
        resolved = resolve_thresholds(machine, iso_table)
        reading = classify(sensor_data, machine, resolved)
        peak_set = peaks_from_ncd(sensor_data)

        clean = run_rca(peak_set, machine, reading.iso_severity, thresholds)
        warned = run_rca(peak_set, machine, reading.iso_severity, thresholds, gate_warnings=True)

        clean_score = sum(
            f.delta
            for m in clean.primary_findings
            if m.fault == "bearing_outer_race"
            for f in m.confidence_evidence
        )
        warned_score = sum(
            f.delta
            for m in warned.primary_findings
            if m.fault == "bearing_outer_race"
            for f in m.confidence_evidence
        )
        assert warned_score < clean_score


class TestRcaEdgeCases:
    def test_machine_off_below_min_rpm(self, comp_machine, thresholds):
        peak_set = PeakSet(source="ncd_triplet", shaft_freq_hz=0.2, rpm=12.0, peaks=[], velocities_mms={})
        result = run_rca(peak_set, comp_machine, "ok", thresholds)
        assert result.status == "machine_off"
        assert result.primary_findings == []

    def test_no_matches_is_ok_status_empty_list(self, pump_machine, thresholds):
        peak_set = PeakSet(
            source="ncd_triplet",
            shaft_freq_hz=30.0,
            rpm=1800.0,
            velocities_mms={"x": 0.2, "y": 0.2, "z": 0.2},
            peaks=[],
        )
        result = run_rca(peak_set, pump_machine, "ok", thresholds)
        assert result.status == "ok"
        assert result.primary_findings == []
