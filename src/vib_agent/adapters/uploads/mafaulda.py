"""MAFAULDA-format CSV upload adapter (Phase 8 webapp surface): normalizes an
uploaded 8-column ABVT recording into a Case.

Reuses adapters/mafaulda.py's pure building blocks (load_mafaulda_csv,
shaft_hz_from_tach, raw_spectrum) — no new DSP here, only front-door wiring,
same pattern as adapters/uploads/mfpt.py and adapters/uploads/wind_turbine.py.

Unlike the eval path (adapters/mafaulda.py::to_case, which reads the fault
family + severity from the directory structure), a webapp upload has no labelled
path — the family is exactly what the detectors decide, and no expected outcome
is attached. Shaft rate is derived from the tachometer channel; the form RPM is a
cross-check note only.

Like the eval path this emits a RAW acceleration spectrum, not an envelope (see
adapters/mafaulda.py's docstring and config/mafaulda.json's _spectrum_note):
imbalance/misalignment are detected from discrete 1x/2x lines, which are genuine
in a raw spectrum and artifacts in an envelope.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from vib_agent.adapters.mafaulda import load_mafaulda_csv, raw_spectrum, shaft_hz_from_tach
from vib_agent.adapters.uploads.common import (
    UploadForm,
    bearing_spec_from_form,
    machine_type_from_form,
)
from vib_agent.models import BearingSpec, Case, MachineMeta, SensorData

_RPM_CROSSCHECK_TOLERANCE_PCT = 5.0


def parse_mafaulda_csv(
    path: Path,
    form: UploadForm,
    *,
    bearings_cfg: dict[str, Any],
    mafaulda_cfg: dict[str, Any],
) -> tuple[Case, str, str]:
    """Returns (Case, kind, note)."""
    signals = load_mafaulda_csv(path, mafaulda_cfg=mafaulda_cfg)
    fs = float(mafaulda_cfg["sample_rate_hz"])
    shaft_hz = shaft_hz_from_tach(
        signals["tachometer"], fs, search_band_hz=tuple(mafaulda_cfg["tach_search_band_hz"])
    )

    axis_map: dict[str, str] = mafaulda_cfg["axis_map"]
    region = tuple(mafaulda_cfg["spectrum_region_hz"])
    spectra = {
        axis: raw_spectrum(signals[col], fs, region_hz=region) for col, axis in axis_map.items()
    }
    rms_by_axis = {
        axis: float(np.sqrt(np.mean(np.square(signals[col])))) for col, axis in axis_map.items()
    }

    # Form bearing wins if given; else the known ABVT rig bearing (these files
    # come from one rig with published, verified geometry — not fabricated).
    bearing = bearing_spec_from_form(form, bearings_cfg)
    if bearing is None:
        entry = bearings_cfg["bearings"][mafaulda_cfg["bearing_key"]]
        bearing = BearingSpec(
            **{k: v for k, v in entry.items() if not k.startswith("_")},
            model=mafaulda_cfg["bearing_key"],
        )

    machine = MachineMeta(
        mac=f"MAFAULDA-{form.machine_alias}",
        name=form.machine_alias,
        active=True,
        type=machine_type_from_form(form, default="motor"),
        iso_group=form.iso_group,
        iso_support=form.iso_support,
        bearing=bearing,
        axial_axis=mafaulda_cfg["axial_axis"],
        coupled=True,
    )
    sensor_data = SensorData(
        rpm=shaft_hz * 60.0,
        x_rms_ACC_G=rms_by_axis.get("x"),
        y_rms_ACC_G=rms_by_axis.get("y"),
        z_rms_ACC_G=rms_by_axis.get("z"),
    )

    file_rpm = shaft_hz * 60.0
    note = ""
    if form.rpm > 0:
        delta_pct = abs(file_rpm - form.rpm) / form.rpm * 100.0
        if delta_pct > _RPM_CROSSCHECK_TOLERANCE_PCT:
            note = (
                f"Tachometer-derived shaft rate ({file_rpm:.1f} RPM) used for analysis -- "
                f"differs from the form-entered RPM ({form.rpm:.1f}) by {delta_pct:.1f}%."
            )

    case = Case(
        name=path.stem,
        machine=machine,
        sensor_data=sensor_data,
        spectra=spectra,
        source="mafaulda",
        validation_scope=list(mafaulda_cfg["validation_scope"]),
    )
    return case, "mafaulda_csv", note
