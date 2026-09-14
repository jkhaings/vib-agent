"""Consistency contract: after drafting, verify the model's narrative against
the ground-truth AnalysisResult.

Two independent checks, both enforced programmatically rather than trusted to
the system prompt alone -- prompt rules drift, programmatic checks don't:

1. Structural: the first non-whitespace line of the draft must be the exact
   report title (`# Vibration Survey Report — <machine name>`), matching the
   deterministic template's own heading. This is what catches tool-use
   narration or other preamble the model writes before the report itself.
2. Field-by-field: the model appends a structured echo block (fenced, JSON)
   restating what it claimed; this module parses it, strips it from the
   rendered markdown, and compares it against the AnalysisResult.

Either kind of mismatch is never silently accepted -- agent/loop.py retries
once with the diff appended, then hard-fails.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from vib_agent.models import AnalysisResult

ECHO_START = "<<<ECHO_START>>>"
ECHO_END = "<<<ECHO_END>>>"

# Must match report/templates/default_survey.md.j2's own opening line exactly
# ("# Vibration Survey Report — {{ machine.name }}") -- the drafted report and
# the deterministic report are required to open identically.
TITLE_TEMPLATE = "# Vibration Survey Report — {machine_name}"

_ECHO_RE = re.compile(re.escape(ECHO_START) + r"\s*(.*?)\s*" + re.escape(ECHO_END), re.DOTALL)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")

# Session A: a not_assessable reading (no velocity) has NO ISO severity zone, so
# the narrative must never name one. "ISO 20816" / "ISO severity" are allowed --
# they name the standard, not a zone verdict.
_ISO_ZONE_RE = re.compile(r"\bISO\s+zone\b", re.IGNORECASE)
_ZONE_LETTER_RE = re.compile(r"\bzone\s+[A-D]\b", re.IGNORECASE)

# Session LIMITS-1b. Two further rules on the PUBLISHED PROSE, for readings that
# ARE assessable -- where this check used to be a no-op entirely.
#
# Precision is the whole design, for the reason stated further down this file: a
# false positive costs a retry and then a DEGRADE on a correct report. So the
# zone scan reads only VERDICT frames -- the forms that commit the document to a
# letter -- and never every mention of the word. Measured against real renders,
# these frames do not reach "above the 4.5 mm/s Zone C/D boundary", "ISO zone at
# the time of measurement: D", "iso_zone_elevated: WARN", "the next zone
# boundary", a load zone, a wear zone, or the would-give clause, which names a
# DIFFERENT letter legitimately and is not a verdict about this machine.
_ZONE_VERDICT_FRAMES: list[re.Pattern[str]] = [
    # "ISO Zone D" -- adjacent, so "ISO 20816-3 ... would give Zone D" is not it
    re.compile(r"\bISO\s+Zone\s+([A-D])\b", re.IGNORECASE),
    # "is in Zone D" / "places this machine in ISO Zone D"
    re.compile(r"\b(?:is\s+in|placed\s+in|places\s+this\s+machine\s+in)\s+"
               r"(?:ISO\s+)?Zone\s+([A-D])\b", re.IGNORECASE),
    # the fault sheet's own health line, in either renderer's markup
    re.compile(r"\*\*Health:\*\*\s*(?:ISO\s+)?Zone\s+([A-D])\b", re.IGNORECASE),
]

# The phrase that attributes the judgement to the standard. Deliberately the
# "-3" spelling: "per ISO 20816" without it is the NOT-ASSESSABLE sentence
# ("a velocity measurement per ISO 20816 is required"), which is legitimate on
# any basis, and "(ISO 20816-3)" in the parameters table names the unit.
_PER_ISO_RE = re.compile(r"\bper\s+ISO\s+20816-3\b", re.IGNORECASE)


def check_title_line(text: str, machine_name: str) -> str | None:
    """Return a mismatch message if the draft's first non-whitespace line
    isn't exactly the report title -- i.e. any preamble, tool-use narration,
    or other meta-commentary precedes the report. None means the check
    passed."""
    expected = TITLE_TEMPLATE.format(machine_name=machine_name)
    stripped = text.lstrip()
    first_line = stripped.splitlines()[0].strip() if stripped else ""
    if first_line != expected:
        return (
            f"title: draft must open with {expected!r} as its very first line, with no "
            f"preamble or narration before it -- got {first_line!r}"
        )
    return None


def parse_echo_block(text: str) -> dict[str, Any] | None:
    """Extract and JSON-parse the echo block. None if absent or malformed --
    treated as a consistency failure by the caller, never as a crash."""
    match = _ECHO_RE.search(text)
    if match is None:
        return None
    payload = _FENCE_RE.sub("", match.group(1).strip()).strip()
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def strip_echo_block(text: str) -> str:
    """Remove the echo block (and its markers) from the drafted text, leaving
    only the narrative report that gets published."""
    return _ECHO_RE.sub("", text).strip()


def _committed_faults(result: AnalysisResult) -> dict[str, str]:
    """fault -> confidence, from the committed findings only. Excludes the
    'no_significant_findings' sentinel, which the echo block never claims."""
    return {f.fault: f.confidence for f in result.findings if f.fault != "no_significant_findings"}


def check_zone_language(text: str, result: AnalysisResult) -> list[str]:
    """HARD structural assertion (Session A): when the AnalysisResult's ISO zone
    is not_assessable, the drafted narrative must not name an ISO severity zone
    anywhere -- severity was not assessed (no velocity), so any "ISO Zone X" /
    "Zone A" claim is unsupportable. Scans the published narrative (echo block
    stripped -- the echo legitimately carries the zone field) and returns
    mismatches, which ride the same retry-with-diff-then-hard-fail path as the
    title and echo checks.

    Session LIMITS-1b adds two rules for readings that ARE assessable, where
    this check used to do nothing at all (LIMITS-1a F-3):

    2. **The letter must be the analysis's own letter**, on either basis. A
       draft that says "ISO Zone C" over a Zone D analysis is refused, as it
       always should have been.
    3. **A custom basis may not be attributed to the standard.** When
       `zone_basis == "custom"` the zone came from a limit somebody set, and a
       narrative saying "per ISO 20816-3" over that number signs ISO's name to
       it -- which is what the deterministic report itself did until this
       session, and what a drafting model will copy if nothing refuses it.

    Nothing is weakened: rule 1 is untouched, and rule 2 applies on both bases.
    """
    if result.iso is None:
        return []
    if result.iso.iso_zone != "not_assessable":
        return _check_assessable_zone_language(strip_echo_block(text), result)
    narrative = strip_echo_block(text)
    mismatches: list[str] = []
    for rx in (_ISO_ZONE_RE, _ZONE_LETTER_RE):
        m = rx.search(narrative)
        if m is not None:
            mismatches.append(
                f"zone language: this reading is not_assessable (velocity not measured), so the "
                f"report must not name an ISO severity zone, but the narrative says {m.group(0)!r}. "
                f"Render severity as 'unrated -- ISO severity requires velocity data' and state the "
                f"Severity & Coverage boundary instead of a zone."
            )
    return mismatches


def _check_assessable_zone_language(narrative: str, result: AnalysisResult) -> list[str]:
    """Rules 2 and 3 of `check_zone_language`, for a reading that HAS a zone."""
    iso = result.iso
    if iso is None:  # the caller has established this; kept for the type-checker
        return []
    zone = (iso.iso_zone or "").upper()
    mismatches: list[str] = []

    for rx in _ZONE_VERDICT_FRAMES:
        for m in rx.finditer(narrative):
            if m.group(1).upper() != zone:
                mismatches.append(
                    f"zone language: the analysis places this machine in Zone {iso.iso_zone}, "
                    f"but the narrative says {m.group(0)!r}. State the zone the analysis "
                    f"computed; a report may not name a severity zone no tool result produced."
                )
                break  # one message per frame -- the diff rides a retry prompt

    if iso.zone_basis == "custom":
        m = _PER_ISO_RE.search(narrative)
        if m is not None:
            limits = " / ".join(
                f"{v:g}" for v in (iso.th_ab, iso.th_bc, iso.th_cd) if v is not None
            )
            mismatches.append(
                f"zone language: this machine is judged against machine-specific limits "
                f"({limits} mm/s RMS), not against ISO 20816-3, but the narrative says "
                f"{m.group(0)!r}. Say 'per machine-specific limits', and name ISO only in the "
                f"clause stating what ISO 20816-3 would have given the same reading."
            )
    return mismatches


# ─────────────────────────────────────────────────────────────────────────
# Session D: narrative-level fault + confidence checks.
#
# check_echo_against_result validates the model's own JSON echo block, and
# check_zone_language was the only check that read the PUBLISHED PROSE. RUN v5
# found a drafted report that emitted an honest echo (faults: []) while the prose
# asserted "The committed diagnosis is Rotor imbalance, medium confidence" on a
# healthy machine -- invisible to every check. These two close that gap.
#
# Precision matters more than recall here: a false positive costs a retry and
# then a degrade on a CORRECT report. Reports legitimately name fault families in
# the computed fault-frequency table (which always lists BPFO/BPFI/BSF/FTF), in
# the differential ("also considered"), and in negations ("do not correspond to
# bearing fault frequencies"). So we scan only DIAGNOSIS-ASSERTIVE frames -- the
# forms that actually commit the report to a call -- never every mention.
# ─────────────────────────────────────────────────────────────────────────

# Closed lexicon: pdm_core fault id -> how that fault is named in prose (the
# report's own FAULT_LABELS wording, the acronym, and common analyst synonyms).
# Ordered MOST SPECIFIC FIRST: "parallel misalignment" must resolve to
# parallel_misalignment, not to the generic misalignment term that also matches.
_FAULT_LEXICON: list[tuple[str, re.Pattern[str]]] = [
    ("bearing_outer_race", re.compile(r"outer[\s\-]?race|\bBPFO\b", re.IGNORECASE)),
    ("bearing_inner_race", re.compile(r"inner[\s\-]?race|\bBPFI\b", re.IGNORECASE)),
    ("bearing_ball_spin", re.compile(r"ball[\s/\-]?(?:spin|roller)|roller fault|\bBSF\b", re.IGNORECASE)),
    ("bearing_cage", re.compile(r"\bcage\b|\bFTF\b|fundamental train", re.IGNORECASE)),
    ("angular_misalignment", re.compile(r"angular\s+misalign\w*", re.IGNORECASE)),
    ("parallel_misalignment", re.compile(r"parallel\s+misalign\w*", re.IGNORECASE)),
    ("severe_misalignment", re.compile(r"severe\s+misalign\w*", re.IGNORECASE)),
    ("misalignment_general", re.compile(r"misalign\w*", re.IGNORECASE)),
    ("bent_shaft", re.compile(r"bent\s+shaft", re.IGNORECASE)),
    ("mechanical_looseness", re.compile(r"looseness", re.IGNORECASE)),
    ("imbalance", re.compile(r"\b(?:im|un)balance\b", re.IGNORECASE)),
    ("belt_fault", re.compile(r"belt[\s\-]?(?:drive\s+)?(?:fault|defect|wear)", re.IGNORECASE)),
    ("elevated_blade_pass", re.compile(r"blade[\s\-]?pass", re.IGNORECASE)),
    ("possible_resonance", re.compile(r"resonance", re.IGNORECASE)),
    ("rising_trend", re.compile(r"rising\s+(?:vibration\s+)?trend", re.IGNORECASE)),
    ("elevated_vibration_undetermined", re.compile(r"elevated\s+vibration", re.IGNORECASE)),
]

# A generic family term is satisfied by any committed member of its family: prose
# saying "misalignment" is honest when the analysis committed parallel_misalignment.
# The reverse is NOT true -- naming a sub-type the analysis did not commit is an
# upgrade, and is rejected.
_FAULT_FAMILIES: dict[str, frozenset[str]] = {
    "misalignment_general": frozenset(
        {"angular_misalignment", "parallel_misalignment", "severe_misalignment", "misalignment_general"}
    ),
}

# Diagnosis-assertive frames. Each captures the span in which a fault is being
# asserted as THE call. Deliberately narrow.
_ASSERTIVE_FRAMES: list[re.Pattern[str]] = [
    # "The committed diagnosis for <machine> is <SPAN>" (generate.py's own phrasing)
    re.compile(r"committed\s+diagnosis[^.\n]{0,80}?\bis\b([^.\n]{0,120})", re.IGNORECASE),
    # "Diagnosis: <SPAN>" / "diagnosis is <SPAN>"
    re.compile(r"\bdiagnosis\s*(?:is|:)\s*([^.\n]{0,120})", re.IGNORECASE),
    # "diagnosed with <SPAN>"
    re.compile(r"\bdiagnosed\s+with\s+([^.\n]{0,120})", re.IGNORECASE),
    # The Diagnosis section heading: "### <SPAN> — severity: ..."
    re.compile(r"^#{2,5}\s+([^\n]{0,120}?)\s*[—:-]\s*severity", re.IGNORECASE | re.MULTILINE),
]

# Session F2: the DIFFERENTIAL-QUALIFIED headline. When a committed call coexists
# with a strong candidate from another fault family, the report may lead with
# "Possible <X> with evidence of <Y> — further validation recommended."
#
# That clause names TWO faults at once, in two different roles, so it gets its own
# rule rather than a hole in the assertive-frame scan. The rule is STRICTER than
# what a bare assertion faces, not looser:
#
#   X ("possible ...")          must be COMMITTED, exactly as any asserted
#                               diagnosis must be; and
#   Y ("with evidence of ...")  must be a real differential candidate (or itself
#                               committed) -- a fault the AnalysisResult never
#                               raised cannot be smuggled in as corroboration.
#
# Without this, the frame scan resolves the FIRST lexicon hit in the span, which
# for "Possible imbalance with evidence of BPFO" is the bearing term -- i.e. it
# would read the corroborating candidate as the committed call and reject a
# correct report. Note both roles are still checked; nothing is exempted.
_QUALIFIED_HEADLINE_RE = re.compile(
    r"\bpossible\s+(?P<primary>[^.\n]{1,90}?)\s+with\s+evidence\s+of\s+"
    r"(?P<secondary>[^.\n]{1,90}?)\s*[—–-]{1,2}\s*further\s+validation\s+recommended",
    re.IGNORECASE,
)

# Bind a confidence level to the literal word "confidence" so ordinary technical
# prose ("low-frequency peak", "high-pass filter") can never be mistaken for one.
_CONFIDENCE_BINDING = re.compile(
    r"\b(high|medium|low)\b[\s\-]*confidence|confidence[^.\n]{0,24}?\b(high|medium|low)\b",
    re.IGNORECASE,
)


def _resolve_fault_term(span: str) -> str | None:
    """Which fault id this span names, most-specific-first. None if no fault term."""
    for fault_id, pattern in _FAULT_LEXICON:
        if pattern.search(span):
            return fault_id
    return None


def _differential_faults(result: AnalysisResult) -> dict[str, str]:
    """fault -> confidence for candidates the interaction rules did NOT commit."""
    if result.rca is None:
        return {}
    return {d.fault: d.confidence for d in result.rca.differential}


def _satisfied_by(named: str, allowed: dict[str, str]) -> bool:
    """A named fault is satisfied by an exact match, or -- for a generic family
    term -- by any committed member of that family."""
    if named in allowed:
        return True
    family = _FAULT_FAMILIES.get(named)
    return bool(family and (family & set(allowed)))


def _assertion_mismatch(
    named: str, span: str, committed: dict[str, str], differential: dict[str, str]
) -> str:
    """The message for a fault term asserted as THE diagnosis that the
    AnalysisResult does not support at that tier."""
    if not committed:
        return (
            f"fault language: the AnalysisResult committed NO findings for this reading, "
            f"so the report must not assert any diagnosis -- but the narrative asserts "
            f"{named!r} (in {span.strip()!r}). State the no-findings result explicitly "
            f"('Committed diagnosis: none — parameters within normal range'), describe "
            f"what was checked, and do not name a fault as the diagnosis."
        )
    if _satisfied_by(named, differential):
        return (
            f"fault language: {named!r} is a DIFFERENTIAL candidate (considered and not "
            f"committed), but the narrative asserts it as the diagnosis (in "
            f"{span.strip()!r}). The committed finding(s) are {sorted(committed)}. Report "
            f"{named!r} only in the differential, with its adjudication."
        )
    return (
        f"fault language: the narrative asserts {named!r} as the diagnosis (in "
        f"{span.strip()!r}), but the AnalysisResult committed {sorted(committed)} and "
        f"carries no such finding. Every fault named as a diagnosis must come from the "
        f"AnalysisResult."
    )


# ─────────────────────────────────────────────────────────────────────────
# Session TFIX: the baseline-training decision is not analyst-facing.
# ─────────────────────────────────────────────────────────────────────────
#
# A shipped drafted sample said:
#
#   "This reading was not used to update the machine's baseline, as ISO Zone D
#    readings are excluded from baseline training."
#
# That is NOT a hallucination, and treating it as one would be the wrong fix.
# It is an accurate relay of two computed fields the model is handed in its
# ground-truth JSON: `quality_gate.train_baseline` is False and
# `quality_gate.train_reasons` is `["iso_zone_D"]`, because `run_quality_gate`
# appends `iso_zone_{zone}` for zones C and D and its own docstring says it
# decides "whether Layer 2 may train its baseline on this reading".
#
# The problem is the SUBJECT, not the truth. On a single-file analysis there is
# no Layer 2 baseline and no stored machine history for one to belong to — the
# report's own Limitations say so — so "the machine's baseline" names a thing
# the reader does not have, and "was not used to update" implies a maintenance
# process that did not run because there is nothing to maintain. No report on
# any path publishes this decision; it is an internal signal for a layer that
# did not execute.
#
# So the rule is narrow: on a reading with no baseline layer, the narrative may
# not NARRATE the training decision. It stays free to say a baseline is absent
# — which is what the deterministic limitation does — and free to relay the
# cause library's field-measurement sense of the word.
_BASELINE_VERBS = r"updat\w*|train\w*|exclud\w*|includ\w*|add(?:ed|ing)?|fed|feed\w*|incorporat\w*|contribut\w*"
_BASELINE_CLAIM_RES = (
    # verb ... baseline   ("excluded from baseline training", "used to update the machine's baseline")
    re.compile(rf"\b(?:{_BASELINE_VERBS})\b[^.;]{{0,60}}?\bbaseline\b", re.IGNORECASE),
    # baseline ... verb   ("the baseline was updated", "the baseline is trained on")
    re.compile(rf"\bbaseline\b[^.;]{{0,60}}?\b(?:{_BASELINE_VERBS})\b", re.IGNORECASE),
)


def check_baseline_claims(text: str, result: AnalysisResult) -> list[str]:
    """The narrative must not narrate the baseline-TRAINING decision when no
    baseline layer ran.

    `result.zscore is None` is the discriminator, and it is the real one: the
    Welford z-score layer IS the baseline, so when it did not run there is
    nothing that could have been trained, updated or excluded. On a streaming
    reading that carries one, this check is a no-op and the model may describe
    it.

    Deliberately narrow. Two uses of the word appear in the very report this
    check was written for and both must survive:

      * "requires a streaming baseline, which a single-file analysis does not
        carry" — the product's own limitation, a statement that there is NO
        baseline;
      * "compare the first post-maintenance reading with the pre-removal
        baseline" — the cause library's field-measurement sense, which is about
        the analyst's own records and not about this system at all.
    """
    if result.zscore is not None:
        return []
    narrative = strip_echo_block(text)
    mismatches: list[str] = []
    seen: set[str] = set()
    for rx in _BASELINE_CLAIM_RES:
        for m in rx.finditer(narrative):
            span = m.group(0).strip()
            key = span.lower()
            if key in seen:
                continue
            seen.add(key)
            mismatches.append(
                f"baseline claim: the narrative says {span!r}, but no baseline layer ran on this "
                f"reading (zscore is null), so there is nothing that was trained, updated or "
                f"excluded. `quality_gate.train_baseline` and `train_reasons` are an INTERNAL "
                f"signal for Layer 2 and are published in no report on any path — do not relay "
                f"them. If the absence matters, the Limitations section already states it."
            )
    return mismatches


# ─────────────────────────────────────────────────────────────────────────
# Session REPORT-4, item 2 — a channel that was never measured is not evidence.
#
# The shipped report the operator read said, on a job with ONE uploaded channel:
#
#   "The axial axis (x) showed no elevated bearing-frequency content; the
#    axial-to-radial ratio is 0.0, consistent with a radially loaded outer-race
#    defect."
#
# Both halves are fabrications, and the second is where the first came from.
# `pdm_core/bearing_rca.py:366` computes the ratio as `v.get(axial, 0.0) /
# v_radial_max`, and an axis that was never measured is absent from `v` — so it
# reads 0.0, which is indistinguishable from an axis that was measured and found
# silent. The model was handed that 0.0 as ground truth and did the only
# reasonable thing with it: reported a quiet axial channel as corroboration.
#
# Two defences, because either alone leaves the door open:
#
#   1. `loop.py` no longer SHOWS the model a value derived from an unmeasured
#      axis — it is masked to "not measured" in the ground-truth JSON. That
#      removes the cause.
#   2. this check refuses the SENTENCE, whatever put the idea there. A drafting
#      model does not need a 0.0 in its input to write "the axial axis showed
#      nothing"; it needs only to know the machine has three axes.
#
# What must NOT be refused is the honest form. "Only the y-axis velocity was
# measured; the x-axis was not available" is the report telling the truth about
# its own coverage, and it names an unmeasured axis in every clause. So a
# sentence that itself states the absence is exempt, and the check looks for the
# conjunction of an unmeasured axis AND an evidential claim about it.

#: Words that make a sentence a statement OF ABSENCE rather than a claim about
#: what an axis showed. Any of these and the sentence is the report being honest.
_ABSENCE_MARKERS: tuple[str, ...] = (
    "not measured", "was not measured", "were not measured", "not available",
    "not collected", "not captured", "not supplied", "not uploaded", "not provided",
    "unavailable", "could not", "cannot", "no velocity", "not assessed",
    "was not", "were not", "is not available", "absent", "missing", "only the",
    "not enable", "would enable", "would additionally", "single-axis", "single axis",
)

#: An evidential claim about a channel: it showed / shows / carries / contains
#: something, or a number is quoted for it.
_EVIDENCE_CLAIM_RE = re.compile(
    r"\b(showed|shows|showing|carried|carries|contains|contained|confirmed|confirms|"
    r"exhibited|exhibits|displayed|displays|indicated|indicates|revealed|reveals|"
    r"ratio is|ratio of|consistent with|corroborat\w*|support\w* the)\b",
    re.IGNORECASE,
)

#: How a narrative names an axis: "the x-axis", "axis (x)", "the axial axis",
#: "on x". Built per unmeasured axis, so a report with all three measured runs
#: no patterns at all.
def _axis_mention_res(axis: str, axial_axis: str | None) -> list[re.Pattern[str]]:
    pats = [rf"\b{axis}-axis\b", rf"\baxis\s*\(\s*{axis}\s*\)\b", rf"\bthe\s+{axis}\s+axis\b"]
    if axial_axis is not None and axis == axial_axis:
        pats.append(r"\bthe\s+axial\s+axis\b")
        pats.append(r"\baxial-to-radial\b")
        pats.append(r"\baxial\s+(?:channel|measurement|reading|velocity)\b")
    return [re.compile(p, re.IGNORECASE) for p in pats]


#: Sentence-ish spans. Deliberately crude: this splits on terminators AND on
#: semicolons, because the sentence that prompted this check hid its fabrication
#: in the clause AFTER a semicolon ("...; the axial-to-radial ratio is 0.0").
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")


def check_measured_channels(
    text: str, result: AnalysisResult, measured_axes: Sequence[str] | None
) -> list[str]:
    """Refuse a drafted sentence that offers an UNMEASURED channel as evidence.

    `measured_axes` is `PeakSet.measured_axes` — the axes that actually reported
    a reading. None means unknown, and unknown is not a licence: the check is a
    no-op, exactly as every other consumer of that field treats None (see
    `models.py:495`). An empty measured set is likewise a no-op, because a
    reading with no measured axis at all is a gate-fail and never reaches a
    draft."""
    if not measured_axes:
        return []
    unmeasured = [a for a in ("x", "y", "z") if a not in measured_axes]
    if not unmeasured:
        return []
    axial_axis = result.rca.axial_axis if result.rca is not None else None
    patterns = {a: _axis_mention_res(a, axial_axis) for a in unmeasured}

    narrative = strip_echo_block(text)
    mismatches: list[str] = []
    seen: set[str] = set()
    for raw in _SENTENCE_SPLIT_RE.split(narrative):
        span = " ".join(raw.split())
        if not span:
            continue
        lowered = span.lower()
        if any(marker in lowered for marker in _ABSENCE_MARKERS):
            continue
        if not _EVIDENCE_CLAIM_RE.search(span):
            continue
        for axis, res in patterns.items():
            if not any(rx.search(span) for rx in res):
                continue
            key = (axis, lowered)
            if key in seen:
                continue
            seen.add(key)
            measured = ", ".join(measured_axes)
            mismatches.append(
                f"unmeasured channel cited as evidence: the narrative says {span!r}, but the "
                f"{axis!r} axis was NOT measured on this reading — the measured axes are "
                f"{measured}. An absent channel is not a quiet one: nothing was collected "
                f"there, so it can neither show a signature nor fail to show one, and any "
                f"ratio computed against it is an artefact of the missing value rather than a "
                f"measurement. State the coverage limit instead, or say nothing about that axis."
            )
            break
    return mismatches


def check_fault_term_language(text: str, result: AnalysisResult) -> list[str]:
    """HARD structural assertion (Session D): every diagnosis-assertive use of a
    fault term in the PUBLISHED PROSE must correspond to a finding the
    AnalysisResult actually carries, at the tier the prose asserts.

    On a no-findings result, assertive diagnosis language is forbidden outright --
    that is the exact shape of the RUN v5 fabrication (healthy machine, prose
    committed to "Rotor imbalance").

    Session F2 adds the differential-qualified headline ("Possible X with evidence
    of Y — further validation recommended") as a SEPARATE, stricter rule: X is
    held to the committed bar, and Y must be a candidate the AnalysisResult
    actually raised."""
    narrative = strip_echo_block(text)
    committed = _committed_faults(result)
    differential = _differential_faults(result)
    mismatches: list[str] = []
    seen: set[str] = set()

    # ── 1. differential-qualified headlines, on their own (stricter) terms ──
    for match in _QUALIFIED_HEADLINE_RE.finditer(narrative):
        primary_span, secondary_span = match.group("primary"), match.group("secondary")
        primary_named = _resolve_fault_term(primary_span)
        secondary_named = _resolve_fault_term(secondary_span)
        if primary_named is not None and primary_named not in seen and not _satisfied_by(
            primary_named, committed
        ):
            seen.add(primary_named)
            mismatches.append(_assertion_mismatch(primary_named, primary_span, committed, differential))
        if (
            secondary_named is not None
            and not _satisfied_by(secondary_named, differential)
            and not _satisfied_by(secondary_named, committed)
        ):
            mismatches.append(
                f"qualified headline: the narrative offers {secondary_named!r} as corroborating "
                f"evidence (in {match.group(0).strip()!r}), but the AnalysisResult neither "
                f"committed it nor raised it in the differential "
                f"({sorted(differential) or 'empty'}). 'with evidence of' may only name a "
                f"candidate the analysis actually raised."
            )

    # ── 2. the ordinary assertive frames ───────────────────────────────────
    for frame in _ASSERTIVE_FRAMES:
        for match in frame.finditer(narrative):
            span = match.group(1)
            # A qualified headline inside this span was fully checked above, by a
            # stricter rule; drop it so its two roles are not re-read as one
            # assertion (which would resolve to whichever fault the lexicon hits
            # first, not the one being committed). Any fault named OUTSIDE the
            # clause is still checked here.
            span = _QUALIFIED_HEADLINE_RE.sub(" ", span)
            named = _resolve_fault_term(span)
            if named is None or named in seen:
                continue
            if _satisfied_by(named, committed):
                continue
            seen.add(named)
            mismatches.append(_assertion_mismatch(named, span, committed, differential))
    return mismatches


def check_confidence_binding(text: str, result: AnalysisResult) -> list[str]:
    """HARD structural assertion (Session D): a confidence level stated next to a
    fault term in the prose must equal that finding's computed confidence. Catches
    silent upgrades ("high confidence" over a medium finding) that the echo block
    would still report honestly."""
    narrative = strip_echo_block(text)
    committed = _committed_faults(result)
    differential = _differential_faults(result)
    known = {**differential, **committed}  # committed wins on overlap
    if not known:
        return []

    mismatches: list[str] = []
    seen: set[tuple[str, str]] = set()
    for match in _CONFIDENCE_BINDING.finditer(narrative):
        stated = (match.group(1) or match.group(2) or "").lower()
        # nearest preceding fault term within the same sentence-ish window.
        # "with evidence of" is a boundary too (Session F2): in "Possible X (high
        # confidence) with evidence of Y (medium confidence)" the second level
        # belongs to Y, and without the cut it would be read against X.
        window = narrative[max(0, match.start() - 160) : match.start()]
        cut = max(window.rfind(". "), window.rfind("\n\n"), window.lower().rfind("with evidence of"))
        named = _resolve_fault_term(window[cut + 1 :] if cut != -1 else window)
        if named is None:
            continue
        expected = known.get(named)
        if expected is None:
            family = _FAULT_FAMILIES.get(named)
            if family:
                expected = next((known[f] for f in sorted(family) if f in known), None)
        if expected is None or stated == expected or (named, stated) in seen:
            continue
        seen.add((named, stated))
        mismatches.append(
            f"confidence binding: the narrative states {stated!r} confidence for {named!r}, but "
            f"the AnalysisResult computed {expected!r}. Relay the computed confidence exactly."
        )
    return mismatches


# ─────────────────────────────────────────────────────────────────────────
# Session H: the numeric-quote check.
#
# The LLM never does math -- but it does TRANSCRIBE numbers into the executive
# summary, and a transcription slip reads exactly like an analysis result. The
# field-report failure class is a decimal-place typo: a 4.52 mm/s overall quoted
# as 45.2 mm/s turns a Zone C reading into a catastrophic one, and every other
# check passes (the echo block is honest, the fault name is right, the
# confidence matches).
#
# So: every decimal quoted in the executive summary must be a number the
# analysis actually computed, within the tolerance of its own stated precision.
# The allowed set is built from the AnalysisResult itself -- every numeric leaf,
# every number appearing inside a pdm_core-authored string (evidence sentences,
# confidence-factor details, threshold notes), plus the shaft- and fault-frequency
# harmonics a report legitimately names. Scoped to the executive summary because
# that is where the headline numbers land and because a false positive costs a
# retry and then a degrade on an otherwise correct report.
# ─────────────────────────────────────────────────────────────────────────

_EXEC_SUMMARY_RE = re.compile(
    r"^#{1,4}[ \t]*Executive Summary[ \t]*$\n(.*?)(?=^#{1,4}[ \t]|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)

# A bare decimal. The lookbehind keeps out identifiers ("v1.5") and the tail of
# a longer dotted run ("6.5.2" -> ".2"); the lookahead keeps out the head of one
# ("6.5.2" -> "6.5") but deliberately ALLOWS a glued unit, so "45.2mm/s" and
# "107.2Hz" are still checked. Integers are excluded -- "ISO 20816-3",
# "2 additional findings" and "30 days" are not measurements.
_DECIMAL_RE = re.compile(r"(?<![\w.])(\d{1,7}\.\d{1,6})(?![\d.])")

# Numbers inside pdm_core-authored strings are computed values too.
_NUMBER_IN_TEXT_RE = re.compile(r"(?<![\w])(\d{1,9}(?:\.\d{1,6})?)")

_SHAFT_ORDERS = (0.5, 1, 2, 3, 4, 5, 6)
_FAULT_HARMONICS = (1, 2, 3)


# Session NQ-TS -- metadata keys whose numerals are NOT measurements: wall-clock
# time and machine/site identity. `AnalysisResult.ts` is a plain `str`
# (models.py:555), so the walk below used to harvest every number in it. From
# "2026-08-25T07:52:11.123456+00:00" the MINUTE is preceded by ":" and lands in
# the whitelist (the hour is shielded only by the "T", which the lookbehind
# reads as a word char) -- so a drafted "52.00 mm/s" typo passed the check for
# one minute in every hour. That is the 07:52 UTC gate failure. Identity fields
# leak the same way: "SYN-COMP-01" contributes 1.0.
#
# pdm_core's own prose is deliberately NOT excluded -- the numerals in
# findings[].reason and primary_findings[].evidence ARE computed values a
# narrative may legitimately quote, which
# test_numeric_quotes.py::test_numbers_from_pdm_core_evidence_prose_pass pins.
_METADATA_KEYS = frozenset(
    {"ts", "mac", "machine_id", "factory_id", "factory_timezone", "machine_type"}
)


def _walk_numbers(node: Any, sink: set[float]) -> None:
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        sink.add(float(node))
    elif isinstance(node, str):
        for token in _NUMBER_IN_TEXT_RE.findall(node):
            try:
                sink.add(float(token))
            except ValueError:  # pragma: no cover - regex guarantees parseability
                continue
    elif isinstance(node, dict):
        for key, value in node.items():
            if key in _METADATA_KEYS:
                continue
            _walk_numbers(value, sink)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _walk_numbers(value, sink)


def computed_numbers(result: AnalysisResult) -> set[float]:
    """Every number this analysis computed or was configured with, as the
    narrative is allowed to quote it -- including the harmonics of the shaft
    rate and of each computed fault frequency, which a report names routinely
    ("2x BPFO at 214.1 Hz") and which are exact multiples, not new math."""
    allowed: set[float] = set()
    _walk_numbers(result.model_dump(), allowed)

    rca = result.rca
    if rca is not None:
        if rca.shaft_freq_hz:
            for order in _SHAFT_ORDERS:
                allowed.add(rca.shaft_freq_hz * order)
        if rca.bearing_freqs is not None:
            for base in rca.bearing_freqs.model_dump().values():
                if isinstance(base, (int, float)):
                    for harmonic in _FAULT_HARMONICS:
                        allowed.add(float(base) * harmonic)
        for match in rca.primary_findings:
            for base in (match.freq_hz, match.expected_hz):
                if base:
                    for harmonic in _FAULT_HARMONICS:
                        allowed.add(float(base) * harmonic)
        # Session REPORT-3 (item 4) -- the SHAFT ORDER of each of those
        # frequencies, closing SESSION_REPORT2.md F-1.
        #
        # REPORT-2 put "107.25 Hz (3.58x)" beside every fault frequency in the
        # report EXCEPT the Executive Summary, and the exception was this
        # function: an order RATIO is not a frequency, so a model transcribing
        # the deterministic summary verbatim was refused on '3.58', retried, and
        # could degrade the shop-window sample. That was measured at the time,
        # not assumed (tests/test_report2_diagnosis.py).
        #
        # This WIDENS the whitelist; it does not weaken the contract (common law
        # #7). Every value added is computed here, from numbers the result
        # already holds, by the same arithmetic the renderer uses --
        # `round(f / shaft, 2)`, which is exactly `charts.fmt_hz_order`'s
        # `{f/shaft:.2f}` and `generate._order_cell`'s. The set it adds is small
        # and bounded: one ratio per fault frequency already in `allowed` above,
        # never a free-floating decimal. A narrative that quotes an order the
        # analysis did not compute is still refused.
        if rca.shaft_freq_hz:
            shaft = float(rca.shaft_freq_hz)
            ordered: list[float] = []
            if rca.bearing_freqs is not None:
                ordered += [float(v) for v in rca.bearing_freqs.model_dump().values()
                            if isinstance(v, (int, float))]
            for match in rca.primary_findings:
                ordered += [float(v) for v in (match.freq_hz, match.expected_hz) if v]
            for freq in ordered:
                allowed.add(round(freq / shaft, 2))
    return allowed


def _quote_matches(quoted: str, allowed: set[float]) -> bool:
    """True when `quoted` is one of the computed values, read at the precision
    the narrative chose to state it in. Accepts both round-to-nearest and
    truncation, since analysts and models write both."""
    value = float(quoted)
    decimals = len(quoted.split(".")[1])
    half_ulp = 0.5 * (10.0**-decimals) + 1e-9
    ulp = (10.0**-decimals) + 1e-9
    for computed in allowed:
        if abs(value - computed) <= half_ulp:
            return True
        if 0.0 <= (abs(computed) - abs(value)) < ulp and (computed >= 0) == (value >= 0):
            return True
    return False


def check_numeric_quotes(text: str, result: AnalysisResult) -> list[str]:
    """HARD structural assertion (Session H): every decimal in the drafted
    executive summary must trace to a computed value, within the rounding
    tolerance of its own stated precision. Mismatches ride the same
    retry-with-diff-then-hard-fail path as every other consistency check."""
    narrative = strip_echo_block(text)
    match = _EXEC_SUMMARY_RE.search(narrative)
    if match is None:
        return []
    section = match.group(1)
    allowed = computed_numbers(result)

    mismatches: list[str] = []
    seen: set[str] = set()
    for quoted in _DECIMAL_RE.findall(section):
        if quoted in seen or _quote_matches(quoted, allowed):
            continue
        seen.add(quoted)
        mismatches.append(
            f"numeric quote: the executive summary states {quoted!r}, which is not a value this "
            f"analysis computed (checked against every number in the AnalysisResult, at the "
            f"precision quoted). Every number in the narrative must be transcribed from the "
            f"AnalysisResult exactly -- check for a misplaced decimal point or a dropped digit."
        )
    return mismatches


# ── Session R1 — damage stage language ────────────────────────────────────
# The stage block itself is rendered deterministically and spliced in after the
# draft, so the model never authors it. But the model IS shown the deterministic
# report as its reference structure, so it can legitimately restate the stage in
# prose -- and that restatement is exactly where a stage can drift or be
# invented. These two failures are what the check exists for:
#
#   * the narrative names a DIFFERENT stage than the one computed;
#   * the narrative names a stage when NO stage was computed (fabrication) --
#     the most dangerous case, because "Stage 4" on a machine we could not stage
#     is an unearned severity claim the honesty floor exists to prevent.
_STAGE_NUMBER_RE = re.compile(
    r"\bstage\s*[-– ]?\s*(1|2|3|4|one|two|three|four)\b", re.IGNORECASE
)
# Qualitative stage claims with no number. Only used for the fabrication check;
# requiring a number elsewhere keeps false positives near zero on a hard gate.
_QUALITATIVE_STAGE_RE = re.compile(
    r"\b(early|advanced|late|terminal|final)[-– ]stage\b", re.IGNORECASE
)
# Our OWN wording, which the model is supposed to carry through verbatim. It
# contains "early-stage" and must never be read as a fabricated stage claim.
_OWN_STAGE_PHRASES = (
    "early-stage detection requires hf/ultrasonic trending",
    "early-stage defect from an incidental tone",
)
_STAGE_WORD_TO_NUMBER = {
    "one": "1", "two": "2", "three": "3", "four": "4",
    "1": "1", "2": "2", "3": "3", "4": "4",
}


def _strip_own_stage_phrases(text: str) -> str:
    lowered = text.lower()
    for phrase in _OWN_STAGE_PHRASES:
        lowered = lowered.replace(phrase, "")
    return lowered


def check_stage_language(
    text: str, result: AnalysisResult, staging_cfg: dict[str, Any] | None = None
) -> list[str]:
    """HARD structural assertion (Session R1): drafted stage wording must match
    the computed stage, and no stage language may appear when none was computed.

    `staging_cfg` is the already-resolved staging profile; the caller loads it
    (pdm_core is pure and does no I/O). Passing None uses the module's
    documented defaults.
    """
    from vib_agent.pdm_core.staging import classify_bearing_stage

    narrative = strip_echo_block(text)
    scrubbed = _strip_own_stage_phrases(narrative)
    stage = classify_bearing_stage(result, staging_cfg)

    numbered = {
        _STAGE_WORD_TO_NUMBER[m.group(1).lower()] for m in _STAGE_NUMBER_RE.finditer(scrubbed)
    }
    qualitative = bool(_QUALITATIVE_STAGE_RE.search(scrubbed))

    # No stage was computed -- any stage claim is invented.
    if not stage.determinable:
        if not numbered and not qualitative:
            return []
        named = ", ".join(f"stage {n}" for n in sorted(numbered)) or "a qualitative stage"
        why = (
            "no bearing fault was committed"
            if stage.fault is None
            else "the evidence did not reach stage 3, so the stage is not determinable"
        )
        return [
            f"damage stage: the narrative claims {named}, but this analysis computed no damage "
            f"stage -- {why}. Stages 1-2 are invisible to route-band spectra, so an early stage "
            "cannot be asserted from this measurement and a later one was not evidenced. Remove "
            "the stage language, or state only what the Damage Stage Estimate section says."
        ]

    # A stage WAS computed -- any number the narrative names must be that one.
    expected = "4" if stage.stage == "stage_4_suspected" else "3"
    wrong = sorted(numbered - {expected})
    if wrong:
        named = ", ".join(f"stage {n}" for n in wrong)
        return [
            f"damage stage: the narrative names {named}, but this analysis computed "
            f"{stage.label!r} for {stage.fault}. Transcribe the stage from the Damage Stage "
            "Estimate section exactly -- it is not a judgement the narrative may re-make."
        ]
    return []


# ─────────────────────────────────────────────────────────────────────────
# Session R2-BUILD — cause language.
#
# The cause section is rendered deterministically from knowledge/causes.yaml and
# spliced in after drafting, exactly like the Session R1 stage block. But the
# model is shown the deterministic report as its reference structure, so it can
# reproduce or restate the section -- and a cause is a far more attractive thing
# to embellish than a stage number, because plausible bearing causes are exactly
# what a language model has an enormous, unsourced supply of.
#
# The failure this closes is therefore NOT "the model got a cause wrong". It is
# "the model added a cause that is not in the approved library, or presented a
# hypothesis as a finding" -- either of which converts a reviewed, cited
# knowledge layer back into the guess-wearing-a-citation's-clothes failure the
# whole reference library exists to prevent, one layer further out.
#
# Three assertions, each mechanical:
#
#   1. SUBSET. Every cause NAME, every MECHANISM and every CITATION inside a
#      drafted cause section must come from the lookup result for the committed
#      fault. Mechanisms are compared verbatim (whitespace-normalised) because
#      the model is explicitly told not to author this section: if it reproduces
#      it, it reproduces it exactly, and then the published text is identical to
#      what would have been spliced anyway.
#   2. ZERO. When no bearing fault was committed there IS no lookup result, so
#      any cause language at all is fabrication.
#   3. HYPOTHESIS ONLY. No wording that upgrades a candidate to an established
#      finding, anywhere in the section.
# ─────────────────────────────────────────────────────────────────────────

_CAUSE_SECTION_RE = re.compile(
    r"^#{2,4}[ \t]*Possible underlying causes[^\n]*\n(.*?)(?=^#{1,2}[ \t]|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_CAUSE_SUBHEADING_RE = re.compile(r"^#{3,5}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
#: A citation token as the report renders it: `[#01 §5 Damage and actions, p.66]`.
#: Nothing else in a report emits this shape, which is what makes it usable as a
#: zero-false-positive fabrication probe across the WHOLE narrative.
_CAUSE_CITATION_RE = re.compile(r"\[#\d{1,3}[ab]?[ \t][^\]\n]{1,200}\]")

#: Language that turns a hypothesis into a finding. Phrase-level on purpose:
#: the section's own framing legitimately contains "confirmation", "confirm or
#: exclude" and "committed" (of the FAULT, which really was committed), so a
#: bare word-level ban would fire on correct text.
_CAUSE_ASSERTIVE_PHRASES: tuple[str, ...] = (
    "the root cause is",
    "the root cause of",
    "root cause has been",
    "the underlying cause is",
    "the cause is",
    "the cause was",
    "cause is confirmed",
    "is the confirmed",
    "has been confirmed",
    "we have confirmed",
    "confirmed cause",
    "cause has been identified",
    "the identified cause",
    "we determined that",
    "the analysis determined the cause",
    "definitively",
)

#: The zero-case probes. Each is language this feature introduces and nothing
#: else in the report says, so firing on one is never a coincidence.
_CAUSE_PRESENCE_PHRASES: tuple[str, ...] = (
    "possible underlying cause",
    "underlying cause",
)


def _normalise_claim(text: str) -> str:
    """Whitespace-collapsed, emphasis-stripped, lowercased — so a mechanism that
    survived a markdown round-trip still compares equal to the library's."""
    return re.sub(r"\s+", " ", text.replace("*", "").replace("_", "")).strip().lower()


def _cause_section(narrative: str) -> str | None:
    match = _CAUSE_SECTION_RE.search(narrative)
    return match.group(1) if match else None


def check_cause_language(
    text: str, result: AnalysisResult, causes: list[Any] | None = None
) -> list[str]:
    """HARD structural assertion (Session R2-BUILD): drafted cause wording must
    be a subset of the approved lookup, must not appear at all when no bearing
    fault was committed, and must never present a cause as established.

    `causes` is the already-resolved lookup result; passing None resolves it
    here from the same loader the report renders from, so the check and the
    render can never be reading two different books.

    The resolved-here lookup uses stage "any" while the REPORT keys its lookup
    to the computed damage stage. That is deliberate and one-directional: "any"
    can only ever return a SUPERSET, so the check can fail to reject a cause the
    report would not have printed, and can never reject one it would. Erring the
    other way would degrade correct reports, which costs an analyst a drafted
    narrative to save nothing. (Moot today -- every entry in the shipped book is
    `stage: any` -- but the asymmetry is the reason it is safe to stay moot.)
    """
    from vib_agent.knowledge import BEARING_FAULT_FAMILIES, lookup_causes_for

    narrative = strip_echo_block(text)
    committed_bearing = [f.fault for f in result.findings if f.fault in BEARING_FAULT_FAMILIES]
    resolved = causes if causes is not None else lookup_causes_for(committed_bearing)

    section = _cause_section(narrative)
    lowered = narrative.lower()
    mismatches: list[str] = []

    # ── 2. ZERO: nothing was committed, so there is nothing to explain ──────
    if not resolved:
        why = (
            "no bearing fault was committed for this reading"
            if not committed_bearing
            else f"the approved cause library holds no entry for {sorted(committed_bearing)}"
        )
        if section is not None:
            mismatches.append(
                f"cause language: the narrative writes a 'Possible underlying causes' section, "
                f"but {why}, so there is no fault to explain and no approved cause to name. "
                "Remove the section entirely."
            )
        for phrase in _CAUSE_PRESENCE_PHRASES:
            if phrase in lowered:
                mismatches.append(
                    f"cause language: the narrative says {phrase!r}, but {why}. A cause may only "
                    "be named for a committed bearing fault, and only from the approved library."
                )
                break
        for token in _CAUSE_CITATION_RE.findall(narrative):
            mismatches.append(
                f"cause language: the narrative cites {token!r}, but {why}. Reference-library "
                "citations may only appear inside the cause section of a committed bearing fault."
            )
            break
        return mismatches

    # ── 1. SUBSET: names, mechanisms and citations all come from the lookup ─
    allowed_names = {_normalise_claim(c.cause): c for c in resolved}
    allowed_mechanisms = {_normalise_claim(c.mechanism) for c in resolved}
    allowed_citations = {
        cit.rendered()
        for c in resolved
        for cit in list(c.citations)
        + [o for observation in c.discriminating_evidence for o in observation.citations]
    }

    for token in set(_CAUSE_CITATION_RE.findall(narrative)):
        if token not in allowed_citations:
            mismatches.append(
                f"cause citation: the narrative cites {token!r}, which is not a citation any "
                f"approved cause for {sorted(committed_bearing)} carries. Citations are copied "
                "from the cause library verbatim; they are never constructed."
            )

    if section is None:
        # The model wrote no section. The deterministic one is spliced in after
        # drafting, so this is the normal, correct outcome -- nothing to check.
        return mismatches

    for raw_name in _CAUSE_SUBHEADING_RE.findall(section):
        name = _normalise_claim(raw_name)
        cause = allowed_names.get(name)
        if cause is None:
            mismatches.append(
                f"fabricated cause: the narrative offers {raw_name.strip()!r} as a possible "
                f"underlying cause, but it is not in the approved cause library for "
                f"{sorted(committed_bearing)}. Approved causes are: "
                f"{sorted(c.cause for c in resolved)}. Never add a cause from your own "
                "knowledge -- an unapproved cause has no source behind it."
            )

    normalised_section = _normalise_claim(section)
    for cause in resolved:
        if _normalise_claim(cause.cause) not in normalised_section:
            continue  # this cause was not written up; dropping one is allowed
        if _normalise_claim(cause.mechanism) not in normalised_section:
            mismatches.append(
                f"cause mechanism: the narrative writes up {cause.cause!r} but does not carry "
                "its mechanism as the library states it. Reproduce the cause section from the "
                "reference report verbatim -- the mechanism is a sourced claim, not prose to "
                "rewrite."
            )

    # ── 3. HYPOTHESIS ONLY ─────────────────────────────────────────────────
    #
    # The matched phrase is deliberately NOT quoted back. Session F2's rule is
    # that the drafting model is never HANDED the vocabulary the report must not
    # use (tests/test_session_f2.py::test_the_drafting_model_is_never_taught_the
    # _phrase), and a retry prompt is handed to the model exactly like the first
    # one. Detecting a phrase is not teaching it; echoing it is. So the message
    # names the CATEGORY and the fix, and the phrase list stays here.
    lowered_section = section.lower()
    if any(phrase in lowered_section for phrase in _CAUSE_ASSERTIVE_PHRASES):
        mismatches.append(
            "cause language: the cause section contains wording that states a cause as "
            "established. Nothing in this section is confirmed by a vibration measurement — a "
            "vibration reading identifies the fault, not what produced it, and the evidence that "
            "would settle these was not collected. Every entry is a candidate for the analyst to "
            "confirm or exclude and must be worded as one; remove any wording saying a cause has "
            "been established, identified, determined or proven."
        )
    return mismatches


def check_echo_against_result(echo: dict[str, Any] | None, result: AnalysisResult) -> list[str]:
    """Return human-readable mismatches between the echo block and the
    AnalysisResult; an empty list means the draft is consistent."""
    if echo is None:
        return ["missing or unparseable structured echo block"]

    mismatches: list[str] = []

    expected_zone = result.iso.iso_zone if result.iso is not None else None
    echo_zone = echo.get("zone")
    if echo_zone != expected_zone:
        mismatches.append(f"zone: echo says {echo_zone!r}, AnalysisResult says {expected_zone!r}")

    expected_faults = _committed_faults(result)
    echo_faults_raw = echo.get("faults")
    if not isinstance(echo_faults_raw, list):
        mismatches.append("faults: echo block is missing a 'faults' list")
        echo_faults_raw = []
    echo_faults: dict[str, str] = {
        str(item["fault"]): str(item.get("confidence"))
        for item in echo_faults_raw
        if isinstance(item, dict) and "fault" in item
    }

    if set(echo_faults) != set(expected_faults):
        mismatches.append(
            f"faults: echo says {sorted(echo_faults)}, AnalysisResult committed {sorted(expected_faults)}"
        )
    else:
        for fault, expected_conf in expected_faults.items():
            if echo_faults.get(fault) != expected_conf:
                mismatches.append(
                    f"confidence for {fault}: echo says {echo_faults.get(fault)!r}, "
                    f"AnalysisResult says {expected_conf!r}"
                )

    expected_measurements = {m.technique for m in result.recommended_measurements}
    echo_measurements_raw = echo.get("recommended_measurements")
    if not isinstance(echo_measurements_raw, list):
        mismatches.append("recommended_measurements: echo block is missing a 'recommended_measurements' list")
        echo_measurements_raw = []
    echo_measurements = {str(m) for m in echo_measurements_raw}
    if echo_measurements != expected_measurements:
        mismatches.append(
            f"recommended_measurements: echo says {sorted(echo_measurements)}, "
            f"AnalysisResult recommends {sorted(expected_measurements)}"
        )

    return mismatches
