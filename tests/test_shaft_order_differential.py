"""Session R3-DIFF — the Layer 5 shaft-order differential.

Two gates, one send-blocker between them.

THE DEFECT. Prod test B2_blower is a textbook imbalance trio: 1x-dominant,
radial-H 4.2 / radial-V 3.1 / axial 0.9 mm/s, 2x around 0.4, no bearing tones.
It committed "severe misalignment, medium confidence", and the evidence it cited
for "higher harmonics" was three rank-3 peaks at 0.020 mm/s -- 4.5-12.4x the axis
spectrum mean, every one of them BELOW the amplitude floor the spectrum figure
already draws on the same page. Two independent failures stacked:

  (1a) the noise floor was allowed to be evidence, and
  (1b) the misalignment family was TRIGGERED by 1x/2x PRESENCE, which every
       rotating machine has and which therefore cannot separate misalignment
       from imbalance -- the two conditions differ in PROPORTION, and nothing
       asked about proportion.

Both gates are route-profile-only by construction (the constants are absent from
`streaming`, and a missing constant turns the gate off), so the frozen NCD
pipeline is untouched -- asserted here, not assumed.
"""

from __future__ import annotations

import pytest

from tests.fixtures import REFERENCE_CASES
from vib_agent.config import load_config, load_thresholds
from vib_agent.models import Case, MachineMeta, SensorData, Spectrum
from vib_agent.pdm_core.bearing_rca import (
    _build_context,
    _clears_evidence_floor,
    _misalignment_amplitude_gate,
    peaks_from_ncd,
    peaks_from_spectrum,
    run_rca,
)
from vib_agent.pdm_core.iso_classify import classify, resolve_thresholds
from vib_agent.pipeline import run_analysis

RPM = 1780.0
SHAFT = RPM / 60.0  # 29.667 Hz
FMAX, LINES = 500.0, 2000

_MISALIGNMENT = {
    "angular_misalignment",
    "parallel_misalignment",
    "severe_misalignment",
    "misalignment_general",
    "bent_shaft",
}


def _spectrum(peaks: list[tuple[float, float]], noise: float = 0.0008) -> Spectrum:
    """A velocity spectrum with `peaks` as (frequency Hz, amplitude mm/s) on a
    flat broadband bed -- the bed is what the amplitude floor is measured against."""
    freqs = [i * FMAX / LINES for i in range(LINES)]
    amp = [noise] * LINES
    for freq, target in peaks:
        idx = round(freq / FMAX * LINES)
        for offset in (-1, 0, 1):
            i = idx + offset
            if 0 <= i < LINES:
                amp[i] = max(amp[i], target if offset == 0 else target * 0.3)
    return Spectrum(freq_hz=freqs, amplitude=amp, fmax_hz=FMAX, kind="velocity")


def _machine(**over) -> MachineMeta:
    base = dict(
        mac="B2-BLOWER", name="B2 blower", machine_class="medium", mounting="rigid",
        axial_axis="x", coupled=True, rpm_nominal=RPM, active=True,
        iso_group="2", iso_support="rigid", machine_type="motor",
    )
    base.update(over)
    return MachineMeta(**base)


def _sensor(v_axial: float, v_h: float, v_v: float, third: tuple[float, float, float]) -> SensorData:
    return SensorData(
        mode=0, msg_type="regular", odr="800Hz", temperature=30.0, rpm=RPM,
        x_velocity_mm_sec=v_axial, y_velocity_mm_sec=v_h, z_velocity_mm_sec=v_v,
        x_rms_ACC_G=0.05, y_rms_ACC_G=0.22, z_rms_ACC_G=0.17,
        x_max_ACC_G=0.11, y_max_ACC_G=0.48, z_max_ACC_G=0.37,
        x_displacement_mm=0.004, y_displacement_mm=0.019, z_displacement_mm=0.014,
        x_peak_one_Hz=SHAFT, y_peak_one_Hz=SHAFT, z_peak_one_Hz=SHAFT,
        x_peak_two_Hz=2 * SHAFT, y_peak_two_Hz=2 * SHAFT, z_peak_two_Hz=2 * SHAFT,
        x_peak_three_Hz=third[0] * SHAFT,
        y_peak_three_Hz=third[1] * SHAFT,
        z_peak_three_Hz=third[2] * SHAFT,
    )


def _b2_blower_case(
    *, v_axial: float = 0.9, v_h: float = 4.2, v_v: float = 3.1,
    amp_2x_h: float = 0.40, amp_2x_axial: float = 0.10, name: str = "B2_blower_trio",
) -> Case:
    """The prod trio, as three declared directions merged into one Case.

    Session E direction -> axis mapping: axial -> x, radial-h -> y, radial-v -> z.
    The rank-3 peaks at 0.020 mm/s are the sub-floor content the defect cited.
    """
    spectra = {
        "y": _spectrum([(SHAFT, v_h), (2 * SHAFT, amp_2x_h), (4.90 * SHAFT, 0.020)]),
        "z": _spectrum([(SHAFT, v_v), (2 * SHAFT, 0.30), (7.10 * SHAFT, 0.020)]),
        "x": _spectrum([(SHAFT, v_axial), (2 * SHAFT, amp_2x_axial), (4.92 * SHAFT, 0.020)]),
    }
    return Case(
        name=name, machine=_machine(), sensor_data=_sensor(v_axial, v_h, v_v, (4.92, 4.90, 7.10)),
        spectra=spectra, raw_spectra=spectra, source="upload", validation_scope=["rca"],
    )


@pytest.fixture
def route() -> dict:
    """The profile every upload is analysed under. Named explicitly: a bare
    load_thresholds() resolves `active_profile`, which is a default, not a decision."""
    return load_thresholds("route")


@pytest.fixture
def streaming() -> dict:
    return load_thresholds("streaming")


def _analyse(case: Case, thresholds: dict):
    return run_analysis(
        case, iso_table=load_config("iso_zones")["zones"], thresholds=thresholds,
        rules=load_config("next_measurements"),
    )


def _rca(case: Case, thresholds: dict):
    """run_rca directly, so a test can read primary/differential without the
    synthesize layer in between."""
    machine = case.machine
    sd = case.sensor_data
    reading = classify(sd, machine, resolve_thresholds(machine, load_config("iso_zones")["zones"]))
    velocities = {a: v for a, v in (("x", sd.x_velocity_mm_sec), ("y", sd.y_velocity_mm_sec),
                                    ("z", sd.z_velocity_mm_sec)) if v is not None}
    ps = peaks_from_spectrum(case.spectra, sd.rpm, velocities)
    return run_rca(ps, machine, reading.iso_severity, thresholds)


# ══════════════════════════════════════════════════════════════════════════════
# The send-blocker itself
# ══════════════════════════════════════════════════════════════════════════════
class TestB2BlowerTrio:
    """The prod case, end to end through the same pipeline the webapp calls."""

    def test_it_commits_imbalance(self, route):
        faults = {f.fault for f in _analyse(_b2_blower_case(), route).findings}
        assert "imbalance" in faults, f"expected imbalance, got {sorted(faults)}"

    def test_it_does_not_commit_misalignment(self, route):
        faults = {f.fault for f in _analyse(_b2_blower_case(), route).findings}
        assert not (faults & _MISALIGNMENT), (
            f"misalignment committed on a 1x-dominant radial trio: {sorted(faults & _MISALIGNMENT)}"
        )

    def test_severe_misalignment_specifically_is_gone(self, route):
        """The exact string the prod report shipped."""
        result = _analyse(_b2_blower_case(), route)
        assert "severe_misalignment" not in {f.fault for f in result.findings}
        prose = " ".join(
            [f.reason or "" for f in result.findings]
            + [str(f.evidence) for f in result.findings]
            + [m.evidence or "" for m in result.rca.primary_findings]
        ).lower()
        assert "severe misalignment" not in prose
        assert "coupling under significant distress" not in prose

    def test_misalignment_is_demoted_not_dropped(self, route):
        """The product pillar: a suppressed candidate is still shown, with the
        reason it lost. Silently deleting it would trade one dishonesty for another."""
        rca = _rca(_b2_blower_case(), route)
        demoted = [d for d in rca.differential if d.fault in _MISALIGNMENT]
        assert demoted, "misalignment vanished entirely instead of being adjudicated"
        adjudication = demoted[0].adjudication
        assert "2×/1×" in adjudication and "0.30" in adjudication
        assert "phase measurement" in adjudication  # names what would resolve it

    def test_the_committed_imbalance_carries_its_own_evidence(self, route):
        rca = _rca(_b2_blower_case(), route)
        imbalance = next(m for m in rca.primary_findings if m.fault == "imbalance")
        assert "1×" in imbalance.evidence
        assert imbalance.axis in ("y", "z")  # a radial axis, never the axial one


class TestNoSubFloorPeakIsEvidence:
    """Item 1a, stated as the property that actually matters: the three 0.020 mm/s
    peaks may not appear anywhere a human reads."""

    #: 4.90x / 4.92x / 7.10x of a 29.667 Hz shaft.
    SUB_FLOOR_HZ = (145.25, 146.0, 210.75)

    def _all_prose(self, rca) -> str:
        parts: list[str] = []
        for m in rca.primary_findings:
            parts.append(m.evidence or "")
            parts.extend(f.detail for f in m.confidence_evidence)
        for d in rca.differential:
            parts.append(d.adjudication or "")
        return " ".join(parts)

    def test_no_sub_floor_frequency_is_quoted(self, route):
        prose = self._all_prose(_rca(_b2_blower_case(), route))
        for hz in self.SUB_FLOOR_HZ:
            assert f"{hz:.1f}" not in prose, f"sub-floor peak {hz} Hz quoted in: {prose}"

    def test_no_sub_floor_peak_is_counted_as_a_harmonic(self, route):
        """The defect's own words were 'higher harmonics (5x)'. The 4.90x peak is
        within tolerance of 5x, so it MATCHED -- it just should never have counted."""
        prose = self._all_prose(_rca(_b2_blower_case(), route))
        assert "higher harmonics" not in prose.lower()
        assert "5x" not in prose.lower()

    def test_the_harmonic_sets_themselves_exclude_it(self, route):
        case = _b2_blower_case()
        sd = case.sensor_data
        ps = peaks_from_spectrum(case.spectra, sd.rpm, {"x": 0.9, "y": 4.2, "z": 3.1})
        ctx = _build_context(ps, case.machine, 0.03, route["confidence"],
                             floor_min=route["rca"]["floor_min"])
        assert ctx.harmonics["5x"] == [], "a sub-floor peak survived into the 5x set"
        assert ctx.has_higher_harmonics is False
        # ...while the real content is untouched.
        assert {p.axis for p in ctx.harmonics["1x"]} == {"x", "y", "z"}
        assert {p.axis for p in ctx.harmonics["2x"]} == {"x", "y", "z"}

    def test_the_peak_list_is_not_filtered(self, route):
        """ctx.peaks keeps every peak. The bearing detector reads it directly and
        has its own, deliberately different treatment of the same floor (demote to
        the differential at LOW, naming the resolving capture -- not hide), and peak
        RANK must stay computed over the axis's full peak list."""
        case = _b2_blower_case()
        ps = peaks_from_spectrum(case.spectra, case.sensor_data.rpm, {"x": 0.9, "y": 4.2, "z": 3.1})
        ctx = _build_context(ps, case.machine, 0.03, route["confidence"],
                             floor_min=route["rca"]["floor_min"])
        assert len(ctx.peaks) == 9
        assert any(abs(p.freq - 145.25) < 0.5 for p in ctx.peaks)

    def test_the_floor_is_the_one_the_figures_draw(self, route):
        """One constant, two consumers. If these ever diverge, a reader sees a peak
        under the dashed line on the chart and named in the prose above it."""
        from vib_agent.report import charts

        spectrum = _spectrum([(SHAFT, 4.2), (2 * SHAFT, 0.4), (4.90 * SHAFT, 0.020)])
        drawn = charts._amplitude_floor_value(spectrum, route)
        mean_amp = sum(spectrum.amplitude) / len(spectrum.amplitude)
        assert drawn == pytest.approx(mean_amp * route["rca"]["floor_min"])
        # the 0.020 peak sits under the line the reader is shown...
        assert 0.020 < drawn
        # ...and the gate agrees, on the same numbers.
        peak = next(p for p in peaks_from_spectrum({"y": spectrum}, RPM, {"y": 4.2}).peaks
                    if abs(p.freq - 4.90 * SHAFT) < 1.0)
        assert not _clears_evidence_floor(peak, {"y": mean_amp}, route["rca"]["floor_min"])


# ══════════════════════════════════════════════════════════════════════════════
# The misalignment gate, both branches
# ══════════════════════════════════════════════════════════════════════════════
class TestTheMisalignmentGate:
    def test_branch_a_a_real_radial_2x_still_commits(self, route):
        """Raise the 2x to 30% of the 1x and the same trio is misalignment again --
        the gate discriminates on proportion, it does not simply ban the diagnosis."""
        case = _b2_blower_case(amp_2x_h=4.2 * 0.31)
        faults = {f.fault for f in _analyse(case, route).findings}
        assert faults & _MISALIGNMENT, f"a genuine radial 2x was gated out: {sorted(faults)}"

    def test_branch_a_is_a_threshold_not_a_slope(self, route):
        """Just under the bar is gated; just over it commits."""
        under = {f.fault for f in _analyse(_b2_blower_case(amp_2x_h=4.2 * 0.29), route).findings}
        over = {f.fault for f in _analyse(_b2_blower_case(amp_2x_h=4.2 * 0.31), route).findings}
        assert not (under & _MISALIGNMENT)
        assert over & _MISALIGNMENT

    def test_branch_b_an_axial_dominant_trio_still_commits(self, route):
        """The angular signature: energy on the AXIAL axis. Nothing to do with 2x."""
        case = _b2_blower_case(v_axial=4.0, v_h=1.0, v_v=0.9, name="axial_dominant")
        faults = {f.fault for f in _analyse(case, route).findings}
        assert faults & _MISALIGNMENT, f"an axial-dominant trio was gated out: {sorted(faults)}"
        assert "imbalance" not in faults

    def test_branch_b_reads_1x_or_2x_not_both(self, route):
        """A dominant axial 1x with NO 2x anywhere is the classic angular /
        bent-shaft signature -- and is exactly the Session E conjunction trio.
        Reading the clause as a conjunction would have made that case commit
        nothing and reopened the axis-mislabel bug Session E closed."""
        spectra = {
            "y": _spectrum([(SHAFT, 0.15)]),
            "z": _spectrum([(SHAFT, 0.14)]),
            "x": _spectrum([(SHAFT, 0.60)]),
        }
        ps = peaks_from_spectrum(spectra, RPM, {"x": 0.60, "y": 0.15, "z": 0.14})
        ctx = _build_context(ps, _machine(), 0.03, route["confidence"],
                             floor_min=route["rca"]["floor_min"])
        assert ctx.has_2x_axial is False and ctx.has_1x_axial is True
        assert _misalignment_amplitude_gate(
            ctx, route["rca"]["misalignment_2x_1x_min"],
            route["rca"]["misalignment_axial_radial_min"],
        ) is None

    def test_the_gate_cannot_rescue_a_radial_trio_either_way(self, route):
        """B2 fails branch (b) on the RATIO (0.21 < 0.50), so the 1x-or-2x reading
        above costs the fix nothing."""
        rca = _rca(_b2_blower_case(), route)
        assert rca.axial_radial_ratio == pytest.approx(0.21, abs=0.01)
        assert not {m.fault for m in rca.primary_findings} & _MISALIGNMENT


# ══════════════════════════════════════════════════════════════════════════════
# Both gates are off wherever the question cannot be asked
# ══════════════════════════════════════════════════════════════════════════════
class TestTheFrozenProfileIsUntouched:
    """profiles.streaming carries neither constant, so both gates are inert there.
    The integrity rule in config/thresholds.json is not advice."""

    def test_streaming_carries_neither_constant(self, streaming):
        rca = streaming["rca"]
        assert "misalignment_2x_1x_min" not in rca
        assert "misalignment_axial_radial_min" not in rca
        assert "floor_min" not in rca

    def test_the_same_trio_is_unchanged_under_streaming(self, streaming, route):
        """The pre-R3 diagnosis, still reachable -- proof the change is profile-scoped
        and not a global rewrite of the differential."""
        streamed = {f.fault for f in _analyse(_b2_blower_case(), streaming).findings}
        routed = {f.fault for f in _analyse(_b2_blower_case(), route).findings}
        assert "severe_misalignment" in streamed
        assert routed != streamed

    def test_the_gate_is_inert_without_its_constants(self, route):
        case = _b2_blower_case()
        ps = peaks_from_spectrum(case.spectra, RPM, {"x": 0.9, "y": 4.2, "z": 3.1})
        ctx = _build_context(ps, case.machine, 0.03, route["confidence"],
                             floor_min=route["rca"]["floor_min"])
        assert _misalignment_amplitude_gate(ctx, None, 0.50) is None
        assert _misalignment_amplitude_gate(ctx, 0.30, None) is None
        assert _misalignment_amplitude_gate(ctx, 0.30, 0.50) is not None

    def test_ncd_triplets_carry_no_amplitude_so_the_floor_is_inert(self, route, machines):
        """The NCD sensor reports rank, not level. A gate that cannot read an
        amplitude must not invent one."""
        sd = SensorData(**REFERENCE_CASES["T07_imbalance"]["sensor_data"])
        ps = peaks_from_ncd(sd)
        assert ps.axis_mean_amp is None
        machine = machines[REFERENCE_CASES["T07_imbalance"]["mac"]]
        floored = _build_context(ps, machine, 0.05, route["confidence"],
                                 floor_min=route["rca"]["floor_min"])
        bare = _build_context(ps, machine, 0.05, route["confidence"])
        assert {k: [p.freq for p in v] for k, v in floored.harmonics.items()} == {
            k: [p.freq for p in v] for k, v in bare.harmonics.items()
        }


class TestExistingFixturesAreUnchanged:
    """The brief's own acceptance condition: the healthy fixture's peaks clear the
    floor, so nothing about it moves."""

    def _rca_ncd(self, case_name, machines, iso_table, thresholds):
        case = REFERENCE_CASES[case_name]
        machine = machines[case["mac"]]
        sd = SensorData(**case["sensor_data"])
        reading = classify(sd, machine, resolve_thresholds(machine, iso_table))
        return run_rca(peaks_from_ncd(sd), machine, reading.iso_severity, thresholds)

    @pytest.mark.parametrize(
        "case_name,expected",
        [
            ("T07_imbalance", "imbalance"),
            ("T09_angular_misalignment", "angular_misalignment"),
            ("T08_bent_shaft_uncoupled", "bent_shaft"),
        ],
    )
    def test_reference_diagnoses_still_reproduce(
        self, case_name, expected, machines, iso_table, thresholds
    ):
        rca = self._rca_ncd(case_name, machines, iso_table, thresholds)
        assert expected in {m.fault for m in rca.primary_findings}

    def test_the_healthy_fixture_stays_healthy_on_route_too(self, machines, iso_table, route):
        rca = self._rca_ncd("T01_healthy_zone_a", machines, iso_table, route)
        assert not {m.fault for m in rca.primary_findings} & _MISALIGNMENT

    def test_a_healthy_spectrum_trio_clears_the_floor(self, route):
        """A quiet machine's real peaks are still far above the broadband bed --
        the floor removes noise, not small signals."""
        case = _b2_blower_case(v_axial=0.20, v_h=0.30, v_v=0.28, amp_2x_h=0.05,
                               amp_2x_axial=0.03, name="healthy_trio")
        ps = peaks_from_spectrum(case.spectra, RPM, {"x": 0.20, "y": 0.30, "z": 0.28})
        ctx = _build_context(ps, case.machine, 0.03, route["confidence"],
                             floor_min=route["rca"]["floor_min"])
        assert ctx.has_1x_radial and ctx.has_1x_axial
        assert ctx.has_2x_radial and ctx.has_2x_axial
