"""Unit conversion to mm/s RMS -- applied by every upload adapter before
Layer 1. Wrong units are the #1 severity-misclassification vector (every
ISO 20816 zone boundary is stated in mm/s RMS); this is the single place
the conversion happens, so it can't silently drift per-adapter, and the
conversion actually applied is always handed back as a plain-English note
for the report (never assumed silently).
"""

from __future__ import annotations

import math

from vib_agent.adapters.uploads.common import DetectionType, VelocityUnit

_MM_PER_IN = 25.4
_SQRT2 = math.sqrt(2.0)

_UNIT_LABELS: dict[VelocityUnit, str] = {"mm_s": "mm/s", "in_s": "in/s"}
_DETECTION_LABELS: dict[DetectionType, str] = {
    "rms": "RMS",
    "peak": "peak",
    "peak_to_peak": "peak-to-peak",
}


def to_mms_rms(
    values: list[float],
    *,
    velocity_unit: VelocityUnit = "mm_s",
    detection_type: DetectionType = "rms",
) -> tuple[list[float], str]:
    """Convert a sequence of velocity readings to mm/s RMS.

    Returns (converted_values, conversion_note). The note states exactly
    what was applied -- including when nothing was, so the report never
    leaves units ambiguous.
    """
    factor = 1.0
    symbols: list[str] = []

    if velocity_unit == "in_s":
        factor *= _MM_PER_IN
        symbols.append("×25.4")

    if detection_type == "peak":
        factor /= _SQRT2
        symbols.append("÷√2")
    elif detection_type == "peak_to_peak":
        factor /= 2 * _SQRT2
        symbols.append("÷2√2")

    converted = [v * factor for v in values]

    if not symbols:
        return converted, "Input already in mm/s RMS — no conversion applied."

    note = (
        f"Input velocity in {_UNIT_LABELS[velocity_unit]} {_DETECTION_LABELS[detection_type]}, "
        f"converted to mm/s RMS ({', '.join(symbols)})."
    )
    if detection_type != "rms":
        note += " Peak/RMS conversion assumes a sinusoidal signal."
    return converted, note
