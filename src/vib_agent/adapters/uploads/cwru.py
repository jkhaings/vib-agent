"""CWRU-format .mat upload adapter (product surface).

Normalizes an uploaded 12 kHz Drive-End .mat into a Case, taking ALL machine
context from the form (rpm, bearing, ISO group/support) and the signal from the
file's `*_DE_time` channel. `fs = 12000` for this family (config/cwru.json).

Deliberately shares NO code with the eval path's filename-convention parsing
(adapters/cwru.py::_parse_filename / filename_to_expected / to_case), which
derives ground-truth `expected` blocks from the CWRU starter-set naming scheme —
an EVAL concern. The webapp discards the user's filename and stores the upload as
`upload.mat`, so any loader that demands a `Normal_N / OR0##@6_N / …` name rejects
every real upload (the bug this adapter fixes — same class as the Phase 7B
wind-turbine fix). This path imposes no filename requirements of any kind.

Same front-door pattern as adapters/uploads/mfpt.py and
adapters/uploads/wind_turbine.py: reuse the pure DSP building blocks
(load_cwru_signal, envelope_spectrum, raw_spectrum), add only wiring here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from vib_agent.adapters.cwru import envelope_spectrum, load_cwru_signal, raw_spectrum
from vib_agent.adapters.uploads.common import (
    UploadForm,
    bearing_spec_from_form,
    machine_type_from_form,
)
from vib_agent.models import BearingSpec, Case, MachineMeta, SensorData


def parse_cwru_mat(
    path: Path,
    form: UploadForm,
    *,
    bearings_cfg: dict[str, Any],
    cwru_cfg: dict[str, Any],
) -> tuple[Case, str, str]:
    """Returns (Case, kind, conversion_note). Raises ValueError with user-facing
    copy on unreadable content (untrusted upload — never a raw traceback)."""
    try:
        signal, fs = load_cwru_signal(path, cwru_cfg)
    except ValueError:
        raise  # already user-facing (see load_cwru_signal)
    except Exception as exc:  # noqa: BLE001 -- untrusted input, keep the message user-safe
        raise ValueError("could not read a valid vibration signal from this .mat file") from exc

    if signal.size < 256:
        raise ValueError("this recording is too short to analyze")

    # Machine context comes from the form. Bearing: the form model wins; otherwise
    # the CWRU-family default (a 6205-class drive-end bearing) so RCA can still run.
    bearing = bearing_spec_from_form(form, bearings_cfg)
    if bearing is None:
        default = bearings_cfg["bearings"][cwru_cfg["bearing_key"]]
        bearing = BearingSpec(**{k: v for k, v in default.items() if not k.startswith("_")})

    band = tuple(cwru_cfg["envelope_band_hz"])
    region = tuple(cwru_cfg["envelope_region_hz"])
    try:
        envelope = envelope_spectrum(signal, fs, band_hz=band, region_hz=region)
        raw = raw_spectrum(signal, fs)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("could not compute a spectrum from this recording") from exc

    rms_g = float(np.sqrt(np.mean(np.square(signal))))

    machine = MachineMeta(
        mac=f"CWRU-{form.machine_alias}",
        name=form.machine_alias,
        active=True,
        type=machine_type_from_form(form, default="motor"),
        iso_group=form.iso_group,
        iso_support=form.iso_support,
        bearing=bearing,
        axial_axis="x",
    )
    case = Case(
        name=form.machine_alias,
        machine=machine,
        sensor_data=SensorData(rpm=form.rpm, y_rms_ACC_G=rms_g),
        spectra={"y": envelope},
        # Session B (B1): envelope for the bearing detectors, raw for the 1x-family
        # detectors (which must never read the envelope's artifact 1x line).
        raw_spectra={"y": raw},
        source="cwru",
        # Acceleration-only single snapshot: only RCA is meaningful, and there is
        # no ground-truth label to attach (that's an eval-only concept).
        validation_scope=["rca"],
        expected=None,
    )
    return case, "cwru_mat", ""
