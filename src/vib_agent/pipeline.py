"""The canonical deterministic pipeline: one Case → one AnalysisResult.

This is the fixed analysis order made executable — quality gate → machine
context/ISO classify → trend (if history) → bearing RCA (if a reading) →
synthesize → recommend. The Phase 4 agent's system prompt mirrors this exact
order; both paths produce the same numbers because both call the same
pdm_core functions.

Zero LLM, zero network. Layer 2 (z-score) and Layer 3 (Isolation Forest)
decline for a single-file analysis: z-score needs a streaming baseline that a
case file does not carry, and Layer 3 is stubbed pending the sidecar port.
The history story is carried by Layer 4 (trend); the report notes the
declines under Limitations.
"""

from __future__ import annotations

from typing import Any

from vib_agent.models import (
    AnalysisResult,
    Case,
    CaseExpected,
    IsolationForestResult,
    MachineMeta,
    QualityGateResult,
)
from vib_agent.pdm_core.bearing_rca import (
    enrich_with_sidebands,
    measured_axes_from_sensor_data,
    peaks_from_ncd,
    peaks_from_spectrum,
    run_rca,
)
from vib_agent.pdm_core.iso_classify import classify, mark_not_assessable, resolve_thresholds
from vib_agent.pdm_core.quality_gate import run_quality_gate
from vib_agent.pdm_core.recommendations import recommend_measurements
from vib_agent.pdm_core.synthesize import synthesize_findings
from vib_agent.pdm_core.trend import compute_trend


def run_analysis(
    case: Case,
    *,
    iso_table: dict[str, dict[str, float]],
    thresholds: dict[str, Any],
    rules: dict[str, Any] | None = None,
) -> AnalysisResult:
    """Run the full deterministic pipeline over one case.

    `iso_table` = config iso_zones["zones"]; `thresholds` = a resolved profile
    (config.load_thresholds()); `rules` = config next_measurements (for
    follow-up measurements) — if None, no measurements are emitted.
    """
    machine: MachineMeta = case.machine
    sensor_data = case.sensor_data
    if sensor_data is None:
        raise ValueError("run_analysis requires case.sensor_data (a current reading)")

    resolved = resolve_thresholds(machine, iso_table)
    reading = classify(sensor_data, machine, resolved)

    # Gate spectrum quality checks (non-flat/clipping/ski-slope/speed-sanity) run
    # on the RAW acquired signal when available (Session B B1: `raw_spectra`), else
    # the bearing/envelope spectrum. This resolves for MAFAULDA only (single-axis
    # bearing datasets key "y" and have no velocity -> dominant_axis None -> get("x")
    # is None); MAFAULDA's raw_spectra is the same raw spectrum its gate read pre-B1,
    # so verdicts stay byte-identical.
    gate_spectrum = None
    quality_spectra = case.raw_spectra or case.spectra
    if quality_spectra:
        gate_spectrum = quality_spectra.get(reading.dominant_axis or "x")

    gate: QualityGateResult = run_quality_gate(
        reading,
        sensor_data,
        machine,
        thresholds["quality_gate"],
        lifecycle=case.lifecycle,
        battery_percent=case.battery_percent,
        spectrum=gate_spectrum,
    )

    # 7B fix (b): a units_plausibility WARN on a reading that DID produce a zone
    # means the velocity was present but implausible -- an implausible value must
    # not anchor an ISO 20816 severity zone, so withhold it (the measured value
    # is preserved for the report to show, labelled implausible). Absent-velocity
    # readings are already not_assessable from classify(); gate-fail cases are
    # exempt (no diagnosis is made there, and it keeps machine-off fixtures in
    # their existing zone).
    if (
        gate.overall != "fail"
        and reading.iso_zone != "not_assessable"
        and any(c.name == "units_plausibility" and c.status == "warn" for c in gate.checks)
    ):
        reading = mark_not_assessable(reading, "implausible velocity units")

    # Layer 4 — trend (if history). Computed before RCA so a rising trend can
    # feed the RCA confidence rubric as corroborating evidence.
    trend = None
    if case.history:
        trend = compute_trend(case.history, reading.th_bc, thresholds["trend"])
    trend_rising = trend is not None and trend.severity != "ok"

    # Layer 3 — Isolation Forest declines (stub) whenever history exists;
    # otherwise not applicable.
    isolation_forest = None
    if case.history:
        isolation_forest = IsolationForestResult(
            status="not_enough_history", n_samples=len(case.history)
        )

    rca = None
    if gate.overall != "fail":
        if case.spectra:
            # This 0.0 fallback is an RCA-INPUT contract (PeakSet.velocities_mms,
            # used for axial/radial velocity reporting), NOT emitted ISO severity
            # -- so it is deliberately left byte-identical to before Session A
            # (changing what RCA receives would move confidence outputs, which the
            # regression floor forbids). Severity truthfulness is handled entirely
            # on the Reading/Finding side; velocity-input honesty is Session B.
            velocities = {
                "x": sensor_data.x_velocity_mm_sec or 0.0,
                "y": sensor_data.y_velocity_mm_sec or 0.0,
                "z": sensor_data.z_velocity_mm_sec or 0.0,
            }
            # Session PDMFIX: the 0.0 coercion above is deliberately preserved (it
            # is the RCA-input contract), so it is no longer the only record of what
            # was measured -- `measured_axes` carries that truth alongside it. A
            # channel that was never measured must not be readable downstream as a
            # channel that was measured and found silent.
            measured_axes = measured_axes_from_sensor_data(sensor_data)
            rca_cfg = thresholds["rca"]
            max_peaks = rca_cfg.get("spectrum_max_peaks_per_axis", 3)
            prom = rca_cfg.get("spectrum_peak_prominence")
            # Defaults (3, None) match peaks_from_spectrum's own defaults exactly,
            # so a profile without these keys (streaming) behaves byte-identically.
            peak_set = peaks_from_spectrum(
                case.spectra, sensor_data.rpm or 0.0, velocities,
                max_peaks_per_axis=max_peaks, prominence=prom,
                measured_axes=measured_axes,
            )
            # Session B (B1): the 1x-family detectors read the RAW/velocity peaks
            # when an adapter emitted them; None => run_rca reuses the bearing
            # context (byte-identical to pre-B1 for envelope-only / NCD cases).
            flow_peak_set = None
            if case.raw_spectra:
                flow_peak_set = peaks_from_spectrum(
                    case.raw_spectra, sensor_data.rpm or 0.0, velocities,
                    max_peaks_per_axis=max_peaks, prominence=prom,
                    measured_axes=measured_axes,
                )
        else:
            peak_set = peaks_from_ncd(sensor_data)
            flow_peak_set = None

        rca = run_rca(
            peak_set,
            machine,
            reading.iso_severity,
            thresholds,
            gate_warnings=(gate.overall == "warn"),
            trend_rising=trend_rising,
            flow_peak_set=flow_peak_set,
            # Session PDMFIX: the 3-tier resolver's output (machine override >
            # factory default > ISO table), already computed above and previously
            # discarded after classify(). The 1x-family severity gate answers to
            # THIS machine's own zone boundaries, never to an ISO number in a detector.
            iso_thresholds=resolved.thresholds,
        )
        if case.spectra:
            rca = enrich_with_sidebands(
                rca, case.spectra, rca.shaft_freq_hz, thresholds["rca"]["tolerance_pct"] / 100.0,
                thresholds["confidence"],
            )

    # S12FIX (HANDOFF-08-27 §5.1, ruling D-3): an RCA that raised produced no
    # fault screen at all, and `synthesize_findings` refuses only on a gate FAIL
    # -- so a status="error" RCA falls straight through to
    # `no_significant_findings` at HIGH confidence, carrying the reading's full
    # ISO zone. That is a crash of ours reading as a clean bill, and it is worst
    # on a quiet machine, where the sentence is literally "no significant
    # findings" over an unscreened bearing tone.
    #
    # The failure is OURS, not the data's: the gate passed and stays honest, the
    # zone is a real measurement and stays computed. What must not survive is
    # the DIAGNOSIS -- so nothing is committed, and every downstream consumer
    # branches on `rca.status` (never on findings being empty) to say why.
    # `machine_off` is deliberately NOT included: it is a determination, not a
    # failure, and it reaches here only behind a passing gate.
    analysis_failed = rca is not None and rca.status == "error"
    findings = [] if analysis_failed else synthesize_findings(reading, gate, rca, trend)

    recommended = []
    if rules is not None:
        recommended = recommend_measurements(
            rca.primary_findings if rca is not None else [],
            rca.differential if rca is not None else [],
            gate,
            machine,
            trend,
            rules,
            reading=reading,
        )

    return AnalysisResult(
        machine_id=reading.machine_id,
        mac=reading.mac,
        ts=reading.ts,
        quality_gate=gate,
        iso=reading,
        zscore=None,  # declines: no streaming baseline in a single-file analysis
        isolation_forest=isolation_forest,
        trend=trend,
        rca=rca,
        findings=findings,
        recommended_measurements=recommended,
    )


def analysis_to_expected(result: AnalysisResult) -> CaseExpected:
    """Project the eval `expected` block from a completed analysis. Faults are
    the committed primary findings (the differential is deliberately excluded —
    those are the candidates the pipeline did NOT commit to).
    """
    # S12FIX: ground truth cannot be projected from an analysis that crashed --
    # the empty `faults` list below would otherwise become an eval expectation
    # asserting this case HAS no faults, which is the same clean-bill hazard one
    # layer further out.
    if result.rca is not None and result.rca.status == "error":
        raise ValueError(
            "cannot project expected outcome: the RCA failed "
            f"({result.rca.reason}) — this analysis has no committed diagnosis"
        )
    faults = [m.fault for m in result.rca.primary_findings] if result.rca is not None else []
    return CaseExpected(
        zone=result.iso.iso_zone if result.iso is not None else None,
        faults=faults,
        insufficient=(result.quality_gate.overall == "fail"),
        gate_overall=result.quality_gate.overall,
    )
