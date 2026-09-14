"""Wind-turbine-format .mat upload adapter (Phase 7B webapp surface):
normalizes an uploaded {tach, vibration} .mat recording into a Case.

Reuses adapters/wind_turbine.py's pure building blocks (load_wt_mat,
shaft_hz_from_tach, overall_rms_g) and adapters/cwru.py's envelope_spectrum —
no new DSP or geometry logic here, only front-door wiring, same pattern as
adapters/uploads/mfpt.py.

Shaft rate is DERIVED from the file's own tachometer pulse times (its own
higher-fidelity source, same precedence as the MFPT upload path); the
form-entered RPM is recorded as a cross-check note only when it disagrees.

TWO HONEST LIMITATIONS are surfaced as report notes rather than papered over:

1. NO HISTORY. A single uploaded recording carries no prior readings, so
   Layers 2 (Welford) and 4 (trend) decline exactly as they do for every
   other single-file upload — even though this dataset's whole point is the
   50-day degradation history. The webapp has no multi-file ingest, and its
   only history-bearing path (the trend CSV) is velocity-only by design
   (adapters/uploads/units.py: VelocityUnit is mm_s|in_s), so acceleration-g
   history cannot be uploaded without mislabelling g as mm/s — the exact
   severity-misclassification vector units.py exists to prevent.

2. NO BEARING GEOMETRY by default. See BLOCKED_phase7b_bearing_geometry.md:
   the SKF 32222 J2's roller count/diameter are unobtainable, so unless the
   uploader names a bearing_model on the form, MachineMeta.bearing is None
   and the bearing detectors correctly do not fire. No geometry is invented
   to make the report look fuller.

Consequence: a report drafted from this path is THIN by construction. That
thinness is a truthful reflection of what one acceleration-only snapshot of
an unknown bearing supports — not a defect to be padded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vib_agent.adapters.cwru import envelope_spectrum
from vib_agent.adapters.uploads.common import (
    UploadForm,
    bearing_spec_from_form,
    machine_type_from_form,
)
from vib_agent.adapters.wind_turbine import (
    load_wt_mat,
    overall_rms_g,
    shaft_hz_from_tach,
)
from vib_agent.models import Case, CaseExpected, MachineMeta, SensorData

_RPM_CROSSCHECK_TOLERANCE_PCT = 5.0


def parse_wind_turbine_mat(
    path: Path,
    form: UploadForm,
    *,
    bearings_cfg: dict[str, Any],
    wt_cfg: dict[str, Any],
) -> tuple[Case, str, str]:
    """Returns (Case, kind, note)."""
    data = load_wt_mat(path, wt_cfg=wt_cfg)
    shaft_hz = shaft_hz_from_tach(
        data["tach"], pulses_per_rev=int(wt_cfg["tach_pulses_per_rev"])
    )
    envelope = envelope_spectrum(
        data["vibration"],
        data["fs"],
        band_hz=tuple(wt_cfg["envelope_band_hz"]),
        region_hz=tuple(wt_cfg["envelope_region_hz"]),
    )
    rms_g = overall_rms_g(data["vibration"])

    # Form-supplied bearing wins if given; otherwise None (geometry BLOCKED —
    # never fabricated). config's bearing_key is null today, so this stays None
    # until a real SKF 32222 J2 entry is supplied.
    bearing = bearing_spec_from_form(form, bearings_cfg)
    if bearing is None and wt_cfg.get("bearing_key"):
        entry = bearings_cfg["bearings"][wt_cfg["bearing_key"]]
        from vib_agent.models import BearingSpec

        bearing = BearingSpec(**{k: v for k, v in entry.items() if not k.startswith("_")})

    machine = MachineMeta(
        mac=f"WT-{form.machine_alias}",
        name=form.machine_alias,
        active=True,
        type=machine_type_from_form(form, default="motor"),
        iso_group=form.iso_group,
        iso_support=form.iso_support,
        bearing=bearing,
        axial_axis="x",
    )

    file_rpm = shaft_hz * 60.0
    notes: list[str] = []
    if form.rpm > 0:
        delta_pct = abs(file_rpm - form.rpm) / form.rpm * 100.0
        if delta_pct > _RPM_CROSSCHECK_TOLERANCE_PCT:
            notes.append(
                f"Tachometer-derived shaft rate ({file_rpm:.1f} RPM) used for analysis -- "
                f"differs from the form-entered RPM ({form.rpm:.1f}) by {delta_pct:.1f}%."
            )
    if bearing is None:
        notes.append(
            "No bearing geometry available for this machine, so bearing-fault detectors "
            "did not run. Supply a bearing model to enable them."
        )
    notes.append(
        "Single recording: no prior readings accompany this upload, so trend and "
        "anomaly analysis are not applicable to it."
    )

    case = Case(
        name=path.stem,
        machine=machine,
        sensor_data=SensorData(rpm=file_rpm, y_rms_ACC_G=rms_g),
        spectra={"y": envelope},
        source="wind_turbine",
        # Acceleration-only, single snapshot: only RCA is meaningful here.
        # trend_relative/anomaly are in scope for the 50-file CHRONOLOGICAL
        # run (scripts/run_wind_turbine.py), not for a lone upload.
        validation_scope=["rca"],
        expected=CaseExpected(faults=[], fault_type="run_to_failure_inner_race_endpoint"),
    )
    return case, "wind_turbine_mat", " ".join(notes)
