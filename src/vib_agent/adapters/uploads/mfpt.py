"""MFPT-format .mat upload adapter (Phase 7 webapp surface): normalizes a
customer-uploaded MFPT-shaped 'bearing' struct .mat file into a Case.

Reuses adapters/mfpt.py's pure building blocks (load_mfpt_mat,
equivalent_bearing_from_frequencies) and adapters/cwru.py's envelope_spectrum
-- no new DSP or geometry logic here, only front-door wiring, same pattern
as every other file in this package.

Unlike adapters/mfpt.py::to_case() (keyed to the 23 known MFPT benchmark
filenames, eval-only), a webapp upload has no known filename to look up --
geometry is derived from whatever the file itself carries:
  - embedded ball/cage/outer/inner orders present (MFPT's own real-world-
    file convention) -> equivalent_bearing_from_frequencies(outer, inner),
    same derivation as the eval real-world path; faults never fabricated.
  - no embedded orders (MFPT's own rig-file convention) -> bearing from the
    upload form if given, else config/mfpt.json's MFPT_NICE lab-rig default
    (the convention this exact struct shape implies).

File-embedded shaft rate is authoritative for the analysis (own
higher-fidelity source, same as adapters/mfpt.py::to_case()); the
form-entered RPM is recorded as a cross-check note only when it disagrees.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from vib_agent.adapters.cwru import envelope_spectrum
from vib_agent.adapters.mfpt import equivalent_bearing_from_frequencies, load_mfpt_mat
from vib_agent.adapters.uploads.common import (
    UploadForm,
    bearing_spec_from_form,
    machine_type_from_form,
)
from vib_agent.models import BearingSpec, Case, CaseExpected, MachineMeta, SensorData

_RPM_CROSSCHECK_TOLERANCE_PCT = 5.0


def parse_mfpt_mat(
    path: Path,
    form: UploadForm,
    *,
    bearings_cfg: dict[str, Any],
    mfpt_cfg: dict[str, Any],
) -> tuple[Case, str, str]:
    """Returns (Case, kind, note)."""
    data = load_mfpt_mat(path)
    envelope = envelope_spectrum(
        data["gs"],
        data["sr"],
        band_hz=tuple(mfpt_cfg["envelope_band_hz"]),
        region_hz=tuple(mfpt_cfg["envelope_region_hz"]),
    )
    rms_g = float(np.sqrt(np.mean(np.square(data["gs"]))))

    expected: CaseExpected | None = None
    if "outer" in data and "inner" in data:
        bearing = equivalent_bearing_from_frequencies(data["outer"], data["inner"])
        expected = CaseExpected(
            faults=[],
            fault_type="real_world",
            embedded_ball_order=data.get("ball"),
            embedded_cage_order=data.get("cage"),
            embedded_outer_order=data.get("outer"),
            embedded_inner_order=data.get("inner"),
        )
    else:
        bearing = bearing_spec_from_form(form, bearings_cfg)
        if bearing is None:
            default = bearings_cfg["bearings"][mfpt_cfg["bearing_key"]]
            bearing = BearingSpec(**{k: v for k, v in default.items() if not k.startswith("_")})

    machine = MachineMeta(
        mac=f"MFPT-{form.machine_alias}",
        name=form.machine_alias,
        active=True,
        type=machine_type_from_form(form, default="motor"),
        iso_group=form.iso_group,
        iso_support=form.iso_support,
        bearing=bearing,
        axial_axis="x",
    )

    file_rpm = data["rate"] * 60.0
    note = ""
    if form.rpm > 0:
        delta_pct = abs(file_rpm - form.rpm) / form.rpm * 100.0
        if delta_pct > _RPM_CROSSCHECK_TOLERANCE_PCT:
            note = (
                f"File-embedded shaft rate ({file_rpm:.1f} RPM) used for analysis -- differs "
                f"from the form-entered RPM ({form.rpm:.1f}) by {delta_pct:.1f}%."
            )

    case = Case(
        name=path.stem,
        machine=machine,
        sensor_data=SensorData(rpm=file_rpm, y_rms_ACC_G=rms_g),
        spectra={"y": envelope},
        source="mfpt",
        validation_scope=["rca"],
        expected=expected,
    )
    return case, "mfpt_mat", note
