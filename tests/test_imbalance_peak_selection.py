"""Session DOMINANT-RANK, commit 2 — the imbalance call argues from the
STRONGEST radial 1x peak, not the first one the dict happened to yield.

THE DEFECT (`outputs/PROD_READINESS.md:416`, slice S13 at `:742`). The line was

    radial_peak = next((p for p in ctx.harmonics["1x"] if p.axis in ctx.radial), None)

`ctx.peaks` is built by iterating `spectra.items()`, and on the multi-axis
upload path that dict is populated in **upload SLOT order**. So `next(...)`
returned "whichever radial channel the analyst happened to drop into slot 1",
and every number the finding reports — axis, frequency, radial velocity — came
from that peak. Worse: `_one_x_gate_axis` returns `match.axis` for imbalance, so
the PDMFIX severity gate then judged the reading on that same accidental axis.

Measured on the fixture below (radial-H at 1.25 mm/s = Zone A, radial-V at
2.40 mm/s = Zone B, one identical machine):

    slot order H,V  ->  "no significant findings"      <- a clean bill
    slot order V,H  ->  imbalance committed on z, medium

Two opposite diagnoses from the same measurement, decided by a file-picker. The
clean bill is the dangerous half: a 2.4 mm/s Zone-B 1x line went unreported
because a quieter channel was uploaded first.

Scope note, deliberate: `_intra_radial_1x_dominates`'s own `best_1x` still
selects by cross-axis RANK while comparing competitors by AMPLITUDE
(`PROD_READINESS.md:929`). That is the GATE's selection — it changes which
readings fire, not which peak is reported — and it is left open here on purpose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vib_agent.config import load_thresholds
from vib_agent.models import MachineMeta, Peak, PeakSet
from vib_agent.pdm_core.bearing_rca import (
    _build_context,
    _one_x_gate_axis,
    _strongest_radial_1x,
    peaks_from_spectrum,
    run_rca,
)

# The real product path: CSV on disk -> parse_upload -> merge_channels ->
# run_analysis. Reused rather than re-described — and reused DELIBERATELY, for
# the reason `test_one_x_severity_gate`'s own docstring gives: a hand-built
# PeakSet carries `measured_axes=None`, which resolves to `axial_measured=True`,
# so a pin built from one would pass no matter how badly the real derivation
# broke.
from tests.test_one_x_severity_gate import (  # noqa: F401 -- `cfg` is used AS a fixture
    SHAFT,
    analyse,
    cfg,
    merge,
    parse,
    write_csv,
)

#: Zone A on this machine's own boundaries (group 2 / rigid: ab = 1.4).
QUIET_MMS = 1.25
#: Zone B — loud enough for the 1x-family severity gate to admit it.
LOUD_MMS = 2.40


@pytest.fixture(scope="module")
def channels(tmp_path_factory) -> dict:
    """One machine, two radial channels, written once. The radial-V channel is
    the louder one; radial-H is the quiet channel that used to speak for it."""
    d = tmp_path_factory.mktemp("domrank")
    return {
        "radial_h": parse(write_csv(Path(d) / "h.csv", QUIET_MMS)),
        "radial_v": parse(write_csv(Path(d) / "v.csv", LOUD_MMS)),
    }


def _merged(channels: dict, order: tuple[str, ...], cfg: dict):
    return merge([(d, channels[d]) for d in order], cfg)


def _imbalance(result):
    return next((m for m in result.rca.primary_findings if m.fault == "imbalance"), None)


ORDERS = [("radial_h", "radial_v"), ("radial_v", "radial_h")]
ORDER_IDS = ["h-then-v", "v-then-h"]


# ── the premise, asserted rather than assumed ───────────────────────────────


class TestThePremise:
    """UNEXPLAINED-PEAK's discipline: prove the fixture really exhibits the
    condition under test, so the pins below cannot pass vacuously."""

    def test_the_first_radial_1x_is_not_the_strongest(self, channels, cfg):
        case = _merged(channels, ("radial_h", "radial_v"), cfg)
        peak_set = peaks_from_spectrum(case.spectra, case.sensor_data.rpm, {})
        radial_1x = [
            p for p in peak_set.peaks
            if p.axis in ("y", "z") and abs(p.freq - SHAFT) / SHAFT <= 0.03
        ]
        assert [p.axis for p in radial_1x][0] == "y", "slot 1 (radial-H) must come first"
        strongest = max(radial_1x, key=lambda p: p.amplitude)
        assert strongest.axis == "z", "and the LOUDER radial 1x must be the other one"

    def test_the_two_channels_straddle_the_severity_gate(self, channels, cfg):
        """Zone A on one radial axis, Zone B on the other — which is what makes
        the old slot-order dependence produce two OPPOSITE diagnoses."""
        case = _merged(channels, ("radial_h", "radial_v"), cfg)
        v = case.sensor_data
        assert v.y_velocity_mm_sec == pytest.approx(QUIET_MMS, abs=0.01)
        assert v.z_velocity_mm_sec == pytest.approx(LOUD_MMS, abs=0.01)


# ── S13's named acceptance: order-independence ──────────────────────────────


class TestSlotOrderIndependence:
    """`PROD_READINESS.md:742` states the acceptance for this slice in as many
    words: 'shuffle the axis insertion order, assert the diagnosis is
    identical'."""

    def test_the_committed_diagnosis_is_the_same_both_ways(self, channels, cfg):
        committed = [
            [f.fault for f in analyse(_merged(channels, order, cfg), cfg).findings]
            for order in ORDERS
        ]
        assert committed[0] == committed[1], (
            f"the diagnosis still depends on upload slot order: {dict(zip(ORDER_IDS, committed))}"
        )

    def test_every_reported_number_is_the_same_both_ways(self, channels, cfg):
        reported = []
        for order in ORDERS:
            match = _imbalance(analyse(_merged(channels, order, cfg), cfg))
            assert match is not None, order
            reported.append(
                (match.axis, match.freq_hz, match.radial_velocity_mms, match.confidence)
            )
        assert reported[0] == reported[1]

    @pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
    def test_the_louder_channel_is_the_one_reported(self, channels, cfg, order):
        match = _imbalance(analyse(_merged(channels, order, cfg), cfg))
        assert match is not None
        assert match.axis == "z"
        assert match.radial_velocity_mms == pytest.approx(LOUD_MMS, abs=0.01)


class TestTheQuietChannelNoLongerSpeaksForTheLoudOne:
    """The dangerous half of the defect: with the quiet channel in slot 1 the
    severity gate judged a Zone-A velocity and dropped the candidate, so the
    report read 'no significant findings' on a machine with a 2.4 mm/s 1x line."""

    def test_a_quiet_first_slot_no_longer_produces_a_clean_bill(self, channels, cfg):
        result = analyse(_merged(channels, ("radial_h", "radial_v"), cfg), cfg)
        faults = [f.fault for f in result.findings]
        assert "no_significant_findings" not in faults
        assert "imbalance" in faults

    def test_the_severity_gate_judges_the_reported_axis(self, channels, cfg):
        """`_one_x_gate_axis` returns `match.axis` for imbalance — so fixing the
        reported peak is what fixes which velocity the gate reads."""
        case = _merged(channels, ("radial_h", "radial_v"), cfg)
        result = analyse(case, cfg)
        match = _imbalance(result)
        assert match is not None
        route = load_thresholds("route")
        ctx = _build_context(
            peaks_from_spectrum(case.spectra, case.sensor_data.rpm, {}),
            case.machine, route["rca"]["tolerance_pct"] / 100.0, route["confidence"],
        )
        assert _one_x_gate_axis(match, ctx) == "z"


# ── the selector itself: amplitude, then rank, and ties stand still ─────────


_MACHINE = MachineMeta(
    mac="TEST-DOMRANK-02", name="Selection Rig", active=True, type="pump",
    iso_group="2", iso_support="rigid", axial_axis="x",
)


def _ctx(peaks: list[Peak]):
    route = load_thresholds("route")
    peak_set = PeakSet(
        source="spectrum", kind="velocity", shaft_freq_hz=30.0, rpm=1800.0,
        velocities_mms={"x": 0.4, "y": 5.0, "z": 5.0}, peaks=peaks,
    )
    return _build_context(
        peak_set, _MACHINE, route["rca"]["tolerance_pct"] / 100.0, route["confidence"]
    )


class TestTheSelector:
    def test_amplitude_wins_over_list_position(self):
        peaks = [
            Peak(axis="y", freq=30.0, rank=1, amplitude=0.9),   # first in list
            Peak(axis="z", freq=30.0, rank=1, amplitude=2.0),   # louder
        ]
        assert _strongest_radial_1x(_ctx(peaks)).axis == "z"

    def test_amplitude_less_peaks_fall_back_to_rank(self):
        """NCD triplets carry no amplitude; rank IS the sensor's loudness order.
        The lower rank wins even though it is second in the list."""
        peaks = [Peak(axis="y", freq=30.0, rank=2), Peak(axis="z", freq=30.0, rank=1)]
        assert _strongest_radial_1x(_ctx(peaks)).axis == "z"

    def test_a_mixed_list_falls_back_to_rank(self):
        """One amplitude-less candidate makes amplitude comparison meaningless
        for the whole set, so the rank proxy governs — never a silent `None`
        comparison."""
        peaks = [Peak(axis="y", freq=30.0, rank=2, amplitude=9.0), Peak(axis="z", freq=30.0, rank=1)]
        assert _strongest_radial_1x(_ctx(peaks)).axis == "z"

    def test_a_tie_keeps_the_pre_existing_answer(self):
        """`max` returns the FIRST extremal element, so equal amplitudes resolve
        exactly as `next(...)` did — byte-identical where there is nothing to fix."""
        peaks = [
            Peak(axis="y", freq=30.0, rank=1, amplitude=1.5),
            Peak(axis="z", freq=30.0, rank=1, amplitude=1.5),
        ]
        assert _strongest_radial_1x(_ctx(peaks)).axis == "y"

    def test_the_axial_axis_is_never_a_candidate(self):
        peaks = [
            Peak(axis="x", freq=30.0, rank=1, amplitude=9.0),   # axial, and loudest
            Peak(axis="y", freq=30.0, rank=1, amplitude=1.0),
        ]
        assert _strongest_radial_1x(_ctx(peaks)).axis == "y"

    def test_no_radial_1x_is_none(self):
        assert _strongest_radial_1x(_ctx([Peak(axis="x", freq=30.0, rank=1, amplitude=1.0)])) is None


class TestTheSentenceDescribesTheChosenPeak:
    """Commit 1 made the sentence rank-honest; commit 2 makes it honest about
    the right peak. Together: the reported axis, frequency and velocity all
    describe the line the detector actually argued from."""

    def test_single_axis_readings_are_untouched(self):
        """The one-radial-channel case — `before.csv` and every single-file
        upload — has exactly one candidate, so nothing about it can move."""
        peaks = [Peak(axis="y", freq=30.0, rank=1, amplitude=1.0)]
        peak_set = PeakSet(
            source="spectrum", kind="velocity", shaft_freq_hz=30.0, rpm=1800.0,
            velocities_mms={"x": 0.4, "y": 5.0, "z": 0.5}, peaks=peaks,
        )
        match = next(
            m for m in run_rca(peak_set, _MACHINE, "warn", load_thresholds("route")).primary_findings
            if m.fault == "imbalance"
        )
        assert (match.axis, match.freq_hz) == ("y", 30.0)
        assert "dominant on radial axis y" in match.evidence
