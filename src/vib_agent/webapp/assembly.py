"""Session E — multi-axis upload assembly.

Merges 1-3 parsed single-axis Cases (each carrying its channel on the ``"y"``
axis, the upload-adapter convention) into ONE Case, mapping the analyst's
declared direction onto the pipeline's axis semantics:

    axial            -> "x"      (MachineMeta.axial_axis stays "x", always)
    radial-horizontal -> "y"
    radial-vertical   -> "z"

Every merge decision here FEEDS the existing 3-axis pdm_core logic
(``_build_context``'s v_radial/v_axial, the axial-ratio, the misalignment-variant
discrimination, the imbalance radial-dominance gate). Nothing in ``pdm_core`` is
changed — this module only assembles the Case those pure functions already
understand, exactly the shape ``adapters/uploads/mafaulda.py`` already builds.

Pure: imports only ``vib_agent.models`` and ``vib_agent.pipeline.run_analysis``.
No FastAPI, no I/O. The single-defaulted-channel path is a strict identity
pass-through, so today's single-file reports stay byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from scipy.signal import find_peaks

from vib_agent.models import Case, Check, HistoryPoint, SensorData, Spectrum
from vib_agent.pipeline import run_analysis

Direction = Literal["radial_h", "radial_v", "axial"]

DIRECTION_TO_AXIS: dict[Direction, str] = {"axial": "x", "radial_h": "y", "radial_v": "z"}
DIRECTION_LABELS: dict[Direction, str] = {
    "radial_h": "Radial – horizontal",
    "radial_v": "Radial – vertical",
    "axial": "Axial",
}
DIRECTION_SHORT: dict[Direction, str] = {"radial_h": "H", "radial_v": "V", "axial": "A"}
ALL_DIRECTIONS: tuple[Direction, ...] = ("radial_h", "radial_v", "axial")

# Per-axis SensorData field suffixes that a single-channel adapter may populate.
_AXIS_SUFFIXES: tuple[str, ...] = (
    "velocity_mm_sec",
    "rms_ACC_G",
    "max_ACC_G",
    "displacement_mm",
    "peak_one_Hz",
    "peak_two_Hz",
    "peak_three_Hz",
)

# Canonical validation-scope order (matches models.Case's Literal order), so the
# merged scope is deterministic regardless of channel order.
_SCOPE_ORDER = ("zone", "severity", "rca", "trend", "trend_relative", "anomaly")

# Cross-file speed agreement: consider candidate peaks up to this many shaft
# orders; accept a candidate that matches k*shaft for k up to _KMAX (a
# parallel-misalignment radial channel is legitimately 2x-dominant, so a
# 1x-only check would false-alarm on a valid trio).
_SPEED_BAND_MAX_ORDERS = 5.0
_SPEED_AGREEMENT_KMAX = 3
# Session G2 correction: agreement means running-speed content is PRESENT at
# k*shaft, not that it DOMINATES the band. The dominance test this replaced
# fail-closed on the most ordinary case there is -- a bearing fault. BPFO at
# ~3.5x shaft sits inside the 5-order band and routinely outranks 1x, so a
# three-channel upload of a genuinely faulted machine was refused with "no
# channel shows running-speed content", i.e. the very signature the product
# exists to find made it decline to analyse. A peak counts as present when it
# stands this many times above the spectrum's own median amplitude.
_SPEED_PRESENCE_RATIO = 3.0


# ── inputs / outputs ────────────────────────────────────────────────────────
@dataclass
class ChannelUpload:
    """One MEASUREMENTS slot as received by the API (pre-parse)."""

    slot: int
    path: Path
    direction: Direction
    assumed: bool  # True only for a single file whose direction was defaulted


@dataclass
class ParsedChannel:
    direction: Direction
    assumed: bool
    case: Case  # single-axis, spectrum on "y"
    kind: str
    conversion_note: str


@dataclass
class UnreadableChannel:
    direction: Direction
    safe_message: str


@dataclass
class ChannelStatus:
    direction: Direction
    label: str
    status: Literal["ok", "unreadable", "gate_fail"]
    detail: str | None
    assumed: bool
    has_velocity: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "label": self.label,
            "short": DIRECTION_SHORT[self.direction],
            "status": self.status,
            "detail": self.detail,
            "assumed": self.assumed,
            "has_velocity": self.has_velocity,
        }


@dataclass
class MergeOutcome:
    case: Case
    conversion_note: str
    # Notes inserted after the Machine Details table (channels-measured, per-channel
    # status, mixed-scale coverage, speed warning), in top-to-bottom visual order.
    report_notes: list[str] = field(default_factory=list)
    # The next-tier line, inserted into Severity & Coverage (falls back to Machine
    # Details when that section is absent). Kept separate so it lands in the right
    # section without interleaving with the Machine Details notes.
    coverage_note: str | None = None
    channel_summary: dict[str, Any] = field(default_factory=dict)
    fail_closed_checks: list[Check] = field(default_factory=list)


# ── remap ───────────────────────────────────────────────────────────────────
def remap_channel_case(case: Case, direction: Direction) -> Case:
    """Return a copy of a single-axis ``"y"`` Case with its spectrum, raw
    spectrum, and per-axis SensorData fields moved onto the declared axis.
    Identity when the declared axis is already ``"y"`` (radial-horizontal)."""
    axis = DIRECTION_TO_AXIS[direction]

    for label, spec in (("spectra", case.spectra), ("raw_spectra", case.raw_spectra)):
        if spec and set(spec) - {"y"}:
            raise ValueError(
                f"channel {label} carries axes {sorted(spec)} — expected a single-axis "
                "'y' upload (the assembly layer maps one channel per file)"
            )
    if axis == "y":
        return case

    def _move(spec: dict[str, Spectrum] | None) -> dict[str, Spectrum] | None:
        if not spec or "y" not in spec:
            return None
        return {axis: spec["y"]}

    sd = case.sensor_data
    sd_kwargs: dict[str, Any] = {}
    if sd is not None:
        sd_kwargs = {"rpm": sd.rpm, "temperature": sd.temperature, "msg_type": sd.msg_type, "mode": sd.mode}
        for suffix in _AXIS_SUFFIXES:
            sd_kwargs[f"{axis}_{suffix}"] = getattr(sd, f"y_{suffix}")
    new_sd = SensorData(**sd_kwargs) if sd is not None else None

    return case.model_copy(
        update={
            "spectra": _move(case.spectra),
            "raw_spectra": _move(case.raw_spectra),
            "sensor_data": new_sd,
        }
    )


def _has_velocity(case: Case) -> bool:
    """A channel informs ISO severity iff its adapter produced a real velocity
    scale — signalled by ``severity`` remaining in its validation_scope
    (RCA-only formats — unscaled WAV, envelope-only MAT — drop it)."""
    return "severity" in (case.validation_scope or [])


def _merge_scope(cases: list[Case]) -> list[str]:
    present = {s for c in cases for s in (c.validation_scope or [])}
    return [s for s in _SCOPE_ORDER if s in present]


def _merge_cases(channels: list[tuple[Direction, Case]]) -> Case:
    """Merge remapped single-axis cases into one multi-axis Case. `channels`
    are (direction, remapped_case) pairs; the first is slot-1 (machine + scalar
    reading context)."""
    base_dir, base = channels[0]
    base_sd = base.sensor_data

    spectra: dict[str, Spectrum] = {}
    all_have_raw = all(c.raw_spectra for _, c in channels)
    raw_spectra: dict[str, Spectrum] = {}
    sd_kwargs: dict[str, Any] = {}
    if base_sd is not None:
        sd_kwargs = {
            "rpm": base_sd.rpm,
            "temperature": base_sd.temperature,
            "msg_type": base_sd.msg_type,
            "mode": base_sd.mode,
        }

    for direction, case in channels:
        axis = DIRECTION_TO_AXIS[direction]
        if case.spectra:
            spectra.update(case.spectra)
        if all_have_raw and case.raw_spectra:
            raw_spectra.update(case.raw_spectra)
        if case.sensor_data is not None:
            for suffix in _AXIS_SUFFIXES:
                sd_kwargs[f"{axis}_{suffix}"] = getattr(case.sensor_data, f"{axis}_{suffix}")

    sensor_data = SensorData(**sd_kwargs) if base_sd is not None else None
    return base.model_copy(
        update={
            "spectra": spectra or None,
            "raw_spectra": raw_spectra or None,
            "sensor_data": sensor_data,
            "validation_scope": _merge_scope([c for _, c in channels]),
        }
    )


# ── cross-file speed agreement ──────────────────────────────────────────────
@dataclass
class _SpeedReport:
    warn: bool
    no_agreement: bool  # determinate channels exist but none agree with stated RPM
    per_channel_hz: list[tuple[Direction, float | None]]


def _channel_spectrum(case: Case, direction: Direction) -> Spectrum | None:
    axis = DIRECTION_TO_AXIS[direction]
    for spec in (case.raw_spectra, case.spectra):
        if spec and axis in spec:
            return spec[axis]
    return None


def _dominant_low_freq(spectrum: Spectrum, band_max_hz: float) -> float | None:
    amp = spectrum.amplitude
    if not amp:
        return None
    idxs, _ = find_peaks(amp)
    best_amp = -1.0
    best_freq: float | None = None
    for i in idxs:
        f = spectrum.freq_hz[i]
        if 0.0 < f <= band_max_hz and amp[i] > best_amp:
            best_amp = amp[i]
            best_freq = f
    return best_freq


def _has_shaft_content(spectrum: Spectrum, shaft_hz: float, tol: float, kmax: int) -> bool:
    """Is there a peak at the stated running speed (or its first harmonics) that
    stands above this spectrum's own noise floor? Presence, not dominance --
    see _SPEED_PRESENCE_RATIO."""
    amps = list(spectrum.amplitude)
    freqs = list(spectrum.freq_hz)
    if not amps or not freqs:
        return False
    ordered = sorted(amps)
    floor = ordered[len(ordered) // 2]
    steps = [b - a for a, b in zip(freqs, freqs[1:]) if b > a]
    bin_step = sorted(steps)[len(steps) // 2] if steps else 0.0
    for k in range(1, kmax + 1):
        target = k * shaft_hz
        # never narrower than the spectrum's own resolution
        window = max(tol * target, 1.5 * bin_step)
        near = [a for f, a in zip(freqs, amps) if abs(f - target) <= window]
        if near and (floor <= 0 or max(near) >= _SPEED_PRESENCE_RATIO * floor):
            return True
    return False


def _speed_agreement(channels: list[tuple[Direction, Case]], rpm: float, tol_pct: float) -> _SpeedReport:
    shaft_hz = (rpm or 0.0) / 60.0
    tol = tol_pct / 100.0
    per: list[tuple[Direction, float | None]] = []
    agree_flags: list[bool] = []
    if shaft_hz <= 0:
        return _SpeedReport(warn=False, no_agreement=False, per_channel_hz=[(d, None) for d, _ in channels])

    band_max = _SPEED_BAND_MAX_ORDERS * shaft_hz
    for direction, remapped in channels:
        spec = _channel_spectrum(remapped, direction)
        cand = _dominant_low_freq(spec, band_max) if spec is not None else None
        per.append((direction, cand))
        if cand is None or spec is None:
            continue  # indeterminate — never counts as agreement or disagreement
        # `cand` (the dominant in-band peak) is what the warning REPORTS; whether
        # the channel agrees is a question of presence at k*shaft, which a defect
        # tone louder than 1x must not be able to answer for it.
        agree_flags.append(_has_shaft_content(spec, shaft_hz, tol, _SPEED_AGREEMENT_KMAX))

    if not agree_flags:  # every channel indeterminate
        return _SpeedReport(warn=False, no_agreement=False, per_channel_hz=per)
    warn = not all(agree_flags)
    no_agreement = not any(agree_flags)
    return _SpeedReport(warn=warn, no_agreement=no_agreement, per_channel_hz=per)


def _speed_warning_note(rpm: float, per: list[tuple[Direction, float | None]]) -> str:
    shaft_hz = (rpm or 0.0) / 60.0
    parts = [
        f"{DIRECTION_LABELS[d]} {hz:.1f} Hz" if hz is not None else f"{DIRECTION_LABELS[d]} (none)"
        for d, hz in per
    ]
    return (
        "Speed check warning: the uploaded files' dominant running-speed peaks "
        f"({'; '.join(parts)}) do not agree with the stated running speed "
        f"({shaft_hz:.1f} Hz) — these files may not be from the same machine or "
        "operating condition."
    )


# ── report notes ────────────────────────────────────────────────────────────
def channels_measured_note(present: set[Direction]) -> str:
    measured = [
        f"{DIRECTION_LABELS[d].lower()} ({DIRECTION_TO_AXIS[d]})"
        for d in ALL_DIRECTIONS
        if d in present
    ]
    missing = [DIRECTION_LABELS[d].lower() for d in ALL_DIRECTIONS if d not in present]
    note = "Channels measured: " + ", ".join(measured)
    if missing:
        note += "; not measured: " + ", ".join(missing)
    return note + "."


def next_tier_note(present: set[Direction]) -> str | None:
    if "axial" not in present:
        return (
            "An axial measurement would additionally enable misalignment-variant "
            "and bent-shaft discrimination."
        )
    if not {"radial_h", "radial_v"} <= present:
        return (
            "A second radial measurement would additionally strengthen imbalance "
            "and parallel-misalignment discrimination."
        )
    return None


def _channel_status_note(statuses: list[ChannelStatus]) -> str | None:
    if all(s.status == "ok" and s.has_velocity for s in statuses):
        return None
    parts = []
    for s in statuses:
        if s.status == "ok" and s.has_velocity:
            parts.append(f"{s.label}: OK")
        elif s.status == "ok":
            parts.append(f"{s.label}: OK (no amplitude scale — frequency identification only)")
        elif s.status == "gate_fail":
            parts.append(f"{s.label}: excluded ({s.detail})")
        else:
            parts.append(f"{s.label}: unreadable ({s.detail})")
    return "Channel status — " + "; ".join(parts) + "."


def _coverage_note(statuses: list[ChannelStatus]) -> str | None:
    included = [s for s in statuses if s.status == "ok"]
    with_v = [s.label for s in included if s.has_velocity]
    without_v = [s.label for s in included if not s.has_velocity]
    if with_v and without_v:
        return (
            f"Severity/ISO assessment is based on the {', '.join(with_v)} channel(s); "
            f"the {', '.join(without_v)} channel(s) carry no amplitude scale and "
            "contributed frequency identification only."
        )
    return None


# ── the entry point ─────────────────────────────────────────────────────────
def merge_channels(
    parsed: list[ParsedChannel],
    unreadable: list[UnreadableChannel],
    *,
    iso_table: dict[str, Any],
    thresholds: dict[str, Any],
    rules: dict[str, Any] | None = None,
    speed_tolerance_pct: float = 5.0,
    client_history: list[HistoryPoint] | None = None,
) -> MergeOutcome:
    """Assemble a merged Case + report notes + channel statuses + optional
    fail-closed checks from the parsed channels. Pure and deterministic.

    Session HIST-1. `client_history` is the analyst's browser-held trend points
    (already parsed and validated at the form boundary by
    `adapters.uploads.common.parse_client_history`). It attaches HERE rather
    than at any of `_assemble`'s four return points, because there are four of
    them — the identity fast-path, the all-channels-failed path, the
    no-speed-agreement path and the ordinary merge — and a history that reached
    three of them would be a silent hole exactly where the report claims a
    trend. History is machine-level, not per-channel, so after the merge is
    also the only place it means one thing.

    `None`/empty leaves the outcome untouched, so the single-defaulted-channel
    identity pass-through stays a strict identity and today's single-file
    reports stay byte-identical (Session E's byte-compat matrix).
    """
    outcome = _assemble(
        parsed, unreadable, iso_table=iso_table, thresholds=thresholds,
        rules=rules, speed_tolerance_pct=speed_tolerance_pct,
    )
    if client_history:
        outcome.case = attach_client_history(outcome.case, client_history)
    return outcome


def attach_client_history(case: Case, history: list[HistoryPoint]) -> Case:
    """`case` with the analyst's browser-held points as its history, stamped
    with where they came from so the report can say it.

    A history the FILE supplied is never overwritten: the `mode=trend` CSV lane
    builds `Case.history` from the analyst's own upload
    (`adapters/uploads/tabular.py::parse_trend`), and those points are the
    file's, not the card's. The webapp refuses that combination with a 422
    before a job exists, so this branch is a backstop, not a policy.

    `model_copy(update=...)` is the established Case-level idiom in this module
    (`_merge_cases`); both fields are plain optionals with no cross-field
    validator, so nothing is skipped by not re-validating a Case that carries
    whole spectra.
    """
    if case.history:
        return case
    return case.model_copy(update={"history": list(history),
                                   "history_source": "analyst_supplied"})


def _assemble(
    parsed: list[ParsedChannel],
    unreadable: list[UnreadableChannel],
    *,
    iso_table: dict[str, Any],
    thresholds: dict[str, Any],
    rules: dict[str, Any] | None = None,
    speed_tolerance_pct: float = 5.0,
) -> MergeOutcome:
    """The merge itself — four return paths, none of which knows about history."""
    n_files = len(parsed) + len(unreadable)

    # Identity fast-path — a single, direction-defaulted, cleanly-parsed file is
    # exactly today's single-file upload; return its Case untouched so the report
    # is byte-identical (no per-channel gate, no speed check, no notes).
    if n_files == 1 and len(parsed) == 1 and parsed[0].assumed and not unreadable:
        ch = parsed[0]
        status = ChannelStatus(
            ch.direction, DIRECTION_LABELS[ch.direction], "ok", None,
            assumed=True, has_velocity=_has_velocity(ch.case),
        )
        return MergeOutcome(
            case=ch.case,
            conversion_note=ch.conversion_note,
            report_notes=[],
            channel_summary={"channels": [status.to_dict()], "speed_warning": None},
        )

    statuses: list[ChannelStatus] = [
        ChannelStatus(u.direction, DIRECTION_LABELS[u.direction], "unreadable", u.safe_message,
                      assumed=False, has_velocity=False)
        for u in unreadable
    ]

    # Per-channel quality gate (multi-file only). Running the whole pipeline per
    # single-axis case gives a verdict identical to uploading that file alone.
    passers: list[ParsedChannel] = []
    if n_files >= 2:
        for ch in parsed:
            try:
                probe = run_analysis(ch.case, iso_table=iso_table, thresholds=thresholds, rules=rules)
            except Exception:  # noqa: BLE001 — treat an un-analyzable channel as a gate failure, not a crash
                statuses.append(ChannelStatus(ch.direction, DIRECTION_LABELS[ch.direction], "gate_fail",
                                              "channel could not be analyzed", False, _has_velocity(ch.case)))
                continue
            if probe.quality_gate.overall == "fail":
                reasons = "; ".join(c.reason for c in probe.quality_gate.checks
                                    if c.status == "fail" and c.reason) or "data-quality gate failed"
                statuses.append(ChannelStatus(ch.direction, DIRECTION_LABELS[ch.direction], "gate_fail",
                                              reasons, False, _has_velocity(ch.case)))
                continue
            passers.append(ch)
            statuses.append(ChannelStatus(ch.direction, DIRECTION_LABELS[ch.direction], "ok", None,
                                          ch.assumed, _has_velocity(ch.case)))
    else:  # single explicit-direction file — the whole-case gate in run_analysis IS the gate
        passers = list(parsed)
        statuses.extend(
            ChannelStatus(ch.direction, DIRECTION_LABELS[ch.direction], "ok", None,
                          ch.assumed, _has_velocity(ch.case))
            for ch in parsed
        )

    remapped_all = [(ch.direction, remap_channel_case(ch.case, ch.direction)) for ch in parsed]
    remapped_pass = [(ch.direction, remap_channel_case(ch.case, ch.direction)) for ch in passers]

    conversion_note = _merge_conversion_notes(parsed)
    present = {ch.direction for ch in passers}
    base_notes = _machine_notes(present, statuses)
    tier = next_tier_note(present)
    rpm = parsed[0].case.sensor_data.rpm if parsed[0].case.sensor_data else 0.0

    # All channels failed the gate → insufficient-data (fail closed).
    if n_files >= 2 and not passers:
        merged = _merge_cases(remapped_all)
        reason = "; ".join(f"{s.label}: {s.detail}" for s in statuses if s.status == "gate_fail") \
            or "every uploaded channel failed data-quality checks"
        checks = [Check(name="per_channel_quality", status="fail",
                        reason=f"every uploaded channel failed data-quality checks ({reason})")]
        return MergeOutcome(merged, conversion_note, base_notes, tier,
                            _summary(statuses, None), checks)

    # Cross-file speed agreement (on gate-passing channels).
    speed_warning: str | None = None
    if n_files >= 2 and len(remapped_pass) >= 2:
        speed = _speed_agreement(remapped_pass, rpm, speed_tolerance_pct)
        if speed.no_agreement:
            merged = _merge_cases(remapped_pass)
            checks = [Check(name="cross_channel_speed_agreement", status="fail",
                            reason="no uploaded channel shows running-speed content consistent with the "
                                   "stated RPM — these files may not be from the same machine or operating condition")]
            return MergeOutcome(merged, conversion_note, base_notes, tier,
                                _summary(statuses, None), checks)
        if speed.warn:
            speed_warning = _speed_warning_note(rpm, speed.per_channel_hz)
            base_notes.append(speed_warning)

    merged = _merge_cases(remapped_pass)
    return MergeOutcome(merged, conversion_note, base_notes, tier, _summary(statuses, speed_warning))


def _machine_notes(present: set[Direction], statuses: list[ChannelStatus]) -> list[str]:
    """Machine-Details-hook notes in top-to-bottom visual order (channels
    measured, per-channel status, mixed-scale coverage). The speed warning is
    appended by the caller when applicable."""
    notes: list[str] = [channels_measured_note(present)]
    status_note = _channel_status_note(statuses)
    if status_note:
        notes.append(status_note)
    coverage = _coverage_note(statuses)
    if coverage:
        notes.append(coverage)
    return notes


def _summary(statuses: list[ChannelStatus], speed_warning: str | None) -> dict[str, Any]:
    return {"channels": [s.to_dict() for s in statuses], "speed_warning": speed_warning}


def _merge_conversion_notes(parsed: list[ParsedChannel]) -> str:
    notes = [(ch.direction, ch.conversion_note) for ch in parsed if ch.conversion_note]
    if not notes:
        return ""
    uniq = {note for _, note in notes}
    if len(uniq) == 1:
        return next(iter(uniq))
    return " ".join(f"{DIRECTION_LABELS[d]}: {note}" for d, note in notes)
