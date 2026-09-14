"""FastAPI app factory (Phase 6): the whole product surface is upload ->
poll -> download PDF, plus a handful of static pages. Same-origin only, no
CORS, no accounts, no database -- the in-memory job registry is the only state
there is, and a job's scratch directory is deleted with the job.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
import os
import shutil
import tempfile
import traceback
from collections import deque
from functools import partial
from pathlib import Path
from typing import Any, Callable, Sequence

from contextlib import asynccontextmanager

import anthropic
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vib_agent import __version__
from vib_agent.pipeline import run_analysis
from vib_agent.adapters.uploads.common import (
    DEFAULT_LOCATION_LABELS,
    MACHINE_TYPES,
    UploadForm,
    parse_client_history,
    resolve_iso_class,
)
from vib_agent.adapters.uploads.recipe import (
    FALLBACK_EXTENSIONS,
    INFERRED_EXTENSIONS,
    ParseRecipe,
)
from vib_agent.adapters.uploads.sample import NotTextError, read_text_sample, structure_fingerprint
from vib_agent.adapters.uploads.verify import failed as failed_checks
from vib_agent.adapters.uploads.verify import verify_case
from vib_agent.agent.inference import COULD_NOT_INTERPRET, InferenceError, infer_recipe
from vib_agent.config import load_config, load_thresholds
from vib_agent.models import Case, Check
from vib_agent.webapp import assembly, locations
from vib_agent.webapp.mdpage import render_doc
from vib_agent.webapp.hardening import (
    HSTS_HEADER,
    NO_STORE_PREFIXES,
    SECURITY_HEADERS,
    IpRateLimiter,
    client_ip,
    enabled_external_services,
    hsts_enabled,
    is_status_request,
    request_is_https,
    third_parties_sentence,
    third_party_details,
)
from vib_agent.webapp.jobs import Job, JobRegistry
from vib_agent.webapp.parsing import ParseError, SampleError, parse_in_subprocess, sample_in_subprocess
from vib_agent.webapp.security import (
    InviteCodeError,
    RateLimitError,
    RateLimiter,
    parse_invite_codes,
    resolve_code_label,
    validate_upload,
)
from vib_agent.webapp.spend import SpendGuard
from vib_agent.webapp.worker import (
    mark_error,
    process_compare_job,
    process_job,
    # Session INTAKE-2. The SAME two functions that build location 1's
    # `trend_point` and `committed_fault`, reused for the other locations --
    # deliberately imported rather than re-derived, because two spellings of
    # "the trendable scalar" is how one point's card stops matching another's.
    _committed_fault,
    _trend_point,
)

_STATIC_DIR = Path(__file__).parent / "static"
_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
_OUTPUTS_DIR = Path(__file__).resolve().parents[3] / "outputs"

_log = logging.getLogger("vib_agent.webapp")
if not _log.handlers:
    # Own handler + explicit level, independent of uvicorn's own logging
    # dictConfig (which runs after this module is imported and does not
    # guarantee INFO-level output on the root logger) -- this is the one
    # log line the privacy model promises, so it must not depend on
    # whatever logging setup the process happens to inherit.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _log.addHandler(_handler)
_log.setLevel(logging.INFO)


def _log_job_outcome(job: Job) -> None:
    """The only per-job log line: timestamp (via the logging formatter),
    invite-code label, kind, size, duration, token cost, outcome. Never
    file contents or the machine alias -- neither is ever passed in.

    S7 adds two things. It is emitted AT MOST ONCE per job: a worker thread
    abandoned by the max-runtime sweep runs on to completion and would otherwise
    write a second, contradicting line for a job already accounted for. And an
    `error` outcome carries ` fail=<error_code>` -- the taxonomy category, so the
    operator can tell a bad upload from a crash of ours without a traceback in
    this line. It is `fail=` rather than `code=` because `code=` is already the
    invite-code label here, and it sits BEFORE `outcome=` because
    scripts/beta_digest.py anchors its parse on `outcome=` being last.
    """
    if job.outcome_logged:
        return
    job.outcome_logged = True
    tokens = job.token_usage or {}
    total_tokens = tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0)
    _log.info(
        "job=%s code=%s kind=%s size_bytes=%d duration_ms=%s tokens=%d%s outcome=%s",
        job.id,
        job.code_label,
        job.kind or "unknown",
        job.file_size,
        job.duration_ms if job.duration_ms is not None else "n/a",
        total_tokens,
        f" fail={job.error_code}" if job.error_code else "",
        job.state,
    )


def _log_internal_failure(job: Job, exc: BaseException) -> None:
    """WHY a job crashed on our side, with the traceback -- precisely mirroring
    worker._log_draft_failure, which exists for this reason one layer down.

    Deliberately a SECOND line rather than a field on the outcome line above:
    that line is the clean, PII-free per-job record the privacy model promises
    and it must not grow a stack trace. Our own stack frames are not request
    data, and they are the most useful thing the operator will have. Never
    raises: a logging failure must not be the reason a job fails."""
    try:
        _log.warning(
            "job=%s internal_failure exc=%s.%s\n%s",
            job.id,
            type(exc).__module__,
            type(exc).__qualname__,
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip(),
        )
    except Exception:  # noqa: BLE001 -- logging is never the reason a job fails
        pass


# Analyst-facing copy for the two failures S7 introduces. Both are
# `server_error`: the card must not send the analyst to the CSV template for
# either, because in neither case was the file the problem.
_INTERNAL_ERROR_MESSAGE = (
    "The analysis failed on our side — your file was not the problem. "
    "Please try again, or email us the job reference and we will look."
)
_TIMEOUT_MESSAGE = (
    "The analysis ran past its time limit and was stopped, so there is no report. "
    "This was on our side, not your file — please try again, or email us the job reference."
)
# Session FLIP-1. The SAME taxonomy code (`timeout`) with a different sentence,
# because the two lanes are the same category to the wire and different events to
# the person reading the card. The line above says "this was on our side", which
# is true of an analysis that hung and false of a confirm card nobody answered —
# and `worker.mark_error` takes the message as a parameter precisely so a lane
# can say what actually happened without a ninth `ERROR_TAXONOMY` row (wire law
# #5: map, do not add).
#
_ABANDONED_CONFIRM_MESSAGE = (
    "This analysis was waiting for you to confirm how your file should be read, and was "
    "cleared after its 60 minutes. Nothing was analysed and no report was produced. "
    "Please upload the file again."
)
# One string for both storage refusals: the disk guard before the upload is read,
# and the write that fails a few lines later. To the analyst this is the same
# condition arriving at a slightly different moment, and a second wording would
# imply a second problem.
_STORAGE_FULL_MESSAGE = "Server storage is temporarily full — please try again shortly."

_FUNNEL_MESSAGE = (
    "Different instrument or format? Email the file to {contact_email} and we'll add "
    "your format within days."
)

_BAD_DIRECTION_MSG = (
    "Choose a direction for each file: radial (horizontal), radial (vertical), or axial."
)


def _slot_present(f: UploadFile | None) -> bool:
    """A MEASUREMENTS slot is present iff a real file was chosen. Browsers post
    empty parts for untouched inputs; the JS deletes them, but treat an empty
    filename as absent defensively too."""
    return f is not None and bool((f.filename or "").strip())


#: Session HIST-2. In compare mode a slot is a TIME, not a direction: slot 1 is
#: the before reading and slot 2 the after. The order is the upload order and
#: nothing reorders it, so a message that names a side is always naming the
#: file the analyst put there.
_COMPARE_LABELS: tuple[str, ...] = ("Before", "After")

#: Inserted after Machine Details on every compare-mode report. It states the
#: pairing the whole document rests on, in the analyst's own upload order, so a
#: reader never has to guess which file was which.
_COMPARE_PROVENANCE_NOTE = (
    "**Before / after comparison** — two readings of one measurement point. "
    "Before is the first file uploaded; After is the second, and is the reading "
    "this report's diagnosis, severity and figures describe."
)

_COMPARE_NEEDS_TWO = (
    "A before/after comparison reads exactly two files — the earlier reading first, "
    "the later one second. Upload two, or switch the schema back to Spectrum."
)


def _slot_label(mode: str, index: int, direction: "assembly.Direction") -> str:
    """What to call one upload slot in an analyst-facing message."""
    if mode == "compare" and index < len(_COMPARE_LABELS):
        return _COMPARE_LABELS[index]
    return assembly.DIRECTION_LABELS[direction]


def _resolve_directions(
    slots: list[tuple[UploadFile, str | None]], mode: str
) -> tuple[list["assembly.Direction"], bool]:
    """Map the provided per-slot directions onto the direction vocabulary,
    defaulting a single file's missing direction to radial-horizontal (assumed).
    Raises HTTPException(400) with friendly copy on any invalid combination."""
    n = len(slots)

    # Session HIST-2. Compare reads exactly two files and direction is not a
    # comparison concept: both readings are the SAME measurement point at two
    # times, so both take the single-file default axis and each parses through
    # the identity path it would have taken uploaded alone.
    if mode == "compare":
        if n != 2:
            raise HTTPException(status_code=400, detail=_COMPARE_NEEDS_TWO)
        return ["radial_h", "radial_h"], True

    # Trend / MAFAULDA read a single file; direction is irrelevant. Multi-file in
    # those modes is a user error.
    if mode != "spectrum":
        if n > 1:
            raise HTTPException(
                status_code=400,
                detail="Trend and MAFAULDA analyses read a single file — upload one file, "
                "or switch the schema to Spectrum.",
            )
        return ["radial_h"], True  # single non-spectrum file behaves exactly as today

    if n == 1:
        d = slots[0][1]
        # `d is None` covers the browser's actual wire format, but only via a
        # rule in FastAPI rather than anything here: an empty-string value on a
        # NON-REQUIRED Form field is coerced back to that field's default before
        # this function is called (fastapi/dependencies/utils.py, the
        # `isinstance(value, str) and value == ""` branch). That matters because
        # index.html makes radial-horizontal the EMPTY option value and app.js
        # substitutes 'radial_h' only for multi-file posts, so a single-file
        # upload -- and every press of the demo button -- posts `direction=""`,
        # not an absent field. If that coercion ever narrows, every single-file
        # upload becomes a 400 telling the analyst to choose the direction they
        # already chose. Pinned at the HTTP boundary by
        # tests/test_multiaxis.py::test_browser_empty_direction_is_the_identity_path.
        if d is None:
            return ["radial_h"], True  # the only defaulting path — byte-identical to today
        if d not in assembly.DIRECTION_TO_AXIS:
            raise HTTPException(status_code=400, detail=_BAD_DIRECTION_MSG)
        return [d], False

    directions: list[assembly.Direction] = []
    for _, d in slots:
        if d is None:
            raise HTTPException(
                status_code=400,
                detail="Each file needs a direction when uploading more than one — "
                "set the selector on every file.",
            )
        if d not in assembly.DIRECTION_TO_AXIS:
            raise HTTPException(status_code=400, detail=_BAD_DIRECTION_MSG)
        directions.append(d)  # type: ignore[arg-type]

    seen: set[str] = set()
    for d in directions:
        if d in seen:
            raise HTTPException(
                status_code=400,
                detail=f"Two files are both marked {assembly.DIRECTION_LABELS[d]} — "
                "check the direction selectors.",
            )
        seen.add(d)
    return directions, False


# Session INTAKE-HONEST — the closed vocabularies for the two select-backed
# acquisition fields. Kept in sync with static/index.html's <option> values;
# an unknown value is an analyst-facing 422, never a job.
_WINDOW_TYPES = ("hanning", "flattop", "rectangular", "other")
_INTEGRATION_KINDS = ("none", "hardware", "software")


def _acquisition_422(form_dict: dict[str, Any]) -> str | None:
    """First problem with the declared acquisition settings, or None when they
    are acceptable. All six fields are optional; '' has already been
    normalized to None by the caller. Validated before the job is created or
    the daily allowance charged, so a typo costs nothing."""
    for key in ("sensor_sensitivity_mv_per_g", "fmax_hz"):
        value = form_dict.get(key)
        if value is not None and value <= 0:
            return f"{key} must be a positive number"
    for key in ("spectral_lines", "averages"):
        value = form_dict.get(key)
        if value is not None and value < 1:
            return f"{key} must be a positive whole number"
    window = form_dict.get("window_type")
    if window is not None and window not in _WINDOW_TYPES:
        return f"window_type must be one of {', '.join(_WINDOW_TYPES)}"
    integration = form_dict.get("integration")
    if integration is not None and integration not in _INTEGRATION_KINDS:
        return f"integration must be one of {', '.join(_INTEGRATION_KINDS)}"
    return None


#: Session GEOM-A — the vocabularies the geometry selects may use.
_COUPLING_STATES = ("coupled", "uncoupled")
_DRIVE_TYPES = ("direct_on_line", "vfd", "soft_starter")
#: Session INTAKE-2 — the mounting answer the ISO support class is read from.
_MOUNTINGS = ("rigid", "flexible")
#: What location 1 is called when the analyst left `measurement_location` blank.
#: It is only ever used to LABEL a result -- never substituted into the form dict
#: -- so a single-location job still sends `measurement_location=None` to the
#: adapters and its report is unchanged. The word matches the label the browser
#: migrates a blank-location trend series onto, so one machine's history does not
#: split between "" and "Default" (see app.js's TREND_KEY migration).
_FIRST_LOCATION_LABEL = "Default"
#: The measurement location is free text that travels into the report and into
#: the drafting prompt, so it is bounded here like every other field that does.
_LOCATION_MAX_CHARS = 60
#: The machine alias, bounded for the same reason and for one more: it is the
#: key readings are grouped by, so it reaches a DATABASE COLUMN.
#:
#: A MIRRORED LITERAL, deliberately, and it is `_LOCATION_MAX_CHARS`'s own shape
#: -- `db/models.py:110` carries `_LOCATION_LEN = 60  # app.py::_LOCATION_MAX_CHARS`
#: as the comment pointing back. This module cannot import `db/models.py` to read
#: `CARD_FIELD_MAX["machine_alias"]` from it: `[db]` is an optional extra, the db
#: package is imported inside `create_app`'s flag branch and nowhere else, and an
#: import here would load SQLAlchemy on the backend production runs.
#: `tests/test_rename1_alias.py` asserts the two numbers are equal when the extra
#: IS installed, which is where a drift would be caught.
#:
#: WHY IT WAS MISSING UNTIL NOW (Session JOBDB F-3, ACCOUNT-2 F-6). SQLite does
#: not enforce VARCHAR length and Postgres does, so an uncapped alias is a row
#: that fits on a laptop and raises StringDataRightTruncation in production.
#: `db/recorder.py` already refuses to record one -- silently, which costs the
#: analyst the run and writes no machine row. This is the half that tells them.
_ALIAS_MAX_CHARS = 120
_PULLEY_FIELDS = ("drive_pulley_mm", "driven_pulley_mm", "pulley_center_distance_mm")


def _alias_422(form_dict: dict[str, Any]) -> str | None:
    """The machine alias is too long, or None. Session RENAME+PRICE (A3).

    Free, like every other `_*_422`: it runs before the job is created and
    before the daily allowance is spent, so a paste that is one character over
    costs nothing.

    **It names a length rather than restating a rule**, because there is no rule
    to restate -- an alias is free text and the only thing wrong with this one is
    that it does not fit. The sentence says what to do about it, which for a
    bounded field is "shorten it", and says by how much: an analyst who pasted a
    plant description into the box wants the number, not the policy.

    NOT `measurement_location`'s wording, which is `_geometry_422:601`'s
    `f"measurement_location must be {N} characters or fewer"` -- that prints a
    raw form-field name at somebody who never saw one. LIMITS-1c F-3 is the
    precedent: a refusal an analyst reads is written for the analyst.

    An EMPTY alias is not refused here and must not be. It is optional on the
    form (`index.html`: "Machine alias (optional)"), and `routes_machines.py`
    refuses an unnamed machine on the path where a name is actually required.
    """
    alias = form_dict.get("machine_alias")
    if alias is None or len(alias) <= _ALIAS_MAX_CHARS:
        return None
    return (
        f"the machine alias is {len(alias)} characters and the longest we can "
        f"store is {_ALIAS_MAX_CHARS} — shorten it. Readings are grouped by the "
        "alias, so it is a label rather than a description; the machine's full "
        "description belongs in the report, not in this box."
    )


def _machine_422(form_dict: dict[str, Any]) -> str | None:
    """First problem with the machine kind and its rated spec, or None.

    Session INTAKE-2. This is the free 422 that makes PARTC F-2 unreachable in
    the product: the type is REQUIRED here, so no report can go out claiming a
    kind of machine nobody stated. `machine_type_from_form`'s
    `MACHINE_TYPE_UNKNOWN` fallback exists for callers that never passed this
    boundary; a form post cannot reach it.

    Charged nothing: like every other `_*_422`, this runs before the job is
    created and before the daily allowance is spent, so a typo is free.
    """
    machine_type = form_dict.get("machine_type")
    if not machine_type:
        return (
            "choose what kind of machine this is — the report names it and the "
            "recommendations depend on it"
        )
    if machine_type not in MACHINE_TYPES:
        return f"machine type must be one of {', '.join(MACHINE_TYPES)}"

    mounting = form_dict.get("mounting")
    if mounting is not None and mounting not in _MOUNTINGS:
        return f"mounting must be one of {', '.join(_MOUNTINGS)}"

    for key, label in (("rated_kw", "rated power"), ("driven_rpm", "driven speed")):
        value = form_dict.get(key)
        if value is not None and value <= 0:
            return f"{label} must be a positive number"
    return None


#: Session GEOM-1 -- the four controls under "Not on the list? Enter the
#: bearing's geometry", in the order the form asks for them and in the words
#: the form labels them with. A message that names `bearing_ball_dia_mm` sends
#: an analyst looking for a control that does not exist under that name.
_BEARING_GEOMETRY_LABELS = {
    "bearing_n_balls": "rolling element count",
    "bearing_ball_dia_mm": "ball / roller diameter",
    "bearing_pitch_dia_mm": "pitch diameter",
    "bearing_contact_angle_deg": "contact angle",
}


#: Session LIMITS-1c -- the machine's own severity limits, in the order the
#: form shows them and with the label an analyst reads. One definition, read by
#: the refusal below and by nothing else; `thresholds_from_form` in
#: `adapters/uploads/common.py` owns turning them into a `MachineThresholds`.
_LIMIT_LABELS: dict[str, str] = {
    "limit_ab": "Zone A/B",
    "limit_bc": "Zone B/C",
    "limit_cd": "Zone C/D",
}


def _thresholds_422(form_dict: dict[str, Any]) -> str | None:
    """First problem with the machine-specific severity limits, or None.

    Three states, and only three: none given (the ISO path, and by far the
    common one), all three given and valid, or a refusal.

    **The rules are not restated here.** `MachineThresholds`
    (`models.py:69-86`) already validates `> 0` and `ab < bc < cd`; this
    constructs one and turns its `ValueError` into a sentence. A second copy of
    those two rules is a second thing to keep in step, and the copy that drifts
    is the one nobody reads.

    **Why this is a free 422 and not left to the model.** `machine_from_form`
    runs INSIDE the parse sandbox, so a `MachineThresholds` that raises there
    comes back as a PARSE_ERROR -- a claim about the analyst's FILE, for a
    number they typed into a form. That is the hazard `_geometry_422`'s
    docstring names, and the answer is the same: refuse it here, before
    anything is charged or created.
    """
    stated = {k: form_dict.get(k) for k in _LIMIT_LABELS if form_dict.get(k) is not None}
    if not stated:
        return None
    missing = [label for name, label in _LIMIT_LABELS.items() if name not in stated]
    if missing:
        # All three or none: a machine judged against one boundary and two ISO
        # ones is judged against nothing anybody chose.
        return (
            "machine-specific severity limits need all three boundaries — "
            + ", ".join(missing)
            + (" is" if len(missing) == 1 else " are")
            + " still blank. Leave all three blank to judge against ISO 20816-3."
        )
    from pydantic import ValidationError

    from vib_agent.models import MachineThresholds

    try:
        MachineThresholds(ab=stated["limit_ab"], bc=stated["limit_bc"],
                          cd=stated["limit_cd"])
    except ValidationError as exc:
        # The validator's own sentence, read STRUCTURALLY. `str(exc)` renders a
        # multi-line report whose last line is pydantic's docs URL, so parsing
        # that was measured returning
        # "For further information visit https://errors.pydantic.dev/..." to an
        # analyst. `.errors()[0]["msg"]` is the message itself, prefixed with
        # pydantic's own "Value error, ".
        detail = str(exc.errors()[0].get("msg", "")).removeprefix("Value error, ")
        for name, label in _LIMIT_LABELS.items():
            detail = detail.replace(f"thresholds.{name.removeprefix('limit_')}", label)
        detail = detail.replace("thresholds must satisfy ab < bc < cd",
                                "the Zone A/B, B/C and C/D limits must increase")
        if not detail:
            return ("machine-specific severity limits: each boundary must be a positive "
                    "number, and they must increase from Zone A/B to Zone C/D.")
        return f"machine-specific severity limits: {detail}"
    return None


def _bearing_geometry_422(
    form_dict: dict[str, Any], known_bearings: tuple[str, ...] = ()
) -> str | None:
    """First problem with the bearing, or None. Split out of `_geometry_422`
    only for length -- it is the same contract and the same single source.

    Every condition here has a twin in `adapters/uploads/common.py` (in
    `bearing_spec_from_form` or in `BearingSpec`'s own validators). The twin is
    the backstop; this is the one an analyst reads.
    """
    model = form_dict.get("bearing_model")
    stated = {
        name: form_dict.get(name)
        for name in _BEARING_GEOMETRY_LABELS
        if form_dict.get(name) is not None
    }

    if model and stated:
        return (
            "choose a bearing from the list or enter its geometry, not both — "
            "the analysis cannot be run against two different bearings"
        )
    if model and known_bearings and model not in known_bearings:
        # Before GEOM-1 this was a ValueError inside the parse sandbox, so a
        # bearing we hold no geometry for came back as a failure about the
        # analyst's FILE. It is now a free 422 that names the way forward.
        return (
            f"“{model}” is not a bearing we hold geometry for — pick one from the "
            "Bearing model list, or leave it at Not listed and enter the geometry"
        )
    if not stated:
        return None

    missing = [
        _BEARING_GEOMETRY_LABELS[name]
        for name in ("bearing_n_balls", "bearing_ball_dia_mm", "bearing_pitch_dia_mm")
        if name not in stated
    ]
    if len(missing) == 3:
        # Only the contact angle was given. Naming three missing fields would be
        # technically right and useless; what happened is that the analyst
        # answered the optional one and none of the required ones.
        return (
            "a contact angle on its own is not a bearing — the rolling element count, the "
            "ball / roller diameter and the pitch diameter are what the fault frequencies "
            "are computed from"
        )
    if missing:
        named = " and ".join(missing) if len(missing) == 2 else missing[0]
        return (
            "bearing geometry needs the rolling element count, the ball / roller diameter "
            f"and the pitch diameter — {named} {'are' if len(missing) == 2 else 'is'} missing "
            "(contact angle may be left blank and is taken as 0°)"
        )

    if stated["bearing_n_balls"] < 1:
        return "the bearing's rolling element count must be a positive whole number"
    for name in ("bearing_ball_dia_mm", "bearing_pitch_dia_mm"):
        if stated[name] <= 0:
            return f"the bearing's {_BEARING_GEOMETRY_LABELS[name]} must be a positive number of millimetres"
    if stated["bearing_ball_dia_mm"] >= stated["bearing_pitch_dia_mm"]:
        return (
            "the bearing's ball / roller diameter must be smaller than its pitch diameter — "
            "as entered, the rolling elements would not fit in the bearing"
        )
    angle = stated.get("bearing_contact_angle_deg")
    if angle is not None and not (0 <= angle <= 90):
        return "the bearing's contact angle must be between 0 and 90 degrees"
    return None


def _geometry_422(
    form_dict: dict[str, Any], known_bearings: tuple[str, ...] = ()
) -> str | None:
    """First problem with the declared machine geometry, or None when it is
    acceptable. Same contract as `_acquisition_422` and for the same reason:
    MachineMeta and BeltSpec validate this geometry properly, but they do it
    INSIDE the parse sandbox, where a raised ValueError becomes a PARSE_ERROR
    -- an analyst's typo filed as `upload_unreadable`, which is a claim about
    their file. Checking here makes a typo a free 422 before a job exists.
    '' has already been normalized to None by the caller.

    Session GEOM-1 adds the BEARING to what this covers, which closes the older
    half of the same defect: `bearing_spec_from_form` has always raised for a
    model it holds no geometry for, and it raises in the sandbox -- so a
    bearing designation we do not carry reached the analyst as an error about
    the file they uploaded. `known_bearings` is `config/bearings.json`'s keys,
    passed in rather than read here so this stays a pure function of the form.
    An empty tuple means "do not check the catalogue" and is what every caller
    that has no catalogue to hand gets.

    One sentence per problem, each naming the control the analyst has to go
    back to -- and this is the ONLY place those sentences exist.
    """
    bearing_problem = _bearing_geometry_422(form_dict, known_bearings)
    if bearing_problem is not None:
        return bearing_problem

    for key in ("blades", "gear_teeth_driving", "gear_teeth_driven", "rotor_bars"):
        value = form_dict.get(key)
        if value is not None and value < 1:
            return f"{key} must be a positive whole number"
    poles = form_dict.get("poles")
    if poles is not None and (poles < 2 or poles % 2 != 0):
        # A machine is wound in pole PAIRS; an odd count does not exist, and
        # recording one would poison the pole-pass frequency computed from it.
        return "poles must be a positive even number (2, 4, 6, ...)"
    line_freq = form_dict.get("line_freq_hz")
    if line_freq is not None and line_freq <= 0:
        return "line_freq_hz must be a positive number"
    coupling = form_dict.get("coupling")
    if coupling is not None and coupling not in _COUPLING_STATES:
        return f"coupling must be one of {', '.join(_COUPLING_STATES)}"
    drive_type = form_dict.get("drive_type")
    if drive_type is not None and drive_type not in _DRIVE_TYPES:
        return f"drive_type must be one of {', '.join(_DRIVE_TYPES)}"
    location = form_dict.get("measurement_location")
    if location is not None and len(location) > _LOCATION_MAX_CHARS:
        return f"measurement_location must be {_LOCATION_MAX_CHARS} characters or fewer"

    pulleys = [form_dict.get(key) for key in _PULLEY_FIELDS]
    if any(v is not None for v in pulleys):
        if any(v is None for v in pulleys):
            return ("belt geometry needs all three of drive pulley diameter, driven pulley "
                    "diameter and centre distance — or none of them")
        if any(v <= 0 for v in pulleys):
            return "pulley diameters and centre distance must be positive numbers"
        drive, driven, centres = pulleys
        if centres <= (drive + driven) / 2:
            return ("the centre distance must exceed the sum of the pulley radii — "
                    "the pulleys as entered would overlap")
        if not form_dict.get("rpm"):
            return "a belt frequency needs the running speed (RPM) to derive from"
    return None


#: Session HIST-1 — the browser's trend card only ever accumulates points from
#: ordinary spectrum analyses, and only that mode can use them:
#:   * `trend` builds Case.history from the analyst's own file
#:     (adapters/uploads/tabular.py::parse_trend), so a card history there is a
#:     collision between two different sets of readings, not extra evidence;
#:   * `mafaulda` is an acceleration-only benchmark lane with no velocity
#:     scalar to trend;
#:   * `compare` is two readings of ONE point in one request — its own answer
#:     to "what changed", and it never merges (see `_run_compare`), so a
#:     history could not reach a Case there even if this allowed it.
_HISTORY_MODES = ("spectrum",)

#: Form keys that are this layer's business and not the adapters'. `history` is
#: here for a reason beyond tidiness: it must never cross into the parse
#: sandbox. It is analyst-held data about PAST readings, it is validated at the
#: form boundary, and it attaches to the merged Case in the trusted parent
#: (`assembly.merge_channels`) — the sandbox reads the uploaded file and
#: nothing else. One tuple, read by both parse lanes, so a new key of this
#: kind cannot be stripped on one lane and forwarded on the other.
_NOT_UPLOAD_FORM_FIELDS = ("retain_trace", "share_format", "compare", "history")


def _history_422(form_dict: dict[str, Any]) -> str | None:
    """First problem with the analyst's browser-held trend history, or None.

    Same contract and the same reason as `_geometry_422`: the payload is
    untrusted text from a form field, and validating it here makes a bad card a
    free 422 before a job exists rather than a `PARSE_ERROR` from inside the
    sandbox — which would file the analyst's own saved readings as a claim
    about the file they uploaded. It is deliberately NOT an S7 taxonomy code:
    a malformed history must never become a job failure.
    """
    raw = form_dict.get("history")
    if raw is None or not str(raw).strip():
        return None
    mode = "compare" if form_dict.get("compare") else form_dict.get("mode")
    if mode not in _HISTORY_MODES:
        return ("a saved trend history can only be sent with a spectrum analysis — "
                "this upload is in a different mode")
    try:
        parse_client_history(str(raw))
    except ValueError as exc:
        return str(exc)
    return None




#: `db`-mode stand-in for the invite label on the per-job log line and the daily
#: counter: eight hex of the account id. Enough to group one analyst's jobs in a
class ConfirmFile(BaseModel):
    """One file's corrections on a multi-file confirm card."""

    model_config = ConfigDict(extra="forbid")

    slot: int = Field(..., ge=1, le=3)
    velocity_unit: str | None = Field(None, pattern="^(mm_s|in_s)$")
    detection_type: str | None = Field(None, pattern="^(rms|peak|peak_to_peak)$")
    direction: str | None = Field(None, pattern="^(radial_h|radial_v|axial)$")
    skip: bool = False


class ConfirmBody(BaseModel):
    """The analyst's answer to the confirm card. Closed and bounded, exactly
    like the recipe itself: the things the card lets them correct, and the two
    opt-in flags. Anything else is rejected (422), not ignored.

    The top-level unit/detection fields apply to every interpreted file (the
    single-file card's shape, unchanged); `files` carries per-file corrections
    and per-file skips for the multi-file card. RPM is one machine's speed and
    is therefore always global."""

    model_config = ConfigDict(extra="forbid")

    velocity_unit: str | None = Field(None, pattern="^(mm_s|in_s)$")
    detection_type: str | None = Field(None, pattern="^(rms|peak|peak_to_peak)$")
    rpm: float | None = Field(None, gt=0, le=100_000)
    files: list[ConfirmFile] = Field(default_factory=list, max_length=3)
    share_format: bool = False
    retain_trace: bool = False


class AppState:
    """Everything a request handler needs, assembled once at app creation --
    no globals, so tests can build an isolated instance per app."""

    def __init__(
        self,
        *,
        webapp_cfg: dict[str, Any],
        iso_table: dict[str, Any],
        thresholds: dict[str, Any],
        rules: dict[str, Any],
        bearings_cfg: dict[str, Any],
        cwru_cfg: dict[str, Any],
        mfpt_cfg: dict[str, Any],
        invite_codes: dict[str, str],
        contact_email: str,
        retain_traces_enabled: bool,
        anthropic_client_factory: Callable[[], Any],
        base_tmp: Path | None = None,
    ) -> None:
        self.webapp_cfg = webapp_cfg
        self.iso_table = iso_table
        self.thresholds = thresholds
        self.rules = rules
        self.bearings_cfg = bearings_cfg
        self.cwru_cfg = cwru_cfg
        self.mfpt_cfg = mfpt_cfg
        self.invite_codes = invite_codes
        self.contact_email = contact_email
        self.retain_traces_enabled = retain_traces_enabled
        self.anthropic_client_factory = anthropic_client_factory

        self.registry = JobRegistry(
            ttl_minutes=webapp_cfg["job_ttl_minutes"], base_tmp=base_tmp,
            # S7: the max-runtime timeout. NOT the TTL -- a 60-minute wait before
            # a hung job is called hung is exactly the window the prototype's
            # honest-uncertainty card was drawn for. Measured this session: a
            # complete job is 5-6 s, so 600 s is ~100x, and queue wait is
            # excluded by construction (the clock starts after the semaphore).
            max_runtime_s=float(webapp_cfg.get("job_max_runtime_s", 600)),
        )
        self.rate_limiter = RateLimiter(
            per_code_daily_jobs=webapp_cfg["per_code_daily_jobs"],
            global_daily_jobs=webapp_cfg["global_daily_jobs"],
        )
        # v6-A: per-IP sliding-window limits (a second layer above the per-code
        # daily caps) + the free-disk floor for new jobs.
        self.ip_limiter = IpRateLimiter(
            requests_per_minute=webapp_cfg.get("ip_requests_per_minute", 60),
            job_posts_per_hour=webapp_cfg.get("ip_job_posts_per_hour", 10),
            status_requests_per_minute=webapp_cfg.get("ip_status_requests_per_minute", 300),
        )
        self.disk_min_free_mb = int(webapp_cfg.get("disk_min_free_mb", 500))
        self.tmp_path = base_tmp or Path(tempfile.gettempdir())
        self.spend_guard = SpendGuard(daily_token_budget=webapp_cfg["daily_token_budget"])
        # Session G: structure-only fingerprint -> recipe. In memory, bounded,
        # and never persisted -- the same rule as the job registry, and DB-1 did
        # not touch it: no upload, no recipe and no analysis artefact reaches a
        # database on any backend.
        # A hit skips the inference call entirely (and its cost).
        self.inference_cfg = dict(webapp_cfg.get("inference", {}))
        self.recipe_cache: dict[str, str] = {}
        self.agent_cfg = load_config("agent")
        self.worker_semaphore = asyncio.Semaphore(webapp_cfg["worker_concurrency"])
        # S7: job id -> its background task, so the max-runtime sweep can cancel
        # the right worker BEFORE it marks the job terminal, and so no pending
        # task is garbage-collected mid-flight. Per-app, like everything else
        # here: no module globals, so tests stay isolated.
        self.background_tasks: dict[str, "asyncio.Task[None]"] = {}
        # S7 / UXD Q3: last-emitted stamp per refusal condition, so a full disk
        # (which refuses EVERY request) cannot make its own warning dominate the
        # log. One line per condition per window, with the suppressed count.
        self.guard_log: dict[str, tuple[float, int]] = {}
        self.guard_log_window_s = float(webapp_cfg.get("sweep_interval_s", 60))
        # DEP-6. Observed end-to-end durations of COMPLETED jobs on this
        # process, so the queue-depth 503 can put a real number in Retry-After
        # instead of "shortly". Bounded and in memory, like everything else
        # here -- this is a running estimate, not a metric store.
        self.recent_durations: deque[float] = deque(maxlen=20)

    # ── DEP-6: honest Retry-After on the two guard 503s ──────────────────
    # Both refusals used to say "try again shortly" and carry NO Retry-After,
    # which left the client with nothing to count down and the analyst with
    # nothing to plan around. The per-IP 429 family is the one rejection the
    # backend already tells the whole truth about (hardening.py:129-142 computes
    # the seconds until this IP's oldest entry leaves the window), and the
    # asymmetry was never a decision -- it was a gap.
    #
    # The rule these two follow: a Retry-After is only worth emitting if
    # waiting that long actually improves the odds. Both below satisfy that,
    # and NEITHER pretends to more precision than it has.

    # The figure the product already quotes to analysts ("Drafting takes about
    # a minute"), used only until this process has measured its own jobs. It is
    # a starting estimate that gets replaced by observation, not a constant
    # standing in for one.
    _ASSUMED_JOB_SECONDS = 60.0
    _MIN_RETRY_AFTER_S = 5
    # Matches the client's MAX_RETRY_AFTER_S: a larger value would be silently
    # truncated there, so the two would disagree about what was promised.
    _MAX_RETRY_AFTER_S = 300

    def record_job_duration(self, duration_ms: float | None) -> None:
        """One completed job's wall time, for the queue estimate. Called from
        the single per-job outcome path, so it counts each job exactly once."""
        if duration_ms is not None and duration_ms > 0:
            self.recent_durations.append(duration_ms / 1000.0)

    def queue_retry_after(self, pending: int) -> int:
        """Seconds until a worker slot is plausibly free: how long a job
        actually takes here, times how many are ahead, divided by how many run
        at once.

        `pending` deliberately counts what `pending_job_count()` counts, which
        INCLUDES `awaiting_confirm` jobs that are not competing for a worker
        (DEP-3's caveat). That over-counts, so this OVER-estimates the wait --
        the right direction to be wrong in: a Retry-After that expires into a
        second refusal teaches the analyst that the numbers are decorative.
        """
        seconds = sorted(self.recent_durations)[len(self.recent_durations) // 2] \
            if self.recent_durations else self._ASSUMED_JOB_SECONDS
        permits = max(1, int(self.webapp_cfg.get("worker_concurrency", 2)))
        estimate = seconds * max(1, pending) / permits
        return max(self._MIN_RETRY_AFTER_S,
                   min(self._MAX_RETRY_AFTER_S, math.ceil(estimate)))

    def disk_retry_after(self) -> int:
        """A conservative fixed value, and fixed ON PURPOSE.

        Disk is freed by the TTL sweep, which runs every `sweep_interval_s` but
        can only reclaim jobs that have actually aged out -- so what frees space
        is somebody else's report reaching 60 minutes old, which this process
        cannot see coming. There is no honest way to compute the moment. What
        IS true is that several sweeps will have run within five minutes, and
        that erring long is the safe direction. So: the client's own ceiling,
        which is also the longest value it will honour without truncating.
        """
        return self._MAX_RETRY_AFTER_S

    def log_guard_refusal(self, condition: str, detail: str, code_label: str) -> None:
        """One WARNING per refusal condition per window, with the count of the
        ones it stands for.

        Rules this line obeys, each for a reason: no IP ever (the v6-A rule --
        the app log carries no request addresses and the proxy sets no access
        log); the invite-code LABEL only, never the raw code, the same field
        `_log_job_outcome` already emits; no filename, no size, no machine alias
        -- the upload has not been read at this point and must not be; and
        WARNING rather than INFO, because unlike a per-job outcome this is
        operator-actionable."""
        now = time.monotonic()
        last, suppressed = self.guard_log.get(condition, (None, 0))
        if last is not None and (now - last) < self.guard_log_window_s:
            self.guard_log[condition] = (last, suppressed + 1)
            return
        self.guard_log[condition] = (now, 0)
        _log.warning("%s %s code=%s%s outcome=refused", condition, detail, code_label,
                     f" suppressed={suppressed}" if suppressed else "")

    def pending_job_count(self) -> int:
        return sum(1 for j in self.registry.all_jobs()
                   if j.state in ("queued", "running", "awaiting_confirm"))

    # ── Session G: the fingerprint cache ─────────────────────────────────
    def cached_recipe(self, fingerprint: str) -> ParseRecipe | None:
        raw = self.recipe_cache.get(fingerprint)
        if raw is None:
            return None
        try:
            return ParseRecipe(**json.loads(raw))
        except (ValidationError, ValueError):  # a stale shape is a miss, never a crash
            self.recipe_cache.pop(fingerprint, None)
            return None

    def cache_recipe(self, fingerprint: str, recipe: ParseRecipe) -> None:
        limit = int(self.inference_cfg.get("cache_max_entries", 500))
        self.recipe_cache[fingerprint] = recipe.model_dump_json()
        while len(self.recipe_cache) > limit:  # oldest first (insertion order)
            self.recipe_cache.pop(next(iter(self.recipe_cache)))

    def record_format_ping(self, fingerprint: str, recipe: ParseRecipe) -> None:
        """Opt-in only. Writes the STRUCTURE and the RECIPE -- no reading, no
        machine name, no filename, no invite code, and with the two numbers a
        recipe can carry (rpm/fs read from a header) stripped, so the record is
        purely 'a file shaped like this is read like this'."""
        target = self.inference_cfg.get("share_format_log")
        if not target:
            return
        payload = recipe.model_dump()
        payload["rpm_value"] = None
        payload["fs_value"] = None
        line = json.dumps({"fingerprint": fingerprint, "recipe": payload}, sort_keys=True)
        path = Path(target)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[3] / path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as handle:
                handle.write(line + "\n")
        except OSError:
            pass  # a shared-format note is never worth failing a job for


def _parse_one(job: Job, channel: "assembly.ChannelUpload", upload_form_dict: dict[str, Any],
               state: AppState, *, recipe_json: str | None = None, keep_upload: bool = False):
    """Parse one uploaded channel in its own sandbox; returns a ParsedChannel or
    raises ParseError. The raw upload is unlinked whether parsing succeeds or not
    (it never outlives parsing) -- EXCEPT when `keep_upload` says a fallback may
    still need it (Session G2: a .csv/.xlsx that fails template parsing is read
    again through schema inference, so deleting it on failure would destroy the
    only copy)."""
    out_path = job.job_dir / f"_case_{channel.slot}.json"
    try:
        case_dict, kind, conversion_note = parse_in_subprocess(
            channel.path,
            upload_form_dict,
            bearings_cfg_path=_CONFIG_DIR / "bearings.json",
            cwru_cfg_path=_CONFIG_DIR / "cwru.json",
            mfpt_cfg_path=_CONFIG_DIR / "mfpt.json",
            wt_cfg_path=_CONFIG_DIR / "wind_turbine.json",
            mafaulda_cfg_path=_CONFIG_DIR / "mafaulda.json",
            out_path=out_path,
            timeout_s=state.webapp_cfg["parse_timeout_s"],
            memory_mb=state.webapp_cfg["parse_memory_mb"],
            recipe_json=recipe_json,
        )
        out_path.unlink(missing_ok=True)
    except Exception:
        if not keep_upload:
            channel.path.unlink(missing_ok=True)
        raise
    else:
        channel.path.unlink(missing_ok=True)  # raw upload never outlives parsing
    return assembly.ParsedChannel(
        direction=channel.direction, assumed=channel.assumed,
        case=Case.model_validate(case_dict), kind=kind, conversion_note=conversion_note,
    )


PROVENANCE_NOTE = "Format interpreted via schema inference; confirmed by the analyst."

_INFERENCE_UNAVAILABLE = (
    "We couldn’t read this file’s layout right now — the interpretation service on our side "
    "was unavailable. Your file may be fine; please try again in a few minutes, or upload a "
    "CSV/XLSX export using our template."
)


def _file_interpretation(plan: dict[str, Any], form_rpm: float) -> dict[str, Any]:
    """One file's line on the confirm card. Every string is OURS, derived from
    the recipe's enumerated values or from our own status vocabulary — no
    inferred and no file-supplied text is ever rendered."""
    entry = {
        "slot": plan["slot"],
        "label": plan["label"],
        "direction": plan["direction"],
        "source": plan["source"],          # "inference" | "template"
        "status": plan["status"],          # "ok" | "unknown"
        "from_cache": bool(plan.get("from_cache")),
        "message": plan.get("message"),
    }
    if plan["source"] == "template":
        entry.update({
            "headline": "read with our CSV/XLSX template",
            "x_axis": "Hz", "severity_available": True, "ignored_columns": 0,
            "rpm": form_rpm, "rpm_from": "the form", "kind": "spectrum",
            "amplitude_unit": "", "amplitude_label": "", "detection": "",
            "editable": {"velocity_unit": "", "detection_type": "", "rpm": form_rpm},
        })
        return entry
    if plan["status"] != "ok" or not plan.get("recipe"):
        entry.update({"headline": "could not be interpreted", "severity_available": False})
        return entry
    recipe = ParseRecipe(**json.loads(plan["recipe"]))
    described = recipe.describe(form_rpm=form_rpm)
    described["editable"] = {
        "velocity_unit": recipe.amplitude_unit if recipe.amplitude_unit in ("mm_s", "in_s") else "",
        "detection_type": recipe.detection,
        "rpm": described["rpm"],
    }
    entry.update(described)
    return entry


def _interpretation_payload(plans: list[dict[str, Any]], form_dict: dict[str, Any]) -> dict[str, Any]:
    """The confirm card's contents. A single file keeps the flat shape Session G
    shipped; every job also carries the per-file list the multi-file card reads."""
    form_rpm = float(form_dict["rpm"])
    files = [_file_interpretation(plan, form_rpm) for plan in plans]
    payload: dict[str, Any] = {
        "multi": len(files) > 1,
        "files": files,
        "usable": sum(1 for f in files if f["status"] == "ok"),
    }
    if len(files) == 1:
        payload.update({k: v for k, v in files[0].items() if k not in ("slot", "label")})
    return payload


def _infer_one(job: Job, plan: dict[str, Any], form_dict: dict[str, Any], state: AppState) -> None:
    """Sample one file and get a recipe for it — from the cache when its shape
    is already known, otherwise from one inference call. Mutates `plan` in place;
    a failure marks the file unknown rather than failing the whole job, because
    the other files may still be readable."""
    sample_out = job.job_dir / f"_sample_{plan['slot']}.txt"
    try:
        sample = sample_in_subprocess(
            Path(plan["path"]), out_path=sample_out,
            timeout_s=state.webapp_cfg["parse_timeout_s"],
            memory_mb=state.webapp_cfg["parse_memory_mb"],
        )
    except SampleError as exc:
        plan.update(status="unknown", message=str(exc.safe_message))
        return

    fingerprint = structure_fingerprint(sample)
    plan["fingerprint"] = fingerprint
    cached = state.cached_recipe(fingerprint)
    if cached is not None:
        plan.update(status="ok", recipe=cached.model_dump_json(), from_cache=True)
        return

    # Cost guard: inference is metered under the SAME per-job budget as drafting,
    # and a host with no key degrades to the friendly card rather than stranding
    # the job (Session F2's lesson, applied at birth).
    #
    # `unavailable=True` records that the plan failed because OUR call could not
    # be made, not because the layout resisted reading. S7-ACCEPT F-5: without
    # the distinction, a key outage funnelled a good file into
    # `interpretation_failed` (bad_upload, not retryable) -- the taxonomy told
    # the analyst their format was unsupported and that retrying was pointless,
    # both false. `_infer_and_pause` reads this flag to pick the code.
    if state.spend_guard.exceeded():
        plan.update(status="unknown", message=_INFERENCE_UNAVAILABLE, unavailable=True)
        return
    try:
        client = state.anthropic_client_factory()
    except Exception:  # noqa: BLE001 -- no API key on this host
        plan.update(status="unknown", message=_INFERENCE_UNAVAILABLE, unavailable=True)
        return
    try:
        outcome = infer_recipe(
            sample,
            extension=Path(plan["path"]).suffix.lower(),
            stated_rpm=float(form_dict["rpm"]),
            client=client,
            agent_cfg=state.agent_cfg,
            inference_cfg=state.inference_cfg,
        )
    except InferenceError as exc:
        # An outage mid-call (the SDK resolves auth per-request, so a missing
        # key raises HERE, not at the factory above) is still an outage: keep
        # the canonical unavailable message and flag, so the funnel picks
        # `inference_unavailable` rather than blaming the file.
        if exc.unavailable:
            plan.update(status="unknown", message=_INFERENCE_UNAVAILABLE, unavailable=True)
        else:
            plan.update(status="unknown", message=exc.safe_message)
        return

    usage = job.token_usage or {"input_tokens": 0, "output_tokens": 0}
    job.token_usage = {k: usage.get(k, 0) + outcome.usage.get(k, 0) for k in
                       ("input_tokens", "output_tokens")}
    state.spend_guard.record(outcome.usage["input_tokens"] + outcome.usage["output_tokens"])
    plan.update(status="ok", recipe=outcome.recipe.model_dump_json(), from_cache=False)


def _compare_conversion_note(parsed: list["assembly.ParsedChannel"]) -> str:
    """The two readings' unit-conversion notes, labelled by side only when they
    differ. Two files exported the same way say it once; two files exported
    differently must each say what was done to them, because that difference is
    itself a reason a delta could be wrong."""
    notes = [ch.conversion_note for ch in parsed]
    if not any(notes):
        return ""
    if notes[0] == notes[1]:
        return notes[0]
    return "; ".join(
        f"{label}: {note}" for label, note in zip(_COMPARE_LABELS, notes) if note
    )


def _run_compare(
    job: Job, form_dict: dict[str, Any], parsed: list["assembly.ParsedChannel"],
    unreadable: list["assembly.UnreadableChannel"], state: AppState,
    *, notes: Sequence[str] = (),
) -> None:
    """Session HIST-2 — the compare lane, where the two parsed files diverge
    from every other upload.

    Nothing is merged. `assembly.merge_channels` combines channels into ONE
    measurement, which is the exact opposite of what a before/after pair is:
    these are two readings of one point at two times, and each keeps its own
    Case, its own pipeline run and its own verdict.
    """
    if len(parsed) < 2:
        # One side could not be read, so there is no pair. `upload_unreadable`
        # is the accurate claim and `not_comparable` would be the wrong one:
        # nothing was compared and found incompatible — a file could not be read.
        detail = "; ".join(u.safe_message for u in unreadable)
        mark_error(
            job,
            "A before/after comparison needs both files. "
            + (detail or "One of the two files could not be read."),
            code="upload_unreadable",
        )
        _log_job_outcome(job)
        return

    before, after = parsed[0], parsed[1]
    job.kind = "compare(" + ",".join(ch.kind for ch in (before, after)) + ")"
    retain_trace = bool(form_dict.get("retain_trace")) and state.retain_traces_enabled
    drafting_available = True
    try:
        client = state.anthropic_client_factory()
    except Exception:  # noqa: BLE001 -- keyless host: degrade, never strand
        client = None
        drafting_available = False

    process_compare_job(
        job,
        before_case=before.case,
        after_case=after.case,
        iso_table=state.iso_table,
        thresholds=state.thresholds,
        rules=state.rules,
        spend_guard=state.spend_guard,
        conversion_note=_compare_conversion_note([before, after]),
        retain_trace=retain_trace,
        client=client,
        assembly_notes=[*notes, _COMPARE_PROVENANCE_NOTE],
        drafting_available=drafting_available,
    )
    state.record_job_duration(job.duration_ms)
    _log_job_outcome(job)


def _infer_and_pause(
    job: Job, form_dict: dict[str, Any], plans: list[dict[str, Any]], state: AppState
) -> None:
    """Session G/G2 intake:每 file gets a recipe (or is marked uninterpretable),
    then the job PAUSES for the analyst.

    Nothing is analysed here. The uploads stay in the job directory until the
    interpretation is confirmed (or the TTL sweep reclaims them), because a
    recipe is a hypothesis about a file and the analyst is the one who can say
    whether it is right.
    """
    job.state = "running"
    job.phase = "analyzing"
    for plan in plans:
        if plan["source"] == "inference" and plan["status"] != "ok":
            _infer_one(job, plan, form_dict, state)

    usable = [p for p in plans if p["status"] == "ok"]
    if not usable:
        messages = "; ".join(
            f"{p['label']}: {p['message']}" if len(plans) > 1 else str(p["message"])
            for p in plans if p.get("message"))
        # S7-ACCEPT F-5: `interpretation_failed` means the LAYOUT resisted
        # reading -- bad_upload, don't retry. If ANY file failed only because
        # our inference call could not be made (no key, spend budget), that is
        # `inference_unavailable` -- server_error, retry later -- because a
        # retry after the outage can genuinely change the outcome, and telling
        # the analyst their format is unsupported would be false.
        code = ("inference_unavailable" if any(p.get("unavailable") for p in plans)
                else "interpretation_failed")
        mark_error(job, messages or COULD_NOT_INTERPRET, code=code)
        _log_job_outcome(job)
        return

    job.kind = "inferred(" + ",".join(Path(p["path"]).suffix.lstrip(".") for p in usable) + ")"
    job.pending = {"form": dict(form_dict), "channels": plans}
    job.recipe_json = usable[0].get("recipe")  # single-file compatibility
    job.fingerprint = usable[0].get("fingerprint")
    job.interpretation = _interpretation_payload(plans, form_dict)
    job.phase = None
    job.state = "awaiting_confirm"


def _resume_confirmed(job: Job, state: AppState) -> None:
    """After the analyst confirms: execute every confirmed recipe over its FULL
    file in the existing sandbox, verify each one against its own data, then
    assemble and analyse exactly as any multi-channel upload."""
    pending = job.pending or {}
    form_dict = dict(pending.get("form", {}))
    plans = [p for p in pending.get("channels", []) if p["status"] == "ok" and not p.get("skip")]
    upload_form_dict = {k: v for k, v in form_dict.items()
                        if k not in _NOT_UPLOAD_FORM_FIELDS}
    rpm = float(form_dict["rpm"])

    parsed: list[assembly.ParsedChannel] = []
    unreadable: list[assembly.UnreadableChannel] = []
    verification_failures: list[Check] = []
    rejected: list[assembly.ParsedChannel] = []   # parsed fine, but the data disagreed
    endorsed: list[tuple[str, ParseRecipe]] = []

    for plan in plans:
        channel = assembly.ChannelUpload(slot=plan["slot"], path=Path(plan["path"]),
                                         direction=plan["direction"], assumed=plan["assumed"])
        if plan["source"] == "template":
            # Already parsed by its own adapter before the pause; nothing inferred
            # about it, so nothing to verify beyond the ordinary quality gate.
            parsed.append(assembly.ParsedChannel(
                direction=plan["direction"], assumed=plan["assumed"],
                case=Case.model_validate(plan["case"]), kind=plan["kind"],
                conversion_note=plan.get("note", "")))
            continue
        recipe = ParseRecipe(**json.loads(plan["recipe"]))
        try:
            channel_parsed = _parse_one(job, channel, upload_form_dict, state,
                                        recipe_json=plan["recipe"])
        except ParseError as exc:
            unreadable.append(assembly.UnreadableChannel(plan["direction"], exc.safe_message))
            continue

        # MANDATORY verification gate, per file: an inferred layout is a
        # hypothesis, and this is where that file's own data gets to contradict
        # it. The gate reads the DATA, never the recipe.
        checks = verify_case(channel_parsed.case, rpm=rpm, tolerances=state.inference_cfg)
        failures = failed_checks(checks)
        if failures:
            verification_failures.extend(failures)
            rejected.append(channel_parsed)  # kept: the insufficient-data report needs a Case
            unreadable.append(assembly.UnreadableChannel(
                plan["direction"], failures[0].reason or "the interpreted layout did not fit the data"))
            continue
        for check in checks:
            if check.status == "warn" and check.reason:
                job.pending = job.pending or {}
                verification_warnings = job.pending.setdefault("warnings", [])
                if check.reason not in verification_warnings:
                    verification_warnings.append(check.reason)
        parsed.append(channel_parsed)
        endorsed.append((plan.get("fingerprint") or "", recipe))

    warnings = list((job.pending or {}).get("warnings", []))
    notes = [PROVENANCE_NOTE, *warnings]
    forced: list[Check] = []

    if not parsed:
        # Nothing survived. `error` stays reserved for uploads that could not be
        # READ at all; a file that parsed cleanly but whose interpretation the
        # data contradicted gets an insufficient-data REPORT naming what went
        # wrong -- never a diagnosis on a misread file. The rejected Case is
        # what that report is hung on (machine details, what was collected).
        if not rejected:
            mark_error(job, "; ".join(u.safe_message for u in unreadable) or COULD_NOT_INTERPRET,
                       code="interpretation_failed")
            job.pending = None
            _log_job_outcome(job)
            return
        parsed, unreadable, forced = [rejected[0]], [], verification_failures

    if form_dict.get("compare"):
        # An inferred layout on the compare lane: the recipes were confirmed and
        # verified above exactly as on any other upload, and the pair diverges
        # only here, where a merge would otherwise combine two TIMES into one
        # measurement.
        job.pending = None
        _run_compare(job, form_dict, parsed, unreadable, state, notes=notes)
        return

    try:
        outcome = assembly.merge_channels(
            parsed, unreadable,
            iso_table=state.iso_table, thresholds=state.thresholds, rules=state.rules,
            speed_tolerance_pct=state.webapp_cfg.get("speed_agreement_tolerance_pct", 5.0),
            client_history=parse_client_history(form_dict.get("history")),
        )
    except Exception:  # noqa: BLE001 -- a merge problem degrades, never a stuck job
        mark_error(job, "Could not combine the uploaded files — please check they are from the "
                        "same machine and measurement session.", code="merge_failed")
        job.pending = None
        _log_job_outcome(job)
        return

    # Only ever cache a recipe the data endorsed.
    if not forced:
        for fingerprint, recipe in endorsed:
            if fingerprint:
                state.cache_recipe(fingerprint, recipe)
                if form_dict.get("share_format"):
                    state.record_format_ping(fingerprint, recipe)

    job.kind = parsed[0].kind if len(parsed) == 1 else \
        "multiaxis(" + ",".join(p.kind for p in parsed) + ")"
    job.pending = None
    retain_trace = bool(form_dict.get("retain_trace")) and state.retain_traces_enabled
    drafting_available = True
    try:
        client = state.anthropic_client_factory()
    except Exception:  # noqa: BLE001 -- keyless host: degrade, never strand
        client = None
        drafting_available = False

    process_job(
        job,
        outcome.case,
        iso_table=state.iso_table,
        thresholds=state.thresholds,
        rules=state.rules,
        spend_guard=state.spend_guard,
        conversion_note=outcome.conversion_note,
        retain_trace=retain_trace,
        client=client,
        assembly_notes=[*notes, *outcome.report_notes],
        coverage_note=outcome.coverage_note,
        forced_fail_checks=[*forced, *outcome.fail_closed_checks],
        channel_summary=outcome.channel_summary,
        drafting_available=drafting_available,
    )
    _log_job_outcome(job)


def _process_job_sync(
    job: Job, form_dict: dict[str, Any],
    location_plans: list[dict[str, Any]], state: AppState
) -> None:
    """Session INTAKE-2 — one analysis per measurement location, in order.

    A single-location job calls `_process_first_location` exactly once, with the
    form dict the pre-INTAKE-2 product built, so everything about it is
    unchanged: same parse, same merge, same report, same bytes. That is not a
    happy accident, it is the reason the dispatcher is a wrapper rather than a
    rewrite of the body below.

    The other locations are analysed FIRST and their results hung on the job, so
    that by the time location 1 renders the document the note naming them is
    already true rather than a promise. Each is one point, which is exactly what
    `assembly.merge_channels` and `pipeline.run_analysis` have always taken --
    `assembly.py` is not opened by this session.
    """
    extra_plans = [plan for plan in location_plans if plan["index"] != 1]
    first_plan = next(plan for plan in location_plans if plan["index"] == 1)

    if extra_plans:
        # Session REPORTFIX-1. Each pass returns BOTH the wire entry (the
        # contract, published by GET /api/jobs/{id}) and the live objects that
        # produced it. They are kept apart deliberately: `job.locations` is what
        # the contract promises and nothing may be added to it, and
        # `job.location_docs` never leaves this process.
        analysed = [_analyse_extra_location(job, plan, state) for plan in extra_plans]
        job.locations = [entry for entry, _doc in analysed]
        job.location_docs = [doc for _entry, doc in analysed if doc is not None]

    _process_first_location(
        job, first_plan["form"], first_plan["channels"], state,
        # `other_locations` is still read -- it gates the schema-inference
        # fallback, which a multi-location job does not offer. `first_label` is
        # gone with the R-3 note that was its only reader; the roster takes
        # location 1's name from `case.machine.location`, the same field
        # `location_form_dict` substitutes for every other point, so all N
        # labels come from one source.
        other_locations=job.locations or [],
    )


def _other_locations_note(first_label: str, others: list[dict[str, Any]]) -> str:
    """One sentence naming the measurement locations this document does NOT cover.

    Session INTAKE-2, ruling R-3. It names each point and what was found there,
    because the alternative -- a report that covers one of four measured points
    and says nothing about it -- reads as a clean bill for the whole machine.
    The standing Part C rule calls that a fail however green the job looks, and
    it is the exact defect class Phase 7B found ("ISO Zone A" on known-faulted
    machines).

    Every number in it comes from that location's own analysis; nothing here
    summarises or re-judges. A location that could not be read says so.
    """
    parts: list[str] = []
    for entry in others:
        label = entry.get("label") or "an unnamed location"
        if entry.get("status") == "ok":
            zone = entry.get("iso_zone")
            rms = entry.get("severity_rms_mms")
            found = entry.get("committed_fault")
            # Session LIMITS-1c. The same question the health line asks, on
            # the same wire field: a point judged against a plant limit is not
            # in an "ISO Zone", and this sentence names every OTHER point on
            # the machine.
            custom = entry.get("zone_basis") == "custom"
            if zone:
                said = f"Zone {zone}" if custom else f"ISO Zone {zone}"
            else:
                said = "no zone" if custom else "no ISO zone"
            if rms is not None:
                said += f" at {rms:.2f} mm/s"
            if found:
                said += f", {found}"
            parts.append(f"{label} — {said}")
        elif entry.get("status") == "gate_fail":
            parts.append(f"{label} — data quality gate failed, not diagnosed")
        else:
            parts.append(f"{label} — {entry.get('message') or 'not analysed'}")
    return (
        f"**This report covers {first_label or 'the first measurement location'} only.** "
        f"{len(others)} further measurement location"
        f"{'' if len(others) == 1 else 's'} on this machine "
        f"{'was' if len(others) == 1 else 'were'} analysed in the same run: "
        + "; ".join(parts)
        + ". Their full sections are not in this document — read each alongside this one, "
        "and do not read this report as a verdict on the whole machine."
    )


def _analyse_extra_location(
    job: Job, plan: dict[str, Any], state: AppState
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Analyse one location other than the first: its wire entry, and its objects.

    Returns `(entry, doc)`. `entry` is `docs/contracts/machine_result.md` §3
    exactly — the shape the contract promises and the wire publishes. `doc` is
    Session REPORTFIX-1's addition: `{"label", "result", "case"}`, the live
    `AnalysisResult` and `Case` this pass already builds and used to discard on
    return, or None when there was no analysis to keep.

    The split is the point. The contract carries `committed_fault` as a bare
    model id — no frequency, no shaft order, no confidence — so a report drawn
    from the wire alone can name a fault but cannot show its evidence, and the
    report law forbids inventing the number. Widening the contract to carry
    that evidence would change what `GET /api/jobs/{id}` promises; handing the
    renderer the object in-process changes nothing anyone can observe.

    A location that cannot be read is RECORDED, not raised: three good points and
    one unreadable file is still three points of analysis an analyst should get,
    and the entry says which one was lost. The whole-job `error` state stays
    reserved for a job with nothing readable in it at all, which is the first
    location's business and unchanged below.
    """
    label = plan["label"]
    upload_form_dict = {k: v for k, v in plan["form"].items()
                        if k not in _NOT_UPLOAD_FORM_FIELDS}
    parsed: list[assembly.ParsedChannel] = []
    unreadable: list[assembly.UnreadableChannel] = []

    for index, channel in enumerate(plan["channels"]):
        try:
            # `keep_upload=False`: the schema-inference fallback is not available
            # to these locations (`locations_422` refuses the extensions that
            # always need it, and a CSV whose template parse fails is unreadable
            # here rather than a pause) -- so there is nothing to keep the raw
            # file for, and it is unlinked as soon as the parse returns.
            parsed.append(_parse_one(job, channel, upload_form_dict, state,
                                     keep_upload=False))
        except ParseError as exc:
            unreadable.append(assembly.UnreadableChannel(
                channel.direction,
                f"{_slot_label('', index, channel.direction)}: {exc.safe_message}",
            ))

    if not parsed:
        return {"label": label, "status": "unreadable",
                "message": "; ".join(u.safe_message for u in unreadable)
                           or "this location could not be read"}, None

    try:
        outcome = assembly.merge_channels(
            parsed, unreadable,
            iso_table=state.iso_table, thresholds=state.thresholds, rules=state.rules,
            speed_tolerance_pct=state.webapp_cfg.get("speed_agreement_tolerance_pct", 5.0),
            # No client history: a saved trend belongs to ONE machine and point,
            # and the browser posts the series for the point it is uploading.
            # Replaying the first location's history against every other point
            # would file one point's past under another's name -- the exact
            # confusion the per-location trend key exists to end.
            client_history=None,
        )
        probe = run_analysis(
            outcome.case,
            iso_table=state.iso_table, thresholds=state.thresholds, rules=state.rules,
        )
    except Exception:  # noqa: BLE001 -- one bad location must not lose the others
        _log.warning("location_failed job=%s", job.id)
        return {"label": label, "status": "error",
                "message": "this location could not be analysed"}, None

    gate = probe.quality_gate
    failed = gate.overall == "fail"
    entry: dict[str, Any] = {
        "label": label,
        "status": "gate_fail" if failed else "ok",
        "channels": [c.direction for c in plan["channels"]],
        "rpm": plan["form"].get("rpm"),
        "bearing": plan["form"].get("bearing_model") or (
            "geometry as entered" if plan["form"].get("bearing_n_balls") else None
        ),
    }
    # `status` GATES THE NUMBERS -- contract section 5 rule 1, and until Session
    # INTAKEFIX-1 this function did not obey it. A `gate_fail` entry carried
    # `iso_zone` (measured: "A"), `severity_rms_mms`, `trend_point` and
    # `committed_fault` off the probe, so a point the data-quality gate REFUSED
    # TO DIAGNOSE reached the wire as a clean bill. That is the Phase-7B defect
    # the standing Part C rule is written from -- "ISO Zone A" on a machine
    # nobody assessed -- and it had two live consumers: `report/generate.py`
    # ignores them (it gates on `_loc_ok`), but `app.js::saveOtherLocationReadings`
    # did not, so the analyst's browser filed an undiagnosed point onto the
    # machine's trend. Withheld HERE, at the producer, because that is where the
    # contract says it belongs and because a consumer-side fix leaves the untrue
    # value on the wire for the next consumer.
    if not failed:
        entry["trend_point"] = _trend_point(probe)
        entry["committed_fault"] = _committed_fault(probe)
        if probe.iso is not None:
            entry["iso_zone"] = probe.iso.iso_zone
            entry["severity_rms_mms"] = probe.iso.severity_rms
            # Session LIMITS-1c — SESSION_LIMITS1B.md F-2, whose owner this
            # session is. `iso_zone` above is the zone letter, and until now the
            # wire said nothing about WHOSE scale produced it, so every consumer
            # had to assume ISO 20816-3. That assumption was harmless only while
            # nothing in the product could set a custom limit; item 2 ends that.
            #
            # `report/generate.py::_sheet_health` is the consumer that made it
            # visible: a route whose WORST point is an extra location builds
            # page 1's health line from these scalars, and hardcoded
            # "per ISO 20816-3" — so a machine judged against a plant limit read
            # "ISO Zone B ... per ISO 20816-3" immediately before "Judged against
            # machine-specific limits". One page, two authorities, one of them
            # untrue.
            entry["zone_basis"] = probe.iso.zone_basis
            entry["iso_zone_would_be"] = probe.iso.iso_zone_would_be
    if failed:
        # `Check.reason`, not `.detail` — there is no such attribute, and this
        # line raised `AttributeError` for every extra location whose gate
        # failed. It is OUTSIDE the try/except above (which wraps the merge and
        # the analysis), so it reached `_run_guarded` and turned the WHOLE job
        # into `internal_error`: three good points thrown away because a fourth
        # had no sample rate. That is the exact opposite of this function's own
        # promise four paragraphs up — "a location that cannot be read is
        # RECORDED, not raised". Found by Session INTAKEFIX-1's item-1 gate pin,
        # which is the first test in the tree ever to fail an EXTRA location's
        # quality gate. `and c.reason` because the field is `str | None`, which
        # is what `worker._gate_summary` and `assembly.py:483` already do.
        entry["gate_reasons"] = [c.reason for c in gate.checks
                                 if c.status == "fail" and c.reason]
    return entry, {"label": label, "result": probe, "case": outcome.case}


def _process_first_location(
    job: Job, form_dict: dict[str, Any], channels: list["assembly.ChannelUpload"],
    state: AppState, *, other_locations: list[dict[str, Any]] | None = None,
) -> None:
    """Runs entirely off the event loop (invoked via asyncio.to_thread): a
    sandboxed parse PER channel, then assembly into one Case, then
    pipeline/draft/PDF via worker.process_job.

    This is the pass that renders the document and reaches the terminal
    transition. Session INTAKE-2 renamed it from `_process_job_sync` and added
    the two keyword arguments; its body is otherwise what it was, which is what
    keeps a one-location job byte-identical.

    Session G2 -- ONE routing rule for both the inference lane and the tabular
    fallback: every channel is offered to its own adapter first, and only the
    ones that have no adapter (.txt/.dat/.asc) or whose adapter FAILED
    (.csv/.xlsx) go to schema inference. If no channel needed inference, this
    function behaves exactly as it did before, pause included -- there isn't
    one. If any did, the whole job pauses on one combined confirm card, where a
    template-parsed channel is shown as read with our template.
    """
    upload_form_dict = {k: v for k, v in form_dict.items()
                        if k not in _NOT_UPLOAD_FORM_FIELDS}
    compare_mode = bool(form_dict.get("compare"))
    parsed: list[assembly.ParsedChannel] = []
    unreadable: list[assembly.UnreadableChannel] = []
    plans: list[dict[str, Any]] = []
    needs_inference = False

    for index, channel in enumerate(channels):
        suffix = channel.path.suffix.lower()
        label = _slot_label("compare" if compare_mode else "", index, channel.direction)
        plan = {"slot": channel.slot, "path": str(channel.path), "direction": channel.direction,
                "assumed": channel.assumed, "label": label,
                "source": "inference", "status": "pending", "recipe": None, "message": None,
                "fingerprint": None, "case": None, "kind": None, "note": ""}
        if suffix in INFERRED_EXTENSIONS:
            needs_inference = True
            plans.append(plan)
            continue

        # Session INTAKE-2. The schema-inference fallback is available only when
        # this is the ONLY location. The confirm-and-resume pause is a
        # conversation about one point; resuming it into a machine whose other
        # locations were already analysed is the half-wired shape HIST-1 warns
        # about, and `locations_422` already refuses the extensions that always
        # need it. With other locations present an unreadable CSV stays
        # unreadable, and the message already names the file and the direction.
        fallback_possible = suffix in FALLBACK_EXTENSIONS and not other_locations
        try:
            channel_parsed = _parse_one(job, channel, upload_form_dict, state,
                                        keep_upload=fallback_possible)
        except ParseError as exc:
            if fallback_possible:
                # The template could not read it. That is the fallback lane's
                # whole reason to exist -- read it by inference instead of
                # handing back an error card.
                needs_inference = True
                plans.append(plan)
                continue
            message = f"{label}: {exc.safe_message}" if compare_mode else exc.safe_message
            unreadable.append(assembly.UnreadableChannel(channel.direction, message))
            continue
        parsed.append(channel_parsed)
        plan.update(source="template", status="ok",
                    case=json.loads(channel_parsed.case.model_dump_json()),
                    kind=channel_parsed.kind, note=channel_parsed.conversion_note)
        plans.append(plan)

    if needs_inference:
        _infer_and_pause(job, form_dict, plans, state)
        return

    if not parsed:
        # `error` is reserved for unparseable uploads. Single-file: today's exact
        # message. Multi-file: name every channel that failed.
        if len(channels) == 1:
            mark_error(job, unreadable[0].safe_message, code="upload_unreadable")
        else:
            mark_error(job, "None of the uploaded files could be read. " + "; ".join(
                f"{assembly.DIRECTION_LABELS[u.direction]}: {u.safe_message}" for u in unreadable),
                code="upload_unreadable")
        _log_job_outcome(job)
        return

    if compare_mode:
        _run_compare(job, form_dict, parsed, unreadable, state)
        return

    try:
        outcome = assembly.merge_channels(
            parsed, unreadable,
            iso_table=state.iso_table, thresholds=state.thresholds, rules=state.rules,
            speed_tolerance_pct=state.webapp_cfg.get("speed_agreement_tolerance_pct", 5.0),
            client_history=parse_client_history(form_dict.get("history")),
        )
    except Exception:  # noqa: BLE001 -- a merge problem must degrade to an error, not a stuck job
        mark_error(job, "Could not combine the uploaded channels — please check the files match the schema.",
                   code="merge_failed")
        _log_job_outcome(job)
        return

    job.kind = parsed[0].kind if len(parsed) == 1 else "multiaxis(" + ",".join(p.kind for p in parsed) + ")"
    # Session REPORTFIX-1 — INTAKE-2's ruling-R-3 note is GONE from here, which
    # is what its own F-5 asked for. It existed because the numbers arrived a
    # round before the document could carry them: it named each other point in
    # one sentence through the notes channel, so a report covering one of four
    # measured points did not read as a clean bill for the machine.
    #
    # The document carries them now. Page 1 is machine-level -- the worst
    # point's zone with that point NAMED, one conclusion, the fault named once
    # with every position -- and Evidence runs per location in roster order. The
    # note would now repeat, less well, what the document itself says.
    #
    # `_other_locations_note` is deliberately still defined below: it is a pure
    # function over the contract's own fields, it is covered by tests this
    # session does not own, and nothing is served by deleting a tested function
    # in the same breath as the call site.
    report_notes = list(outcome.report_notes)
    retain_trace = bool(form_dict.get("retain_trace")) and state.retain_traces_enabled
    # Constructing the client can itself fail (ANTHROPIC_API_KEY unset on the
    # host is the ordinary case). That is "the LLM path is unavailable", which
    # the contract says degrades to the deterministic report -- so it must be
    # caught HERE: an exception escaping this function would kill the background
    # task and leave the job stuck in `running` until the TTL sweep. `client`
    # stays None so nothing can silently construct a real, key-bearing client
    # further down.
    drafting_available = True
    try:
        client = state.anthropic_client_factory()
    except Exception:  # noqa: BLE001 -- any client-construction failure degrades
        client = None
        drafting_available = False

    process_job(
        job,
        outcome.case,
        iso_table=state.iso_table,
        thresholds=state.thresholds,
        rules=state.rules,
        spend_guard=state.spend_guard,
        conversion_note=outcome.conversion_note,
        retain_trace=retain_trace,
        client=client,
        assembly_notes=report_notes,
        coverage_note=outcome.coverage_note,
        forced_fail_checks=outcome.fail_closed_checks,
        channel_summary=outcome.channel_summary,
        drafting_available=drafting_available,
    )
    state.record_job_duration(job.duration_ms)
    _log_job_outcome(job)


# ── S7: the terminal-state guarantee ─────────────────────────────────────────
# `worker.process_job` is already fully guarded and genuinely never raises. The
# hole was in the two layers ABOVE it: neither wrapper below had a try/except,
# and both call sites discarded the task, so an exception anywhere upstream of
# process_job -- seven concrete sites, six of them reachable, three of them AFTER
# the analyst had confirmed an interpretation -- left the job sitting in
# `queued`/`running` until the TTL sweep. Measured: 31 of 31 samples over 15 s
# on a path whose complete run is 6.2 s, `report.pdf` answering "still being
# prepared" for something that was never coming, and not one line in the
# operator's log that could be joined to the analyst asking about it
# (eval_s7/transcripts/states_raises.json).


async def _run_guarded(state: AppState, job: Job, fn: Callable[..., Any], *args: Any) -> None:
    """Run one job's synchronous work off the loop, and let NOTHING escape.

    Three details, each of which cost something to learn:

      * `CancelledError` is re-raised. The max-runtime sweep cancels this task
        and then marks the job `error(timeout)` itself; swallowing the
        cancellation here would mark it `internal_error` first instead, and the
        sticky-state rule would make that wrong answer the final one.
      * `BaseException`, not `Exception`. The whole point is that nothing
        escapes; a MemoryError or a KeyboardInterrupt in a worker thread must
        still leave the analyst with a terminal state.
      * the failure is NOT re-raised after marking. The job is already marked
        and already logged; re-raising only adds asyncio's
        "Task exception was never retrieved" record, which carries none of the
        fields that make a failure supportable.

    And one from acceptance (S7-ACCEPT F-3): a job that ends in `error` has its
    FILES purged here, because no other path ever would. `_keep_only_report`
    runs only at successful completion, so before this line a rejected upload
    kept the analyst's raw file on disk for the full TTL -- against /privacy's
    "deleted the moment analysis finished". Purging here is safe precisely
    because this point is sequential with the work: the thread has returned (or
    raised), so no writer holds the directory. The timeout-strand path
    deliberately does NOT purge -- its abandoned thread may still be writing --
    which is the ruling's stated cost; that directory is reclaimed by the sweep
    one TTL after the job went terminal. The registry ENTRY survives either
    way, so the error card stays queryable.
    """
    try:
        await asyncio.to_thread(fn, *args)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 -- nothing may escape a background job
        _log_internal_failure(job, exc)
        mark_error(job, _INTERNAL_ERROR_MESSAGE, code="internal_error")
        _log_job_outcome(job)
    if job.state == "error":
        state.registry.purge_job(job)


def _spawn(state: AppState, job_id: str, coro: Any) -> "asyncio.Task[None]":
    """Create a background task, HOLD a reference to it, and retrieve its
    exception when it finishes. Three separate jobs, easy to conflate:

      * holding the reference stops the loop garbage-collecting a pending task
        mid-flight -- asyncio keeps only a weak one;
      * keying it by job id is what lets the max-runtime sweep cancel the RIGHT
        worker before it marks a job `error`;
      * retrieving the exception in the done-callback is what stops asyncio's
        `Task exception was never retrieved` record. That record was, before
        S7, the only trace a crashed job left: no job id, no code label, no
        kind, no duration, and on the `asyncio` logger rather than ours, so it
        could not be joined to the analyst who was asking. The useful record is
        now `_log_job_outcome`'s.
    """
    task = asyncio.create_task(coro)
    state.background_tasks[job_id] = task

    def _reap(finished: "asyncio.Task[None]") -> None:
        if state.background_tasks.get(job_id) is finished:
            state.background_tasks.pop(job_id, None)
        if not finished.cancelled():
            finished.exception()

    task.add_done_callback(_reap)
    return task


async def _resume_confirmed_background(job_id: str, state: AppState) -> None:
    job = state.registry.get(job_id)
    if job is None:
        return
    async with state.worker_semaphore:
        # Re-stamped, not preserved: the pause waiting for the analyst may have
        # been long, and it is not runtime.
        job.worker_started_at = time.monotonic()
        await _run_guarded(state, job, _resume_confirmed, job, state)


async def _process_job_background(
    job_id: str, form_dict: dict[str, Any],
    location_plans: list[dict[str, Any]], state: AppState
) -> None:
    job = state.registry.get(job_id)
    if job is None:
        return
    async with state.worker_semaphore:
        # The max-runtime clock starts HERE -- after the semaphore, so a job that
        # merely waited behind a full queue is never killed for someone else's
        # slowness. Deliberately not `started_at`, which anchors `duration_ms`.
        job.worker_started_at = time.monotonic()
        await _run_guarded(state, job, _process_job_sync, job, form_dict,
                           location_plans, state)


def create_app(
    *,
    webapp_cfg: dict[str, Any] | None = None,
    invite_codes: dict[str, str] | None = None,
    contact_email: str | None = None,
    retain_traces_enabled: bool | None = None,
    anthropic_client_factory: Callable[[], Any] | None = None,
    base_tmp: Path | None = None,
    app_env: str | None = None,
) -> FastAPI:
    """Build the FastAPI app. All arguments default to reading the real
    config/env; tests override them to get an isolated, fast, offline app.

    """
    # Copy so env overrides never mutate the cached config dict.
    webapp_cfg = dict(webapp_cfg or load_config("webapp"))
    for env_key, cfg_key in (
        ("IP_REQUESTS_PER_MINUTE", "ip_requests_per_minute"),
        ("IP_JOB_POSTS_PER_HOUR", "ip_job_posts_per_hour"),
        ("DISK_MIN_FREE_MB", "disk_min_free_mb"),
    ):
        raw = os.environ.get(env_key)
        if raw:
            webapp_cfg[cfg_key] = int(raw)
    app_env = (app_env or os.environ.get("APP_ENV", "dev")).lower()
    invite_codes = invite_codes if invite_codes is not None else parse_invite_codes(os.environ.get("INVITE_CODES"))
    # POSITIONING §7 bans "any placeholder domain or number", and LEGAL-1 §7.8
    # said why this one had become urgent: /terms renders the contact address in
    # the same breath as a refund promise and an accountability designation, so
    # an unset CONTACT_EMAIL printed `support@example.com` next to a commitment
    # somebody is meant to be able to hold us to. Env still wins -- this is the
    # value a host that forgot to set it falls back to, and it is now an address
    # that receives mail.
    contact_email = contact_email or os.environ.get("CONTACT_EMAIL",
                                                    "contact@example.com")
    retain_traces_enabled = (
        retain_traces_enabled
        if retain_traces_enabled is not None
        else os.environ.get("RETAIN_TRACES", "").lower() == "true"
    )
    anthropic_client_factory = anthropic_client_factory or (lambda: anthropic.Anthropic())

    # Session SEC-2. Both resolved HERE rather than per request, for the reason
    # `require_sender` is resolved here: a page describing a third party this
    # process cannot reach, or a year-long HSTS commitment, must follow the
    # configuration the service actually started with.
    external_services = enabled_external_services(os.environ, store_backend="browser")
    hsts_on = hsts_enabled(os.environ)

    state = AppState(
        webapp_cfg=webapp_cfg,
        iso_table=load_config("iso_zones")["zones"],
        # Explicit, never load_thresholds()'s active_profile default: uploads are
        # third-party/route-collected data and must be scored on the 'route' profile.
        # The default resolved to 'streaming' (the NCD sensor pipeline's frozen
        # profile), which silently disabled every route-calibrated detector guard in
        # the shipped product until RUN v5 measured it.
        thresholds=load_thresholds(webapp_cfg["analysis_profile"]),
        rules=load_config("next_measurements"),
        bearings_cfg=load_config("bearings"),
        cwru_cfg=load_config("cwru"),
        mfpt_cfg=load_config("mfpt"),
        invite_codes=invite_codes,
        contact_email=contact_email,
        retain_traces_enabled=retain_traces_enabled,
        anthropic_client_factory=anthropic_client_factory,
        base_tmp=base_tmp,
    )

    sweep_interval_s = float(webapp_cfg.get("sweep_interval_s", 60))

    # How long the sweep waits for a cancelled worker task to unwind before it
    # marks the job. The task is blocked in `asyncio.to_thread`, so cancellation
    # lands on the await and completes essentially at once; the bound exists so a
    # pathological case cannot stall the sweep loop.
    cancel_grace_s = 5.0

    async def _strand_job(job: Job) -> None:
        """A job that stopped without finishing, made terminal — in this order.

        **Cancel the worker task FIRST, then mark.** Not cosmetic: S7E measured
        the other order. The sweeper marked a stranded job, its still-live worker
        then finished, overwrote the state with `degraded`, recreated the very
        directory the sweeper had deleted and wrote `report.pdf` into it — a
        report that existed after the analyst had been told the job failed, in a
        directory no registry entry pointed at any more, invisible to the disk
        guard forever (eval_s7/transcripts/dep4b_sweep.json, part 2c).

        Cancelling frees the worker-semaphore slot at once. The abandoned THREAD
        cannot be interrupted and runs on — `asyncio.to_thread` has no way to
        stop it — so transiently there can be more threads than
        `worker_concurrency`. That is why the terminal state is sticky
        (`jobs.Job.__setattr__`) and the outcome line fires once
        (`_log_job_outcome`): the thread may still finish, but it can no longer
        change the answer or double-count it.

        The job's FILES are not touched, per the ruling. It is the only option of
        the three that makes this code obey the invariant it is here to enforce
        — "a running job's dir must never be deleted out from under the worker"
        — and the cost is bounded: `mark_error` re-anchors the TTL, so a LATER
        sweep reclaims the directory once the job is terminal, one TTL late.

        **Session FLIP-1 gives this function a second lane**: an expired
        `awaiting_confirm` job. It differs in three ways, all of them because
        that job has no worker running: there is no live task to cancel (the
        inference pass returned before the pause, so `task.done()` is already
        true), its files were already purged by the sweep that handed it over,
        and it says a different sentence. Everything else — one claim, one
        outcome line — is the path that was already here, which is the whole
        reason the fix is this small.
        """
        # Read BEFORE `mark_error` flips it. Session FLIP-1: an expired
        # `awaiting_confirm` job now arrives here too (see
        # `jobs.JobRegistry.sweep_expired`), and it is the one lane on which
        # `_TIMEOUT_MESSAGE`'s "this was on our side" would be false.
        abandoned_confirm = job.state == "awaiting_confirm"
        task = state.background_tasks.pop(job.id, None)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=cancel_grace_s)
        message = _ABANDONED_CONFIRM_MESSAGE if abandoned_confirm else _TIMEOUT_MESSAGE
        if mark_error(job, message, code="timeout"):
            if abandoned_confirm:
                # A SECOND LINE, not a field on the outcome line, and not a
                # ninth taxonomy code. Both lanes log `fail=timeout` because
                # both ARE that category to an analyst — but they are different
                # events to the operator: one is an availability problem worth
                # paging about, the other is a distracted user and worth nothing.
                # `_log_internal_failure` makes exactly this move for exactly
                # this reason: the per-job outcome line stays the clean, PII-free
                # record and the diagnosis goes beside it.
                #
                # `job_id=`, NOT `job=`, and that is not a style choice.
                # `scripts/beta_digest.py::parse_log` counts every line
                # containing " job=" that does not match the full per-job format
                # as UNPARSED -- deliberately, because "a silent zero there would
                # hide a format change". So a `job=` prefix here would raise the
                # operator's format-change alarm once per abandoned confirm card.
                # Measured both spellings against the real parser: `job=` scores
                # 1 unparsed, `job_id=` scores 0, like the `ttl_sweep` line
                # beside it. (`_log_internal_failure` already scores 1 and has
                # since S7 -- outputs/SESSION_FLIP1.md F-6; `scripts/` was not
                # this session's to fix.)
                _log.info("confirm_abandoned job_id=%s code=%s", job.id, job.code_label)
            _log_job_outcome(job)

    async def _sweep_once() -> None:
        removed, stranded = state.registry.sweep_expired()
        if removed:
            # Counts, not ids: an ordinary expiry is per-job data with no
            # operator value. A STRANDED job gets its own outcome line below,
            # because that one is a failure and needs to be greppable.
            _log.info("ttl_sweep expired=%d", len(removed))
        for job in stranded:
            await _strand_job(job)

    async def _ttl_sweep_loop() -> None:
        while True:
            await asyncio.sleep(sweep_interval_s)
            try:
                await _sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- the sweeper must outlive one bad pass
                _log.warning("ttl_sweep failed exc=%s", type(exc).__qualname__)

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        _log.info("vib-agent webapp starting mode=%s version=%s", app_env, __version__)
        sweep_task = asyncio.create_task(_ttl_sweep_loop())
        try:
            yield
        finally:
            sweep_task.cancel()

    # openapi_url=None (docs/redoc already off): fully close the schema in every
    # mode — nothing consumes it, and it needn't advertise the surface.
    app = FastAPI(
        title="Vibration Report Drafting",
        docs_url=None, redoc_url=None, openapi_url=None,
        lifespan=_lifespan,
    )
    app.state.vib = state
    # One pass of exactly what the loop above runs every `sweep_interval_s`, so a
    # test can drive the real thing instead of a re-implementation of it, and
    # never waits a minute to do so. (The eval_s7 runners deliberately still call
    # `registry.sweep_expired()` directly -- they are the pre-fix instrument and
    # were left byte-unchanged, which is why part 2 of the DEP-4b reproduction
    # shows the file-preservation half of the fix but not the cancel-and-mark
    # half. That half is pinned in tests/test_terminal_guarantee.py.)
    app.state.sweep_once = _sweep_once

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
        """FastAPI's own 422, rewritten so the browser can show it.

        Session INTAKE-2, and this is HALF OF PARTC F-8. FastAPI's default
        handler answers a validation failure with `{"detail": [ {...}, ... ]}` --
        a LIST of error objects naming `body -> machine_type` and a pydantic
        message. `app.js`'s error branch reads
        `if (res.status >= 500 || typeof detail !== 'string')` and sends
        anything that is not a string to a generic transient card, so the
        server's own account of what was wrong with the request was discarded
        before it could be rendered. Every hand-written refusal in this file
        raises `HTTPException(detail="<one sentence>")`; only the framework's
        differed, and only on the shape the browser cannot use.

        So: one sentence, as a string, the same shape as every other refusal
        here. It deliberately does NOT enumerate the offending fields -- the
        `_*_422` helpers own the analyst-facing wording for every field an
        analyst can actually see, and a message naming `body -> wav_sensitivity`
        sends them looking for a control under a name the form does not use. A
        request that reaches HERE failed FastAPI's own type coercion, which for
        this form means a hand-built post or a browser that sent a number field
        as a word; the operator's detail is in the log, not on the wire.

        No new `ERROR_TAXONOMY` row (wire law #5): this is a request refusal, not
        a job, so it has no `failure_kind` and produces no job to classify --
        the same reasoning EMAIL-1 recorded for its two exception categories.
        """
        _log.warning(
            "request_validation_failed path=%s errors=%d",
            request.url.path, len(exc.errors()),
        )
        return JSONResponse(
            {"detail": (
                "Some of the details on this form could not be read. Check the "
                "numeric fields — speed, rated power, and anything under More "
                "options — then submit again."
            )},
            status_code=422,
        )

    def _rate_limited(request: Request, retry_after: int) -> Response:
        # Every 429 says WHEN to come back. A client that has to guess either
        # gives up or hammers; neither is what we want, and the limiter already
        # knows the answer.
        wait = f"{retry_after} second{'' if retry_after == 1 else 's'}"
        msg = f"Too many requests — please wait {wait} and try again."
        headers = {"Retry-After": str(retry_after)}
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": msg}, status_code=429, headers=headers)
        return HTMLResponse(
            _report_notice_page("Too many requests.", msg, state.contact_email),
            status_code=429, headers=headers,
        )

    @app.middleware("http")
    async def _harden(request: Request, call_next):
        """Per-IP rate limiting (a second layer above the per-code caps) +
        security headers + no-store on the sensitive routes. Never logs the IP."""
        path = request.url.path
        response: Response | None = None
        if path != "/healthz":
            ip = client_ip(request)
            if is_status_request(request.method, path):
                # A browser polling its own job spends from the STATUS budget,
                # not the general one: following our own protocol must never
                # rate-limit a client out of its own analysis (hotfix-net).
                if not state.ip_limiter.check_status_request(ip):
                    response = _rate_limited(request, state.ip_limiter.retry_after(ip, kind="status"))
            elif not state.ip_limiter.check_request(ip):
                response = _rate_limited(request, state.ip_limiter.retry_after(ip))
            elif request.method == "POST" and path == "/api/jobs" and not state.ip_limiter.check_job_post(ip):
                response = _rate_limited(request, state.ip_limiter.retry_after(ip, kind="job"))
        if response is None:
            response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if hsts_on and request_is_https(request):
            # Opt-in and TLS-only (SEC-2). A browser ignores this header on a
            # plaintext response, and it cannot be withdrawn once seen.
            response.headers.setdefault("Strict-Transport-Security", HSTS_HEADER)
        if any(path.startswith(prefix) for prefix in NO_STORE_PREFIXES):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        # Liveness + minimal, non-sensitive status. Rate-limit-exempt.
        return {"status": "ok", "version": __version__, "queue_depth": state.pending_job_count()}

    @app.post("/api/jobs", status_code=202)
    async def create_job(
        request: Request,
        file: UploadFile = File(...),
        file_2: UploadFile | None = File(None),
        file_3: UploadFile | None = File(None),
        direction: str | None = Form(None),
        direction_2: str | None = Form(None),
        direction_3: str | None = Form(None),
        # The invite-code gate. A MISSING one answers 401 rather than 422 --
        # the same answer a wrong code already got, and the answer
        # `resolve_code_label` has always specified for None.
        invite_code: str | None = Form(None),
        # `db` mode only: the double-submit CSRF value the page was rendered
        # with. Ignored on `browser`, where there is no session to protect.
        csrf: str | None = Form(None),
        machine_alias: str = Form(...),
        rpm: float = Form(...),
        iso_group: str = Form(...),
        iso_support: str = Form(...),
        # Session INTAKE-2 — what kind of machine this is (PARTC F-2) and the
        # rated spec the ISO group is derived from.
        #
        # `machine_type` is `Form(None)` and not `Form(...)` deliberately: a
        # missing field must reach `_machine_422`'s sentence written for an
        # analyst, not FastAPI's own validation error. That distinction is the
        # whole of item 7 -- see `_validation_error_handler`.
        #
        # `iso_group` / `iso_support` stay REQUIRED above and are re-derived
        # from `rated_kw` + `mounting` below, with the derivation as the
        # authority. The form posts what it computed; the server does not trust
        # it, and a disagreement is a 422 rather than a silent preference.
        machine_type: str | None = Form(None),
        rated_kw: float | None = Form(None),
        driven_rpm: float | None = Form(None),
        # Session LIMITS-1c -- the machine's OWN severity limits (LIMITS-1a's
        # `MachineThresholds`, pdm_core tier 1). `Form(None)` and not
        # `Form(...)`: an empty number input posts "" and FastAPI reads that as
        # None for an optional float, so all three blank is the ISO path and
        # byte-identical to before. Required would make a blank trio a 422 --
        # INTAKEFIX-1 F-3 is that exact mechanism, measured on iso_group.
        limit_ab: float | None = Form(None),
        limit_bc: float | None = Form(None),
        limit_cd: float | None = Form(None),
        mounting: str | None = Form(None),
        bearing_model: str | None = Form(None),
        # Session GEOM-1 — the bearing itself, for the route bearing the
        # catalogue does not hold (STRANGER B5). Mutually exclusive with
        # `bearing_model` above and refused together by `_geometry_422`. Empty
        # number inputs post "" and FastAPI reads that as None, exactly as it
        # already does for the acquisition and geometry blocks.
        bearing_n_balls: int | None = Form(None),
        bearing_ball_dia_mm: float | None = Form(None),
        bearing_pitch_dia_mm: float | None = Form(None),
        bearing_contact_angle_deg: float | None = Form(None),
        velocity_unit: str = Form("mm_s"),
        detection_type: str = Form("rms"),
        mode: str = Form("spectrum"),
        wav_sensitivity: float | None = Form(None),
        sensor_sensitivity_mv_per_g: float | None = Form(None),
        fmax_hz: float | None = Form(None),
        spectral_lines: int | None = Form(None),
        window_type: str | None = Form(None),
        averages: int | None = Form(None),
        integration: str | None = Form(None),
        # Session GEOM-A — declared machine geometry, all optional. An empty
        # numeric input posts "" and FastAPI reads that as None, exactly as it
        # already does for the acquisition block above.
        measurement_location: str | None = Form(None),
        coupling: str | None = Form(None),
        blades: int | None = Form(None),
        gear_teeth_driving: int | None = Form(None),
        gear_teeth_driven: int | None = Form(None),
        rotor_bars: int | None = Form(None),
        poles: int | None = Form(None),
        line_freq_hz: float | None = Form(None),
        drive_type: str | None = Form(None),
        drive_pulley_mm: float | None = Form(None),
        driven_pulley_mm: float | None = Form(None),
        pulley_center_distance_mm: float | None = Form(None),
        # Session HIST-1 — the analyst's browser-held trend points for this
        # machine + measurement point, as a JSON list of {ts, value}. Optional,
        # and absent on every first upload. Nothing is stored server-side: the
        # points arrive with the request, become Case.history for the length of
        # one analysis, and go with the rest of the job.
        history: str | None = Form(None),
        retain_trace: bool = Form(False),
    ) -> dict[str, str]:
        # ── who is asking ────────────────────────────────────────────────────
        # Two backends, two gates, and the SAME variable out of both: the daily
        # counter, the refund hook and the per-job log line all key on
        # `code_label` and none of them has to know which world it is in.
        try:
            code_label = resolve_code_label(state.invite_codes, invite_code)
        except InviteCodeError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        # NOTE (hotfix-net): the daily counter is NOT spent here. A rejected
        # attempt -- bad code, oversize file, unsupported format, a direction
        # mix-up -- costs the analyst nothing, because it cost us nothing: no
        # parse, no model call, no job. The counter is incremented at the bottom
        # of this handler, once the request is actually accepted. What stays
        # pre-validation is the per-IP POST bucket, which is anti-abuse and must
        # charge for the attempt itself.

        pending = state.pending_job_count()
        if pending >= state.webapp_cfg["queue_depth_max"]:
            state.log_guard_refusal(
                "queue_guard", f"pending={pending} max={state.webapp_cfg['queue_depth_max']}",
                code_label)
            raise HTTPException(
                status_code=503, detail="Server is busy — every worker is occupied right now.",
                headers={"Retry-After": str(state.queue_retry_after(pending))})

        # v6-A disk guard: refuse new work when the job scratch volume is low,
        # before reading the upload into memory or spawning a parse.
        free_bytes = shutil.disk_usage(str(state.tmp_path)).free
        if free_bytes < state.disk_min_free_mb * 1024 * 1024:
            state.log_guard_refusal(
                "disk_guard",
                f"free_mb={free_bytes // (1024 * 1024)} min_mb={state.disk_min_free_mb}",
                code_label)
            raise HTTPException(status_code=503, detail=_STORAGE_FULL_MESSAGE,
                                headers={"Retry-After": str(state.disk_retry_after())})

        # ── Session E: collect the MEASUREMENTS slots (drop empty file inputs) ──
        raw_slots = [(file, direction), (file_2, direction_2), (file_3, direction_3)]
        slots = [(f, d) for (f, d) in raw_slots if _slot_present(f)]
        # `file` is required (File(...)), so at least slot 1 is present.

        # ── Session INTAKE-2: the other measurement locations ────────────────
        # Read off the form FastAPI already parsed, by name, never by position --
        # `webapp/locations.py`'s docstring has the reason, and it is that a
        # positional pairing can desynchronise when the browser drops an empty
        # file part, which would analyse one point's file against another
        # point's bearing and speed.
        #
        # BEFORE `_resolve_directions`, deliberately. Both refuse a
        # multi-location comparison, and this one says why in the language of
        # what the analyst did ("a comparison is two readings of one measurement
        # location") where `_resolve_directions` can only say the file count is
        # wrong -- which, to someone who uploaded four files across two points,
        # reads as a bug in our arithmetic.
        raw_form = await request.form()
        extra_locations = locations.collect_extra_locations(raw_form)
        problem = locations.locations_422(
            extra_locations,
            mode=mode,
            compare=(mode == "compare"),
            inferred_extensions=INFERRED_EXTENSIONS,
            overflow=locations.overflow_indices(raw_form),
            first_label=(measurement_location or "").strip() or None,
        )
        if problem is not None:
            raise HTTPException(status_code=422, detail=problem)

        directions, assumed = _resolve_directions(slots, mode)

        # `mode` is overloaded: to the ADAPTERS it names a CSV/XLSX schema
        # (spectrum / trend / mafaulda), and `compare` is not one of those — it
        # is an analysis mode over two spectrum files. So the adapters are told
        # `spectrum`, exactly as they would be for either file uploaded alone,
        # and the comparison lives entirely in this layer. `UploadForm` and
        # `UploadMode` are untouched, and no adapter learns a new word.
        compare_mode = mode == "compare"

        # Session G2: 2-3 text/tabular files are supported — each gets its own
        # recipe and its own line on ONE combined confirm card.

        # Per-file validate + read (single-file keeps today's exact 4xx strings).
        contents: list[bytes] = []
        multi = len(slots) > 1
        for i, (f, _) in enumerate(slots):
            content = await f.read()
            label = _slot_label(mode, i, directions[i])
            try:
                validate_upload(
                    f.filename or "", content,
                    allowed_extensions=tuple(state.webapp_cfg["allowed_extensions"]),
                    max_bytes=state.webapp_cfg["max_upload_bytes"],
                )
            except ValueError as exc:
                message = str(exc)
                if "exceeds max size" in message:
                    detail = f"The {label} file {message}" if multi else message
                    raise HTTPException(status_code=413, detail=detail) from exc
                funnel = _FUNNEL_MESSAGE.format(contact_email=state.contact_email)
                detail = f"The {label} file: {funnel}" if multi else funnel
                raise HTTPException(status_code=415, detail=detail) from exc
            contents.append(content)

        # Session INTAKE-2. The other locations' files, read and validated the
        # same way and with the same three refusals (413 / 415 / duplicate
        # direction). Done HERE, before the job is created and before the
        # allowance is charged, so a bad file at location 4 costs nothing --
        # the same rule every other check in this handler follows.
        extra_contents: dict[int, list[bytes]] = {}
        extra_directions: dict[int, tuple[list[str], bool]] = {}
        for loc in extra_locations:
            try:
                loc_dirs, loc_assumed = _resolve_directions(loc.slots, mode)
            except HTTPException as exc:
                # `_resolve_directions` raises the analyst-facing 400s (a missing
                # direction on a multi-file post, an unknown word, a duplicate).
                # Re-raised naming the point, because "two files declare the same
                # direction" is unactionable when four locations are open.
                raise HTTPException(
                    status_code=exc.status_code, detail=f"{loc.label}: {exc.detail}"
                ) from exc
            extra_directions[loc.index] = (loc_dirs, loc_assumed)
            read: list[bytes] = []
            for i, (upload, _direction) in enumerate(loc.slots):
                content = await upload.read()
                slot_label = _slot_label(mode, i, loc_dirs[i])
                try:
                    validate_upload(
                        upload.filename or "", content,
                        allowed_extensions=tuple(state.webapp_cfg["allowed_extensions"]),
                        max_bytes=state.webapp_cfg["max_upload_bytes"],
                    )
                except ValueError as exc:
                    message = str(exc)
                    if "exceeds max size" in message:
                        raise HTTPException(
                            status_code=413,
                            detail=f"{loc.label}, the {slot_label} file {message}",
                        ) from exc
                    funnel = _FUNNEL_MESSAGE.format(contact_email=state.contact_email)
                    raise HTTPException(
                        status_code=415,
                        detail=f"{loc.label}, the {slot_label} file: {funnel}",
                    ) from exc
                read.append(content)
            extra_contents[loc.index] = read

        # Session INTAKE-2. Which ISO 20816-3 row applies, and how we know --
        # a rating beats the select, the select beats the default, and the
        # provenance rides the wire so the report can print "(assumed)" instead
        # of presenting a default as a statement. The browser recomputes the
        # select from the rating as the analyst types, so the two agree by
        # submit time; this is the authority either way.
        iso_class = resolve_iso_class(
            rated_kw=rated_kw,
            mounting=(mounting or "").strip().lower() or None,
            stated_group=(iso_group or "").strip() or None,
            stated_support=(iso_support or "").strip().lower() or None,
        )

        form_dict = {
            "machine_alias": machine_alias,
            "rpm": rpm,
            "iso_group": iso_class.group,
            "iso_support": iso_class.support,
            # Session INTAKE-2 — the machine kind (PARTC F-2) and the rated
            # spec it and the ISO group come from. A blank type normalizes to
            # None so `_machine_422` can refuse it in one place.
            "machine_type": (machine_type or "").strip().lower() or None,
            "rated_kw": rated_kw,
            "driven_rpm": driven_rpm,
            # Session LIMITS-1c. Passed through as the analyst typed them;
            # `_thresholds_422` below is what refuses a bad trio, and
            # `thresholds_from_form` is the single place they become a
            # `MachineThresholds`. Nothing here sets `threshold_source` --
            # pdm_core stamps that itself (LIMITS-1a §3).
            "limit_ab": limit_ab,
            "limit_bc": limit_bc,
            "limit_cd": limit_cd,
            "mounting": (mounting or "").strip().lower() or None,
            "bearing_model": bearing_model,
            # Session GEOM-1. `bearing_model` above is passed through raw and
            # keeps arriving as "" from an untouched select -- unchanged, and
            # deliberately so: `bearing_spec_from_form` has always read it with
            # `if not form.bearing_model`, and every check added this session
            # reads a blank the same falsy way rather than a second way.
            "bearing_n_balls": bearing_n_balls,
            "bearing_ball_dia_mm": bearing_ball_dia_mm,
            "bearing_pitch_dia_mm": bearing_pitch_dia_mm,
            "bearing_contact_angle_deg": bearing_contact_angle_deg,
            "velocity_unit": velocity_unit,
            "detection_type": detection_type,
            "mode": "spectrum" if compare_mode else mode,
            "compare": compare_mode,
            "wav_sensitivity": wav_sensitivity,
            # Session INTAKE-HONEST — declared acquisition settings, optional,
            # provenance-only. Empty selects arrive as "" and mean "not
            # provided", so they normalize to None here, before validation.
            "sensor_sensitivity_mv_per_g": sensor_sensitivity_mv_per_g,
            "fmax_hz": fmax_hz,
            "spectral_lines": spectral_lines,
            "window_type": window_type or None,
            "averages": averages,
            "integration": integration or None,
            # Session GEOM-A. Blank selects and a blank location mean "not
            # stated" and must never be recorded as an answer.
            "measurement_location": (measurement_location or "").strip() or None,
            "coupling": coupling or None,
            "blades": blades,
            "gear_teeth_driving": gear_teeth_driving,
            "gear_teeth_driven": gear_teeth_driven,
            "rotor_bars": rotor_bars,
            "poles": poles,
            "line_freq_hz": line_freq_hz,
            "drive_type": drive_type or None,
            "drive_pulley_mm": drive_pulley_mm,
            "driven_pulley_mm": driven_pulley_mm,
            "pulley_center_distance_mm": pulley_center_distance_mm,
            # Session HIST-1. Kept as the RAW string, not a parsed list: this
            # dict is stashed verbatim on `job.pending` for the inference-resume
            # lane, and it has to stay JSON-serializable. It is re-parsed at the
            # attach point, which costs nothing and keeps one source of truth
            # for what a valid card looks like.
            "history": history,
            "retain_trace": retain_trace,
        }
        # Declared acquisition values are validated HERE, before anything is
        # charged or created — a bad value is a free 422 now, never a job and
        # never a PARSE_ERROR from inside the sandbox (which would misfile an
        # analyst typo as `upload_unreadable`, a claim about the file).
        acq_problem = (
            _alias_422(form_dict)
            or _machine_422(form_dict)
            or _thresholds_422(form_dict)
            or _acquisition_422(form_dict)
            or _geometry_422(form_dict, tuple(state.bearings_cfg["bearings"]))
            or _history_422(form_dict)
        )
        if acq_problem is not None:
            raise HTTPException(status_code=422, detail=acq_problem)

        # Session INTAKE-2. Every other location's bearing goes through the SAME
        # `_geometry_422`, on that location's own form dict -- eight sentences,
        # one source. A second copy of that wording, or a laxer check for
        # locations 2-8, is how a screen the analysis cannot run gets offered.
        # The message is prefixed with the point so an analyst with four
        # locations open knows which one to look at.
        known_bearings = tuple(state.bearings_cfg["bearings"])
        for loc in extra_locations:
            loc_problem = _geometry_422(
                locations.location_form_dict(form_dict, loc), known_bearings
            )
            if loc_problem is not None:
                raise HTTPException(status_code=422,
                                    detail=f"{loc.label}: {loc_problem}")
        # Validate the form maps onto UploadForm BEFORE anything is charged or
        # created, so a bad field name/type is a free 422 now rather than a
        # charged one -- and never a silent "error" job later.
        try:
            UploadForm(**{k: v for k, v in form_dict.items()
                          if k not in _NOT_UPLOAD_FORM_FIELDS})
        except TypeError as exc:
            raise HTTPException(status_code=422, detail=f"invalid form fields: {exc}") from exc

        # Everything has passed. THIS is an accepted job, so this is the point at
        # which the invite code's daily budget is spent.
        try:
            state.rate_limiter.check_and_record(code_label)
        except RateLimitError as exc:
            # The window here is a DAY, so Retry-After must not imply a minute.
            raise HTTPException(status_code=429, detail=str(exc),
                                headers={"Retry-After": "3600"}) from exc

        channels: list[assembly.ChannelUpload] = []
        # The writes cannot simply move ABOVE registry.create: their target is
        # job.job_dir, and create() is what produces it (jobs.py). So the loop is
        # wrapped instead. Before S7 an OSError here left the entry and its
        # mkdtemp() dir behind in `queued` for the full TTL, counted against
        # queue_depth_max -- twenty of them and the service answers "Server is
        # busy" to everybody for an hour, with the wrong cause. Measured:
        # pending_job_count 0 -> 1 on a single ENOSPC (eval_s7 §1.2). A plain
        # full disk is the LEAST likely way to get here -- the guard above
        # already refuses below 500 MB free and one upload is capped at 25 MiB
        # -- so this catches OSError generally: a read-only remount, EIO, or a
        # permissions change on the scratch directory are the realistic triggers.
        #
        # `create()` sits INSIDE the guard (S7-ACCEPT F-4): its mkdtemp() is
        # the first filesystem touch, and a permissions change on the scratch
        # directory -- one of the realistic triggers named above -- fails right
        # there, before any write. Measured on acceptance: a chmod 555 jobs
        # directory returned a bare 500 because the guard started one line too
        # low. mkdtemp raises before the entry joins the registry (jobs.py), so
        # a create-failure has no job to mark -- just the WARNING and the 503.
        job: Job | None = None
        try:
            job = state.registry.create(code_label=code_label, file_size=sum(len(c) for c in contents))
            job.refund_hook = partial(state.rate_limiter.refund, code_label)
            # Session INTAKE-2. The declared machine, recorded on the job at
            # the moment it is accepted -- the form is gone by the time the
            # document renders, and `iso_assumed` is a fact about what the
            # analyst told us, not something a later stage can recover.
            job.machine_type = form_dict["machine_type"]
            job.iso_group = iso_class.group
            job.iso_support = iso_class.support
            job.iso_assumed = iso_class.assumed
            # Session INTAKEFIX-1 (INTAKE-2's F-4). The provenance of each half,
            # not just their `or`. `iso_assumed` cannot tell "derived from the
            # 90 kW you entered" from "the group you selected", and the report's
            # own caveat names BOTH halves from a flag that is true when either
            # was assumed -- so a stated group under an assumed support currently
            # reads as though nobody said what the group was.
            job.group_source = iso_class.group_source
            job.support_source = iso_class.support_source
            job.iso_note = iso_class.note
            # The declared nameplate, for the `0005` card columns the store
            # writes. Recorded here for the reason everything above is: the form
            # is gone by the time a job reaches its terminal transition.
            job.rated_kw = rated_kw
            job.driven_rpm = driven_rpm
            for i, ((f, _), content) in enumerate(zip(slots, contents)):
                suffix = Path(f.filename or "").suffix.lower()
                name = "upload" if i == 0 else f"upload_{i + 1}"
                upath = job.job_dir / f"{name}{suffix}"
                upath.write_bytes(content)
                channels.append(assembly.ChannelUpload(slot=i + 1, path=upath,
                                                       direction=directions[i], assumed=assumed))
            # Session INTAKE-2. Location 1's files keep the names and the
            # directory they have always had -- `upload.csv` at the job root --
            # because `tests/test_upload_nameless.py` pins that every adapter
            # parses a stem of literally "upload", and the standing rule about
            # filename-coupled adapter logic is written from a bug that shipped
            # because the webapp renames uploads. Locations 2-8 get a
            # subdirectory each, so the same stem can repeat without collision
            # and a per-location parse still meets `upload.<ext>`.
            location_plans: list[dict[str, Any]] = [{
                "index": 1,
                "label": form_dict.get("measurement_location") or _FIRST_LOCATION_LABEL,
                "form": form_dict,
                "channels": channels,
            }]
            for loc in extra_locations:
                loc_dir = job.job_dir / f"loc{loc.index}"
                loc_dir.mkdir(parents=True, exist_ok=True)
                loc_dirs, loc_assumed = extra_directions[loc.index]
                loc_channels: list[assembly.ChannelUpload] = []
                for i, ((upload, _d), content) in enumerate(
                    zip(loc.slots, extra_contents[loc.index])
                ):
                    suffix = Path(upload.filename or "").suffix.lower()
                    name = "upload" if i == 0 else f"upload_{i + 1}"
                    upath = loc_dir / f"{name}{suffix}"
                    upath.write_bytes(content)
                    loc_channels.append(assembly.ChannelUpload(
                        slot=i + 1, path=upath,
                        direction=loc_dirs[i], assumed=loc_assumed))
                location_plans.append({
                    "index": loc.index,
                    "label": loc.label,
                    "form": locations.location_form_dict(form_dict, loc),
                    "channels": loc_channels,
                })
        except OSError as exc:
            if job is not None:
                # Marked and logged BEFORE it is dropped, so the failure is on
                # the record with its category -- then removed entirely, because
                # as far as anyone can tell this job never existed, which is the
                # truth: nothing was queued and nothing will be analysed.
                mark_error(job, _STORAGE_FULL_MESSAGE, code="upload_write_failed")
                _log_job_outcome(job)
                state.registry.purge_job(job)
                state.registry.drop(job.id)
            _log.warning("upload_write_failed code=%s errno=%s", code_label, exc.errno)
            raise HTTPException(status_code=503, detail=_STORAGE_FULL_MESSAGE) from exc

        _spawn(state, job.id,
               _process_job_background(job.id, form_dict, location_plans, state))
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        job = state.registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        if job.state in ("done", "gate_fail", "degraded") and state.registry.is_expired(job):
            state.registry.purge_job(job)  # lazy TTL purge on access (mirrors download_report)
            raise HTTPException(status_code=410, detail="report has expired and been deleted")
        if job.purged and job.state in ("done", "gate_fail", "degraded"):
            raise HTTPException(status_code=410, detail="report has expired and been deleted")
        payload: dict[str, Any] = {"state": job.state}
        if job.safe_message:
            payload["safe_message"] = job.safe_message
        # DEP-4a. The error card cannot give the right advice from `error` alone:
        # today it sends every failure to the CSV template and the funnel email,
        # which is exactly wrong for a crash of ours on a good file. `failure_kind`
        # is the binary the card branches on; `retryable` is whether submitting the
        # same thing again is sensible. The finer `error_code` is deliberately NOT
        # exposed -- it is the operator's grep handle and lives in the log line.
        if job.failure_kind:
            payload["failure_kind"] = job.failure_kind
            payload["retryable"] = job.retryable
        if job.degraded_reason:
            payload["degraded_reason"] = job.degraded_reason
        # UI-only read-only hints (Session E). Additive; never raw model fields.
        if job.phase:
            payload["phase"] = job.phase
        if job.gate_summary:
            payload["gate_summary"] = job.gate_summary
        if job.result_summary:
            payload["result_summary"] = job.result_summary
        # Session HIST-1. The one machine-readable result on this wire: the
        # browser stores it on the machine's trend card and posts it back as
        # `history` next time. Absent whenever there was no trendable scalar,
        # which is what withholds the save offer in the UI.
        if job.trend_point:
            payload["trend_point"] = job.trend_point
        # Session INTAKE-2. The machine as declared, and the ISO row its
        # severity was judged against. Built field by field like everything
        # else on this payload (wire law #5), and `iso_assumed` ships even when
        # False -- "we were told" is as much a fact as "we were not", and a
        # browser that had to treat absent as false could not tell an old
        # server from a rated machine.
        if job.machine_type:
            payload["machine_type"] = job.machine_type
        if job.iso_group:
            payload["iso_group"] = job.iso_group
            payload["iso_support"] = job.iso_support
            payload["iso_assumed"] = job.iso_assumed
            # Session INTAKEFIX-1 (INTAKE-2's F-4). Beside the flag, never
            # instead of it: `iso_assumed` is still the ONE thing that gates the
            # word "assumed" (contract section 5 rule 3), and these say which
            # half earned it. They ship with `iso_group` because they are
            # meaningless without it.
            payload["group_source"] = job.group_source
            payload["support_source"] = job.support_source
        if job.iso_note:
            payload["iso_note"] = job.iso_note
        # Session INTAKE-2. The other measurement locations' results, in the
        # shape `docs/contracts/machine_result.md` declares. Absent entirely on a
        # single-location job, so nothing about that payload moved.
        if job.locations:
            payload["locations"] = job.locations
        if job.channel_summary:
            payload["channels"] = job.channel_summary
        if job.interpretation:
            payload["interpretation"] = job.interpretation
        return payload

    @app.post("/api/jobs/{job_id}/confirm", status_code=202)
    async def confirm_job(job_id: str, body: ConfirmBody) -> dict[str, str]:
        """One tap from the confirm card. Applies the analyst's corrections to
        each file's recipe, drops the ones they skipped (and any the inference
        pass could not interpret), then resumes the paused job."""
        job = state.registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        if job.state != "awaiting_confirm" or not (job.pending or {}).get("channels"):
            raise HTTPException(status_code=409, detail="This analysis is not waiting for confirmation.")
        if state.registry.is_expired(job):
            state.registry.purge_job(job)
            raise HTTPException(status_code=410, detail="this upload has expired and been deleted")

        pending = dict(job.pending or {})
        plans = [dict(p) for p in pending.get("channels", [])]
        form_dict = dict(pending.get("form", {}))
        per_slot = {f.slot: f for f in body.files}

        if body.rpm:
            # The analyst's stated speed wins over anything read from a header,
            # and every recipe is made to say so rather than keeping a stale value.
            form_dict["rpm"] = body.rpm
        form_dict["share_format"] = bool(body.share_format)
        form_dict["retain_trace"] = bool(body.retain_trace)

        for plan in plans:
            edit = per_slot.get(plan["slot"])
            if edit is not None and edit.direction:
                plan["direction"] = edit.direction
                plan["label"] = assembly.DIRECTION_LABELS[edit.direction]
                plan["assumed"] = False
            if edit is not None and edit.skip:
                plan["skip"] = True
            if plan["source"] != "inference" or plan["status"] != "ok":
                continue
            updates: dict[str, Any] = {}
            unit = (edit.velocity_unit if edit is not None else None) or (
                body.velocity_unit if edit is None else None)
            detection = (edit.detection_type if edit is not None else None) or (
                body.detection_type if edit is None else None)
            if unit:
                updates["amplitude_unit"] = unit
            if detection:
                updates["detection"] = detection
            if body.rpm:
                updates["rpm_source"] = "form"
                updates["rpm_value"] = None
            if not updates:
                continue
            try:
                recipe = ParseRecipe(**json.loads(plan["recipe"])).model_copy(update=updates)
                ParseRecipe(**recipe.model_dump())  # an edit must still fit the schema
            except (ValidationError, ValueError) as exc:
                raise HTTPException(status_code=422,
                                    detail="Those settings don't fit this file.") from exc
            plan["recipe"] = recipe.model_dump_json()

        usable = [p for p in plans if p["status"] == "ok" and not p.get("skip")]
        if not usable:
            raise HTTPException(
                status_code=422,
                detail="No files are left to analyse — un-skip one, or start over with new files.",
            )
        directions = [p["direction"] for p in usable]
        if len(set(directions)) != len(directions):
            raise HTTPException(
                status_code=422,
                detail="Two files are marked with the same direction — give each one its own.",
            )
        if len(usable) > 1:
            # Session E's rule: with more than one channel, every direction is
            # stated, never assumed.
            for plan in usable:
                plan["assumed"] = False

        pending["channels"] = plans
        pending["form"] = form_dict
        job.pending = pending
        job.recipe_json = usable[0].get("recipe")
        job.interpretation = _interpretation_payload(plans, form_dict)
        job.state = "queued"
        # The FIRST task already stamped `worker_started_at` when it ran the
        # inference pass. Leaving that stamp on a job re-entering `queued` would
        # hand the max-runtime sweep a clock that has been running for however
        # long the analyst spent reading the confirm card -- so a job confirmed
        # after a twenty-minute think would be killed for overrunning while it
        # was still waiting for a worker slot. The resume task re-stamps it once
        # it actually has one.
        job.worker_started_at = None
        _spawn(state, job.id, _resume_confirmed_background(job.id, state))
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}/report.pdf")
    async def download_report(job_id: str) -> Response:
        # Browser-navigated endpoint: never a raw JSON detail body. Unknown,
        # expired, or unavailable -> a friendly HTML card. Within TTL the report
        # is served IDEMPOTENTLY -- no purge here -- so a browser PDF viewer's
        # follow-up / ranged requests (see the MFPT double-request in the close-out)
        # all return the same bytes. Intermediates were already deleted at
        # completion (worker._keep_only_report); the TTL sweep removes report.pdf.
        gone = lambda status: HTMLResponse(  # noqa: E731
            _report_notice_page("This report has been deleted.", _REPORT_GONE_DETAIL, state.contact_email),
            status_code=status,
        )
        job = state.registry.get(job_id)
        if job is None:
            return gone(404)
        if job.state == "error":
            # Checked BEFORE `purged`: an error job's files are purged the
            # moment it fails (S7-ACCEPT F-3), but the honest answer here is
            # still "this analysis could not be completed", not "this report
            # has been deleted" -- there never was a report to delete.
            #
            # A FAILED job has no report and never will, so "still being prepared"
            # tells the analyst to wait for something that is not coming. Observed
            # live on 2026-08-05 (outputs/live_lane_aug5/, the prose-file job):
            # the refusal itself was right, the words afterwards were not.
            # The job's own safe_message is already analyst-facing copy -- it is
            # what the error card showed -- so it says WHY here too.
            return HTMLResponse(
                _report_notice_page(
                    "This analysis could not be completed, so there is no report.",
                    (job.safe_message or "The file could not be analysed.")
                    + ' <a href="/">Start a new analysis</a>, or email '
                    f'<a href="mailto:{state.contact_email}">{state.contact_email}</a> '
                    "and we will take a look.",
                    state.contact_email,
                ),
                status_code=409,
            )
        if job.purged:
            return gone(410)
        if job.state not in ("done", "gate_fail", "degraded"):
            return HTMLResponse(
                _report_notice_page(
                    "Your report is still being prepared.",
                    'Give it a moment, then use the download link on the <a href="/">analysis page</a>.',
                    state.contact_email,
                ),
                status_code=409,
            )
        if state.registry.is_expired(job):
            # lazy TTL purge on access; terminal jobs only -- a running job's
            # dir must never be deleted out from under the worker.
            state.registry.purge_job(job)
            return gone(410)
        if job.pdf_path is None or not job.pdf_path.exists():
            # PDF render was unavailable (best-effort); the report was NOT
            # deleted -- say so honestly instead of the gone card.
            return HTMLResponse(
                _report_notice_page(
                    "This report's PDF could not be generated.",
                    'The analysis finished, but the PDF renderer was unavailable. '
                    '<a href="/">Re-run the analysis</a> to try again — or email '
                    f'<a href="mailto:{state.contact_email}">{state.contact_email}</a> '
                    "if it keeps happening.",
                    state.contact_email,
                ),
                status_code=404,
            )
        return Response(content=job.pdf_path.read_bytes(), media_type="application/pdf")



    def _fill_page(name: str, **slots: str) -> str:
        """A static page with its server-side slots filled.

        A slot whose value is empty takes its whole LINE with it, indentation
        included. That is what keeps browser mode byte-identical: the markup
        gains placeholder lines for the account nav and for the meta tag the
        retention ledger reads, and on the backend production runs those lines
        do not reach the browser at all -- not as a blank line, not as an empty
        attribute.
        """
        text = (_STATIC_DIR / name).read_text().replace(
            "{{contact_email}}", state.contact_email
        )
        for key, value in slots.items():
            token = "{{" + key + "}}"
            if value:
                text = text.replace(token, value)
            else:
                text = re.sub(
                    r"^[ \t]*" + re.escape(token) + r"[ \t]*\n", "", text,
                    flags=re.MULTILINE,
                ).replace(token, "")
        return text

    def _account_slots() -> dict[str, str]:
        """Page slots the masthead template expects. Empty in this build."""
        return {"account_head": "", "account_nav": ""}



    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Response:
        """The upload form."""
        return HTMLResponse(_fill_page("index.html", csrf_field="", **_account_slots()))





    @app.get("/style.css")
    async def style_css() -> FileResponse:
        return FileResponse(_STATIC_DIR / "style.css", media_type="text/css")

    @app.get("/app.js")
    async def app_js() -> FileResponse:
        # index.html behaviour, extracted from the inline <script> so the CSP can
        # forbid inline scripts (default-src 'self'). Static — the contact email
        # is read at runtime from index.html's server-substituted <meta> tag.
        return FileResponse(_STATIC_DIR / "app.js", media_type="application/javascript")

    @app.get("/fonts/{name}")
    async def font_file(name: str) -> FileResponse:
        # Self-hosted IBM Plex woff2 (SIL OFL). Basename-only, .woff2-only lookup
        # inside the fonts dir -- no path traversal, no other file types served.
        if not name.endswith(".woff2") or "/" in name or "\\" in name or ".." in name:
            raise HTTPException(status_code=404, detail="not found")
        path = _STATIC_DIR / "fonts" / name
        if not path.exists():
            raise HTTPException(status_code=404, detail="font not found")
        return FileResponse(path, media_type="font/woff2")

    @app.get("/assets/{name}")
    async def asset_file(name: str) -> FileResponse:
        # Landing assets: the sample-report page images. Session UX-4 deleted the
        # vendored three.js UMD build that used to sit beside them, with the
        # `heroDepth` block in app.js that injected it -- the hero's inline SVG
        # was always the figure an analyst reads, and the build was the only
        # console output the site produced. Same rules as /fonts above --
        # basename-only, extension whitelist, nothing else in the directory is
        # reachable. Read-only; no job state is touched here.
        media = {".js": "application/javascript", ".png": "image/png"}.get(
            name[name.rfind(".") :].lower() if "." in name else ""
        )
        if media is None or "/" in name or "\\" in name or ".." in name:
            raise HTTPException(status_code=404, detail="not found")
        path = _STATIC_DIR / "assets" / name
        if not path.exists():
            raise HTTPException(status_code=404, detail="asset not found")
        return FileResponse(path, media_type=media)

    @app.get("/sample.csv")
    async def sample_csv() -> Response:
        return PlainTextResponse((_STATIC_DIR / "sample.csv").read_text(), media_type="text/csv")

    @app.get("/example-spectrum.csv")
    async def example_spectrum() -> Response:
        # Session F2 (demo path): the bundled example file the hero's "Run the
        # example analysis" button uploads. A real 0-400 Hz velocity spectrum
        # (6206 bearing @ 1800 rpm, outer-race fault) that runs through the SAME
        # job flow as any upload -- nothing about the demo is canned, and the
        # result it produces is whatever the pipeline computes.
        return PlainTextResponse(
            (_STATIC_DIR / "example_spectrum.csv").read_text(), media_type="text/csv"
        )

    @app.get("/sample.xlsx")
    async def sample_xlsx() -> FileResponse:
        path = _STATIC_DIR / "sample.xlsx"
        if not path.exists():
            raise HTTPException(status_code=404, detail="sample.xlsx not built")
        return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get("/sample-report.pdf")
    async def sample_report() -> FileResponse:
        # Session SROUTE: the public sample is the SYNTHETIC BPFO demo written by
        # scripts/make_sample.py (`make_case("bpfo", seed=1)`) — a drafted report on a seeded
        # fixture: ISO Zone D, one committed outer-race (BPFO) finding at high confidence.
        # Being seed-generated, the shop-window document cannot be, or be mistaken for, a
        # reading anyone actually took. It replaces the CWRU OR021@6 demo, which was real
        # laboratory data; that corpus stays local (data/cwru/) and is reported on /validation,
        # which is where real-data benchmarks belong. The path is pinned byte-for-byte by
        # tests/test_webapp_e2e.py::test_sample_report_serves_bpfo_synthetic_bytes — before
        # SROUTE this route and scripts/make_sample.py disagreed about which file was served,
        # and nothing checked.
        path = _OUTPUTS_DIR / "demo_package" / "bpfo_synthetic" / "report.pdf"
        if not path.exists():
            raise HTTPException(status_code=404, detail="sample report not available")
        return FileResponse(path, media_type="application/pdf")

    @app.get("/privacy", response_class=HTMLResponse)
    async def privacy() -> Response:
        """What the running process does with an upload. Same bytes for every
        visitor, so no `Cache-Control`.

        The two third-party slots are filled from the SAME value the security
        headers are derived from, so a page naming a service this process cannot
        reach is not representable."""
        return HTMLResponse(_fill_page(
            "privacy.html",
            external_services=third_parties_sentence(external_services),
            third_party_details=third_party_details(external_services),
        ))

    @app.get("/validation", response_class=HTMLResponse)
    async def validation() -> str:
        # The consolidated status is hand-authored in validation_summary.md, because
        # eval/runner.py rewrites cwru_results.md and field_validation_results.md
        # wholesale (write_text) and would silently destroy edits made to them. The
        # machine-scored detail docs stay linked below.
        summary_path = _OUTPUTS_DIR / "validation_summary.md"
        cwru_path = _OUTPUTS_DIR / "cwru_results.md"
        if summary_path.exists():
            body = summary_path.read_text()
        elif cwru_path.exists():
            body = cwru_path.read_text()
        else:
            body = "Validation results pending."
        mfpt_results_path = _OUTPUTS_DIR / "field_validation_results.md"
        mfpt_link = (
            '<a href="/field-validation-results.md">MFPT cross-rig + real-world field '
            "validation (per-file detail)</a> · "
            if mfpt_results_path.exists()
            else ""
        )
        main_html = (
            '<section class="prose">'
            "<h1>Validation record</h1>"
            '<p class="doc-note">Benchmarked in public. The consolidated status below is the '
            "single source of truth (<code>outputs/validation_summary.md</code>); every number is "
            "measured and every miss is stated as a miss.</p>"
            # Session UX-4 (STRANGER U7). This was `<pre class="doc">` and a
            # stranger read it as `cat` output pasted into a page. The
            # renderer is deterministic, dependency-free, escapes before it
            # marks up, and allowlists link targets -- see webapp/mdpage.py
            # for why the two documents it serves need different handling.
            f"{render_doc(body)}"
            f'<p class="doc-note">{mfpt_link}'
            '<a href="/sample-report.pdf">Sample drafted report (synthetic test set)</a></p>'
            "</section>"
        )
        return _page_shell("Validation record", main_html, state.contact_email)

    @app.get("/field-validation-results.md", response_class=HTMLResponse)
    async def field_validation_results() -> str:
        results_path = _OUTPUTS_DIR / "field_validation_results.md"
        if not results_path.exists():
            raise HTTPException(status_code=404, detail="field validation results not available")
        main_html = (
            '<section class="prose"><h1>MFPT field validation — per-file detail</h1>'
            f"{render_doc(results_path.read_text())}"
            '<p class="doc-note"><a href="/validation">← Back to the validation record</a></p>'
            "</section>"
        )
        return _page_shell("MFPT Field Validation", main_html, state.contact_email)

    return app


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Session AUTH-1. The one region of `static/privacy.html` whose wording depends
# on which backend is running: on `browser` it says accounts are not yet in
# effect, on `db` it is replaced wholesale by `static/privacy_accounts.html`.
#
# Comment markers rather than a template placeholder, deliberately. Four
# existing tests read privacy.html straight off disk and count phrases in it
# (tests/test_geometry_webapp.py, tests/test_hist1_browser.py); a marker is
# invisible to a prose count, and keeping the shipped wording in the file it has
# always been in means those pins go on checking the words an analyst reads.
_ACCOUNTS_OPEN = "<!--ACCOUNTS-->"
_ACCOUNTS_CLOSE = "<!--/ACCOUNTS-->"


def _strip_marks(page: str) -> str:
    """Drop the marker lines and nothing else -- the `browser` rendering.

    Byte-identical to what this page served before AUTH-1: the markers occupy
    whole lines of their own, and both lines go.
    """
    for mark in (_ACCOUNTS_OPEN, _ACCOUNTS_CLOSE):
        page = re.sub(r"^[ \t]*" + re.escape(mark) + r"[ \t]*\n", "", page, flags=re.M)
    return page


def _swap_marked(page: str, replacement: str) -> str:
    """Replace everything between the markers -- the `db` rendering."""
    start = page.index(_ACCOUNTS_OPEN)
    end = page.index(_ACCOUNTS_CLOSE) + len(_ACCOUNTS_CLOSE)
    return page[:start] + replacement + page[end:]


# The masthead + labeled envelope-spectrum SVG signature, verbatim from the
# design reference, so the served doc pages (/validation, /field-validation)
# share the same shell as the static index/privacy pages.
_MASTHEAD = """<header>
  <div class="wrap">
    <div class="mast">
      <div class="mark">VIB-AGENT<span>VIBRATION ANALYSIS</span></div>
      <nav>
        <a href="/#/">Machines</a>
        <a href="/validation">Validation</a>
        <a href="/privacy">Privacy</a>
      </nav>
    </div>
  </div>
  <svg class="spectrum" viewBox="0 0 880 64" preserveAspectRatio="none" aria-hidden="true">
    <polyline fill="none" stroke="#C9D2CD" stroke-width="1"
      points="0,58 40,56 60,57 80,54 95,57 110,55 120,20 128,55 150,56 170,53 190,56 215,54 235,57
              255,50 262,34 270,52 290,55 310,53 330,56 350,54 368,44 375,26 383,50 400,55 420,53
              440,56 460,54 480,57 495,40 502,30 510,52 530,55 550,54 570,56 590,53 610,55 625,46
              632,38 640,53 660,55 680,54 700,56 720,55 740,57 760,55 780,56 800,54 820,57 850,56 880,57"/>
    <g class="sig-lb">
      <text x="112" y="14">1&#215;</text>
      <text x="252" y="28">BPFO</text>
      <text x="362" y="20">2&#215;BPFO</text>
      <text x="487" y="24">3&#215;BPFO</text>
    </g>
    <line x1="0" y1="59" x2="880" y2="59" stroke="#D8DCD7" stroke-width="1"/>
  </svg>
</header>"""


def _footer(contact_email: str) -> str:
    return (
        '<footer><div class="wrap row">'
        f'<span>vib-agent — a prototype vibration-analysis agent · <a href="mailto:{contact_email}">{contact_email}</a></span>'
        '<span><a href="/validation">Validation record</a> · '
        '<a href="/privacy">Privacy</a> · '
        "Reports are drafts pending review by a qualified analyst.</span>"
        "</div></footer>"
    )


def _page_shell(title: str, main_html: str, contact_email: str) -> str:
    """Wrap page-body HTML in the shared masthead + stylesheet + footer shell,
    so the f-string-rendered doc pages match the static index/privacy pages."""
    return (
        "<!DOCTYPE html><html lang=\"en\"><head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{title}</title>"
        "<link rel=\"stylesheet\" href=\"/style.css\">"
        "</head><body>"
        f"{_MASTHEAD}"
        f'<main class="wrap">{main_html}</main>'
        f"{_footer(contact_email)}"
        "</body></html>"
    )


def _report_notice_page(headline: str, detail_html: str, contact_email: str) -> str:
    """A friendly full-page card for a report a browser navigated to that isn't
    available (expired, unknown, or still being prepared). Browser-facing GETs
    must never return a raw JSON detail body -- this is the human-readable form.
    The copy names no internal identifiers, job id, or filename."""
    main = (
        '<section class="card" style="margin-top:34px">'
        '<span class="state-tag" style="background:var(--mut)">REPORT UNAVAILABLE</span>'
        f'<p class="kv"><b>{headline}</b> {detail_html}</p>'
        "</section>"
    )
    return _page_shell("Report unavailable", main, contact_email)


# The expired/unknown-report copy, shared by the endpoint and asserted by the
# no-internal-identifiers test. Names no id, filename, or internal convention.
_REPORT_GONE_DETAIL = (
    "Reports are kept for 60 minutes after analysis, then removed per the "
    '<a href="/privacy">privacy policy</a>. <a href="/">Re-run the analysis</a> '
    "to generate a new one."
)


# Default app instance for `uvicorn vib_agent.webapp.app:app`.
app = create_app()
