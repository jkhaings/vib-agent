"""Shared MachineMeta-building helpers for the upload adapters (Phase 6).
Every adapter in this package builds a Case the same way pipeline.run_analysis()
already understands -- no new pdm_core concepts, just new front doors.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic import ValidationError as PydanticValidationError

from vib_agent.config import load_config
from vib_agent.models import (
    AcquisitionMeta,
    BearingSpec,
    BeltSpec,
    Case,
    HistoryPoint,
    MachineMeta,
    MachineThresholds,
)

VelocityUnit = Literal["mm_s", "in_s"]
DetectionType = Literal["rms", "peak", "peak_to_peak"]
UploadMode = Literal["spectrum", "trend", "mafaulda"]
#: Session GEOM-A. "" and None both mean "not stated" and leave
#: MachineMeta.coupled at its default; only "uncoupled" reaches the bent-shaft
#: branch, and only an explicit answer sets `coupled_stated`.
Coupling = Literal["coupled", "uncoupled"]


@dataclass
class UploadForm:
    """Form fields collected on the upload page (webapp/app.py) -- the only
    input the adapters need beyond the file itself."""

    machine_alias: str
    rpm: float
    iso_group: str  # "1" | "2"
    iso_support: str  # "rigid" | "flexible"
    bearing_model: str | None = None
    # Session GEOM-1 -- the bearing itself, when the catalogue has not got it.
    # `bearing_model` names an entry in config/bearings.json; these four ARE
    # the entry, stated by the analyst. The two are mutually exclusive: the
    # webapp refuses both together at the form boundary
    # (`app.py::_geometry_422`) and `bearing_spec_from_form` refuses them again
    # here, because a Case built from two different bearings is not a Case
    # anyone can read. `contact_angle` is the one that may be left out -- 0 deg
    # is the standard assumption for a deep-groove bearing and is the same
    # assumption the three benchmark rigs are recorded under.
    bearing_n_balls: int | None = None
    bearing_ball_dia_mm: float | None = None
    bearing_pitch_dia_mm: float | None = None
    bearing_contact_angle_deg: float | None = None
    velocity_unit: VelocityUnit = "mm_s"
    detection_type: DetectionType = "rms"
    mode: UploadMode = "spectrum"  # tabular files only; ignored elsewhere
    wav_sensitivity: float | None = None  # scale factor to acceleration-g, WAV only

    # Session INTAKE-HONEST — declared acquisition settings, all optional and
    # provenance-only (recorded on Case.acquisition for the report's
    # parameters table; never used in computation). Distinct from
    # wav_sensitivity above, which actively SCALES the WAV signal:
    # sensor_sensitivity_mv_per_g is a statement about the sensor, recorded
    # verbatim, applied to nothing.
    sensor_sensitivity_mv_per_g: float | None = None
    fmax_hz: float | None = None
    spectral_lines: int | None = None
    window_type: str | None = None  # "hanning" | "flattop" | "rectangular" | "other"
    averages: int | None = None
    integration: str | None = None  # "none" | "hardware" | "software"

    # Session GEOM-A — machine geometry, all optional. Unlike the acquisition
    # block above this is NOT provenance-only: `blades`, `coupling` and the
    # three pulley dimensions feed detectors that already exist in pdm_core and
    # had no way to be reached from the product (FAULT_COVERAGE §3, the three
    # "UNREACHABLE in product" rows). The remaining fields have no detector
    # yet; they are recorded so the report can say the geometry is on file.
    measurement_location: str | None = None  # -> MachineMeta.location
    coupling: str | None = None              # "coupled" | "uncoupled" | None
    blades: int | None = None
    gear_teeth_driving: int | None = None
    gear_teeth_driven: int | None = None
    rotor_bars: int | None = None
    poles: int | None = None
    line_freq_hz: float | None = None
    drive_type: str | None = None            # "direct_on_line" | "vfd" | "soft_starter"
    # Belt drive: the three dimensions that determine the belt fundamental.
    # They arrive together or not at all (BeltSpec enforces it).
    drive_pulley_mm: float | None = None
    driven_pulley_mm: float | None = None
    pulley_center_distance_mm: float | None = None

    # Session INTAKE-2 -- what kind of machine this is, and the rated spec the
    # ISO 20816-3 group is derived from.
    #
    # `machine_type` closes PARTC F-2. Before this field every upload-lane
    # report read "Type motor" because `machine_from_form` hard-coded the
    # literal, and that is not cosmetic: `pdm_core/recommendations.py:179`
    # branches on `machine.type`. There is deliberately NO default here -- a
    # default is what hid the defect for a whole product lifetime, so a missing
    # type is refused at the form boundary (`app.py::_machine_422`) and again in
    # `machine_from_form` below.
    #
    # `rated_kw` and `mounting` are the ISO group/support inputs. They are
    # recorded on nothing -- `MachineMeta` has no kW field and `models.py` is
    # not this session's to open -- they exist to be read by
    # `iso_from_rating()`, whose output IS stored (`iso_group`/`iso_support`).
    machine_type: str | None = None
    rated_kw: float | None = None
    driven_rpm: float | None = None
    mounting: str | None = None
    # Session LIMITS-1c -- the machine's own severity limits. Unlike `rated_kw`
    # above these ARE recorded: `thresholds_from_form` turns them into the
    # `MachineThresholds` that `MachineMeta.thresholds` has carried since
    # LIMITS-1a, which is the slot `pdm_core/iso_classify.py`'s tier 1 reads.
    # All three or none -- `app.py::_thresholds_422` refuses a partial trio at
    # the form boundary, before anything reaches this sandbox.
    limit_ab: float | None = None
    limit_bc: float | None = None
    limit_cd: float | None = None


#: The machine kinds the intake offers. A dropdown, never free text: the value
#: reaches `pdm_core/recommendations.py:179`, which asks whether this machine is
#: motor-driven, and a typo there silently changes the advice an analyst signs.
#: "other" is the honest escape hatch -- it is not motor-driven as far as the
#: recommendation rules are concerned, which is the conservative direction.
MACHINE_TYPES: tuple[str, ...] = (
    "motor", "pump", "fan", "compressor", "gearbox", "blower", "other",
)

#: Default measurement-location labels per machine kind (Session INTAKE-2).
#: A route analyst names points the same way on every machine of a kind, so the
#: form prefills rather than asking. A typed label always wins.
DEFAULT_LOCATION_LABELS: dict[str, tuple[str, ...]] = {
    "motor": ("Motor DE", "Motor NDE"),
    "pump": ("Pump DE", "Pump NDE"),
    "compressor": ("Compressor DE", "Compressor NDE"),
    "fan": ("Fan DE", "Fan NDE"),
    "blower": ("Blower DE", "Blower NDE"),
    "gearbox": ("Gearbox input", "Gearbox output"),
    "other": ("Point 1", "Point 2"),
}

# VERIFY -- ISO 20816-3 group boundaries, in kW. READ FROM CONFIG, not declared.
#
# Session INTAKEFIX-1 closed the second half of INTAKE-2's F-2: these are
# tunables and "all tunable constants live in config/*.json" (CLAUDE.md), so
# they now come from `config/iso20816_3.json` and this module reads whatever the
# config contains. They went to a file of their own rather than into
# `config/iso_zones.json`, which INTAKE-2's finding suggested: that file is the
# zone TABLE (the mm/s boundaries a severity is judged against) and these are
# the rule for choosing a ROW of it. Two questions, two files, and
# `iso_zones.json` keeps a shape every reader already knows.
#
# THE FIRST HALF OF F-2 IS STILL OPEN AND THE MARKERS STAY. `references/INDEX.md`
# and `references/GAPS.md` were grepped again this session -- still zero hits for
# "20816" -- so these remain assumed constants with `"source": "unverified - no
# indexed reference"` in the config beside them. **The values were not touched by
# the move.** Indexing the standard is the operator's errand; when it is done,
# the config's `source` fields take an INDEX number and these markers come off.
#
# Read at import, which is cheap and cached (`load_config` is `lru_cache`d) and
# is what makes a missing or malformed config a loud failure at startup rather
# than a silent default in the middle of classifying a machine.
_ISO_20816_3 = load_config("iso20816_3")
ISO_GROUP_2_MIN_KW = float(_ISO_20816_3["groups"]["group_2"]["min_kw"])
ISO_GROUP_1_MIN_KW = float(_ISO_20816_3["groups"]["group_1"]["min_kw"])

#: What an analyst gets when they say nothing. Group 2 rigid is the smaller,
#: TIGHTER zone row (`config/iso_zones.json`: 2_rigid A/B boundary 1.4 mm/s vs
#: 1_flexible's 3.5), so assuming it cannot silently upgrade a machine into a
#: better zone than it earned. The `assumed` flag rides the wire so the report
#: can print it; it is never hidden.
ISO_DEFAULT_GROUP = "2"
ISO_DEFAULT_SUPPORT = "rigid"

#: Below this the standard does not apply at all. ISO 20816-3's scope starts at
#: 15 kW, so a 7.5 kW machine is classified against a table it is not in. We
#: still classify it (Group 2 is the closest row and refusing would leave the
#: analyst with nothing) and say so, rather than printing a zone that looks as
#: authoritative as the others.
ISO_BELOW_SCOPE_NOTE = (
    "rated power is below ISO 20816-3's 15 kW scope floor; "
    "the Group 2 zone boundaries are the closest available and are applied as a guide"
)


#: How an ISO group or support class was arrived at. Three answers, never
#: collapsed into a bool, for the same reason `coupled_stated`, `history_source`
#: and `readings.zone_source` all exist in this tree: a report that says
#: "Group 2" must be able to say WHY, and "because nobody told us" is a
#: different sentence from "because the analyst rated it at 90 kW".
IsoSource = Literal["rated", "stated", "assumed"]


@dataclass(frozen=True)
class IsoClass:
    """The ISO 20816-3 row this machine is classified against, with provenance.

    `group`/`support` are what `config/iso_zones.json` is keyed by
    (`f"{group}_{support}"`). `note` carries the below-scope caveat when the
    rated power is under the standard's floor. `assumed` is the one-line answer
    the report prints "(assumed)" from -- true when NEITHER a rating nor a
    stated answer was given for that half.
    """

    group: str
    support: str
    group_source: IsoSource
    support_source: IsoSource
    note: str | None = None

    @property
    def assumed(self) -> bool:
        return self.group_source == "assumed" or self.support_source == "assumed"

    @property
    def zone_key(self) -> str:
        return f"{self.group}_{self.support}"


def group_from_rated_kw(rated_kw: float) -> tuple[str, str | None]:
    """The ISO 20816-3 group for a rated power, and the caveat if there is one.

    Boundaries are ISO_GROUP_*_MIN_KW above -- read from
    `config/iso20816_3.json`, both still marked VERIFY because the standard is
    not in `references/INDEX.md` yet.
    """
    if rated_kw > ISO_GROUP_1_MIN_KW:
        return "1", None
    if rated_kw < ISO_GROUP_2_MIN_KW:
        return "2", ISO_BELOW_SCOPE_NOTE
    return "2", None


def resolve_iso_class(
    *,
    rated_kw: float | None = None,
    mounting: str | None = None,
    stated_group: str | None = None,
    stated_support: str | None = None,
) -> IsoClass:
    """Settle which ISO 20816-3 row applies, and record how (Session INTAKE-2).

    Precedence, per half, and the same precedence GEOM-1 F-4 established for a
    bearing: a DERIVATION from the rated spec wins, then what the analyst
    stated directly, then the default.

    A rating winning over the select is not the select being ignored -- the
    browser recomputes the select FROM the rating as the analyst types, so by
    submit time the two agree. This ordering is what makes that safe to rely on
    and is why a disagreement is not an error: an older client, or a post built
    by hand, simply has its rating honoured.

    With neither, it is Group 2 rigid and `assumed` is True. Group 2 rigid is
    the TIGHTEST row in `config/iso_zones.json` (A/B at 1.4 mm/s against
    1_flexible's 3.5), so assuming cannot promote a machine into a kinder zone
    than it earned.
    """
    note: str | None = None
    if rated_kw is not None:
        group, note = group_from_rated_kw(rated_kw)
        group_source: IsoSource = "rated"
    elif stated_group in ("1", "2"):
        group, group_source = stated_group, "stated"
    else:
        group, group_source = ISO_DEFAULT_GROUP, "assumed"

    if mounting in ("rigid", "flexible"):
        support, support_source = mounting, "rated"
    elif stated_support in ("rigid", "flexible"):
        support, support_source = stated_support, "stated"
    else:
        support, support_source = ISO_DEFAULT_SUPPORT, "assumed"

    return IsoClass(
        group=group,
        support=support,
        group_source=group_source,
        support_source=support_source,
        note=note,
    )


def acquisition_from_form(form: UploadForm) -> AcquisitionMeta | None:
    """The declared acquisition settings as a Case-attachable model, or None
    when nothing was declared — an untouched form leaves Case.acquisition
    exactly as absent as it was before this session existed."""
    meta = AcquisitionMeta(
        sensor_sensitivity_mv_per_g=form.sensor_sensitivity_mv_per_g,
        fmax_hz=form.fmax_hz,
        spectral_lines=form.spectral_lines,
        window_type=form.window_type or None,
        averages=form.averages,
        integration=form.integration or None,
    )
    if all(v is None for v in meta.model_dump().values()):
        return None
    return meta


# ─────────────────────────────────────────────────────────────────────────
# Session GEOM-1 — bearing geometry entered by hand
#
# STRANGER B5, the blocking row UX-5 could not close: "Nearly every real
# machine on my route will be 'Not listed', which silently turns the headline
# feature off." The catalogue holds five route bearings. A route holds
# thousands, and a bearing we do not hold is a BPFO/BPFI/BSF/FTF screen that
# does not run — on the product's headline capability.
#
# `pdm_core` needed nothing: `BearingSpec` has always taken the four numbers
# and `bearing_rca` has always computed from them. The path stopped three
# layers short of the analyst, and those layers are these.
# ─────────────────────────────────────────────────────────────────────────

#: The three that must arrive together — they are what the frequency formulas
#: read. Contact angle is deliberately not among them: 0 deg is the standard
#: deep-groove assumption and is what `BearingSpec` already defaults to, so
#: demanding it would refuse a complete answer.
BEARING_GEOMETRY_REQUIRED = (
    "bearing_n_balls",
    "bearing_ball_dia_mm",
    "bearing_pitch_dia_mm",
)
BEARING_GEOMETRY_FIELDS = BEARING_GEOMETRY_REQUIRED + ("bearing_contact_angle_deg",)


def bearing_geometry_stated(form: UploadForm) -> bool:
    """Did the analyst put ANY bearing geometry on this form?

    Any single field, not all of them: a partial answer is a mistake to be
    named, never a reason to fall back to the catalogue and analyse a machine
    against a bearing nobody chose.
    """
    return any(getattr(form, name) is not None for name in BEARING_GEOMETRY_FIELDS)


def _geometry_number(value: float) -> str:
    """One number, as both the report and the page write it: `46.0` -> `46`,
    `7.940` -> `7.94`.

    Fixed at four decimals and then trimmed, rather than `%g` or `repr`,
    because `static/app.js::geomNum` has to produce the SAME STRING from the
    same input and neither of those has a JavaScript twin. Four decimals is
    past any bearing dimension in millimetres and any contact angle in degrees;
    `tests/test_geom1_geometry.py` diffs the two implementations.
    """
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def bearing_geometry_label(
    n_balls: int, ball_dia_mm: float, pitch_dia_mm: float, contact_angle_deg: float
) -> str:
    """What `BearingSpec.model` carries for a hand-entered bearing.

    NOT a designation, and that is the whole point. `report/` prints
    `bearing.model` in four places (the Machine Details row, the drafted
    report's duty strip, the fault-frequency map header, and the markdown
    survey table) and this session may not open any of them — so this ONE
    string is the report's only chance to say where the numbers came from.
    A catalogue name here would claim we hold geometry we do not; a bare
    "(geometry supplied)" would leave an analyst who typed 3.904 for 39.04 with
    a wrong BPFO and nothing on the page to check it against. So it names the
    provenance AND the four numbers, in the order the form asks for them.

    `model=None` is deliberately not used: `default_survey.md.j2` renders the
    row through Jinja's `default()` filter, which only fires for an UNDEFINED
    value — a `None` prints the word "None" into the report's bearing row.
    """
    elements = "1 element" if n_balls == 1 else f"{n_balls} elements"
    return (
        f"geometry as entered — {elements}, "
        f"element {_geometry_number(ball_dia_mm)} mm, "
        f"pitch {_geometry_number(pitch_dia_mm)} mm, "
        f"contact {_geometry_number(contact_angle_deg)}\u00b0"
    )


def bearing_spec_from_form(form: UploadForm, bearings_cfg: dict[str, Any]) -> BearingSpec | None:
    """The bearing this run analyses against: the analyst's own geometry, a
    catalogue entry, or nothing.

    Every raise here still happens INSIDE the parse sandbox, where a ValueError
    becomes a PARSE_ERROR — a claim about the analyst's FILE. That is why every
    condition below is also checked by `webapp/app.py::_geometry_422` before a
    job exists, in the analyst's words. These are the backstop, in the adapters'
    words, for the CLI and for anything that reaches an adapter without passing
    the form boundary.
    """
    if bearing_geometry_stated(form):
        if form.bearing_model:
            raise ValueError(
                "a bearing model and hand-entered bearing geometry cannot both be given; "
                "the analysis would have two different bearings to choose between"
            )
        missing = [n for n in BEARING_GEOMETRY_REQUIRED if getattr(form, n) is None]
        if missing:
            raise ValueError(
                "bearing geometry needs all of bearing_n_balls, bearing_ball_dia_mm and "
                f"bearing_pitch_dia_mm; missing {', '.join(missing)}"
            )
        angle = form.bearing_contact_angle_deg
        # BearingSpec validates the four numbers itself (positive, ball < pitch,
        # angle 0-90) and its messages are the ones that reach a CLI user.
        return BearingSpec(
            n_balls=form.bearing_n_balls,
            ball_dia_mm=form.bearing_ball_dia_mm,
            pitch_dia_mm=form.bearing_pitch_dia_mm,
            contact_angle_deg=0.0 if angle is None else angle,
            model=bearing_geometry_label(
                form.bearing_n_balls,
                form.bearing_ball_dia_mm,
                form.bearing_pitch_dia_mm,
                0.0 if angle is None else angle,
            ),
        )
    if not form.bearing_model:
        return None
    entry = bearings_cfg["bearings"].get(form.bearing_model)
    if entry is None:
        raise ValueError(
            f"unknown bearing_model {form.bearing_model!r} (known: {sorted(bearings_cfg['bearings'])})"
        )
    return BearingSpec(
        n_balls=entry["n_balls"],
        ball_dia_mm=entry["ball_dia_mm"],
        pitch_dia_mm=entry["pitch_dia_mm"],
        contact_angle_deg=entry.get("contact_angle_deg", 0.0),
        model=form.bearing_model,
    )


#: What a machine whose kind nobody stated is called. NOT one of
#: `MACHINE_TYPES` -- it is the absence of an answer, and the dropdown never
#: offers it.
#:
#: Session INTAKE-2 / PARTC F-2. The point of this constant is that it is TRUE.
#: The literal it replaces was `"motor"`, which made every upload-lane report
#: assert "Type motor" about a compressor, and `pdm_core/recommendations.py:179`
#: read that assertion back as "this machine is motor-driven". "unknown" is
#: already this repo's word for the same gap (`models.py:386`, `Reading`), it
#: reads correctly in a report, and it is the CONSERVATIVE value at
#: recommendations.py:179 -- an unknown machine is not treated as motor-driven.
#:
#: The product can never reach it: `app.py::_machine_422` refuses a blank type
#: at the form boundary and the select has no blank option. It is what a
#: hand-built `UploadForm` gets -- a test fixture, or a caller that never passed
#: through the form.
MACHINE_TYPE_UNKNOWN = "unknown"


def machine_type_from_form(form: UploadForm, *, default: str | None = None) -> str:
    """The machine kind as stated, never guessed (Session INTAKE-2, PARTC F-2).

    This is the twin of `app.py::_machine_422`, and it exists for the reason
    `app.py:361-366` sets out for the bearing checks: a raise inside the parse
    sandbox reaches the analyst as a claim about their FILE, so the webapp
    refuses at the form boundary first and this is the backstop for anything
    arriving without passing it.

    `default` is the rig default for the benchmark lanes (GEOM-1 F-4's
    precedence: the form's answer wins; otherwise the family default). With no
    default and nothing stated the answer is `MACHINE_TYPE_UNKNOWN` -- see the
    constant for why that is a fix and `"motor"` was a bug.

    An unrecognised type IS refused, because it can only come from a
    hand-crafted post: the control is a `<select>`.
    """
    stated = (form.machine_type or "").strip().lower()
    if stated:
        if stated not in MACHINE_TYPES:
            raise ValueError(
                f"{stated!r} is not a machine type this analysis knows; "
                f"choose one of: {', '.join(MACHINE_TYPES)}"
            )
        return stated
    return default if default is not None else MACHINE_TYPE_UNKNOWN


def thresholds_from_form(form: UploadForm) -> MachineThresholds | None:
    """The analyst's own severity limits, or None for the ISO path.

    Session LIMITS-1c, and the whole of item 3's wire. LIMITS-1a's ruling
    (SESSION_LIMITS1.md §3) is what this obeys: there is no second, differently
    named spelling of these three numbers. They arrive on the form, they become
    the `MachineThresholds` that already existed, and `pdm_core` decides what
    that means -- in particular this function never sets `threshold_source`,
    which `iso_classify.resolve_thresholds` stamps `"custom"` on its own.

    None when any is missing, which on the product path means all three are:
    `app.py::_thresholds_422` has already refused a partial trio with a sentence
    an analyst can act on. The `all(...)` here is the backstop for the lanes
    that do not come through that form at all, and it fails CLOSED -- to ISO,
    never to a half-stated limit.

    `source_note` is deliberately not set. The form offers no field for it, so
    `resolve_thresholds` supplies its own "Machine-specific override".
    """
    values = (form.limit_ab, form.limit_bc, form.limit_cd)
    if not all(v is not None for v in values):
        return None
    return MachineThresholds(ab=values[0], bc=values[1], cd=values[2])


def machine_from_form(
    form: UploadForm,
    bearings_cfg: dict[str, Any],
    *,
    mac_prefix: str,
    type_default: str | None = None,
) -> MachineMeta:
    return MachineMeta(
        mac=f"{mac_prefix}-{form.machine_alias}",
        name=form.machine_alias,
        active=True,
        type=machine_type_from_form(form, default=type_default),
        iso_group=form.iso_group,
        iso_support=form.iso_support,
        bearing=bearing_spec_from_form(form, bearings_cfg),
        rpm_nominal=form.rpm,
        # Session LIMITS-1c, item 3 -- the entire wire, one keyword. The slot
        # has existed since LIMITS-1a; nothing in the product could fill it.
        thresholds=thresholds_from_form(form),
    )


# ─────────────────────────────────────────────────────────────────────────
# Session GEOM-A — machine geometry
#
# Geometry is applied ONCE, at the single dispatch point
# (`adapters/uploads/__init__.py::parse_upload`), exactly the way Session
# INTAKE-HONEST attaches `Case.acquisition`. It deliberately does NOT live in
# `machine_from_form` below: only four of the nine upload lanes call that
# helper (tabular, WAV, UFF, recipe) — the .mat lanes (CWRU, MFPT,
# wind-turbine) and MAFAULDA build their own MachineMeta, and geometry the
# analyst typed must reach the analysis whatever read the file.
# ─────────────────────────────────────────────────────────────────────────

#: Belt frequency is derived, not measured, so the number is only as good as
#: the theoretical wrap length below: real belts come in standard lengths and a
#: worn drive runs at a slightly different centre distance than the drawing
#: says. The report prints this alongside the derived value.
BELT_DERIVATION_CAVEAT = (
    "Belt length is the theoretical wrap length for the stated pulley diameters and centre "
    "distance, not a measured belt; a fitted belt is a standard size and the running centre "
    "distance moves with tension and wear."
)


def belt_length_mm(drive_mm: float, driven_mm: float, center_mm: float) -> float:
    """Open-belt wrap length, the standard two-pulley formula:

        L = 2C + (pi/2)(D1 + D2) + (D2 - D1)^2 / (4C)

    All arguments and the result are millimetres. Pure arithmetic on three
    supplied dimensions — no measurement, no threshold, no config."""
    return (
        2.0 * center_mm
        + (math.pi / 2.0) * (drive_mm + driven_mm)
        + (driven_mm - drive_mm) ** 2 / (4.0 * center_mm)
    )


def belt_frequency_hz(drive_mm: float, driven_mm: float, center_mm: float, rpm: float) -> float:
    """The belt fundamental in Hz — the rate at which one point on the belt
    returns to one point on the drive:

        belt speed [mm/s] = pi * D1 * N1 / 60
        belt frequency [Hz] = belt speed / L

    `rpm` is the speed of the shaft carrying the DRIVE pulley (D1) — i.e. the
    running speed the analyst entered for the machine that was measured."""
    length = belt_length_mm(drive_mm, driven_mm, center_mm)
    return (math.pi * drive_mm * rpm / 60.0) / length


def belt_spec_from_form(form: UploadForm, *, rpm: float) -> BeltSpec | None:
    """A BeltSpec derived from the form's pulley geometry, or None when the
    analyst supplied none of it. Raises ValueError (via BeltSpec) when the
    geometry is partial or physically impossible — the webapp catches that at
    the form boundary and answers 422 before a job exists."""
    dims = (form.drive_pulley_mm, form.driven_pulley_mm, form.pulley_center_distance_mm)
    if all(v is None for v in dims):
        return None
    if any(v is None for v in dims) or rpm <= 0:
        # BeltSpec's own validator states the all-or-nothing rule; a missing
        # running speed is stated here because BeltSpec never sees the rpm.
        if rpm <= 0 and not any(v is None for v in dims):
            raise ValueError("belt frequency needs a running speed (RPM) to derive from")
        raise ValueError(
            "belt geometry needs ALL of drive_pulley_mm, driven_pulley_mm and "
            "pulley_center_distance_mm, or none of them"
        )
    drive_mm, driven_mm, center_mm = dims
    return BeltSpec(
        freq_hz=belt_frequency_hz(drive_mm, driven_mm, center_mm, rpm),
        drive_pulley_mm=drive_mm,
        driven_pulley_mm=driven_mm,
        center_distance_mm=center_mm,
        belt_length_mm=belt_length_mm(drive_mm, driven_mm, center_mm),
    )


def _blank_to_none(value: str | None) -> str | None:
    """An untouched <select> posts "", which means "not stated" — never a value."""
    return value or None


def apply_machine_geometry(case: Case, form: UploadForm) -> Case:
    """Attach the form's declared machine geometry to `case.machine`.

    The belt fundamental is derived from the pulley dimensions and the SHAFT
    SPEED THE ANALYSIS WILL USE (`case.sensor_data.rpm`), not blindly from the
    form's RPM: the MFPT, wind-turbine and MAFAULDA lanes take their shaft rate
    from the file and say so in the report, and a belt frequency derived from a
    speed the detectors do not use would be a number matched against the wrong
    spectrum.

    Re-validated through MachineMeta rather than `model_copy(update=...)`, which
    skips validators: a geometry that reached here unchecked must still fail
    loudly rather than be recorded as fact.
    """
    geometry: dict[str, Any] = {}

    location = _blank_to_none(form.measurement_location)
    if location is not None:
        geometry["location"] = location

    coupling = _blank_to_none(form.coupling)
    if coupling is not None:
        if coupling not in ("coupled", "uncoupled"):
            raise ValueError("coupling must be 'coupled' or 'uncoupled'")
        geometry["coupled"] = coupling == "coupled"
        geometry["coupled_stated"] = True

    for field in ("blades", "gear_teeth_driving", "gear_teeth_driven", "rotor_bars",
                  "poles", "line_freq_hz"):
        value = getattr(form, field)
        if value is not None:
            geometry[field] = value

    drive_type = _blank_to_none(form.drive_type)
    if drive_type is not None:
        geometry["drive_type"] = drive_type

    rpm = 0.0
    if case.sensor_data is not None and case.sensor_data.rpm:
        rpm = float(case.sensor_data.rpm)
    elif form.rpm:
        rpm = float(form.rpm)
    belt = belt_spec_from_form(form, rpm=rpm)
    if belt is not None:
        geometry["belt"] = belt

    if not geometry:
        return case
    machine = MachineMeta.model_validate({**case.machine.model_dump(), **geometry})
    case.machine = machine
    return case


# ─────────────────────────────────────────────────────────────────────────
# Session HIST-1 — client-held trend history
#
# The analyst's browser stores one point per finished analysis and posts them
# back on the next upload for the same machine + measurement point. The
# payload is UNTRUSTED text from a form field, so it is parsed and validated
# HERE, at the form boundary, and the webapp answers 422 before a job exists
# — never inside the parse sandbox, where a ValueError becomes a PARSE_ERROR
# and files an analyst's own saved data as a claim about their file. That is
# the `_geometry_422` rule (webapp/app.py) applied to a second untrusted field.
#
# Only `ts` and `value` cross the boundary: `HistoryPoint` is what
# `pdm_core.trend.compute_trend` consumes and it carries no zone and no axis.
# The card's zone and dominant axis stay in the browser, which is where the
# badge is drawn — so nothing here needs a wider element model, and
# `models.HistoryPoint` is untouched.
# ─────────────────────────────────────────────────────────────────────────

#: A trend card holds one point per reading. Route intervals are monthly at
#: their sparsest and daily at their densest, so 400 covers a decade of
#: monthly readings or a year of daily ones — far past what a browser would
#: accumulate, and small enough that parsing the payload stays invisible.
#: In code rather than config/webapp.json, following GEOM-A's
#: `_LOCATION_MAX_CHARS` and HIST-2's eight in-code defaults: it is a payload
#: sanity bound, not a tuning knob anyone should turn per deployment.
HISTORY_MAX_POINTS = 400


class ClientHistoryPoint(BaseModel):
    """One stored reading exactly as the browser posts it back.

    Closed (`extra="forbid"`) on the `ConfirmBody` precedent: an unknown key
    means the browser and the server disagree about the card's shape, and
    saying so is more useful to the analyst than silently dropping it.
    """

    model_config = ConfigDict(extra="forbid")

    ts: datetime
    value: float

    @field_validator("value")
    @classmethod
    def _usable_scalar(cls, v: float) -> float:
        # D-5: the stored scalar is severity_rms in mm/s. A NaN, an infinity or
        # a negative would each reach compute_trend's OLS and come back out as a
        # number, so they are refused here rather than regressed against.
        if not math.isfinite(v):
            raise ValueError("value must be a finite number")
        if v < 0:
            raise ValueError("value must not be negative")
        return v


def parse_client_history(raw: str | None) -> list[HistoryPoint]:
    """The analyst's browser-held trend points, as `Case.history` wants them.

    Returns `[]` for absent, blank or empty input — an analyst who has saved
    nothing yet is not an error, and an empty list must read as "no history"
    rather than as a history of zero readings.

    Raises `ValueError` with an analyst-facing sentence for anything else; the
    webapp turns that into a 422 before a job exists.

    Points come back sorted oldest-first. `compute_trend` sorts its own copy,
    but `report/charts.py::_render_trend` plots `case.history` in list order
    under the axis label "oldest → newest" — so the order has to be true here.
    """
    if raw is None:
        return []
    text = raw.strip()
    if not text:
        return []

    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise ValueError("the saved trend history could not be read as JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("the saved trend history must be a list of readings")
    if len(payload) > HISTORY_MAX_POINTS:
        raise ValueError(
            f"the saved trend history carries {len(payload)} readings; "
            f"the limit is {HISTORY_MAX_POINTS}"
        )

    points: list[HistoryPoint] = []
    for i, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"saved trend reading {i} is not an object with a ts and a value"
            )
        try:
            parsed = ClientHistoryPoint.model_validate(item)
        except PydanticValidationError as exc:
            raise ValueError(
                f"saved trend reading {i} is not usable: {_first_pydantic_message(exc)}"
            ) from exc
        points.append(HistoryPoint(ts=parsed.ts, value=parsed.value))

    points.sort(key=lambda p: p.ts)
    return points


def _first_pydantic_message(exc: PydanticValidationError) -> str:
    """The first problem, named with its field — enough for an analyst to fix
    the card, without pasting a Pydantic traceback into a form error."""
    errors = exc.errors()
    if not errors:
        return "it does not match the expected shape"
    first = errors[0]
    field = ".".join(str(p) for p in first.get("loc", ())) or "the reading"
    return f"{field}: {first.get('msg', 'is invalid')}"
