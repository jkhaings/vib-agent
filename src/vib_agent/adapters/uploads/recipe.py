"""Session G: the parse RECIPE — a closed-vocabulary description of how to read
a text export, and the deterministic executor that carries it out.

The split this module exists to enforce:

    a model may describe a file's LAYOUT;  only this code reads its NUMBERS.

`ParseRecipe` is therefore a hard boundary, not a convenience type. Every field
is either an enumerated literal or a bounded number — there is no free-text
field anywhere in it, which is what makes it impossible for an inferred recipe
to carry a path, a URL, a format string, an expression, or any other instruction
into the executor. `extra="forbid"` means an invented field fails validation
rather than being ignored. (`tests/test_recipe.py` asserts the no-free-text
property by introspection, so a future field cannot quietly reopen the hole.)

It lives in adapters/, not models.py, on purpose: a recipe describes an upload's
on-disk layout and never crosses into pdm_core — the Case it produces does.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vib_agent.adapters.uploads.common import UploadForm, machine_from_form
from vib_agent.adapters.uploads.sample import decode_upload_bytes
from vib_agent.adapters.uploads.tabular import _ACCEL_PROXY_SCALE, ACCEL_PROXY_NOTE
from vib_agent.adapters.uploads.units import to_mms_rms
from vib_agent.models import Case, HistoryPoint, SensorData, Spectrum

# Extensions that have no adapter of their own and take the inference lane directly.
INFERRED_EXTENSIONS: tuple[str, ...] = (".txt", ".dat", ".asc")
# Session G2: .csv/.xlsx keep their template adapters and are only routed here
# when template parsing FAILS — the fallback lane, never the first choice.
FALLBACK_EXTENSIONS: tuple[str, ...] = (".csv", ".xlsx")
INFERENCE_CAPABLE_EXTENSIONS: tuple[str, ...] = INFERRED_EXTENSIONS + FALLBACK_EXTENSIONS

# ── closed vocabulary ────────────────────────────────────────────────────────
RecipeKind = Literal["spectrum", "waveform", "trend"]
Delimiter = Literal["comma", "semicolon", "tab", "whitespace", "pipe"]
DecimalMark = Literal["dot", "comma"]
XUnit = Literal["hz", "cpm", "orders", "seconds", "index"]
AmplitudeUnit = Literal["mm_s", "in_s", "g", "m_s2", "um", "mil", "unknown"]
Detection = Literal["rms", "peak", "peak_to_peak"]
ColumnRole = Literal["x", "amplitude", "timestamp", "value", "ignore"]
Orientation = Literal["columns", "rows"]
ThousandsSeparator = Literal["none", "comma", "space", "apostrophe"]
RpmSource = Literal["form", "file_header"]
FsSource = Literal["none", "file_header", "x_column"]
TimestampFormat = Literal["iso8601", "epoch_seconds"]

_DELIMITER_CHARS: dict[str, str | None] = {
    "comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|", "whitespace": None,
}

# Velocity units are the only ones that can carry an ISO 20816 severity claim.
_VELOCITY_UNITS = ("mm_s", "in_s")
# Amplitude units we can honestly relabel as acceleration-g (exact conversions only).
_ACCEL_UNITS = ("g", "m_s2")
_G = 9.80665

MAX_COLUMNS = 32


class ParseRecipe(BaseModel):
    """How to read one text export. Closed vocabulary; no free text anywhere."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: RecipeKind
    delimiter: Delimiter
    decimal_mark: DecimalMark = "dot"
    thousands_separator: ThousandsSeparator = "none"
    # "rows" means the file is transposed: each ROLE is a row running left to
    # right, not a column running down the page. Some analysers export a
    # frequency row above an amplitude row; this is that shape, and nothing more.
    orientation: Orientation = "columns"
    skip_rows: int = Field(0, ge=0, le=1000)
    header_rows: int = Field(0, ge=0, le=10)
    columns: list[ColumnRole] = Field(..., min_length=1, max_length=MAX_COLUMNS)

    x_unit: XUnit = "hz"
    amplitude_unit: AmplitudeUnit = "unknown"
    detection: Detection = "rms"
    timestamp_format: TimestampFormat = "iso8601"

    rpm_source: RpmSource = "form"
    rpm_value: float | None = Field(None, gt=0, le=100_000)
    fs_source: FsSource = "none"
    fs_value: float | None = Field(None, gt=0, le=10_000_000)

    @model_validator(mode="after")
    def _coherent(self) -> "ParseRecipe":
        roles = self.columns
        if self.kind == "trend":
            if roles.count("timestamp") != 1 or roles.count("value") != 1:
                raise ValueError("a trend recipe needs exactly one timestamp column and one value column")
        else:
            if roles.count("amplitude") != 1:
                raise ValueError(f"a {self.kind} recipe needs exactly one amplitude column")
            if roles.count("x") > 1:
                raise ValueError("at most one x column")
            if self.kind == "spectrum" and roles.count("x") == 0:
                raise ValueError("a spectrum recipe needs an x (frequency) column")
            if self.kind == "spectrum" and self.x_unit in ("seconds", "index"):
                raise ValueError("a spectrum's x column must be a frequency unit (hz, cpm, or orders)")
            if self.kind == "waveform" and roles.count("x") == 0 and self.fs_source != "file_header":
                raise ValueError("a waveform recipe with no time column needs a sample rate from the header")
        if self.orientation == "rows" and self.kind == "trend":
            raise ValueError("a trend history is read down the page, not across it")
        if self.rpm_source == "file_header" and self.rpm_value is None:
            raise ValueError("rpm_source='file_header' requires rpm_value")
        if self.fs_source == "file_header" and self.fs_value is None:
            raise ValueError("fs_source='file_header' requires fs_value")
        return self

    # ── UI/report-facing description (built from the closed vocabulary only) ──
    def describe(self, *, form_rpm: float) -> dict[str, Any]:
        """A plain-language summary for the confirm card and the provenance
        line. Every string here is OURS, assembled from enumerated values —
        no inferred text is ever echoed to the analyst or into the report."""
        kind_label = {"spectrum": "spectrum", "waveform": "time waveform",
                      "trend": "trend history"}[self.kind]
        unit_label = {"mm_s": "mm/s", "in_s": "in/s", "g": "g (acceleration)",
                      "m_s2": "m/s² (acceleration)", "um": "µm (displacement)",
                      "mil": "mil (displacement)", "unknown": "unknown units"}[self.amplitude_unit]
        detection_label = {"rms": "RMS", "peak": "peak", "peak_to_peak": "peak-to-peak"}[self.detection]
        x_label = {"hz": "Hz", "cpm": "CPM", "orders": "shaft orders",
                   "seconds": "seconds", "index": "sample index"}[self.x_unit]
        rpm = self.rpm_value if self.rpm_source == "file_header" else form_rpm
        return {
            "kind": self.kind,
            "headline": f"{unit_label} {detection_label} {kind_label}",
            "x_axis": x_label,
            "amplitude_unit": self.amplitude_unit,
            "amplitude_label": unit_label,
            "detection": self.detection,
            "rpm": rpm,
            "rpm_from": "the file header" if self.rpm_source == "file_header" else "the form",
            "severity_available": self.amplitude_unit in _VELOCITY_UNITS,
            "columns": list(self.columns),
            "delimiter": self.delimiter,
            "orientation": self.orientation,
            # Honest disclosure on the confirm card: a file can carry more data
            # than one channel's worth (a second amplitude column, a phase
            # column). One recipe reads ONE channel, so say what was left.
            "ignored_columns": self.columns.count("ignore"),
        }


class RecipeExecutionError(ValueError):
    """The recipe did not fit the file. Message is analyst-safe (no traceback,
    no file contents)."""


# ── execution ────────────────────────────────────────────────────────────────


def _split(line: str, delimiter: Delimiter) -> list[str]:
    char = _DELIMITER_CHARS[delimiter]
    return line.split() if char is None else line.split(char)


_THOUSANDS_CHARS: dict[str, tuple[str, ...]] = {
    "comma": (",",), "space": (" ",), "apostrophe": ("\u2019", "'"),
}


class _GroupingError(ValueError):
    """A separator declared as a thousands mark does not group digits in threes,
    so deleting it would change the value rather than tidy it."""


def _groups_in_threes(integer_part: str, separator: str) -> bool:
    """Does `integer_part` use `separator` the way a thousands mark is used?

    '1,234,567' does. '0,78' does not \u2014 that comma is a DECIMAL mark, and
    deleting it turns 0.78 into 78. '1,23456' does not either. Absence of the
    separator is fine; this only judges the ones that are there.
    """
    groups = integer_part.split(separator)
    if len(groups) == 1:
        return True
    head, *rest = groups
    head = head.lstrip("+-")
    if not head.isdigit() or not 1 <= len(head) <= 3:
        return False
    return all(group.isdigit() and len(group) == 3 for group in rest)


def _to_float(raw: str, decimal_mark: DecimalMark, thousands: ThousandsSeparator = "none") -> float:
    """One field as a number, under the recipe's declared numeric conventions.

    The grouping guard is why this is not a one-line float(). Deleting a
    declared thousands separator is only safe if it really is one: read with
    thousands="comma", the EU amplitude '0,78' becomes 78 -- a hundredfold error
    that lands on a real-looking spectrum, keeps its shape, and (before this)
    passed the verification gate as an ISO severity claim. The trap runs the
    other way too: with decimal_mark="comma" the dot is a grouping mark by
    definition, so the US value '1.23' would become 123.

    Neither can be settled from the field alone, so neither is guessed at. A
    separator that does not group in threes raises, the row is dropped by the
    caller, and a file where that happens throughout refuses with a message
    naming the mismatch. (INTAKE-HARDEN, fixture decimal_comma_cpm.txt.)
    """
    text = raw.strip().strip('"').strip("'").replace("\u00a0", " ")

    decimal_char = "," if decimal_mark == "comma" else "."
    integer_part = text.split(decimal_char, 1)[0]
    grouping: list[str] = list(_THOUSANDS_CHARS.get(thousands, ()))
    if decimal_mark == "comma":
        grouping.append(".")
    for char in grouping:
        if char in integer_part and not _groups_in_threes(integer_part, char):
            raise _GroupingError(
                f"{char!r} is being read as a thousands separator, but it does not group "
                "the digits in threes")
        if char != ".":
            text = text.replace(char, "")

    if decimal_mark == "comma":
        # A EU export writes 1.234,56 — the dot is the thousands mark there.
        text = text.replace(".", "").replace(",", ".")
    return float(text)


def _cell_rows(path: Path) -> list[list[str]]:
    """An .xlsx sheet as text cells. `data_only=True` reads the STORED VALUE of
    every cell — a formula is never evaluated here, and never seen."""
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    rows: list[list[str]] = []
    for row in sheet.iter_rows(values_only=True):
        if row is None:
            continue
        rows.append(["" if cell is None else str(cell) for cell in row])
    workbook.close()
    return rows


def _raw_rows(path: Path, recipe: ParseRecipe) -> list[list[str]]:
    """Every line of the file as a list of fields, before roles are applied.
    Spreadsheet cells are already split, so the delimiter does not apply to
    them (an .xlsx recipe's delimiter is inert, and says so on the card).

    The decode is the SAME one the sample used (adapters/uploads/sample.py), so
    the executor reads the file the model was shown rather than a different one.
    It was a bare utf-8 read until INTAKE-HARDEN, which meant a UTF-16 export
    yielded no numeric rows at all and a UTF-8 BOM turned the first datum into
    '﻿10.0' — one silently dropped row, invisible unless you counted them
    (fixture utf8_bom_nohdr.txt). `lenient` keeps today's behaviour for a file
    whose TAIL is junk: a text header over packed binary costs the rows the
    binary occupies, not the whole upload.
    """
    if path.suffix.lower() == ".xlsx":
        return _cell_rows(path)
    text = decode_upload_bytes(path.read_bytes(), lenient=True)
    return [_split(line, recipe.delimiter) for line in text.splitlines()]


def _read_table(path: Path, recipe: ParseRecipe) -> list[list[str]]:
    """The data rows the recipe describes, one list of fields per row.

    For a TRANSPOSED file (orientation="rows") each role is a row running left
    to right; the table is turned upright here so everything downstream reads
    the same shape it always has.

    Rows that do not fit are dropped (instrument exports carry footers and blank
    lines), but if most of the file fails to fit we say so rather than analysing
    the remnant."""
    raw = _raw_rows(path, recipe)[recipe.skip_rows :][recipe.header_rows :]
    n_cols = len(recipe.columns)

    if recipe.orientation == "rows":
        role_rows = [row for row in raw if any(field.strip() for field in row)][:n_cols]
        if len(role_rows) < n_cols:
            raise RecipeExecutionError(
                "the file does not carry one row per described series")
        width = min(len(row) for row in role_rows)
        if width < 8:
            raise RecipeExecutionError("too few values in the transposed rows to analyse")
        return [[role_rows[role][i] for role in range(n_cols)] for i in range(width)]

    rows: list[list[str]] = []
    considered = 0
    for fields in raw:
        if not any(field.strip() for field in fields):
            continue
        considered += 1
        if len(fields) >= n_cols:
            rows.append(fields)
    if not rows:
        raise RecipeExecutionError("no data rows matched the interpreted layout")
    if considered and len(rows) / considered < 0.5:
        raise RecipeExecutionError("most lines did not match the interpreted layout")
    return rows


def _column(rows: list[list[str]], recipe: ParseRecipe, role: ColumnRole) -> list[str]:
    index = recipe.columns.index(role)
    return [row[index] for row in rows]


def _numeric_pairs(
    rows: list[list[str]], recipe: ParseRecipe, roles: tuple[ColumnRole, ...]
) -> list[tuple[float, ...]]:
    """Every row's values for `roles`, dropping rows with a non-numeric field
    (footers, units lines, 'END OF DATA'). Raises if too few survive.

    A row dropped because the file's decimal/thousands convention is not the one
    the recipe declared is counted separately: when that is what emptied the
    file, the refusal says so instead of the generic 'too few numeric rows',
    which is the difference between an analyst re-exporting blindly and one who
    knows the interpretation was European when the file was American.
    """
    indices = [recipe.columns.index(role) for role in roles]
    out: list[tuple[float, ...]] = []
    grouping_failures = 0
    for row in rows:
        try:
            out.append(tuple(_to_float(row[i], recipe.decimal_mark, recipe.thousands_separator)
                             for i in indices))
        except _GroupingError:
            grouping_failures += 1
        except (ValueError, IndexError):
            continue
    if len(out) < 8:
        if grouping_failures > len(out):
            raise RecipeExecutionError(
                "the numbers in this file do not match the decimal and thousands-separator "
                "convention it was interpreted with, so no reading could be trusted")
        raise RecipeExecutionError("too few numeric rows to analyse (need at least 8)")
    return out


def _x_to_hz(values: list[float], recipe: ParseRecipe, shaft_hz: float) -> list[float]:
    if recipe.x_unit == "hz":
        return values
    if recipe.x_unit == "cpm":
        return [v / 60.0 for v in values]
    if recipe.x_unit == "orders":
        return [v * shaft_hz for v in values]
    raise RecipeExecutionError("this x-axis unit cannot be converted to frequency")


def _amplitudes_to_mms(values: list[float], recipe: ParseRecipe) -> tuple[list[float], str]:
    """Velocity amplitudes -> mm/s RMS through the one shared converter.
    Everything else is returned untouched, with a note that says so."""
    if recipe.amplitude_unit in _VELOCITY_UNITS:
        return to_mms_rms(values, velocity_unit=recipe.amplitude_unit, detection_type=recipe.detection)
    return values, ""


_NO_SEVERITY_NOTE = {
    "g": "Amplitudes are in acceleration (g), not velocity — ISO 20816 severity is not computed; "
         "fault-frequency identification only.",
    "m_s2": "Amplitudes are in acceleration (m/s², converted to g) — ISO 20816 severity is not "
            "computed; fault-frequency identification only.",
    "um": "Amplitudes are in displacement (µm) — ISO 20816 severity is a velocity judgement and is "
          "not computed; fault-frequency identification only.",
    "mil": "Amplitudes are in displacement (mil) — ISO 20816 severity is a velocity judgement and "
           "is not computed; fault-frequency identification only.",
    "unknown": "Amplitude units could not be established from the file — ISO 20816 severity is NOT "
               "computed; fault-frequency identification only.",
}


def _accel_proxy(values: list[float], recipe: ParseRecipe) -> float:
    """A value for the ONE field the quality gate's machine_running check reads.

    Real acceleration when the units really are acceleration (g directly, m/s²
    by the exact 9.80665 conversion). For displacement and unknown units there
    is no honest conversion to g at all, so this follows the precedent
    adapters/uploads/wav.py set for an unscaled recording: normalise by the
    signal's own maximum and report the RMS of that — a scale-free
    "there is signal here, and it is not silence" measure, never a real g
    reading. Nothing downstream treats it as one: those units already set
    validation_scope=["rca"], so severity and zone are excluded.
    """
    if not values:
        return 0.0
    rms = math.sqrt(sum(v * v for v in values) / len(values))
    if recipe.amplitude_unit == "m_s2":
        return rms / _G
    if recipe.amplitude_unit == "g":
        return rms
    if recipe.amplitude_unit in _VELOCITY_UNITS:
        return rms * _ACCEL_PROXY_SCALE
    peak = max(abs(v) for v in values)
    return (rms / peak) if peak > 0 else 0.0


def _spectrum_case(
    path: Path, recipe: ParseRecipe, form: UploadForm, bearings_cfg: dict[str, Any]
) -> tuple[Case, str]:
    rows = _read_table(path, recipe)
    shaft_hz = (recipe.rpm_value if recipe.rpm_source == "file_header" else form.rpm) / 60.0
    pairs = _numeric_pairs(rows, recipe, ("x", "amplitude"))
    freqs = _x_to_hz([p[0] for p in pairs], recipe, shaft_hz)
    raw_amps = [p[1] for p in pairs]
    amps, note = _amplitudes_to_mms(raw_amps, recipe)

    # A text spectrum export is a DIRECT spectrum, never a demodulated one, so it
    # is labelled velocity or raw_acceleration -- never "envelope", whose 1x line
    # would be read as an artifact (Session B, B1).
    spectrum = Spectrum(freq_hz=freqs, amplitude=amps, fmax_hz=max(freqs),
                        kind="velocity" if recipe.amplitude_unit in _VELOCITY_UNITS
                        else "raw_acceleration")
    machine = machine_from_form(form, bearings_cfg, mac_prefix="UPLOAD-INFER")
    rpm = recipe.rpm_value if recipe.rpm_source == "file_header" else form.rpm

    if recipe.amplitude_unit in _VELOCITY_UNITS:
        # Parseval, exactly as the CSV spectrum adapter does: each converted bin
        # is an RMS-equivalent amplitude for its own component.
        overall = math.sqrt(sum(v * v for v in amps))
        sensor_data = SensorData(rpm=rpm, y_velocity_mm_sec=overall,
                                 y_rms_ACC_G=overall * _ACCEL_PROXY_SCALE)
        case = Case(name=form.machine_alias, machine=machine, sensor_data=sensor_data,
                    spectra={"y": spectrum}, source="upload")
        return case, note

    sensor_data = SensorData(rpm=rpm, y_rms_ACC_G=_accel_proxy(amps, recipe))
    case = Case(name=form.machine_alias, machine=machine, sensor_data=sensor_data,
                spectra={"y": spectrum}, source="upload", validation_scope=["rca"])
    return case, _NO_SEVERITY_NOTE[recipe.amplitude_unit]


def _waveform_case(
    path: Path, recipe: ParseRecipe, form: UploadForm, bearings_cfg: dict[str, Any]
) -> tuple[Case, str]:
    # Same DSP the CWRU/WAV paths use — no new signal processing is invented here.
    from vib_agent.adapters.cwru import envelope_spectrum, raw_spectrum

    rows = _read_table(path, recipe)
    has_x = "x" in recipe.columns
    if has_x:
        pairs = _numeric_pairs(rows, recipe, ("x", "amplitude"))
        times = [p[0] for p in pairs]
        signal = [p[1] for p in pairs]
        # Sample rate from the DATA, not from the model: median sample interval.
        deltas = sorted(t2 - t1 for t1, t2 in zip(times, times[1:]) if t2 > t1)
        if not deltas:
            raise RecipeExecutionError("the time column does not increase — cannot establish a sample rate")
        fs = 1.0 / deltas[len(deltas) // 2]
    else:
        signal = [p[0] for p in _numeric_pairs(rows, recipe, ("amplitude",))]
        fs = float(recipe.fs_value or 0.0)
    if not (0 < fs <= 10_000_000):
        raise RecipeExecutionError("could not establish a plausible sample rate for this waveform")

    import numpy as np

    values, note = _amplitudes_to_mms(signal, recipe)
    array = np.asarray(values, dtype=float)
    # Envelope demodulation needs a resonance band above 1.5 kHz. A route
    # waveform sampled at a few kHz has no such band, so emit the raw spectrum
    # alone rather than band-passing a range the recording does not contain.
    if fs / 2.0 > 1600.0:
        spectrum = envelope_spectrum(array, fs)
        # Session B (B1) precedent: envelope for the bearing detectors, raw for
        # the 1x-family detectors, which must not read the envelope's 1x artifact.
        raw = raw_spectrum(array, fs)
    else:
        spectrum = raw_spectrum(array, fs)
        raw = None
        note = (note + " " if note else "") + (
            f"Sample rate {fs:.0f} Hz has no resonance band above 1.5 kHz, so the analysis uses the "
            "raw spectrum; envelope demodulation was not applicable."
        )
    machine = machine_from_form(form, bearings_cfg, mac_prefix="UPLOAD-INFER")
    rpm = recipe.rpm_value if recipe.rpm_source == "file_header" else form.rpm

    if recipe.amplitude_unit in _VELOCITY_UNITS:
        overall = math.sqrt(sum(v * v for v in values) / len(values))
        sensor_data = SensorData(rpm=rpm, y_velocity_mm_sec=overall,
                                 y_rms_ACC_G=overall * _ACCEL_PROXY_SCALE)
        case = Case(name=form.machine_alias, machine=machine, sensor_data=sensor_data,
                    spectra={"y": spectrum}, raw_spectra={"y": raw} if raw else None,
                    source="upload")
        return case, note

    sensor_data = SensorData(rpm=rpm, y_rms_ACC_G=_accel_proxy(values, recipe))
    case = Case(name=form.machine_alias, machine=machine, sensor_data=sensor_data,
                spectra={"y": spectrum}, raw_spectra={"y": raw} if raw else None,
                source="upload", validation_scope=["rca"])
    unit_note = _NO_SEVERITY_NOTE[recipe.amplitude_unit]
    return case, f"{unit_note} {note}".strip()


def _parse_timestamp(raw: str, fmt: TimestampFormat) -> datetime:
    text = raw.strip().strip('"')
    if fmt == "epoch_seconds":
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    return datetime.fromisoformat(text)


def _trend_case(
    path: Path, recipe: ParseRecipe, form: UploadForm, bearings_cfg: dict[str, Any]
) -> tuple[Case, str]:
    rows = _read_table(path, recipe)
    stamps_raw = _column(rows, recipe, "timestamp")
    values_raw = _column(rows, recipe, "value")
    points: list[tuple[datetime, float]] = []
    for stamp, value in zip(stamps_raw, values_raw):
        try:
            points.append((_parse_timestamp(stamp, recipe.timestamp_format),
                           _to_float(value, recipe.decimal_mark, recipe.thousands_separator)))
        except (ValueError, OSError, OverflowError):
            continue
    if len(points) < 3:
        raise RecipeExecutionError("too few dated readings to build a trend (need at least 3)")

    points.sort(key=lambda p: p[0])
    converted, note = _amplitudes_to_mms([v for _, v in points], recipe)
    history = [HistoryPoint(ts=ts, value=v) for (ts, _), v in zip(points, converted)]
    current = history[-1].value
    machine = machine_from_form(form, bearings_cfg, mac_prefix="UPLOAD-INFER")
    sensor_data = SensorData(
        rpm=recipe.rpm_value if recipe.rpm_source == "file_header" else form.rpm,
        y_velocity_mm_sec=current,
        y_rms_ACC_G=current * _ACCEL_PROXY_SCALE,
    )
    case = Case(name=form.machine_alias, machine=machine, sensor_data=sensor_data,
                history=history, source="upload")
    if recipe.amplitude_unit not in _VELOCITY_UNITS:
        note = _NO_SEVERITY_NOTE[recipe.amplitude_unit]
    return case, note


def execute_recipe(
    path: Path, recipe: ParseRecipe, form: UploadForm, *, bearings_cfg: dict[str, Any]
) -> tuple[Case, str, str]:
    """Read the WHOLE file per the recipe and build a Case. Runs inside the
    existing parse sandbox (webapp/_parse_child.py); the model that produced the
    recipe never sees this data and never contributes a number to it.

    Returns (Case, kind, conversion_note) — the same contract parse_upload has.
    """
    if recipe.kind == "spectrum":
        case, note = _spectrum_case(path, recipe, form, bearings_cfg)
    elif recipe.kind == "waveform":
        case, note = _waveform_case(path, recipe, form, bearings_cfg)
    else:
        case, note = _trend_case(path, recipe, form, bearings_cfg)
    # Session INTAKE-HONEST: every recipe lane derives its acceleration figure
    # through the same velocity proxy (this module's three ACC_G sites), so
    # the assumption states itself here exactly as on the template lanes.
    note = f"{note} {ACCEL_PROXY_NOTE}" if note else ACCEL_PROXY_NOTE
    return case, f"inferred_{recipe.kind}", note
