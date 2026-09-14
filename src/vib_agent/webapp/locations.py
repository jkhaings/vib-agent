"""Session INTAKE-2 — N measurement locations on one machine.

A route machine is not one point. A motor has a drive end and a non-drive end; a
pump set has four bearings; a gearbox has an input and an output. Until this
session the intake modelled ONE point with three direction slots, so an analyst
who measured four points ran four jobs — and got back four *machines*, because
the identity a machine is filed under is `alias|measurement_location` (UX-1
ruling D-2).

This module is the intake half of fixing that: it reads the locations off the
form, refuses the ones that cannot be analysed, and hands back a list the worker
runs one at a time. **It contains no analysis.** Each location goes through
`assembly.merge_channels` and `pipeline.run_analysis` exactly as a single-point
upload always has — `assembly.py` is not opened by this session, because merging
the channels of ONE point is precisely what it already does right.

## The wire shape, and why location 1 is special

Location 1 keeps every field name the form has always used — `file`, `file_2`,
`file_3`, `direction`, `measurement_location`, `bearing_model`, `rpm`, the four
geometry fields. Locations 2–8 use a `loc<i>_` prefix.

That asymmetry is deliberate and it is load-bearing. The single-file,
defaulted-direction upload is pinned BYTE-IDENTICAL
(`tests/test_multiaxis.py::test_browser_empty_direction_is_the_identity_path`,
`assembly.py:449-463`), and every shipped pin, every adapter and the whole
inference lane read those names. Renaming them to `loc1_*` would have made a
one-location upload a new code path on the day the form changed, which is the
opposite of what a byte-identity pin is for.

## Why the files are read off the raw form and not declared as parameters

Seven more locations × three slots is 21 more `File(None)` parameters, plus ~56
`Form(None)`s for the labels, bearings, speeds and notes. The alternative that
avoids the explosion — one repeated `list[UploadFile]` plus a parallel list of
indices — is the one thing that must not be built here: the browser deletes
untouched file parts (`app.js`'s `clearSlot`, and Session E's empty-part
handling), so two order-matched lists can DESYNCHRONISE, and a desynchronised
list means a file analysed against another location's bearing at another
location's speed. That is a wrong diagnosis, signed. The axis-mislabel bug
Session E's conjunction proof exists to catch is the same family.

So every part is addressed by its own name, `loc3_file_2`, and read out of the
FormData mapping that FastAPI has already parsed and cached. Nothing is
positional, and a missing part is missing rather than shifting its neighbours.

## What a multi-location job may not be

Three refusals, all at the form boundary, all free:

* **not `compare`, and not `trend`/`mafaulda` mode.** The same precedent that
  already makes multi-FILE uploads spectrum-only (`app.py:249,256`): a
  comparison is two readings of one point, and a trend CSV builds its history
  from its own file.
* **no schema-inference formats** (`.txt/.dat/.asc`). The confirm-and-resume
  pause is a single-job, single-point conversation; wiring it across N locations
  is the "feature wired into most of the paths" shape HIST-1 warns about, and a
  half-wired pause would strand jobs. The refusal names the way forward — run
  that location on its own — rather than failing silently.
* **at most `MAX_LOCATIONS`.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vib_agent.adapters.uploads.common import (
    BEARING_GEOMETRY_FIELDS,
    DEFAULT_LOCATION_LABELS,
)

#: What a location's result can say about itself, and the whole vocabulary.
#:
#: Declared here rather than left as literals at the site that builds an entry,
#: because `docs/contracts/machine_result.md` enumerates these for REPORT-3 and
#: `tests/test_intake2_contract.py` diffs the document against THIS tuple. The
#: first version of that pin grepped `"status": "..."` out of `app.py` and caught
#: the inference plan's own unrelated `pending` -- a pin measuring the wrong
#: thing. One declaration, two readers.
#:
#: Only `ok` carries numbers. `gate_fail` means the data-quality gate failed and
#: therefore NO DIAGNOSIS WAS MADE, which is a different statement from "nothing
#: was found" and the report has to be able to tell them apart.
LOCATION_STATUSES: tuple[str, ...] = ("ok", "gate_fail", "unreadable", "error")

#: One machine, at most this many points, per job. Eight covers a motor-pump set
#: with four bearings measured in two directions each and leaves headroom; it is
#: also small enough that N sandboxed parses and N analyses stay inside the
#: job's runtime bound (`JobRegistry.max_runtime_s`).
MAX_LOCATIONS = 8

#: `app.py::_LOCATION_MAX_CHARS`, which bounds the single `measurement_location`
#: field a job has always had. A location label is the same kind of value --
#: free text that reaches the report and the drafting prompt -- so it gets the
#: same bound rather than a second opinion about it.
LABEL_MAX_CHARS = 60

#: A mount note is a sentence an analyst writes about how the point is mounted
#: ("magnet on paint", "stud, gearbox housing"). It reaches the report, so it is
#: bounded like every other free-text field that does.
MOUNT_NOTE_MAX_CHARS = 120

#: The per-location fields, other than the files and their directions. Each is
#: read from `loc<i>_<name>` and lands in that location's own form dict under
#: the name the adapters already know.
#:
#: `rpm` is the SPEED OVERRIDE: blank means "the machine speed", which is the
#: `rpm` the form has always asked for once. A gearbox output turns at a
#: different speed from its input, and analysing both against one shaft rate
#: puts every order in the wrong place -- so this is the field that makes a
#: gearbox analysable at all, not a convenience.
PER_LOCATION_FIELDS: tuple[str, ...] = (
    "label", "bearing_model", "rpm", "mount_note", *BEARING_GEOMETRY_FIELDS,
)

#: The three slot names, in the order the form presents them. Location 1 uses
#: these verbatim; location `i` uses `loc<i>_<name>`.
SLOT_FIELDS: tuple[str, ...] = ("file", "file_2", "file_3")
DIRECTION_FIELDS: tuple[str, ...] = ("direction", "direction_2", "direction_3")


def prefixed(index: int, name: str) -> str:
    """The form name for `name` at location `index`.

    Location 1 is unprefixed -- see the module docstring for why that asymmetry
    is deliberate rather than untidy.
    """
    return name if index == 1 else f"loc{index}_{name}"


def default_labels(machine_type: str | None) -> tuple[str, ...]:
    """The labels the form prefills for this kind of machine.

    A route analyst names points the same way on every machine of a kind, so the
    form offers rather than asks. A typed label always wins; these are never
    substituted for one an analyst cleared.
    """
    return DEFAULT_LOCATION_LABELS.get(
        (machine_type or "").strip().lower(), DEFAULT_LOCATION_LABELS["other"]
    )


@dataclass
class RawLocation:
    """One location as the form stated it, before validation.

    `slots` holds `(upload, direction)` pairs in slot order with the empty ones
    already dropped -- an untouched file input posts an empty part, and Session E
    established that those are removed rather than reasoned about.
    """

    index: int
    fields: dict[str, Any] = field(default_factory=dict)
    slots: list[tuple[Any, str | None]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return str(self.fields.get("label") or "").strip()

    @property
    def present(self) -> bool:
        """Whether this location was filled in at all.

        A location with no file is not a location: the form appends a block the
        moment "Add location" is pressed, and an analyst who presses it and
        changes their mind must not be refused for leaving it empty. Location 1
        is the exception -- its file is the job's required upload, so its absence
        is the existing 422 about a missing file, raised elsewhere.
        """
        return bool(self.slots) or bool(self.label) or any(
            self.fields.get(name) for name in PER_LOCATION_FIELDS if name != "label"
        )


def _slot_is_present(upload: Any) -> bool:
    """Mirrors `app.py::_slot_present`.

    An untouched `<input type=file>` posts a part with an empty filename, and
    some browsers post nothing at all. Both mean "no file here".
    """
    return upload is not None and bool(getattr(upload, "filename", "") or "")


def collect_extra_locations(form: Any) -> list[RawLocation]:
    """Locations 2..MAX_LOCATIONS, read off the parsed form by name.

    `form` is the `FormData` FastAPI already parsed for the declared parameters;
    asking for it again returns the cached mapping rather than re-reading the
    body. Indices above `MAX_LOCATIONS` are not read at all -- the count refusal
    in `locations_422` is about what was SENT, so a post naming `loc9_file` is
    refused there rather than silently ignored here.
    """
    found: list[RawLocation] = []
    for index in range(2, MAX_LOCATIONS + 1):
        raw = RawLocation(index=index)
        for name in PER_LOCATION_FIELDS:
            value = form.get(prefixed(index, name))
            # A `FormData` value is a string or an UploadFile; blank strings mean
            # "not stated" here exactly as they do for every declared field.
            if isinstance(value, str):
                value = value.strip() or None
            raw.fields[name] = value
        for slot_name, direction_name in zip(SLOT_FIELDS, DIRECTION_FIELDS):
            upload = form.get(prefixed(index, slot_name))
            if not _slot_is_present(upload):
                continue
            direction = form.get(prefixed(index, direction_name))
            direction = (direction or "").strip() or None if isinstance(direction, str) else None
            raw.slots.append((upload, direction))
        if raw.present:
            found.append(raw)
    return found


def overflow_indices(form: Any) -> list[int]:
    """Location indices past the cap that the post nonetheless carries.

    Read so the refusal can say what was too many, rather than ignoring the
    extras and analysing a subset of what the analyst sent -- silently dropping
    a measured point is the worst available outcome here.
    """
    over: list[int] = []
    index = MAX_LOCATIONS + 1
    while index <= MAX_LOCATIONS + 40:  # bounded: never trust a post's shape
        if any(form.get(prefixed(index, n)) is not None
               for n in (*SLOT_FIELDS, *PER_LOCATION_FIELDS)):
            over.append(index)
        index += 1
    return over


def location_form_dict(base: dict[str, Any], loc: RawLocation | None) -> dict[str, Any]:
    """`base`, with one location's own answers substituted.

    THIS is what makes one run per location cost no new analysis code. Every
    adapter, the sandbox, `assembly.merge_channels` and `pipeline.run_analysis`
    take a form dict describing ONE point; a location is exactly that, so each
    one is handed its own dict and the existing path runs unchanged N times.

    Three substitutions:

    * `measurement_location` becomes the location's label, so the point the
      report names and the key its trend is filed under are the same string --
      the identity `app.js::trendKey()` and `db/recorder.py` already agree on.
    * the bearing — model OR the four geometry numbers — becomes this location's.
      A motor DE and NDE are frequently different bearings, and analysing both
      against one of them puts the defect frequencies of one bearing on the
      other's spectrum.
    * `rpm` becomes the speed override when there is one. A gearbox output does
      not turn at its input's speed, and every order in the analysis is derived
      from that number.

    `loc=None` returns `base` unchanged, which is location 1 on a single-location
    job: its answers are already the base's, so the dict it is analysed with is
    the dict the pre-INTAKE-2 product built. That identity is what keeps the
    byte-identical single-file path byte-identical.
    """
    if loc is None:
        return base
    out = dict(base)
    if loc.label:
        out["measurement_location"] = loc.label
    speed = loc.fields.get("rpm")
    if speed is not None:
        out["rpm"] = float(speed)
    # The bearing: INHERITED when this location states nothing, and replaced
    # WHOLESALE when it states anything. Both halves matter.
    #
    # Inheritance, because "the same bearing at all four points" is the ordinary
    # route case -- FIXTURE-1's own compressor train is exactly that -- and an
    # analyst who answered the bearing question once has answered it. Without
    # this, stating the machine's bearing at location 1 silently turned the
    # bearing screen OFF everywhere else: measured on
    # `tests/test_intake2_route_e2e.py`, where the planted BPFO at Compressor DE
    # came back as `possible_resonance` because the 6206 never reached the point.
    #
    # Wholesale rather than field-by-field, because a location that names a
    # catalogue bearing must not inherit the machine's four geometry numbers, and
    # one that states geometry must not inherit a model: the mutual-exclusion 422
    # checks each location's own pair, and a half-inherited bearing would defeat
    # it from the inside.
    stated = [loc.fields.get(name) for name in ("bearing_model", *BEARING_GEOMETRY_FIELDS)]
    if any(value not in (None, "") for value in stated):
        out["bearing_model"] = loc.fields.get("bearing_model")
        for name in BEARING_GEOMETRY_FIELDS:
            out[name] = loc.fields.get(name)
    return out


def locations_422(
    locations: list[RawLocation],
    *,
    mode: str,
    compare: bool,
    inferred_extensions: tuple[str, ...],
    overflow: list[int] | None = None,
    first_label: str | None = None,
) -> str | None:
    """The first problem with the stated locations, or None.

    One sentence per problem, in the words the form labels the control with --
    GEOM-1's rule for its eight bearing messages. Runs before the job exists and
    before the daily allowance is charged, so an analyst's typo is free.

    The bearing geometry itself is NOT re-validated here: `_geometry_422` owns
    those eight sentences and is called per location by the caller, so there is
    one source for that wording rather than a second copy drifting out of step.
    """
    extra = [loc for loc in locations if loc.index != 1]

    if overflow:
        return (
            f"a machine can carry at most {MAX_LOCATIONS} measurement locations in "
            "one run — remove some, or run the rest as a second analysis"
        )
    if len(locations) > MAX_LOCATIONS:
        return (
            f"a machine can carry at most {MAX_LOCATIONS} measurement locations in "
            "one run — remove some, or run the rest as a second analysis"
        )

    if extra and compare:
        return (
            "a before/after comparison is two readings of one measurement location — "
            "remove the extra locations, or run the comparison on its own"
        )
    if extra and mode != "spectrum":
        return (
            "several measurement locations can only be analysed from spectrum files — "
            "a trend file carries its own history for one point"
        )

    for loc in extra:
        if not loc.label:
            return (
                f"measurement location {loc.index} needs a label — name the point "
                "(for example Motor DE) so the report can tell them apart"
            )
        if len(loc.label) > LABEL_MAX_CHARS:
            return (
                f"the label for measurement location {loc.index} must be "
                f"{LABEL_MAX_CHARS} characters or fewer"
            )
        if not loc.slots:
            return (
                f"measurement location {loc.index} ({loc.label}) has a label but no "
                "file — add the measurement, or remove the location"
            )
        note = loc.fields.get("mount_note")
        if note is not None and len(str(note)) > MOUNT_NOTE_MAX_CHARS:
            return (
                f"the mounting note for {loc.label} must be "
                f"{MOUNT_NOTE_MAX_CHARS} characters or fewer"
            )
        speed = loc.fields.get("rpm")
        if speed is not None:
            try:
                if float(speed) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return (
                    f"the running speed for {loc.label} must be a positive number — "
                    "leave it blank to use the machine speed"
                )

    # `first_label` is location 1's `measurement_location`, and it is IN this
    # check rather than exempt from it. A collision between the first location
    # and an added one is the same collision as any other: two points of one
    # machine sharing a key, so one point's trend is filed under the other's and
    # the readings interleave into a series that belongs to neither. It is also
    # the collision an analyst is most likely to make -- they name the added
    # block first and then fill in the machine's own point with the same word.
    labels = [loc.label for loc in locations if loc.label]
    if first_label:
        labels.append(first_label.strip())
    duplicates = {name for name in labels if labels.count(name) > 1}
    if duplicates:
        return (
            f"two measurement locations are both called “{sorted(duplicates)[0]}” — "
            "give each point its own label so the trend for one is not filed under "
            "the other"
        )

    if extra:
        for loc in locations:
            for upload, _direction in loc.slots:
                suffix = "." + str(getattr(upload, "filename", "")).rsplit(".", 1)[-1].lower()
                if suffix in inferred_extensions:
                    name = loc.label or "the first location"
                    return (
                        f"“{getattr(upload, 'filename', 'that file')}” at {name} needs its "
                        "columns confirmed before it can be read, and that is a "
                        "conversation about one measurement location at a time — run "
                        "this location on its own, or upload a CSV with a header row"
                    )
    return None
