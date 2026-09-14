"""Session B (B1) — spectrum kind routing.

Bearing detectors read the ENVELOPE spectrum; the 1x-family detectors (imbalance/
misalignment/looseness/belt/blade-pass/resonance) read the RAW/velocity spectrum,
NEVER the envelope (whose 1x line is a demodulation artifact). Proven by cases
where the raw and envelope spectra carry DIFFERENT tones, so a mis-route would flip
the diagnosis. On the NCD path (no spectra) the flow context reuses the bearing
context, so behavior is byte-identical to pre-B1.
"""

from __future__ import annotations

from tests.fixtures import REFERENCE_CASES
from vib_agent.models import Case, SensorData
from vib_agent.pipeline import run_analysis
from vib_agent.synth.generator import make_spectrum

# shaft 30 Hz @ 1800 rpm; 6206 BPFO ~= 107.16 Hz. Low velocities -> ISO Zone A
# (severity "ok"), which gates the resonance detector out so each case isolates
# bearing-vs-1x-family routing. Acceleration set so the machine reads as running.
_BPFO = 107.16
_1X = 30.0
_2X = 60.0  # a shaft harmonic that no 1x-family detector commits on alone
_SD = dict(
    rpm=1800.0,
    x_velocity_mm_sec=0.1, y_velocity_mm_sec=0.3, z_velocity_mm_sec=0.3,
    x_rms_ACC_G=0.05, y_rms_ACC_G=0.10, z_rms_ACC_G=0.10,
)


def _spectra(peaks_by_axis, kind, seed=1):
    d = make_spectrum(peaks_by_axis, seed=seed)
    return {ax: s.model_copy(update={"kind": kind}) for ax, s in d.items()}


def _run(machine, iso_table, thresholds, rules, *, env_peaks, raw_peaks, sd=None):
    case = Case(
        name="kind-routing",
        machine=machine,
        sensor_data=SensorData(**(sd or _SD)),
        spectra=_spectra(env_peaks, "envelope"),
        raw_spectra=_spectra(raw_peaks, "raw_acceleration"),
    )
    return run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)


def _all_faults(result):
    faults = {f.fault for f in result.findings}
    if result.rca is not None:
        faults |= {d.fault for d in result.rca.differential}
    return faults


def test_bearing_reads_envelope_not_raw(comp_machine, iso_table, thresholds, rules):
    # BPFO in the ENVELOPE only; raw carries a non-diagnostic 2x. Bearing must find
    # BPFO -> it read the envelope, not the (BPFO-free) raw.
    r = _run(
        comp_machine, iso_table, thresholds, rules,
        env_peaks={"y": [(_BPFO, 0.36)], "z": [(_BPFO, 0.33)]},
        raw_peaks={"y": [(_2X, 0.10)], "z": [(_2X, 0.10)]},
    )
    assert "bearing_outer_race" in {f.fault for f in r.findings}


def test_flow_ignores_envelope_1x_artifact(comp_machine, iso_table, thresholds, rules):
    # THE category-error fix: a 1x line in the ENVELOPE (a demodulation artifact)
    # must NOT produce imbalance, because the 1x-family reads the raw (no 1x here).
    r = _run(
        comp_machine, iso_table, thresholds, rules,
        env_peaks={"y": [(_1X, 0.30)], "z": [(_1X, 0.30)]},
        raw_peaks={"y": [(_2X, 0.10)], "z": [(_2X, 0.10)]},
    )
    assert "imbalance" not in _all_faults(r)


def test_flow_reads_raw_1x(comp_machine, iso_table, thresholds, rules):
    # 1x radial in the RAW only (envelope carries a non-diagnostic 2x). Imbalance
    # must fire -> the 1x-family read the raw spectrum.
    # Route-profile-legal imbalance fixture (DQ-0): the product profile arms the
    # PDMFIX 1x-severity gate (commit needs the detector's radial axis >= Zone B,
    # ab=1.4 for this group-2/rigid machine) and the J3 radial-dominance gate
    # (v_radial_max/v_axial >= 7.5). The module's _SD (0.3 mm/s Zone A, dominance
    # 3.0) only ever committed because streaming carries neither key. The routing
    # property is unchanged: the 1x lives ONLY in the raw spectrum, so imbalance
    # firing still proves the raw was read, not the (1x-free) envelope.
    sd = dict(_SD, y_velocity_mm_sec=2.0, z_velocity_mm_sec=2.0)
    r = _run(
        comp_machine, iso_table, thresholds, rules,
        env_peaks={"y": [(_2X, 0.10)], "z": [(_2X, 0.10)]},
        raw_peaks={"y": [(_1X, 0.30)], "z": [(_1X, 0.30)]},
        sd=sd,
    )
    assert "imbalance" in {f.fault for f in r.findings}


def test_ncd_case_unaffected(machines, iso_table, thresholds, rules):
    # No spectra at all: flow reuses the bearing context (byte-identity guard).
    # A healthy NCD reading yields no_significant_findings, exactly as before B1.
    spec = REFERENCE_CASES["T01_healthy_zone_a"]
    case = Case(
        name="t01", machine=machines[spec["mac"]],
        sensor_data=SensorData(**spec["sensor_data"]), battery_percent=spec.get("battery_percent"),
    )
    r = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    assert [f.fault for f in r.findings] == ["no_significant_findings"]
