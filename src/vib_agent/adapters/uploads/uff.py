"""UFF/UNV upload adapter (Phase 6) -- pyuff dataset 58 (Function at Nodal
DOF) only. Two sub-cases, handled differently on physical grounds:

- **Time-response records** (func_type 1): a raw waveform in whatever units
  the exporting instrument used (almost always acceleration, if it's meant
  for envelope/bearing-fault analysis). Reuses adapters/cwru.py's own
  envelope-demodulation chain unchanged -- RCA only, no velocity or ISO zone
  claimed (`validation_scope=["rca"]`), exactly like the CWRU path this
  mirrors. Unit form fields do not apply here: demodulated envelope
  amplitude isn't a velocity reading, so there's nothing to convert.
- **Frequency-domain records** (any other func_type -- FRF, PSD, or an
  already-computed velocity spectrum, which is common LMS/Test.Lab export
  content): treated exactly like the tabular spectrum path -- the unit form
  fields (velocity_unit/detection_type) apply, and the converted overall RMS
  drives full ISO classification plus RCA.

Any other dataset type, or an unreadable func_type, is a safe parse error
naming what was found rather than a silent misinterpretation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyuff

from vib_agent.adapters.cwru import envelope_spectrum
from vib_agent.adapters.uploads.common import UploadForm, machine_from_form
from vib_agent.adapters.uploads.tabular import ACCEL_PROXY_NOTE
from vib_agent.adapters.uploads.units import to_mms_rms
from vib_agent.models import Case, SensorData, Spectrum

_TIME_RESPONSE_FUNC_TYPE = 1
_ACCEL_PROXY_SCALE = 0.08  # see tabular.py for the rationale (same convention)


def _read_first_58(path: Path) -> dict[str, Any]:
    uff_file = pyuff.UFF(str(path))
    types = uff_file.get_set_types()
    idx = next((i for i, t in enumerate(types) if t == 58), None)
    if idx is None:
        raise ValueError(
            "this UFF file has no dataset-58 (Function at Nodal DOF) record to analyze — "
            "export a time-response or spectrum record"
        )
    record = uff_file.read_sets(idx)
    if not isinstance(record, dict):
        raise ValueError("this UFF record has an unexpected shape and could not be read")
    return record


def parse_uff(path: Path, form: UploadForm, *, bearings_cfg: dict[str, Any]) -> tuple[Case, str, str]:
    """Returns (Case, kind, conversion_note)."""
    record = _read_first_58(path)
    func_type = record.get("func_type")
    data = np.asarray(record["data"], dtype=float)
    x = np.asarray(record["x"], dtype=float)
    if data.size == 0 or x.size == 0:
        raise ValueError("this UFF record contains no data points")

    machine = machine_from_form(form, bearings_cfg, mac_prefix="UPLOAD-UFF")

    if func_type == _TIME_RESPONSE_FUNC_TYPE:
        dt = float(x[1] - x[0]) if len(x) > 1 else 0.0
        if dt <= 0:
            raise ValueError("UFF time-response record has no usable abscissa spacing")
        fs = 1.0 / dt
        spectrum = envelope_spectrum(data, fs)
        rms_g = float(np.sqrt(np.mean(np.square(data))))
        sensor_data = SensorData(rpm=form.rpm, y_rms_ACC_G=rms_g)
        case = Case(
            name=form.machine_alias,
            machine=machine,
            sensor_data=sensor_data,
            spectra={"y": spectrum},
            source="upload",
            validation_scope=["rca"],
        )
        return case, "uff_waveform", "Time-domain envelope amplitude — not a velocity reading, no unit conversion applied."

    # B1: a UFF frequency-domain record, converted to mm/s velocity below — a
    # 1x-family kind (not the envelope of the time-response branch above).
    spectrum = Spectrum(
        freq_hz=x.tolist(), amplitude=np.abs(data).tolist(), fmax_hz=float(x[-1]), kind="velocity"
    )
    amplitude, conversion_note = to_mms_rms(
        spectrum.amplitude, velocity_unit=form.velocity_unit, detection_type=form.detection_type
    )
    spectrum = spectrum.model_copy(update={"amplitude": amplitude})
    overall_rms_mms = float(np.sqrt(sum(v * v for v in amplitude)))

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
    return case, "uff_spectrum", f"{conversion_note} {ACCEL_PROXY_NOTE}"
