"""CSV / XLSX upload adapters -- two schemas, one shared row-reading path:
spectrum mode (`freq_hz,amplitude`) and trend mode (`timestamp,overall_rms`).
Both apply the units conversion (units.py) before building anything Layer 1
sees, and both follow adapters/cwru.py's Case-building conventions.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl

from vib_agent.adapters.uploads.common import UploadForm, machine_from_form
from vib_agent.adapters.uploads.units import to_mms_rms
from vib_agent.models import Case, HistoryPoint, SensorData, Spectrum

# Same acceleration-proxy convention as synth/generator.py::packet_from_spectra
# ("Acceleration is a simple velocity-proportional estimate... calibrated so
# ordinary running velocities clear min_running_g and near-zero (machine-off)
# velocities don't") -- reused rather than inventing a second convention,
# since an upload is always a velocity reading, never a real accelerometer
# signal. It exists solely so the quality gate's machine_running check (which
# only reads *_rms_ACC_G) behaves sensibly; real severity/zone/RCA always use
# the real velocity, never this proxy.
_ACCEL_PROXY_SCALE = 0.08

# Session INTAKE-HONEST: the proxy above is an intake ASSUMPTION, so it states
# itself in the report's notes instead of operating silently. Appended to the
# conversion note by every lane that uses the proxy (tabular here, uff.py,
# recipe.py) — the WAV lane never uses it (a WAV carries a real signal).
# Session REPORT-4 (item 1): "severity, ISO zone, and diagnosis" became
# "severity, zone, and diagnosis". This note is inserted INTO the report, so it
# is document text, and on a machine judged against the analyst's own limits it
# was the last line in either document still signing ISO's name to a plant
# number — after the banner, the damage-stage box and the per-location roster
# were fixed in report/. Nothing is lost by dropping the word: the clause says
# which SIGNAL those three read (the uploaded velocity, not the proxy), which is
# true on every basis, and whose thresholds produced the zone is stated where it
# belongs, in the health line's own basis clause.
ACCEL_PROXY_NOTE = (
    "No accelerometer signal was uploaded, so the machine-running check uses a "
    "velocity-proportional acceleration estimate; severity, zone, and "
    "diagnosis read the uploaded velocity values only."
)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".xlsx":
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheet = wb.worksheets[0]
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header = [str(c).strip() for c in next(rows_iter)]
        except StopIteration:
            raise ValueError("XLSX file has no header row") from None
        rows: list[dict[str, str]] = []
        for row in rows_iter:
            if row is None or all(c is None for c in row):
                continue
            rows.append({h: ("" if v is None else str(v)) for h, v in zip(header, row)})
        return rows

    text = path.read_text()
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("CSV file has no header row")
    return [dict(r) for r in reader]


def _require_columns(rows: list[dict[str, str]], columns: tuple[str, ...]) -> None:
    if not rows:
        raise ValueError("file has a header row but no data rows")
    missing = [c for c in columns if c not in rows[0]]
    if missing:
        raise ValueError(f"missing required column(s) {missing} (found: {sorted(rows[0])})")


def parse_spectrum(path: Path, form: UploadForm, *, bearings_cfg: dict[str, Any]) -> tuple[Case, str]:
    """`freq_hz,amplitude` -- one spectrum on the radial 'y' axis (same axis
    convention as adapters/cwru.py). Enough to run full ISO classification
    (unlike CWRU, which has no velocity at all): the overall RMS across bins
    becomes the y-axis velocity reading. Returns (Case, conversion_note)."""
    rows = _read_rows(path)
    _require_columns(rows, ("freq_hz", "amplitude"))

    freqs = [float(r["freq_hz"]) for r in rows]
    raw_amplitude = [float(r["amplitude"]) for r in rows]
    amplitude, conversion_note = to_mms_rms(
        raw_amplitude, velocity_unit=form.velocity_unit, detection_type=form.detection_type
    )

    # Parseval: each converted bin is already an RMS-equivalent amplitude for
    # its own frequency component (units.to_mms_rms folds any peak/pk-pk ->
    # RMS conversion in), so the overall signal RMS is the Euclidean sum of
    # per-bin RMS values across orthogonal FFT bins.
    overall_rms_mms = math.sqrt(sum(v * v for v in amplitude))

    # B1: an uploaded velocity spectrum (mm/s RMS per bin) — a 1x-family kind.
    spectrum = Spectrum(freq_hz=freqs, amplitude=amplitude, fmax_hz=max(freqs), kind="velocity")
    machine = machine_from_form(form, bearings_cfg, mac_prefix="UPLOAD-SPEC")
    sensor_data = SensorData(
        rpm=form.rpm,
        y_velocity_mm_sec=overall_rms_mms,
        y_rms_ACC_G=overall_rms_mms * _ACCEL_PROXY_SCALE,
    )

    case = Case(
        name=form.machine_alias,
        machine=machine,
        sensor_data=sensor_data,
        spectra={"y": spectrum},
        source="upload",
    )
    return case, f"{conversion_note} {ACCEL_PROXY_NOTE}"


def parse_trend(path: Path, form: UploadForm, *, bearings_cfg: dict[str, Any]) -> tuple[Case, str]:
    """`timestamp,overall_rms` -- HistoryPoints for Layer 4, plus a current
    reading derived from the last row so ISO classify + trend both run.
    Layers 2/3 decline exactly as in pipeline.py today (no history baseline
    carried by a single upload). Returns (Case, conversion_note)."""
    rows = _read_rows(path)
    _require_columns(rows, ("timestamp", "overall_rms"))

    raw_values = [float(r["overall_rms"]) for r in rows]
    values, conversion_note = to_mms_rms(
        raw_values, velocity_unit=form.velocity_unit, detection_type=form.detection_type
    )
    timestamps = [datetime.fromisoformat(r["timestamp"]) for r in rows]

    order = sorted(range(len(rows)), key=lambda i: timestamps[i])
    history = [HistoryPoint(ts=timestamps[i], value=values[i]) for i in order]

    current_value = history[-1].value
    machine = machine_from_form(form, bearings_cfg, mac_prefix="UPLOAD-TREND")
    sensor_data = SensorData(
        rpm=form.rpm,
        y_velocity_mm_sec=current_value,
        y_rms_ACC_G=current_value * _ACCEL_PROXY_SCALE,
    )

    case = Case(
        name=form.machine_alias,
        machine=machine,
        sensor_data=sensor_data,
        history=history,
        source="upload",
    )
    return case, f"{conversion_note} {ACCEL_PROXY_NOTE}"
