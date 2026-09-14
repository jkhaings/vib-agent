"""Session GEOM-B — the belt detector's amplitude discipline.

THE DEFECT (found by GEOM-A, which made this detector reachable from the upload
form for the first time; outputs/SESSION_GEOMA.md §4, finding 1):

  `run_rca` called `detect_belt_fault` BARE — `primary.extend(...)` — so belt
  answered to none of the discipline the families it competes with answer to:
  not `_clears_evidence_floor` (the harmonic family's bar), not
  `_one_x_severity_gate` (PDMFIX ruling 1, imbalance and misalignment). On a
  Zone-C machine whose condition was a 3.0 mm/s 1x imbalance, a 0.25 mm/s stray
  inside the +-3% belt window committed `belt_fault` next to the correct call.

THE FIX, three conditions, each pinned here IN ISOLATION — the fixture varies
one property at a time so a passing test names the condition that did the work:

  (1) AMPLITUDE FLOOR   `TestAmplitudeFloor`   — belt line buried in the bed
  (2) DOMINANCE         `TestDominance`        — belt line under the dominant line
  (3) SEVERITY GATE     `TestSeverityGate`     — belt line on a quiet machine

Isolation matters because the three do NOT overlap: measured on the GEOM-A
fixture, (1) and (3) both PASS the stray (peak/axis-mean 23.6 against a 12.73
floor; axis velocity 3.07 mm/s against a 1.4 mm/s Zone-B bar) and only (2)
refuses it. A fix that shipped (1) and (3) alone would have looked complete and
left the reported false positive alive.

INERTNESS (`TestInertWhereTheQuestionCannotBeAsked`) is the other half of the
contract: every condition returns to silence when its question cannot be asked,
so the frozen NCD/streaming path is unchanged. Note honestly which is which —
`floor_min` and the two severity keys are absent from `streaming`, so those two
are profile-gated off; `belt_dominance_min` carries a CODE default (0.3, the
`.get(key, default)` pattern this file already uses for `min_rpm`,
`tolerance_pct` and `imbalance_1x_dominance`, since `config/thresholds.json` was
latched shut for this session), so it is disarmed by the ABSENCE OF AMPLITUDES
instead: the NCD triplets report rank, not level, and rank is not a proportion.

Pins drive real files through the real product path (CSV on disk -> parse_upload
-> run_analysis), the test_one_x_severity_gate.py precedent, except where the
claim is specifically about amplitude-less peaks — which no file path can
produce, so those two use a hand-built NCD-shaped PeakSet.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm, belt_frequency_hz
from vib_agent.config import load_config, load_thresholds
from vib_agent.models import BeltSpec, FaultMatch, MachineMeta, Peak, PeakSet
from vib_agent.pdm_core.bearing_rca import (
    _BELT_CLOSING,
    _ONE_X_CLOSING,
    _build_context,
    _one_x_gate_axis,
    _one_x_severity_gate,
    run_rca,
)
from vib_agent.pipeline import run_analysis

# 1800 rpm -> 30 Hz shaft. ISO group 2 / rigid -> ab=1.4, bc=2.8, cd=4.5.
RPM = 1800.0
SHAFT = RPM / 60.0
# The GEOM-A pulley trio: 120/200 mm on 400 mm centres -> 8.6555 Hz at 1800 rpm,
# 0.289x shaft — neither an integer order nor within tolerance of any harmonic.
D1, D2, CENTRES = 120.0, 200.0, 400.0
BELT_HZ = belt_frequency_hz(D1, D2, CENTRES, RPM)

LINES, DF = 1600, 0.25  # 0.25 Hz bins, fmax 400 Hz


@pytest.fixture(scope="module")
def cfg() -> dict:
    """`route` — the profile every upload is analysed under, named explicitly."""
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
        "bearings": load_config("bearings"),
    }


def _write_csv(
    path: Path, *, overall_mms: float, belt_rel: float, bed_rel: float = 0.001
) -> Path:
    """A `freq_hz,amplitude` velocity spectrum: a 1x shaft line at relative 1.0,
    a belt line at `belt_rel`, a flat bed at `bed_rel`, scaled so the Parseval
    overall RMS is exactly `overall_mms` — which is what `parse_spectrum` reads
    back as the channel's velocity.

    The three knobs map one-to-one onto the three conditions: `belt_rel` is the
    DOMINANCE ratio, `bed_rel` sets the peak-to-mean ratio the FLOOR reads, and
    `overall_mms` is what the SEVERITY gate reads. Scaling is uniform, so it
    moves severity without touching either ratio.
    """
    amp = [bed_rel] * LINES
    for hz, rel in ((SHAFT, 1.0), (BELT_HZ, belt_rel)):
        idx = round(hz / DF)
        for off, frac in ((-1, 0.3), (0, 1.0), (1, 0.3)):
            if 0 <= idx + off < LINES:
                amp[idx + off] = max(amp[idx + off], rel * frac)
    scale = overall_mms / math.sqrt(sum(a * a for a in amp))
    lines = ["freq_hz,amplitude"]
    lines += [f"{i * DF:.4f},{a * scale:.10f}" for i, a in enumerate(amp)]
    path.write_text("\n".join(lines) + "\n")
    return path


def _faults(path: Path, cfg: dict, *, with_geometry: bool = True) -> list[str]:
    geometry = (
        dict(drive_pulley_mm=D1, driven_pulley_mm=D2, pulley_center_distance_mm=CENTRES)
        if with_geometry
        else {}
    )
    form = UploadForm(machine_alias="Belt rig", rpm=RPM, iso_group="2",
                      iso_support="rigid", machine_type="motor", **geometry)
    case, _kind, _note = parse_upload(path, form, bearings_cfg=cfg["bearings"])
    assert (case.machine.belt is not None) is with_geometry
    result = run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                          rules=cfg["rules"])
    assert result.quality_gate.overall != "fail", result.quality_gate.model_dump()
    return [f.fault for f in result.findings]


# ═════════════════════════════════════════════════════════════════════════════
# (1) The amplitude floor — the bar the spectrum FIGURE already draws
# ═════════════════════════════════════════════════════════════════════════════
class TestAmplitudeFloor:
    """`_clears_evidence_floor`: matched-peak amplitude / axis-spectrum MEAN >=
    rca.floor_min (12.73). A line the reader can see sitting under the dashed
    floor on the figure is one the prose may no longer name — figure and text
    answer to a single constant.

    The belt line is the DOMINANT line on its axis in both cases below (ratio
    1.0), and the machine is Zone C in both, so dominance and severity are held
    constant and the bed level alone decides.
    """

    def test_a_belt_line_buried_in_the_broadband_bed_does_not_commit(self, tmp_path, cfg):
        path = _write_csv(tmp_path / "upload.csv", overall_mms=3.0, belt_rel=1.0,
                          bed_rel=0.12)
        assert "belt_fault" not in _faults(path, cfg)

    def test_the_same_line_over_a_quiet_bed_commits(self, tmp_path, cfg):
        path = _write_csv(tmp_path / "upload.csv", overall_mms=3.0, belt_rel=1.0,
                          bed_rel=0.001)
        assert "belt_fault" in _faults(path, cfg)


# ═════════════════════════════════════════════════════════════════════════════
# (2) Dominance — the condition that carries the GEOM-A conjunction proof
# ═════════════════════════════════════════════════════════════════════════════
class TestDominance:
    """The matched belt line must be at least `belt_dominance_min` of the
    loudest line on its own axis. Both cases below clear the floor and clear the
    severity gate; the ratio alone decides.
    """

    def test_a_belt_line_far_under_the_dominant_line_does_not_commit(self, tmp_path, cfg):
        path = _write_csv(tmp_path / "upload.csv", overall_mms=3.0, belt_rel=0.083)
        assert "belt_fault" not in _faults(path, cfg)

    def test_a_belt_line_a_substantial_fraction_of_it_commits(self, tmp_path, cfg):
        path = _write_csv(tmp_path / "upload.csv", overall_mms=3.0, belt_rel=0.5)
        assert "belt_fault" in _faults(path, cfg)

    def test_the_refused_line_really_did_clear_the_other_two_conditions(self, tmp_path, cfg):
        """The isolation claim, asserted rather than asserted-about: the 0.083
        line clears the amplitude floor and its axis clears the severity bar, so
        neither of those refused it. Without this, a future change that broke
        the floor would make the dominance test pass for the wrong reason."""
        from vib_agent.pdm_core.bearing_rca import _clears_evidence_floor, peaks_from_spectrum

        path = _write_csv(tmp_path / "upload.csv", overall_mms=3.0, belt_rel=0.083)
        form = UploadForm(machine_alias="Belt rig", rpm=RPM, iso_group="2",
                          iso_support="rigid", machine_type="motor", drive_pulley_mm=D1, driven_pulley_mm=D2,
                          pulley_center_distance_mm=CENTRES)
        case, _kind, _note = parse_upload(path, form, bearings_cfg=cfg["bearings"])
        velocity = case.sensor_data.y_velocity_mm_sec
        peak_set = peaks_from_spectrum(case.spectra, RPM, {"x": 0.0, "y": velocity, "z": 0.0})
        belt_peak = next(
            p for p in peak_set.peaks
            if abs(p.freq - BELT_HZ) / BELT_HZ <= cfg["thresholds"]["rca"]["tolerance_pct"] / 100
        )
        floor_min = cfg["thresholds"]["rca"]["floor_min"]
        assert _clears_evidence_floor(belt_peak, peak_set.axis_mean_amp, floor_min)
        # Zone-B boundary for group 2 / rigid is 1.4 mm/s; the machine reads ~3.
        assert velocity >= 1.4


# ═════════════════════════════════════════════════════════════════════════════
# (3) The severity gate — the same bar imbalance and misalignment answer to
# ═════════════════════════════════════════════════════════════════════════════
class TestSeverityGate:
    """A belt line that dominates its axis and clears the floor still has to be
    on a machine loud enough for a fault call. Scaling is uniform, so the two
    cases below differ ONLY in overall level.
    """

    def test_a_clean_belt_line_on_a_quiet_machine_does_not_commit(self, tmp_path, cfg):
        path = _write_csv(tmp_path / "upload.csv", overall_mms=0.6, belt_rel=1.0)
        faults = _faults(path, cfg)
        assert "belt_fault" not in faults, faults

    def test_the_same_line_on_an_elevated_machine_commits(self, tmp_path, cfg):
        path = _write_csv(tmp_path / "upload.csv", overall_mms=3.0, belt_rel=1.0)
        assert "belt_fault" in _faults(path, cfg)

    def test_the_gate_judges_the_axis_the_belt_line_was_found_on(self):
        """`_one_x_gate_axis` maps each family to the axis its evidence cites.
        Belt argues from ONE matched line, so that line's axis is the answer."""
        match = FaultMatch(fault="belt_fault", description="d", freq_hz=BELT_HZ,
                           expected_hz=BELT_HZ, axis="z", confidence="medium",
                           confidence_evidence=[], evidence="e")
        ctx = _build_context(
            PeakSet(source="spectrum", kind="velocity", shaft_freq_hz=SHAFT, rpm=RPM,
                    peaks=[], velocities_mms={"x": 5.0, "y": 4.0, "z": 0.2}),
            MachineMeta(mac="B", name="B", active=True, type="motor",
                        iso_group="2", iso_support="rigid"),
            0.03, load_thresholds("route")["confidence"],
        )
        assert _one_x_gate_axis(match, ctx) == "z"

    def test_the_refusal_reason_is_not_the_1x_sentence(self):
        """The gate's closing argument is family-specific. "A 1x shaft-order
        pattern is present on every rotating machine" is TRUE and load-bearing
        for imbalance and misalignment, and FALSE of a belt line — a belt
        frequency is not present on every machine, so the honest refusal is
        about amplitude alone."""
        assert _BELT_CLOSING != _ONE_X_CLOSING
        assert "every rotating machine" not in _BELT_CLOSING


# ═════════════════════════════════════════════════════════════════════════════
# Inertness — every condition returns to silence when it cannot be asked
# ═════════════════════════════════════════════════════════════════════════════
class TestInertWhereTheQuestionCannotBeAsked:
    MACHINE = MachineMeta(
        mac="NCD-BELT-01", name="NCD belt rig", active=True, type="motor",
        iso_group="2", iso_support="rigid", belt=BeltSpec(freq_hz=BELT_HZ),
    )

    def _ncd_peak_set(self) -> PeakSet:
        """An NCD-shaped triplet: rank, no amplitude, no per-axis spectrum mean —
        the shape `peaks_from_ncd` produces from the sensor's onboard peaks. The
        belt line is RANK 3, the quietest of the three, which is exactly the case
        the dominance bar would refuse if it could read levels."""
        return PeakSet(
            source="ncd_triplet", kind="ncd_peaks", shaft_freq_hz=SHAFT, rpm=RPM,
            velocities_mms={"x": 0.4, "y": 3.0, "z": 2.8},
            peaks=[
                Peak(axis="y", freq=SHAFT, rank=1),
                Peak(axis="y", freq=SHAFT * 2, rank=2),
                Peak(axis="y", freq=BELT_HZ, rank=3),
            ],
        )

    def test_amplitude_less_peaks_commit_exactly_as_before(self):
        result = run_rca(self._ncd_peak_set(), self.MACHINE, "warn", load_thresholds("route"))
        assert "belt_fault" in {m.fault for m in result.primary_findings}

    def test_the_streaming_profile_carries_none_of_the_gate_keys(self):
        streaming = load_thresholds("streaming")["rca"]
        for key in ("floor_min", "one_x_severity_min_zone", "one_x_absolute_floor_mms",
                    "belt_dominance_min"):
            assert key not in streaming, key
        result = run_rca(self._ncd_peak_set(), self.MACHINE, "warn", load_thresholds("streaming"))
        assert "belt_fault" in {m.fault for m in result.primary_findings}

    def test_an_unarmed_severity_gate_refuses_nothing(self):
        """No zone key and no absolute floor -> the question cannot be asked ->
        None, for belt exactly as for the 1x family."""
        match = FaultMatch(fault="belt_fault", description="d", freq_hz=BELT_HZ,
                           expected_hz=BELT_HZ, axis="y", confidence="medium",
                           confidence_evidence=[], evidence="e")
        ctx = _build_context(self._ncd_peak_set(), self.MACHINE, 0.05,
                             load_thresholds("streaming")["confidence"])
        assert _one_x_severity_gate(match, ctx, None, None, _BELT_CLOSING) is None
