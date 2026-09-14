"""Session PDMFIX — the 1x-family severity gate and the measured-axial conjunct.

THE DEFECT (two live production reproductions, Aug 27 walkthrough, and the
first is `outputs/S7_ACCEPTANCE.md` F-1):

  A. A healthy SINGLE-CHANNEL upload — 1.25 mm/s, ISO Zone A — committed
     "rotor imbalance at HIGH confidence" and advised field balancing. The
     evidence string asserted the axial axis was "quiet at shaft frequency"
     on a reading that had no axial channel at all. The conjunct was satisfied
     by ABSENCE, and its vacuous satisfaction was worth the exact +0.5 that
     carried the score from 2.5 (medium) to the 3.0 `high` band floor.

  B. A healthy THREE-CHANNEL trio — every axis ~0.6 mm/s, Zone A, axial/radial
     0.82 — committed angular misalignment via the 0.5-1.0 "moderate angular"
     band.

THE DISEASE: 1x-pattern commits were not severity-gated, and on a healthy
machine the rank-1 peak is always 1x, so some 1x pattern always matches. The FP
sweep measured it: 24 of 28 ISO Zone-A cells committed a 1x-family fault, and
every severity column of the 84-cell grid was IDENTICAL.

THE RULINGS PINNED HERE:
  (1) A 1x-family commit needs a severity condition — at least Zone B on the
      axis the detector argued from, on THAT MACHINE'S resolved boundaries, or
      an absolute floor. `TestSeverityGate`, `TestGateAxis`.
  (2) The imbalance "axial quiet" conjunct needs a MEASURED axial channel. An
      absent one never satisfies it and never raises confidence; single-channel
      1x-dominant caps at medium with differential framing.
      `TestAbsentAxialNeverSatisfies`.
  (3) Bearing tones stay UNGATED — a discrete BPFO at Zone A is early detection
      working. `TestBearingTonesStayUngated`.

WHY THESE PINS DRIVE FILES, NOT PeakSets. `PeakSet.measured_axes=None` means
"unknown" and resolves to `axial_measured=True` — the pre-PDMFIX default that
keeps hand-built PeakSets byte-identical. A pin built from a hand-made PeakSet
would inherit that default and pass no matter how badly the real derivation
broke, which is the exact regression these tests exist to catch. So the three
required pins go through the real product path: CSV on disk -> parse_upload ->
merge_channels -> pipeline.run_analysis.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.config import load_config, load_thresholds
from vib_agent.models import BearingSpec, Case, MachineMeta, SensorData, Spectrum
from vib_agent.pdm_core.bearing_rca import (
    _build_context,
    _one_x_gate_axis,
    _one_x_severity_gate,
    measured_axes_from_sensor_data,
    peaks_from_spectrum,
    run_rca,
)
from vib_agent.pdm_core.iso_classify import classify, resolve_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.webapp import assembly as A

# 1770 rpm -> 29.5 Hz shaft. ISO group 2 / rigid -> ab=1.4, bc=2.8, cd=4.5,
# so 0.6/1.25 are Zone A, 1.6/2.4 Zone B, 3.0 Zone C, 5.5 Zone D.
RPM = 1770.0
SHAFT = RPM / 60.0
LINES, DF, FLOOR_REL = 1600, 0.625, 0.0089

_MISALIGNMENT = frozenset(
    {"angular_misalignment", "parallel_misalignment", "severe_misalignment",
     "misalignment_general", "bent_shaft"}
)
_ONE_X_FAMILY = _MISALIGNMENT | {"imbalance"}


# ── fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def cfg() -> dict:
    """The profile every upload is analysed under. Named explicitly: a bare
    load_thresholds() resolves `active_profile`, which is a default, not a
    decision."""
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
    }


@pytest.fixture(scope="module")
def streaming() -> dict:
    return load_thresholds("streaming")


def _shape(r21: float, *, quiet: bool = False) -> list[float]:
    """A 1600-line velocity spectrum shape: 1x at 1.0, 2x at `r21`, flat bed at
    FLOOR_REL, +/-1-bin skirts. Amplitude is set by SCALING this shape, which
    holds the peak-to-mean ratio constant so `rca.floor_min` behaves identically
    at every level and the tests isolate severity alone.

    `quiet=True` is a channel that was really captured and really found nothing
    at shaft frequency: a 1x line present but at ~5.6x the axis mean, well under
    `rca.floor_min` (12.73), so `_clears_evidence_floor` keeps it out of the
    harmonic sets and `has_1x_axial` reads False. NOT a flat array — a dead-flat
    spectrum fails the assembly layer's own `spectrum_non_flat` check and the
    channel is EXCLUDED as unreadable, which is a third state ("measured but
    unusable"), not the measured-and-quiet one this models.
    """
    amp = [FLOOR_REL] * LINES
    peaks = ((SHAFT, 0.05),) if quiet else ((SHAFT, 1.0), (2 * SHAFT, r21))
    for hz, rel in peaks:
        idx = round(hz / DF)
        for off, frac in ((-1, 0.3), (0, 1.0), (1, 0.3)):
            if 0 <= idx + off < LINES:
                amp[idx + off] = max(amp[idx + off], rel * frac)
    return amp


def write_csv(path: Path, overall_mms: float, r21: float = 0.08, *, quiet: bool = False) -> Path:
    """A `freq_hz,amplitude` velocity-spectrum CSV in the tabular adapter's own
    schema, scaled so its Parseval overall RMS is exactly `overall_mms` — which
    is what parse_spectrum will read back as the channel's velocity."""
    amp = _shape(r21, quiet=quiet)
    rms = math.sqrt(sum(a * a for a in amp))
    scale = overall_mms / rms
    lines = ["freq_hz,amplitude"]
    lines += [f"{i * DF:.4f},{a * scale:.10f}" for i, a in enumerate(amp)]
    path.write_text("\n".join(lines) + "\n")
    return path


def parse(path: Path, *, bearing: str | None = None) -> Case:
    form = UploadForm(machine_alias="PDMFIX rig", rpm=RPM, iso_group="2",
                      iso_support="rigid", machine_type="motor", bearing_model=bearing)
    case, _kind, _note = parse_upload(path, form, bearings_cfg=load_config("bearings"))
    return case


def merge(channels: list[tuple[A.Direction, Case]], cfg: dict) -> Case:
    parsed = [
        A.ParsedChannel(direction=d, assumed=False, case=c, kind="tabular_spectrum",
                        conversion_note="")
        for d, c in channels
    ]
    return A.merge_channels(parsed, [], iso_table=cfg["iso_table"],
                            thresholds=cfg["thresholds"], rules=cfg["rules"]).case


def analyse(case: Case, cfg: dict):
    return run_analysis(case, **cfg)


def committed(result) -> dict[str, str]:
    return {f.fault: f.confidence for f in result.findings}


def one_x_committed(result) -> dict[str, str]:
    return {f: c for f, c in committed(result).items() if f in _ONE_X_FAMILY}


# ═════════════════════════════════════════════════════════════════════════════
# Ruling (1) — the severity gate. Both live reproductions, through real files.
# ═════════════════════════════════════════════════════════════════════════════
class TestSeverityGate:
    def test_healthy_trio_commits_nothing(self, tmp_path, cfg):
        """DEFECT B, verbatim: three channels, every axis ~0.6 mm/s (Zone A),
        axial/radial 0.82 — inside the 0.5-1.0 'moderate angular' band that used
        to commit angular_misalignment at medium on a healthy machine."""
        case = merge(
            [
                ("radial_h", parse(write_csv(tmp_path / "h.csv", 0.60))),
                ("radial_v", parse(write_csv(tmp_path / "v.csv", 0.55))),
                ("axial", parse(write_csv(tmp_path / "a.csv", 0.49))),
            ],
            cfg,
        )
        result = analyse(case, cfg)

        assert result.iso.iso_zone == "A"
        assert result.rca.axial_radial_ratio == pytest.approx(0.82, abs=0.02)
        assert one_x_committed(result) == {}, committed(result)

    def test_healthy_single_channel_commits_nothing(self, tmp_path, cfg):
        """DEFECT A, verbatim: one radial channel at 1.25 mm/s, Zone A,
        1x-dominant. Used to commit rotor imbalance at HIGH and advise field
        balancing."""
        result = analyse(parse(write_csv(tmp_path / "single.csv", 1.25)), cfg)

        assert result.iso.iso_zone == "A"
        assert one_x_committed(result) == {}, committed(result)
        assert not any(
            "balanc" in r.technique.lower() for r in result.recommended_measurements
        )

    def test_the_gate_is_the_machines_own_ab_boundary(self, tmp_path, cfg):
        """Straddle the boundary, single channel, everything else held fixed.
        ISO group 2 / rigid resolves ab=1.4, so 1.35 is refused and 1.45 commits.
        The bar comes from resolve_thresholds, not from a number in a detector."""
        below = analyse(parse(write_csv(tmp_path / "below.csv", 1.35)), cfg)
        above = analyse(parse(write_csv(tmp_path / "above.csv", 1.45)), cfg)

        assert below.iso.th_ab == 1.4 and above.iso.th_ab == 1.4
        assert below.iso.iso_zone == "A" and above.iso.iso_zone == "B"
        assert "imbalance" not in committed(below)
        assert "imbalance" in committed(above)

    def test_a_different_machine_class_moves_the_bar_with_it(self, tmp_path, cfg):
        """The same 1.45 mm/s spectrum on a group-1 flexible machine (ab=3.5) is
        Zone A and must NOT commit — proof the gate follows the 3-tier resolver
        rather than a constant. Same file, same amplitude, different machine."""
        case = parse(write_csv(tmp_path / "g1.csv", 1.45))
        case = case.model_copy(
            update={"machine": case.machine.model_copy(
                update={"iso_group": "1", "iso_support": "flexible"})}
        )
        result = analyse(case, cfg)

        assert result.iso.th_ab == 3.5 and result.iso.iso_zone == "A"
        assert one_x_committed(result) == {}, committed(result)

    def test_absolute_floor_only_ever_loosens(self, tmp_path, cfg):
        """A group-1 flexible machine (ab=3.5) at 2.9 mm/s is Zone A by class,
        but clears the 2.8 mm/s absolute floor, so the 1x call is admitted. This
        is the OR-term: it rescues a machine whose own A/B boundary is high, and
        can never tighten the gate."""
        case = parse(write_csv(tmp_path / "floor.csv", 2.9))
        case = case.model_copy(
            update={"machine": case.machine.model_copy(
                update={"iso_group": "1", "iso_support": "flexible"})}
        )
        result = analyse(case, cfg)

        assert result.iso.th_ab == 3.5 and result.iso.iso_zone == "A"
        assert cfg["thresholds"]["rca"]["one_x_absolute_floor_mms"] == 2.8
        assert "imbalance" in committed(result)


# ═════════════════════════════════════════════════════════════════════════════
# Ruling (1) — which axis the gate reads (rider 2)
#
# LEDGERED PRE-EXISTING ISSUE: `FaultMatch.axis` is NOT reliably the axis a
# detector argued from. detect_imbalance sets it to the radial 1x peak it used
# (correct), but detect_misalignment_family sets it from `harmonics["2x"][0]` —
# whichever axis happens to come first in peak-list (dict-iteration) order. So
# an AXIAL-argued angular_misalignment can carry a RADIAL `axis`. The gate is
# now immune (it routes through `_one_x_gate_axis` instead), but the field is
# still what the REPORT displays. That display question is out of scope here
# and is recorded in the session log rather than fixed.
# ═════════════════════════════════════════════════════════════════════════════
class TestGateAxis:
    def _ctx(self, velocities: dict[str, float], peaks_by_axis: dict[str, float], cfg):
        machine = MachineMeta(mac="AX", name="ax", active=True, type="pump",
                              iso_group="2", iso_support="rigid", axial_axis="x", coupled=True)
        spectra = {}
        for axis, overall in peaks_by_axis.items():
            amp = _shape(0.35)
            rms = math.sqrt(sum(a * a for a in amp))
            spectra[axis] = Spectrum(
                freq_hz=[i * DF for i in range(LINES)],
                amplitude=[a * overall / rms for a in amp],
                fmax_hz=(LINES - 1) * DF, kind="velocity",
            )
        ps = peaks_from_spectrum(spectra, RPM, velocities)
        return _build_context(ps, machine, 0.03, cfg["thresholds"]["confidence"],
                              floor_min=cfg["thresholds"]["rca"]["floor_min"])

    def test_angular_is_judged_on_the_axial_axis(self, cfg):
        """The angular branch's own evidence reads '1x shaft peak dominant on
        axial axis (x) with axial-to-radial velocity ratio …'. The gate must
        read x, not whatever axis landed first in the harmonic list."""
        ctx = self._ctx({"x": 1.2, "y": 1.6, "z": 1.4}, {"y": 1.6, "z": 1.4, "x": 1.2}, cfg)
        match = _fault("angular_misalignment", axis="y")  # deliberately mislabelled
        assert _one_x_gate_axis(match, ctx) == "x"

    def test_bent_shaft_is_judged_on_the_axial_axis(self, cfg):
        ctx = self._ctx({"x": 1.2, "y": 1.6, "z": 1.4}, {"y": 1.6, "z": 1.4, "x": 1.2}, cfg)
        assert _one_x_gate_axis(_fault("bent_shaft", axis="y"), ctx) == "x"

    def test_parallel_is_judged_on_the_loudest_radial(self, cfg):
        """'2x shaft peak dominant on radial axes' — the loudest radial, and
        never the axial even when the axial is the loudest axis overall."""
        ctx = self._ctx({"x": 9.0, "y": 1.6, "z": 2.5}, {"y": 1.6, "z": 2.5, "x": 9.0}, cfg)
        assert _one_x_gate_axis(_fault("parallel_misalignment", axis="x"), ctx) == "z"

    def test_cross_axis_subtypes_are_judged_on_the_loudest_axis(self, cfg):
        """severe/general argue from content spanning axes, so the loudest axis
        is the honest reading."""
        ctx = self._ctx({"x": 3.0, "y": 1.6, "z": 1.4}, {"y": 1.6, "z": 1.4, "x": 3.0}, cfg)
        for fault in ("severe_misalignment", "misalignment_general"):
            assert _one_x_gate_axis(_fault(fault, axis="y"), ctx) == "x"

    def test_imbalance_is_judged_on_its_own_match_axis(self, cfg):
        """detect_imbalance sets `axis` to the radial 1x peak it argued from, so
        for imbalance the two coincide and the map defers to it."""
        ctx = self._ctx({"x": 0.2, "y": 1.6, "z": 2.5}, {"y": 1.6, "z": 2.5}, cfg)
        assert _one_x_gate_axis(_fault("imbalance", axis="z"), ctx) == "z"

    def test_a_loud_radial_cannot_license_an_axial_argued_call(self, cfg):
        """The regression the map exists to prevent, end to end through the gate:
        radial at Zone C, axial at Zone A, angular_misalignment mislabelled onto
        the radial. Judged on the radial it would commit; judged on its own
        arguing axis it is refused."""
        ctx = self._ctx({"x": 0.9, "y": 3.0, "z": 2.8}, {"y": 3.0, "z": 2.8, "x": 0.9}, cfg)
        ctx.iso_thresholds = resolve_thresholds(ctx.machine, load_config("iso_zones")["zones"]).thresholds
        verdict = _one_x_severity_gate(_fault("angular_misalignment", axis="y"), ctx, "B", 2.8)
        assert verdict is not None
        assert "argues from axis x" in verdict and "0.90 mm/s" in verdict


def _fault(fault: str, *, axis: str):
    from vib_agent.models import FaultMatch

    return FaultMatch(fault=fault, description="d", axis=axis, confidence="medium", evidence="e")


# ═════════════════════════════════════════════════════════════════════════════
# Ruling (2) — an absent channel never satisfies a conjunct
# ═════════════════════════════════════════════════════════════════════════════
class TestAbsentAxialNeverSatisfies:
    """The decisive pair: IDENTICAL radial data, once alone and once beside a
    measured-and-genuinely-quiet axial channel. Before PDMFIX both produced
    `imbalance / high` with the same "axial axis quiet" sentence — the absent
    channel and the measured-silent one were indistinguishable. They must not be."""

    def _alone(self, tmp_path, cfg):
        return analyse(parse(write_csv(tmp_path / "solo.csv", 2.4)), cfg)

    def _with_quiet_axial(self, tmp_path, cfg):
        case = merge(
            [
                ("radial_h", parse(write_csv(tmp_path / "h2.csv", 2.4))),
                # A real axial capture that genuinely found nothing at shaft
                # frequency: a pure broadband bed, no 1x line.
                ("axial", parse(write_csv(tmp_path / "a2.csv", 0.25, quiet=True))),
            ],
            cfg,
        )
        return analyse(case, cfg)

    def test_both_still_commit_imbalance(self, tmp_path, cfg):
        """The gate is about severity and evidence, not about refusing to
        diagnose: at Zone B both readings still commit."""
        assert "imbalance" in committed(self._alone(tmp_path, cfg))
        assert "imbalance" in committed(self._with_quiet_axial(tmp_path, cfg))

    def test_the_absent_channel_caps_confidence_at_medium(self, tmp_path, cfg):
        alone = self._alone(tmp_path, cfg)
        measured = self._with_quiet_axial(tmp_path, cfg)
        assert committed(alone)["imbalance"] == "medium"
        assert committed(measured)["imbalance"] == "high"

    def test_the_evidence_does_not_claim_a_quiet_axial_it_never_measured(self, tmp_path, cfg):
        """S7_ACCEPTANCE F-1's exact complaint: the reason asserted the axial
        axis was quiet while the evidence recorded no axial ratio at all."""
        reason = next(f.reason for f in self._alone(tmp_path, cfg).findings if f.fault == "imbalance")
        assert "NOT measured" in reason
        assert "quiet at shaft frequency" not in reason

        measured_reason = next(
            f.reason for f in self._with_quiet_axial(tmp_path, cfg).findings if f.fault == "imbalance"
        )
        assert "measured and quiet at shaft frequency" in measured_reason

    def test_the_confidence_factor_stops_double_counting_an_absent_axis(self, tmp_path, cfg):
        """The mechanism, not just the outcome: the doubled
        `axis_pattern_consistent` weight is what an axial channel buys, and an
        absent one buys nothing. Halving it is the 0.5 that separated 3.0 (high)
        from 2.5 (medium)."""
        alone = self._alone(tmp_path, cfg)
        measured = self._with_quiet_axial(tmp_path, cfg)

        def pattern_delta(result):
            match = next(m for m in result.rca.primary_findings if m.fault == "imbalance")
            return next(f.delta for f in match.confidence_evidence
                        if f.name == "axis_pattern_consistent")

        assert pattern_delta(alone) == pytest.approx(0.5)
        assert pattern_delta(measured) == pytest.approx(1.0)

    def test_the_unresolvable_alternative_is_raised_not_hidden(self, tmp_path, cfg):
        """Differential framing, mirroring what the trio path already does: the
        misalignment family the single channel cannot separate itself from is
        surfaced with the missing measurement named."""
        alone = self._alone(tmp_path, cfg)
        raised = {d.fault: d.adjudication for d in alone.rca.differential}
        assert "misalignment_general" in raised
        assert raised["misalignment_general"].startswith("Axial channel not measured")

        measured = self._with_quiet_axial(tmp_path, cfg)
        assert not any(
            d.adjudication.startswith("Axial channel not measured")
            for d in measured.rca.differential
        ), "a measured axial channel resolves it — nothing to raise"

    def test_it_ships_the_measurement_that_resolves_it(self, tmp_path, cfg):
        """The product pillar: every unresolved ambiguity ships with the
        measurement that resolves it — and here that is an axial capture, not
        the more expensive phase work."""
        recs = self._alone(tmp_path, cfg).recommended_measurements
        axial = [r for r in recs if "axial" in r.technique.lower()]
        assert axial, [r.technique for r in recs]
        assert axial[0].priority == "high"
        assert "axial channel was not measured" in axial[0].trigger
        # Cheapest resolving step first among equal-priority recommendations.
        assert recs[0].technique == axial[0].technique

    def test_measured_axes_is_derived_from_the_velocity_fields(self):
        """The derivation itself. `None` = never measured; `0.0` = measured and
        silent. Collapsing the two is the bug."""
        silent = SensorData(rpm=RPM, x_velocity_mm_sec=0.0, y_velocity_mm_sec=2.4)
        assert measured_axes_from_sensor_data(silent) == ["x", "y"]
        absent = SensorData(rpm=RPM, y_velocity_mm_sec=2.4)
        assert measured_axes_from_sensor_data(absent) == ["y"]

    def test_a_single_axial_channel_still_refuses_imbalance(self, tmp_path, cfg):
        """Session E's axis-mislabel pin, re-checked under the new rules: an
        axial-declared single file with a dominant 1x is a misalignment/bent-shaft
        signature, never radial imbalance — and the radial axes are the ones now
        unmeasured."""
        case = merge([("axial", parse(write_csv(tmp_path / "ax.csv", 3.0)))], cfg)
        assert "imbalance" not in committed(analyse(case, cfg))


# ═════════════════════════════════════════════════════════════════════════════
# Ruling (3) — bearing tones are NOT gated
# ═════════════════════════════════════════════════════════════════════════════
class TestBearingTonesStayUngated:
    def _bearing_case(self, overall: float, cfg) -> Case:
        """A 6205 BPFO tone at Zone A amplitude on a single radial channel."""
        shaft, bpfo = 19.75, 70.80
        amp = [FLOOR_REL] * LINES
        for hz, rel in ((bpfo, 1.0), (2 * bpfo, 0.35), (shaft, 0.25)):
            idx = round(hz / DF)
            for off, frac in ((-1, 0.3), (0, 1.0), (1, 0.3)):
                if 0 <= idx + off < LINES:
                    amp[idx + off] = max(amp[idx + off], rel * frac)
        rms = math.sqrt(sum(a * a for a in amp))
        spec = Spectrum(freq_hz=[i * DF for i in range(LINES)],
                        amplitude=[a * overall / rms for a in amp],
                        fmax_hz=(LINES - 1) * DF, kind="velocity")
        brg = load_config("bearings")["bearings"]["6205"]
        machine = MachineMeta(
            mac="BRG", name="brg", active=True, type="pump", iso_group="2",
            iso_support="rigid", axial_axis="x", coupled=True,
            bearing=BearingSpec(model="6205",
                                **{k: v for k, v in brg.items() if not k.startswith("_")}),
        )
        return Case(name="brg", machine=machine, spectra={"y": spec}, raw_spectra={"y": spec},
                    sensor_data=SensorData(rpm=shaft * 60, y_velocity_mm_sec=overall,
                                           y_rms_ACC_G=overall * 0.08),
                    source="upload", validation_scope=["zone", "severity", "rca"])

    def test_a_discrete_bpfo_commits_at_zone_a(self, cfg):
        """Early detection working as designed. The same amplitude that refuses
        a 1x-family commit must still commit a bearing tone — the two are not
        the same evidence and the ruling treats them differently."""
        result = analyse(self._bearing_case(1.0, cfg), cfg)
        assert result.iso.iso_zone == "A"
        assert "bearing_outer_race" in committed(result)

    def test_and_the_1x_family_at_the_same_amplitude_does_not(self, tmp_path, cfg):
        result = analyse(parse(write_csv(tmp_path / "quiet1x.csv", 1.0)), cfg)
        assert result.iso.iso_zone == "A"
        assert one_x_committed(result) == {}


# ═════════════════════════════════════════════════════════════════════════════
# Byte-identity guards — the gate must be unarmed wherever it cannot be asked
# ═════════════════════════════════════════════════════════════════════════════
class TestTheGateIsInertWhereItMustBe:
    def test_streaming_carries_neither_key(self, streaming, cfg):
        """The frozen NCD profile has no gate keys, so the gate never arms there
        — the same pattern as epsilon_sync, floor_min and the misalignment gate."""
        assert streaming["rca"].get("one_x_severity_min_zone") is None
        assert streaming["rca"].get("one_x_absolute_floor_mms") is None
        assert cfg["thresholds"]["rca"]["one_x_severity_min_zone"] == "B"

    def test_a_caller_that_omits_iso_thresholds_is_unaffected(self, tmp_path, cfg):
        """`run_rca` without `iso_thresholds` cannot ask the machine's own zone
        question, so it must not answer it — every pre-PDMFIX caller (and every
        hand-built PeakSet in the suite) keeps its old verdict."""
        case = parse(write_csv(tmp_path / "nogate.csv", 1.25))
        sd = case.sensor_data
        resolved = resolve_thresholds(case.machine, cfg["iso_table"])
        reading = classify(sd, case.machine, resolved)
        velocities = {a: getattr(sd, f"{a}_velocity_mm_sec") or 0.0 for a in ("x", "y", "z")}
        ps = peaks_from_spectrum(case.spectra, sd.rpm, velocities,
                                 measured_axes=measured_axes_from_sensor_data(sd))

        ungated = run_rca(ps, case.machine, reading.iso_severity, cfg["thresholds"])
        gated = run_rca(ps, case.machine, reading.iso_severity, cfg["thresholds"],
                        iso_thresholds=resolved.thresholds)

        assert "imbalance" in {m.fault for m in ungated.primary_findings}
        assert "imbalance" not in {m.fault for m in gated.primary_findings}

    def test_an_acceleration_only_reading_does_not_arm_the_gate(self, cfg):
        """FOUND BY THE CORPUS DIFF, not by the FP sweep — every sweep cell had
        velocity, so none of them could expose this.

        ISO severity is a VELOCITY judgement. On an acceleration-only reading
        (MAFAULDA, CWRU, MFPT, an unscaled WAV) there is no velocity at all:
        `iso_zone` is `not_assessable` and `severity_rms` is None. But
        `PeakSet.velocities_mms` still carries the pipeline's 0.0 coercion, so a
        gate reading it naively sees a perfectly quiet machine and refuses every
        1x-family commit across the whole dataset. The first cut of this gate did
        exactly that: it wiped `misalignment_general` off 24 of 61 MAFAULDA files,
        including 9 with a MISALIGNMENT ground-truth label.

        No measured velocity on the arguing axis -> the severity question cannot
        be asked -> the gate stays unarmed. Same doctrine `iso_classify` already
        states: missing velocity is never coerced to 0.0, because that would fake
        a quiet-machine Zone A reading out of missing data."""
        def spec(rel_1x: float, rel_2x: float) -> Spectrum:
            amp = [FLOOR_REL] * LINES
            for hz, rel in ((SHAFT, rel_1x), (2 * SHAFT, rel_2x)):
                idx = round(hz / DF)
                for off, frac in ((-1, 0.3), (0, 1.0), (1, 0.3)):
                    if 0 <= idx + off < LINES:
                        amp[idx + off] = max(amp[idx + off], rel * frac)
            return Spectrum(freq_hz=[i * DF for i in range(LINES)], amplitude=amp,
                            fmax_hz=(LINES - 1) * DF, kind="velocity")

        machine = MachineMeta(mac="ACC", name="acc", active=True, type="pump", iso_group="2",
                              iso_support="rigid", axial_axis="x", coupled=True)
        spectra = {"x": spec(1.0, 0.5), "y": spec(0.6, 0.4), "z": spec(0.5, 0.3)}
        case = Case(
            name="acc", machine=machine, spectra=spectra, raw_spectra=spectra,
            # No velocity on ANY axis — the acceleration-only shape.
            sensor_data=SensorData(rpm=RPM, x_rms_ACC_G=0.05, y_rms_ACC_G=0.05, z_rms_ACC_G=0.05),
            source="upload", validation_scope=["rca"],
        )
        result = analyse(case, cfg)

        assert result.iso.iso_zone == "not_assessable"
        assert result.iso.severity_rms is None
        assert "misalignment_general" in committed(result), committed(result)

    def test_measured_axes_unknown_means_all_measured(self, cfg):
        """`measured_axes=None` is UNKNOWN, and unknown resolves to the
        pre-PDMFIX assumption. This is the compatibility default every
        hand-built PeakSet in the suite relies on."""
        amp = _shape(0.08)
        rms = math.sqrt(sum(a * a for a in amp))
        spec = Spectrum(freq_hz=[i * DF for i in range(LINES)],
                        amplitude=[a * 2.4 / rms for a in amp],
                        fmax_hz=(LINES - 1) * DF, kind="velocity")
        machine = MachineMeta(mac="U", name="u", active=True, type="pump", iso_group="2",
                              iso_support="rigid", axial_axis="x", coupled=True)
        # A captured spectrum implies a measured channel, so the adapter's own
        # default is the spectra keys -- here y only, no axial.
        ps = peaks_from_spectrum({"y": spec}, RPM, {"x": 0.0, "y": 2.4, "z": 0.0})
        assert ps.measured_axes == ["y"]
        assert _build_context(ps, machine, 0.03,
                              cfg["thresholds"]["confidence"]).axial_measured is False

        # UNKNOWN is the separate, compatibility case: it resolves to the
        # pre-PDMFIX assumption, which is what every hand-built PeakSet in the
        # suite relies on.
        ps_none = ps.model_copy(update={"measured_axes": None})
        assert _build_context(ps_none, machine, 0.03,
                              cfg["thresholds"]["confidence"]).axial_measured is True
