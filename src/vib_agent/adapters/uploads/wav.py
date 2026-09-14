"""WAV upload adapter (Phase 6) -- scipy.io.wavfile.

Frequency content is scale-invariant (bearing fault frequencies show up
regardless of amplitude units), so a WAV recording can always attempt RCA.
Amplitude-dependent severity/ISO-zone claims cannot: they require a
sensitivity/scale factor (form field) that converts the raw samples to
acceleration-g. Without one, the report states plainly that severity is
unavailable rather than guessing at a scale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import wavfile

from vib_agent.adapters.cwru import envelope_spectrum
from vib_agent.adapters.uploads.common import UploadForm, machine_from_form
from vib_agent.models import Case, SensorData


def _read_wav(path: Path) -> tuple[np.ndarray, float]:
    fs, raw = wavfile.read(str(path))
    raw = np.asarray(raw)
    signal = raw[:, 0] if raw.ndim > 1 else raw
    signal = signal.astype(float)
    if np.issubdtype(raw.dtype, np.integer):
        signal = signal / float(np.iinfo(raw.dtype).max)
    return signal, float(fs)


def parse_wav(path: Path, form: UploadForm, *, bearings_cfg: dict[str, Any]) -> tuple[Case, str, str]:
    """Returns (Case, kind, note)."""
    signal, fs = _read_wav(path)
    if signal.size == 0:
        raise ValueError("this file contains no audio samples")

    machine = machine_from_form(form, bearings_cfg, mac_prefix="UPLOAD-WAV")

    if form.wav_sensitivity:
        scaled = signal * form.wav_sensitivity
        rms_g = float(np.sqrt(np.mean(np.square(scaled))))
        spectrum = envelope_spectrum(scaled, fs)
        sensor_data = SensorData(rpm=form.rpm, y_rms_ACC_G=rms_g)
        note = f"Amplitude scaled by sensitivity factor {form.wav_sensitivity} to acceleration-g."
        case = Case(
            name=form.machine_alias,
            machine=machine,
            sensor_data=sensor_data,
            spectra={"y": spectrum},
            source="upload",
        )
        return case, "wav", note

    # Unscaled: normalized-signal RMS is used ONLY as a signal-present proxy
    # for the quality gate's machine_running check (which needs a nonzero
    # *_rms_ACC_G to know the recording isn't silence) -- it is not a real
    # g reading, and severity/zone are excluded from validation_scope so
    # nothing downstream treats it as one.
    spectrum = envelope_spectrum(signal, fs)
    signal_present_proxy = float(np.sqrt(np.mean(np.square(signal))))
    sensor_data = SensorData(rpm=form.rpm, y_rms_ACC_G=signal_present_proxy)
    note = (
        "No amplitude sensitivity/scale provided — amplitude is in arbitrary, normalized "
        "waveform units. Severity and ISO zone are NOT computed; fault-frequency "
        "identification only."
    )
    case = Case(
        name=form.machine_alias,
        machine=machine,
        sensor_data=sensor_data,
        spectra={"y": spectrum},
        source="upload",
        validation_scope=["rca"],
    )
    return case, "wav", note
