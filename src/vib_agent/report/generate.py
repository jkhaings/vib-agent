"""Render an AnalysisResult into a markdown survey report (and optional PDF).

Markdown renders with zero optional dependencies. PDF is best-effort: it needs
both `markdown` and `weasyprint` (the `[pdf]` extra); when either is missing,
`render_report` writes the markdown and skips the PDF with a warning rather
than failing.

Session H adds the evidence layer: a colour-coded status badge, an "Analysis
parameters" table, per-channel spectrum figures with computed overlays, a
trend chart, and an analyst signature block. Every figure is a deterministic
PNG written into `out_dir/charts/` by report/charts.py and referenced by
RELATIVE path from the markdown, so both PDF engines resolve them from the
report directory. The PNGs are INTERMEDIATES — the webapp's completion purge
deletes the whole `charts/` directory once the PDF exists.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from vib_agent.config import load_config
from vib_agent.knowledge import BEARING_FAULT_FAMILIES, CAUSE_HEADING, lookup_causes_for
from vib_agent.models import AnalysisResult, Case, ComparisonResult, Finding, MachineMeta
from vib_agent.pdm_core.bearing_rca import _clears_evidence_floor, _within, peaks_from_spectrum
from vib_agent.pdm_core.compare import repair_word, verdict_word
# Session REC-1. The corrective channel moved from synthesize.py (latched that
# session, and left exactly as it was) to recommendations.py, which now holds
# both channels — see that module's docstring for which is which.
from vib_agent.pdm_core.recommendations import corrective_recommendations_for_findings
from vib_agent.pdm_core.staging import StageEstimate, classify_bearing_stage
from vib_agent.report.charts import (
    ChartSet,
    _channel_spectra,
    analysis_parameters,
    annotate_orders,
    fmt_hz_order,
    render_charts,
    spectrum_kind_and_unit,
    status_text,
    zone_word,
)
from vib_agent.report.cause_art import illustration as cause_illustration
from vib_agent.report.graphics import bearing_map, evidence_insets, stage_ladder
from vib_agent.report.htmlkit import md_inline, stylesheet
from vib_agent.report import render_proc
from vib_agent.report.render_lock import serializes_render

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# Minimal print stylesheet for the weasyprint path. Deliberately small: it sizes
# the Session H figures to the text column and keeps tables readable — it is not
# a theme. (The pandoc/tectonic path gets the equivalent via -V geometry.)
_PDF_CSS = (
    "@page{size:A4;margin:18mm 15mm}"
    "body{font-family:sans-serif;font-size:10pt;line-height:1.45;color:#101816}"
    "h1{font-size:16pt}h2{font-size:12pt;margin-top:14pt}h3{font-size:10.5pt}"
    "img{max-width:100%;height:auto;display:block;margin:6pt 0}"
    "table{border-collapse:collapse;width:100%;font-size:9pt}"
    "th,td{border:1px solid #D8DCD7;padding:3pt 5pt;text-align:left;vertical-align:top}"
    "th{background:#F6F7F5}"
    "code,pre{font-size:9pt}"
)

# Human-readable labels for the report; keys are pdm_core fault ids.
FAULT_LABELS: dict[str, str] = {
    "bearing_outer_race": "Bearing outer-race fault (BPFO)",
    "bearing_inner_race": "Bearing inner-race fault (BPFI)",
    "bearing_ball_spin": "Bearing ball/roller fault (BSF)",
    "bearing_cage": "Bearing cage fault (FTF)",
    "mechanical_looseness": "Mechanical looseness",
    "angular_misalignment": "Angular misalignment",
    "parallel_misalignment": "Parallel misalignment",
    "severe_misalignment": "Severe misalignment",
    "misalignment_general": "Misalignment (sub-type undetermined)",
    "bent_shaft": "Bent shaft",
    "imbalance": "Rotor imbalance",
    "belt_fault": "Belt drive fault",
    "elevated_blade_pass": "Elevated blade-pass",
    "possible_resonance": "Possible resonance",
    "rising_trend": "Rising vibration trend",
    "elevated_vibration_undetermined": "Elevated vibration (cause undetermined)",
    "no_significant_findings": "No significant findings",
}


# Session A: the display text a finding's severity renders as when the reading's
# ISO zone is not_assessable. The Finding.severity value stays the terse
# "unrated" (the echo/consistency contract); only the report widens it.
_UNRATED_SEVERITY_DISPLAY = "unrated — ISO severity requires velocity data"


# Session F2: fault families, for the conflicted-evidence headline ONLY. This is
# a REPORT-LAYER grouping — how a finding is SAID, never how it is adjudicated.
# pdm_core still decides alone what is committed and what is set aside.
_FAULT_FAMILY: dict[str, str] = {
    "bearing_outer_race": "bearing",
    "bearing_inner_race": "bearing",
    "bearing_ball_spin": "bearing",
    "bearing_cage": "bearing",
    "imbalance": "shaft",
    "angular_misalignment": "shaft",
    "parallel_misalignment": "shaft",
    "severe_misalignment": "shaft",
    "misalignment_general": "shaft",
    "bent_shaft": "shaft",
    "mechanical_looseness": "shaft",
    "belt_fault": "drive",
    "elevated_blade_pass": "flow",
    "possible_resonance": "structural",
}
# "Strong" = a candidate the evidence genuinely raised, not a routine low-confidence
# also-ran. Low-confidence differentials stay where they belong: Also considered.
_STRONG_CONFIDENCE: tuple[str, ...] = ("high", "medium")


def _fault_label(fault: str) -> str:
    return FAULT_LABELS.get(fault, fault.replace("_", " ").capitalize())


def _conflicted_pair(result: AnalysisResult):
    """The committed call plus the strongest differential candidate from a
    DIFFERENT fault family, when the evidence really does point two ways.

    Returns (committed_finding, differential_candidate) or None. Same-family
    candidates (a looseness-downgraded misalignment, say) are NOT a conflict --
    they are a sub-type question, and the differential section already states
    the adjudication for them.
    """
    if result.rca is None:
        return None
    primary = [f for f in result.findings if f.fault != "no_significant_findings"]
    if not primary:
        return None
    committed = primary[0]
    family = _FAULT_FAMILY.get(committed.fault)
    if family is None:
        return None
    committed_faults = {f.fault for f in primary}
    candidates = [
        d
        for d in result.rca.differential
        if d.fault not in committed_faults
        and d.confidence in _STRONG_CONFIDENCE
        and _FAULT_FAMILY.get(d.fault) not in (None, family)
    ]
    if not candidates:
        return None
    # strongest first, stable within a confidence tier (pdm_core's own order)
    candidates.sort(key=lambda d: _STRONG_CONFIDENCE.index(d.confidence))
    return committed, candidates[0]


def _conflicted_headline(result: AnalysisResult) -> str | None:
    """The report's headline when the evidence is genuinely conflicted. The
    committed call is still the call -- it is stated, with its confidence, in
    the very next sentence, and the candidate keeps its adjudication in Also
    considered. This sentence exists so a reader cannot walk away with only
    half of that picture."""
    pair = _conflicted_pair(result)
    if pair is None:
        return None
    committed, candidate = pair
    return (
        f"Possible {_fault_label(committed.fault)} with evidence of "
        f"{_fault_label(candidate.fault)} — further validation recommended."
    )


def _display_severity(severity: str) -> str:
    return _UNRATED_SEVERITY_DISPLAY if severity == "unrated" else severity


#: Session REPORT-4 (item 7). The committed finding's heading used to read
#:
#:     Bearing outer-race fault (BPFO) — severity: info, confidence: high
#:
#: and "severity: info" is the one word on that line an analyst cannot use.
#: `Finding.severity` is pdm_core's own terse label, and "info" there means *this
#: finding does not carry the severity* — the ZONE carries it, which is Session
#: A's contract and is stated correctly four lines higher on the same page. A
#: reader who does not know that reads "info" as "informational", i.e. as the
#: mildest severity there is, printed directly beside a high-confidence bearing
#: fault. That is the severity-truthfulness defect Session A fixed at the source
#: reappearing as a display string.
#:
#: So the heading prints the two things that ARE this finding's own: the computed
#: confidence, and — for the fault the staging layer actually staged — the damage
#: stage. Mapped AT RENDER: `Finding.severity` is untouched, pdm_core is not
#: opened, and `analysis.json` is byte-identical.
def _finding_tag(finding: Any, stage: StageEstimate | None) -> str:
    """`"confidence: high · damage stage: 3 (early)"` — the finding's own
    confidence, plus its damage stage where the staging layer determined one for
    THIS fault. Confidence alone otherwise: a stage belongs to the bearing fault
    it was computed for and must not be borrowed by a sibling finding."""
    tag = f"confidence: {finding.confidence}"
    # "unrated" is NOT the "info" case and must keep its sentence. `info` means
    # the zone carries the severity and is stated four lines up; `unrated` means
    # there IS no severity, because no velocity was measured — Session A's
    # contract — and `_UNRATED_SEVERITY_DISPLAY` is the report's own explanation
    # of why. Dropping it here would have removed the reason from every finding
    # heading on an acceleration-only reading, which is the exact
    # severity-truthfulness regression Session A exists to prevent. Caught by
    # `tests/test_report.py::test_not_assessable_renders_coverage_block_and_no_zone_language`.
    if finding.severity == "unrated":
        tag = f"severity: {_UNRATED_SEVERITY_DISPLAY} · {tag}"
    if (stage is not None and stage.determinable and stage.fault == finding.fault):
        # `StageEstimate.label` is "Stage 3 (early)"; the heading already says
        # "damage stage", so the word is not printed twice.
        tag += f" · damage stage: {stage.label.removeprefix('Stage ')}"
    return tag


# ─────────────────────────────────────────────────────────────────────────
# Session REPORT-4, item 6 — the analyst's word for a channel, not ours.
#
# A route analyst measures "radial – horizontal"; the pipeline calls that "y".
# The letter is an internal axis index — `adapters/uploads` parses every
# single-channel file onto "y" and `webapp/assembly.remap_channel_case` then
# moves it to the axis the declared direction maps to (axial->x, radial-h->y,
# radial-v->z, fixed since Session E) — so "the y-axis" in a signed report is
# this codebase's coordinate frame showing through, and on a one-channel job it
# tells the reader nothing they typed.
#
# WHERE THE DIRECTION IS KNOWN, THE DOCUMENT SAYS IT. Where it is not, the letter
# stays exactly as it was: a CLI or NCD reading declared no direction and naming
# one would assert something the analyst never said. That is the whole gate.
#
# The signal is the webapp's own "Channels measured:" note. Nothing in `models.py`
# carries a declared direction — `MachineMeta` has `axial_axis` and no more — and
# `models.py` is not this session's to change, so the note is what there is. The
# labels are read OUT of it rather than spelled again here, which is deliberate:
# a second spelling of the analyst's own word ("radial-horizontal" beside the
# note's "radial – horizontal") is the two-spellings-on-one-page defect this item
# exists to remove, arriving by the back door.
#
# Recorded in the close-out: the MARKDOWN twin cannot reach this yet. It is the
# same mechanism and the same context key, but `webapp/worker.py` inserts its
# notes into report.md as TEXT after the render, so `render_markdown` is never
# handed them. The parameter is here and defaults to None; one line in the
# webapp fills it.

#: The webapp's note, by its opening. Matched as a prefix so the sentence can be
#: reworded after the colon without silently turning this off.
_CHANNELS_MEASURED_PREFIX = "Channels measured: "

#: `"radial – horizontal (y)"` inside that note — the label and the axis it was
#: mapped onto. Only the MEASURED half of the note is scanned: the "not measured"
#: half names directions with no axis in parentheses, so it cannot match anyway,
#: and scanning it would be claiming a channel that was never collected.
_DECLARED_CHANNEL_RE = re.compile(r"([A-Za-z][A-Za-z –—-]*?)\s*\(([xyz])\)")


def declared_axis_names(notes: Sequence[str] | None) -> dict[str, str] | None:
    """`{"y": "radial – horizontal"}` from the webapp's channels-measured note,
    or None when no direction was declared on this reading.

    None and `{}` are different answers and both mean "print the letter": None is
    "no note at all" (a CLI reading), `{}` is "a note that named no axis". Either
    way `axis_phrase` falls back, so nothing that did not declare a direction can
    acquire one."""
    for note in notes or ():
        if not note.startswith(_CHANNELS_MEASURED_PREFIX):
            continue
        measured = note[len(_CHANNELS_MEASURED_PREFIX):].split(";")[0]
        return {axis: " ".join(label.split())
                for label, axis in _DECLARED_CHANNEL_RE.findall(measured)}
    return None


def axis_phrase(axis: str | None, names: dict[str, str] | None) -> str:
    """`"radial – horizontal"` where the analyst declared one, `"y-axis"` where
    they did not. The one place the choice is made, so the health line, the fault
    line, the roster and the Machine Details row cannot come to disagree about
    what a channel is called."""
    if not axis:
        return ""
    declared = (names or {}).get(axis)
    return declared if declared else f"{axis}-axis"


def _not_assessable(result: AnalysisResult) -> bool:
    return result.iso is not None and result.iso.iso_zone == "not_assessable"


def _analysis_failed(result: AnalysisResult) -> bool:
    """S12FIX: the RCA raised, so no fault screen happened (HANDOFF-08-27 §5.1).

    Keyed on the RCA status, never on `findings` being empty — an empty finding
    list is also what a gate-fail and a genuinely clean machine produce, and
    those three must not render as each other. `machine_off` is a determination,
    not a failure, so it is not included.
    """
    return result.rca is not None and result.rca.status == "error"


def _additional_findings(n: int) -> str:
    """` 1 additional finding is also reported.` / ` 2 additional findings are …`

    Session TFIX. Both call sites wrote "N additional finding(s) are also
    reported", which a reader meets two lines under a headline that had the same
    wart. Number and verb agree here.
    """
    return f" {n} additional finding{'' if n == 1 else 's'} {'is' if n == 1 else 'are'} also reported."


# ── the diagnosis in an analyst's order (Session REPORT-2, item 4) ────────
#
# A CAT analyst reviewing the outreach sample wrote the diagnosis as: the overall
# level and its ISO zone first; then the dominant fault frequency with its
# harmonics; then the bearing it matches; then the conclusion. The deterministic
# Executive Summary and the lead paragraph of each committed finding now follow
# that order. Every number is one the AnalysisResult already holds (severity_rms,
# the zone boundary, the matched and computed frequencies); the wording is
# template text; nothing here is drafted.
#
# The Executive Summary prints frequencies WITH the shaft order, like every
# other mention in the document. Session REPORT-3 (item 4) closed
# SESSION_REPORT2.md F-1, which is what made that possible.
#
# REPORT-2 had to make this one place an exception, and recorded why here:
# `agent/consistency.py::check_numeric_quotes` scans the drafted summary for
# decimals the analysis does not hold, and an order ratio (3.58) was not one --
# so a model transcribing the deterministic summary verbatim was refused,
# retried, and could degrade the shop-window sample. Measured at the time, not
# assumed. `computed_numbers` now derives each fault frequency's order with the
# renderer's own arithmetic, so the ratio IS a value the analysis holds and the
# exception is gone. "Every mention" is finally true.

_ZONE_BOUNDARY_WORDS = {
    # zone -> (attribute of the boundary it sits against, how it sits, the boundary's name)
    "D": ("th_cd", "above", "Zone C/D"),
    "C": ("th_bc", "above", "Zone B/C"),
    "B": ("th_ab", "above", "Zone A/B"),
    "A": ("th_ab", "below", "Zone A/B"),
}

_COMPUTED_NAMES = {
    "bearing_outer_race": "BPFO",
    "bearing_inner_race": "BPFI",
    "bearing_ball_spin": "BSF",
    "bearing_cage": "FTF",
}


# ── whose numbers are these? (Session LIMITS-1b) ──────────────────────────
#
# LIMITS-1a F-3: every sentence below used to sign ISO's name to whatever
# `resolve_thresholds` returned. Measured on a machine with a plant limit of
# 5.0/8.0/12.0 mm/s, the health line read
#
#     ISO Zone B — acceptable per ISO 20816-3: overall 5.20 mm/s RMS ...
#
# on a reading ISO 20816-3 itself would have called Zone D, unacceptable. The
# authority is `Reading.zone_basis`, and nothing here re-derives it from
# `threshold_source` -- that names a TIER, not an authority, and maps
# `factory_default` onto "custom" on purpose (SESSION_LIMITS1.md §6).

#: The name the report gives a limit somebody set, wherever it would otherwise
#: have named the standard. One constant, so the health line, the Executive
#: Summary and the ruled sentence cannot come to spell it differently.
_CUSTOM_BASIS_PHRASE = "machine-specific limits"

#: Item 4 -- SESSION_LIMITS1.md F-2, said out loud rather than left to be
#: inferred. `config/thresholds.json`'s `one_x_severity_min_zone` is a zone
#: LETTER and `pipeline.py:169` already feeds the resolved boundaries into
#: `run_rca` as `iso_thresholds`, so a looser plant limit SUPPRESSES 1x-family
#: faults ISO would have committed and a tighter one commits faults ISO would
#: not. That is defensible -- it is the plant's own alarm policy -- but it is
#: not what "custom severity zones" sounds like, so the page says it.
_GATE_NOTE = "Machine-specific limits also govern the 1× severity gate for this machine."


def _custom_basis(result: AnalysisResult) -> bool:
    """True when this reading was judged against a limit somebody set rather
    than against a row of ISO 20816-3."""
    return result.iso is not None and result.iso.zone_basis == "custom"


def _zone_label(result: AnalysisResult) -> str:
    """`"ISO Zone"`, or `"Zone"` when the letter is the machine's own. On a
    custom basis ISO is named in exactly one place -- the would-give clause."""
    return "Zone" if _custom_basis(result) else "ISO Zone"


def _basis_clause(result: AnalysisResult) -> str | None:
    """The operator's ruled sentence (Sep 12), WITHOUT its closing full stop --
    both the markdown and the HTML health line append one of their own:

        `"Judged against machine-specific limits (5 / 8 / 12 mm/s RMS) — ISO
        20816-3 Group 2 rigid support would give Zone D"`

    and, when ISO has no row for this machine, the ruled alternative:

        `"Judged against machine-specific limits (5 / 8 / 12 mm/s RMS); ISO
        20816-3 has no zone for this machine"`

    None on the ISO basis, and None for a withheld reading: `mark_not_assessable`
    clears `iso_zone_would_be` precisely so the ISO answer is not handed back
    through a second door (SESSION_LIMITS1.md §5.2), and this sentence would be
    that door. The boundary figures are formatted `:g`, which is what every
    other boundary on the page already uses -- `%.2f` here would invent a
    precision the analyst did not type.
    """
    iso = result.iso
    if iso is None or not _custom_basis(result) or _not_assessable(result):
        return None
    if iso.th_ab is None or iso.th_bc is None or iso.th_cd is None:
        return None
    limits = f"{iso.th_ab:g} / {iso.th_bc:g} / {iso.th_cd:g} mm/s RMS"
    if iso.iso_zone_would_be:
        # `iso_zone_would_be` is only ever set when the table answered, so the
        # group and support that answered it are on the machine -- but the
        # sentence degrades to naming the standard alone rather than printing
        # a half-built parenthetical if either is somehow absent.
        who = (f" Group {iso.iso_group} {iso.iso_support} support"
               if iso.iso_group and iso.iso_support else "")
        return (f"Judged against {_CUSTOM_BASIS_PHRASE} ({limits}) — ISO 20816-3"
                f"{who} would give Zone {iso.iso_zone_would_be}")
    return (f"Judged against {_CUSTOM_BASIS_PHRASE} ({limits}); "
            f"ISO 20816-3 has no zone for this machine")


def _zone_clause(result: AnalysisResult, names: dict[str, str] | None = None) -> str | None:
    """`"ISO Zone D — unacceptable per ISO 20816-3: overall 5.20 mm/s RMS on the
    y-axis, above the 4.5 mm/s Zone C/D boundary (Group 2, rigid support)"`.
    None when the reading is not assessable (Session A: an unrated reading
    carries no zone language anywhere).

    Session LIMITS-1b: on a custom basis the same sentence reads `"Zone B —
    acceptable per machine-specific limits: overall 5.20 mm/s RMS on the y-axis,
    above the 5 mm/s Zone A/B boundary"` -- the machine's own letter, the
    plant's own authority, and no ISO group cited beside a number ISO did not
    set. `_basis_clause` carries what ISO would have said."""
    iso = result.iso
    if iso is None or _not_assessable(result) or iso.severity_rms is None:
        return None
    zone = iso.iso_zone
    word = zone_word(zone).lower()
    custom = _custom_basis(result)
    attr, how, name = _ZONE_BOUNDARY_WORDS.get(zone, (None, None, None))
    boundary = getattr(iso, attr, None) if attr else None
    authority = _CUSTOM_BASIS_PHRASE if custom else "ISO 20816-3"
    parts = [f"{_zone_label(result)} {zone} — {word} per {authority}: "
             f"overall {iso.severity_rms:.2f} mm/s RMS"]
    if iso.dominant_axis:
        parts.append(f" on the {axis_phrase(iso.dominant_axis, names)}")
    if boundary is not None:
        parts.append(f", {how} the {boundary:g} mm/s {name} boundary")
    # The ISO group and support class did not produce this zone on a custom
    # basis, so they are not cited beside it; `_basis_clause` names them where
    # they DID do work, in the would-give clause.
    if iso.iso_group and iso.iso_support and not custom:
        parts.append(f" (Group {iso.iso_group}, {iso.iso_support} support)")
    return "".join(parts)


def _is_dominant(case: Case | None, fault: str, axis: str | None, freq_hz: float) -> bool:
    """True when the matched line is the loudest bin of the spectrum its detector
    read -- the bearing family reads the envelope, the 1×-family raw/velocity --
    which is what lets the paragraph say "dominated by" rather than assert it."""
    if case is None or not axis:
        return False
    if fault in BEARING_FAULT_FAMILIES or fault.startswith("bearing_"):
        spectrum = (case.spectra or {}).get(axis)
    else:
        spectrum = (case.raw_spectra or {}).get(axis) or (case.spectra or {}).get(axis)
    if spectrum is None or not spectrum.freq_hz:
        return False
    freqs, amps = spectrum.freq_hz, spectrum.amplitude
    idx = min(range(len(freqs)), key=lambda i: abs(freqs[i] - freq_hz))
    return amps[idx] >= max(amps)


def _signature_sentence(
    finding: Finding, result: AnalysisResult, case: Case | None, *, with_orders: bool,
    names: dict[str, str] | None = None,
) -> str | None:
    """`"The envelope spectrum (as supplied) is dominated by 107.25 Hz on the
    y-axis, with its 2× harmonic present, matching the computed BPFO of bearing
    6206 at 107.03 Hz"` -- the dominant frequency, its harmonic, the bearing.
    None for a finding with no frequency (a trend, an undetermined elevation)."""
    ev = finding.evidence
    freq = ev.get("freq_hz")
    axis = ev.get("axis")
    if freq is None:
        return None
    shaft = _shaft_hz(result)

    def hz(value: float) -> str:
        return fmt_hz_order(float(value), shaft) if with_orders else f"{float(value):.2f} Hz"

    series = "spectrum"
    spectrum = None
    if case is not None and axis:
        spectrum = ((case.spectra or {}).get(axis) if finding.fault.startswith("bearing_")
                    else (case.raw_spectra or {}).get(axis) or (case.spectra or {}).get(axis))
    if spectrum is not None:
        series = spectrum_kind_and_unit(case, spectrum, lower=True)
    verb = "is dominated by" if _is_dominant(case, finding.fault, axis, float(freq)) \
        else "carries the committed signature at"
    sentence = f"The {series} {verb} {hz(freq)}"
    if axis:
        sentence += f" on the {axis_phrase(axis, names)}"
    if ev.get("harmonic_present"):
        sentence += ", with its 2× harmonic present"
    expected = ev.get("expected_hz")
    if expected is not None:
        name = _COMPUTED_NAMES.get(finding.fault)
        model = ev.get("bearing_model")
        if name and model:
            sentence += f", matching the computed {name} of bearing {model} at {hz(expected)}"
        elif name:
            sentence += f", matching the computed {name} at {hz(expected)}"
        else:
            sentence += f", matching the computed frequency for {_fault_label(finding.fault).lower()} at {hz(expected)}"
    return sentence + "."


def diagnosis_lead(
    finding: Finding, result: AnalysisResult, case: Case | None, *, first: bool,
    names: dict[str, str] | None = None,
) -> str | None:
    """The lead paragraph of a committed finding's card, in the analyst's order and
    WITH shaft orders: level and zone (first finding only) -> dominant frequency
    and harmonic -> the bearing it matches -> the conclusion. One string, rendered
    by both twins above pdm_core's own evidence sentence."""
    parts: list[str] = []
    zone = _zone_clause(result, names) if first else None
    if zone:
        parts.append(f"Overall vibration places this machine in {zone}.")
    signature = _signature_sentence(finding, result, case, with_orders=True, names=names)
    if signature:
        parts.append(signature)
    if not parts:
        return None
    parts.append(f"Committed diagnosis: {_fault_label(finding.fault)}, {finding.confidence} confidence.")
    return " ".join(parts)


def _executive_summary(result: AnalysisResult, case: Case | None = None,
                       names: dict[str, str] | None = None) -> str:
    machine_id = result.machine_id
    if result.quality_gate.overall == "fail":
        return (
            f"The data submitted for {machine_id} did not pass quality checks, so a diagnosis "
            "could not be made. This is an insufficient-data report: it states what failed and "
            "what to collect next, rather than a condition assessment."
        )
    # S12FIX: the data passed every check and the fault screen still failed --
    # OUR fault, and the reader is owed that distinction rather than a silent
    # empty Diagnosis section (which reads as a clean bill just as loudly).
    if _analysis_failed(result):
        return (
            f"The fault screen for {machine_id} did not complete, so NO diagnosis was made. "
            "The data itself passed the quality checks — this is a failure of the analysis, "
            "not of the measurement, and the reading should be re-submitted. Nothing in this "
            "report should be read as evidence that the machine is healthy."
        )
    primary = [f for f in result.findings if f.fault not in ("no_significant_findings",)]
    headline = _conflicted_headline(result)
    if _not_assessable(result):
        # No velocity -> no ISO zone, no "within acceptable limits". State the
        # coverage boundary instead: fault detection ran, severity did not.
        if not primary:
            return (
                f"{machine_id}: no fault signature was identified in the spectral/envelope "
                "evidence. ISO severity is unrated — a velocity measurement per ISO 20816 is "
                "required to establish it (see Severity & Coverage)."
            )
        committed = primary[0]
        lead = (
            f"{machine_id}: {headline} The committed diagnosis is {_fault_label(committed.fault)} "
            f"({committed.confidence} confidence)."
            if headline
            else f"{machine_id}: the committed diagnosis is {_fault_label(committed.fault)} "
            f"({committed.confidence} confidence)."
        )
        sentence = (
            f"{lead} ISO severity is unrated — a velocity "
            "measurement per ISO 20816 is required to establish it (see Severity & Coverage)."
        )
        if len(primary) > 1:
            sentence += _additional_findings(len(primary) - 1)
        return sentence
    zone = result.iso.iso_zone if result.iso else "?"
    if not primary:
        return (
            f"{machine_id} is in {_zone_label(result)} {zone} with no fault signature "
            "identified. Overall "
            "vibration is within acceptable limits; continue routine monitoring."
        )
    committed = primary[0]
    label = _fault_label(committed.fault)
    # Session REPORT-2 (item 4): zone and level -> dominant frequency and its
    # harmonic -> the bearing it matches -> the conclusion. The committed
    # sentence keeps its exact wording (tests/test_session_f2.py pins it).
    zone_clause = _zone_clause(result, names)
    lead = (f"{machine_id} is in {zone_clause}. " if zone_clause
            else f"{machine_id} is in {_zone_label(result)} {zone}. ")
    signature = _signature_sentence(committed, result, case, with_orders=True, names=names)
    sentence = (
        lead
        + (f"{signature} " if signature else "")
        + (f"{headline} " if headline else "")
        + f"The committed diagnosis is {label} ({committed.confidence} confidence)."
    )
    others = [f for f in primary[1:]]
    if others:
        sentence += _additional_findings(len(others))
    if result.recommended_measurements:
        sentence += (
            " One or more follow-up measurements are recommended to resolve remaining "
            "uncertainty (see below)."
        )
    return sentence


# Session REPORT-4 (item 7) removed `_ZONE_DECISION`, REPORT-3 item 1's zone ->
# work-order verb map ("A"/"B" -> monitor, "C" -> plan for next outage, "D" ->
# act now), and with it the "Decision: monitor" token the fault sheet printed
# after the fault line.
#
# It was a fourth answer to a question the sheet already answers three times, and
# the weakest of the four: the Recommendation block immediately below it states
# the Action, the When and the Reassess, and the Escalation trigger under that
# states what would change them. "Decision: monitor" adds no fact to those — it
# is the zone letter, re-spelled as a verb — and on the operator's own read it
# landed as a fifth verdict restatement in a document already saying the verdict
# too often (item 3). Nothing computed ever depended on it: it rated nothing and
# escalated nothing, which is what makes deleting it a presentation change.
#
# `corrective_recommendations_for_findings` is total over the committed fault ids
# (`tests/test_rec1_recommendations.py` drives every one), so a sheet that used
# to carry a decision always carries the three-part Recommendation instead.

#: Session REPORT-3 (item 7), verbatim from the brief. Printed directly under the
#: health line when the gate's `ski_slope` check is warn or fail. The zone STILL
#: PRINTS beside it: the caveat says the number may be inflated, not that it is
#: unknown, and withholding the zone would be a different claim from the one the
#: check supports.
SKI_SLOPE_CAVEAT = (
    "Overall may be inflated by a low-frequency (mounting or settling) artifact — "
    "re-measure before acting on the zone"
)


def _ski_slope_flagged(result: AnalysisResult) -> bool:
    """Whether the quality gate's own `ski_slope` check wants attention.

    Reads `result.quality_gate.checks` exactly as `_dq_notes` does — by name and
    status, nothing recomputed. `pdm_core/quality_gate.py::_ski_slope_check`
    emits only `not_applicable | pass | warn` today; `fail` is accepted here
    because the brief names both and because a status this function does not
    know must never read as "clear".
    """
    return any(
        c.name == "ski_slope" and c.status in ("warn", "fail")
        for c in result.quality_gate.checks
    )


def _next_boundary(result: AnalysisResult) -> tuple[str | None, float | None]:
    """The zone this machine would cross into next, and the mm/s it crosses at.

    `(None, None)` past Zone D — `_ZONE_NEXT` stops at C, because there is no
    zone above D and no boundary to name. Factored out of `_html_context`, which
    computed it inline, so the severity strip and the fault sheet's escalation
    trigger read ONE number rather than two that could drift.
    """
    zone = result.iso.iso_zone if result.iso is not None else None
    nxt = _ZONE_NEXT.get(zone or "")
    if nxt is None or result.iso is None:
        return None, None
    boundary = {"B": result.iso.th_ab, "C": result.iso.th_bc, "D": result.iso.th_cd}.get(nxt)
    return (nxt, boundary) if boundary is not None else (None, None)


def escalation_trigger(result: AnalysisResult) -> str | None:
    """What would change the answer before the scheduled window — REC-1's F-2.

    The wording is the operator's, fixed by the REPORT-3 brief; only `<value>`
    and `<fault>` substitute, and both are numbers/names the report already
    prints elsewhere (the severity strip's next boundary, the committed
    finding's label). Nothing here is computed: 6 dB is a constant of the
    sentence, not a threshold this module derives.

    Each clause is printed only when its number exists, which is the whole of
    the conditionality:

      * Zone D has no next boundary, so the boundary clause drops and the
        amplitude clause stands alone.
      * A reading with no committed fault has no amplitude to watch, so the
        amplitude clause drops.
      * With neither — a Zone D reading that committed nothing — the line is
        omitted rather than reworded, because any replacement would be new
        customer-facing language sourced from neither the analyst nor the brief.
    """
    if _not_assessable(result) or _analysis_failed(result):
        return None
    if result.quality_gate.overall == "fail":
        return None
    _, boundary = _next_boundary(result)
    committed = [f for f in result.findings if f.fault != "no_significant_findings"]
    # The analyst's own name for the line being watched: "BPFO", not "bearing
    # outer-race fault (bpfo)" — lowercasing the display label mangles the
    # acronym inside it. `_COMPUTED_NAMES` is the same map the signature
    # sentence uses, so page 1 and the evidence name the frequency identically;
    # a fault with no computed name falls back to its label, which carries no
    # acronym to mangle.
    fault = None
    if committed:
        fault = (_COMPUTED_NAMES.get(committed[0].fault)
                 or _fault_label(committed[0].fault).lower())
    clauses: list[str] = []
    if boundary is not None:
        # Session LIMITS-1b. `_next_boundary` reads th_ab/th_bc/th_cd, which on a
        # custom basis are the plant's numbers -- calling the line an "ISO zone
        # boundary" there signs ISO's name to a threshold it did not set.
        whose = "zone" if _custom_basis(result) else "ISO zone"
        clauses.append(f"the overall crosses the next {whose} boundary ({boundary:g} mm/s)")
    if fault is not None:
        clauses.append(f"the {fault} amplitude increases by 6 dB")
    if not clauses:
        return None
    return "Re-measure before the scheduled window if " + " or ".join(clauses) + "."


# ---------------------------------------------------------------------------
# The multi-location roster — INTAKE-2's contract, read AS a contract.
#
# Session REPORTFIX-1. `docs/contracts/machine_result.md` §3 is the wire shape a
# finished job carries, and it is the only source these accessors REQUIRE: each
# one reads the contract's own field name first. REPORT-3 built this module
# against a shape it invented before that contract existed — `{label, result,
# case, charts}`, live Python objects — because the contract file did not exist
# at its merge point (its §7 F-3). Those objects survive here as OPTIONAL
# ENRICHMENT, never as a requirement: a caller holding an `AnalysisResult` gets
# the per-point evidence table and figures, a caller holding only the wire gets
# the compact section, and neither has to have what the other has.
#
# Contract §5 rule 1 is enforced in ONE place, `_loc_ok`: `iso_zone`,
# `severity_rms_mms` and `committed_fault` are read only when `status == "ok"`.
# A `gate_fail` point was not diagnosed, and rendering a blank where its zone
# would go would imply one was assessed.
# ---------------------------------------------------------------------------


def _loc_ok(loc: dict[str, Any]) -> bool:
    """Contract §3 `status`, with absence meaning `ok`.

    The wire always carries `status`; the enrichment-only callers (REPORT-3's
    own pins, and location 1, which is the document's own subject rather than a
    wire entry) do not, and for them "analysed" is the only state there is.
    """
    return (loc.get("status") or "ok") == "ok"


def _loc_result(loc: dict[str, Any]) -> AnalysisResult | None:
    """The live result, when this caller happens to hold one. Never required."""
    return loc.get("result")


def _loc_zone(loc: dict[str, Any]) -> str | None:
    """Contract §3 `iso_zone`, else the enrichment's own."""
    if not _loc_ok(loc):
        return None
    zone = loc.get("iso_zone")
    if zone:
        return zone
    result = _loc_result(loc)
    iso = result.iso if result is not None else None
    return iso.iso_zone if iso is not None else None


def _loc_rms(loc: dict[str, Any]) -> float | None:
    """Contract §3 `severity_rms_mms` — mm/s, unrounded on the wire; rounded at
    the point of display, which the templates do."""
    if not _loc_ok(loc):
        return None
    rms = loc.get("severity_rms_mms")
    if rms is not None:
        return rms
    result = _loc_result(loc)
    iso = result.iso if result is not None else None
    return iso.severity_rms if iso is not None else None


def _loc_axis(loc: dict[str, Any]) -> str | None:
    """The dominant axis. The wire carries it inside `trend_point` (contract §3),
    not as a top-level field — so this is the one accessor that reaches through
    a nested dict rather than reading a name off the entry."""
    if not _loc_ok(loc):
        return None
    trend = loc.get("trend_point") or {}
    axis = trend.get("dominant_axis") if isinstance(trend, dict) else None
    if axis:
        return axis
    result = _loc_result(loc)
    iso = result.iso if result is not None else None
    return iso.dominant_axis if iso is not None else None


def _loc_fault_ids(loc: dict[str, Any]) -> list[str]:
    """The fault ids committed at this point, as MODEL ids (contract §3:
    `committed_fault` is `Finding.fault`, "not a display label — the string a
    query can group by"). Display goes through `_fault_label`, here as everywhere.

    The wire carries at most ONE id; a live result can carry several, and does
    on a machine with two calls. Both collapse to a list so the templates have
    one shape to render.
    """
    if not _loc_ok(loc):
        return []
    result = _loc_result(loc)
    if result is not None:
        return [f.fault for f in result.findings if f.fault != "no_significant_findings"]
    fault = loc.get("committed_fault")
    return [fault] if fault else []


def _loc_not_diagnosed(loc: dict[str, Any]) -> str | None:
    """Why a point carries no numbers, in the words the contract puts there.

    `gate_fail` → `gate_reasons`, one sentence each. `unreadable` / `error` →
    `message`, "written for an analyst". Without this a non-ok point would
    render as a heading with nothing under it, which reads as a point that was
    fine rather than a point that was not assessed.
    """
    status = loc.get("status") or "ok"
    if status == "ok":
        return None
    if status == "gate_fail":
        reasons = [r for r in (loc.get("gate_reasons") or []) if r]
        joined = "; ".join(reasons)
        return (f"data-quality gate failed, so no diagnosis was made — {joined}"
                if joined else "data-quality gate failed, so no diagnosis was made")
    return loc.get("message") or "this location was not analysed"


def fault_sheet_context(
    result: AnalysisResult,
    machine: MachineMeta,
    case: Case | None = None,
    *,
    locations: list[dict[str, Any]] | None = None,
    analyst: str | None = None,
    iso_assumed: bool | None = None,
    #: Session REPORT-4 (item 6) — declared measurement directions, when the
    #: reading carried any. See `axis_phrase`.
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Page 1 — the fault sheet, in the brief's order and nothing else.

    Session REPORT-3 (items 1, 6, 7, 8). Every value is presentation-ready: the
    templates SELECT and PRINT, they never derive, exactly as
    `cause_hypotheses` and `coverage_roster` established. Every number is one
    the `AnalysisResult` already holds; the wording around them is template
    text.

    `locations` is the multi-location roster, `docs/contracts/machine_result.md`
    §3 shape — `label`, `status`, `iso_zone`, `severity_rms_mms`,
    `committed_fault`, `trend_point`, plus the optional live `result` / `case` /
    `charts` a caller may already hold (Session REPORTFIX-1; see the accessors
    above). None, or a single entry, is the single-location product: the sheet
    then names one point and the rest of the document is byte-identical to what
    it was. On a roster the sheet is MACHINE-level — the worst location's zone
    with that location named, one conclusion, and a fault named once with every
    position it was seen at.

    `iso_assumed` is the contract's §2 machine-level flag and the ONLY thing that
    may put "(assumed)" on the page (§5 rule 3). Absent means nobody said, which
    prints nothing rather than guessing either way.
    """
    roster = list(locations or [])
    worst = _worst_location(roster) if roster else None
    # The roster's worst point decides the machine's headline; a single-location
    # report has no roster and the reading in hand IS the machine.
    #
    # A worst point that rode the wire alone has NO AnalysisResult, and this is
    # the one place that matters: everything derived from the object below
    # degrades to None rather than borrowing the document's own result, which
    # would state a different point's evidence under this point's name.
    lead_result = (_loc_result(worst) if worst else result)
    lead_case = (worst.get("case") if worst else case)

    committed = ([f for f in lead_result.findings if f.fault != "no_significant_findings"]
                 if lead_result is not None else [])
    zone = (_loc_zone(worst) if worst
            else (result.iso.iso_zone if result.iso is not None else None))
    return {
        "machine_id": lead_result.machine_id or machine.name,
        # One label per measurement point, in roster order. Falls back to the
        # single `machine.location` string, which is all a pre-INTAKE-2 job
        # carries, and to [] when nobody named a point — never to a guess.
        "locations": ([loc["label"] for loc in roster] if roster
                      else ([machine.location] if machine.location else [])),
        "worst_location": worst["label"] if worst else None,
        # The DATE of collection — the brief's own word, and ten characters
        # rather than nineteen, which is what lets the machine line, the date
        # and the signature rule share one line. The full timestamp is in
        # Machine Details, where it always was. It reaches no document's TEXT
        # today — only the fallback fault-frequency-map PNG's header — so the
        # sheet is the first place an analyst can read it at all.
        "collected": (result.ts or "")[:10] or None,
        # No field anywhere in src/ carries an analyst's name, so this is a
        # blank rule unless a caller supplies one. It is a named context key
        # rather than a hardcoded blank so the intake can fill it without
        # touching this module.
        "analyst": analyst,
        "health": _sheet_health(lead_result, machine, loc=worst, iso_assumed=iso_assumed,
                                names=names),
        # Item 7 — beside the severity, and the zone still prints.
        "data_quality": _sheet_data_quality(lead_result, worst, roster),
        "fault": _sheet_fault(committed, lead_result, lead_case, roster, worst=worst,
                              names=names),
        "recommendation": (_sheet_recommendation(lead_result)
                           if lead_result is not None else None),
        "trigger": escalation_trigger(lead_result) if lead_result is not None else None,
        "no_diagnosis": (lead_result is not None
                         and (lead_result.quality_gate.overall == "fail"
                              or _analysis_failed(lead_result))),
        "not_assessable": (_not_assessable(lead_result) if lead_result is not None
                           else (worst is not None and _loc_zone(worst) is None)),
    }


def _machine_lead(
    result: AnalysisResult, case: Case | None, locations: list[dict[str, Any]] | None,
) -> tuple[AnalysisResult, Case | None]:
    """The reading every MACHINE-LEVEL sentence in the document speaks for.

    Session REPORTFIX-1. A route report is about one machine measured at N
    points, and its document is rendered from location 1 — which on FIXTURE-1's
    set is a healthy point while the fault is three points away. So any sentence
    that names the MACHINE and states a verdict has to speak for the machine,
    and the machine's condition is its worst measured point. That is a
    SELECTION, not an aggregate: contract §5 rule 2 forbids totalling or
    averaging across locations, and this totals nothing — it picks one point's
    own numbers and `fault_sheet_context` names which point they came from.

    Single-location reports are unaffected: a roster of under two returns the
    document's own reading, so the bytes are what they were.

    Falls back to the document's own whenever the worst point rode the wire
    without its objects. A verdict cannot be stated from a zone alone, and a
    half-built one would be worse than location 1's honest reading.
    """
    roster = list(locations or [])
    if len(roster) < 2:
        return result, case
    worst = _worst_location(roster)
    lead = _loc_result(worst)
    if lead is None:
        return result, case
    return lead, (worst.get("case") or case)


def _sheet_data_quality(
    lead_result: AnalysisResult | None, worst: dict[str, Any] | None,
    roster: list[dict[str, Any]],
) -> str | None:
    """Item 7's caveat, raised by ANY measured point rather than only the headline.

    Session REPORTFIX-1. The caveat used to read the lead result alone, which on
    a route is the WORST point — so a ski-slope artifact at any other point
    reached page 1 as a clean severity with nothing beside it. That is the
    inverse of what the check is for: FIXTURE-1's 13th file is a healthy point
    lifted to 20 mm/s by an integration artifact, and the whole reason it exists
    is that a report might call the wrong point the worst.

    A flagged point that is NOT the one on the headline is NAMED, because the
    caveat's own words are about "overall", and an unqualified caveat under a
    Zone D line would read as doubt about the Zone D point specifically.

    A single-location report (no roster) is unchanged, deliberately: same input,
    same bytes.
    """
    if not roster:
        return SKI_SLOPE_CAVEAT if (
            lead_result is not None and _ski_slope_flagged(lead_result)) else None
    flagged = [
        loc["label"] for loc in roster
        if (res := _loc_result(loc)) is not None and _ski_slope_flagged(res)
    ]
    if not flagged:
        return None
    headline = worst.get("label") if worst else None
    if flagged == [headline]:
        # The flagged point IS the one the severity line is about, so the
        # caveat already reads as being about it. Naming it would be furniture.
        return SKI_SLOPE_CAVEAT
    return f"{SKI_SLOPE_CAVEAT} — flagged at {', '.join(flagged)}"


def _worst_location(roster: list[dict[str, Any]]) -> dict[str, Any]:
    """The roster entry whose ISO zone is worst — the machine's headline (item 8).

    Ties break on ROSTER ORDER, which is the order the route was walked, and
    never on amplitude: two points in Zone D are both in Zone D, and naming one
    of them "worse" would be a ranking `pdm_core` did not make. A point whose
    severity was not assessable has no zone to rank and cannot win; a roster of
    nothing but those falls back to the first point, so the sheet still names a
    location rather than none.

    Session REPORTFIX-1: a point whose `status` is not `ok` cannot win either,
    and for the same reason — contract §5 rule 1. A `gate_fail` point was NOT
    diagnosed, so promoting it to the machine's headline would put a zone on
    page 1 that no analysis produced.
    """
    rank = {"A": 0, "B": 1, "C": 2, "D": 3}
    best: dict[str, Any] | None = None
    best_rank = -1
    for loc in roster:
        r = rank.get(_loc_zone(loc) or "", -1)
        if r > best_rank:
            best, best_rank = loc, r
    return best or roster[0]


def _sheet_health(
    result: AnalysisResult | None, machine: MachineMeta, *,
    loc: dict[str, Any] | None = None, iso_assumed: bool | None = None,
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The one health line: overall mm/s RMS, the ISO zone, and the group and
    support class it was judged by — with "(assumed)" when the intake flagged
    the class rather than having been told it.

    `zone_clause` is `_zone_clause`'s exact string, so the sheet and the
    Executive Summary cannot word the same judgement differently.

    Session LIMITS-1b: on a custom basis it is that string PLUS the operator's
    ruled sentence, so page 1 carries both zones -- the machine's own and the
    one ISO 20816-3 would have given the same millimetres per second. A custom
    zone must not hide a real problem, and the only way to prove it is not
    hiding one is to print the other answer beside it.
    """
    iso = result.iso if result is not None else None
    zone = (_loc_zone(loc) if loc is not None
            else (iso.iso_zone if iso is not None else None))
    rms = (_loc_rms(loc) if loc is not None
           else (iso.severity_rms if iso is not None else None))
    axis = (_loc_axis(loc) if loc is not None
            else (iso.dominant_axis if iso is not None else None))
    clause = _zone_clause(result, names) if result is not None else None
    basis = _basis_clause(result) if result is not None else None
    # Session REPORTFIX-1. A worst point that rode the wire alone has no
    # AnalysisResult to phrase the clause from, so it is built from the
    # contract's own scalars: the same sentence, minus the boundary figure the
    # wire does not carry. The numbers are still the wire's -- nothing here is
    # derived, and without this the health line would go silent on exactly the
    # machine that needed it.
    # Session LIMITS-1b, found while proving item 1's not-assessable state.
    # `zone` here is the CONTRACT's §3 `iso_zone`, which for a withheld reading
    # is the sentinel `"not_assessable"` -- truthy, and not a zone at all. The
    # fallback then built `"ISO Zone not_assessable —  per ISO 20816-3: overall
    # 5.20 mm/s RMS"`, a zone verdict and a severity figure for a reading whose
    # zone was deliberately withheld (Session A's contract, and the exact door
    # `mark_not_assessable` closes on `iso_zone_would_be`). Measured at the base
    # commit as well -- this is REPORTFIX-1's, not this session's -- and fixed
    # here because item 1 requires the withheld state to print no zone clause
    # and pin (d) cannot pass while it does. The letters ARE the fallback's own
    # intent: every map it feeds (`_worst_location`'s rank; `_ZONE_DECISION`
    # too, until Session REPORT-4 item 7 removed it) is keyed on A-D.
    if clause is None and zone in ("A", "B", "C", "D") and rms is not None:
        # Session LIMITS-1c closes LIMITS-1b F-2. This branch used to hardcode
        # "ISO Zone {zone} ... per ISO 20816-3", because the contract's §3
        # entry carried no basis and ISO was the only answer the product could
        # produce. It now carries `zone_basis`, so a worst point judged against
        # a plant limit is no longer attributed to a standard that did not set
        # its boundaries -- which had been printing, on one line, "per ISO
        # 20816-3" immediately before `_basis_clause`'s "Judged against
        # machine-specific limits".
        #
        # The group/support parenthetical is dropped on a custom basis for the
        # same reason `_zone_clause` drops it: they did not produce this letter.
        loc_custom = bool(loc is not None and loc.get("zone_basis") == "custom")
        label = "Zone" if loc_custom else "ISO Zone"
        authority = _CUSTOM_BASIS_PHRASE if loc_custom else "ISO 20816-3"
        parts = [f"{label} {zone} — {zone_word(zone).lower()} per {authority}: "
                 f"overall {rms:.2f} mm/s RMS"]
        if axis:
            parts.append(f" on the {axis_phrase(axis, names)}")
        if machine.iso_group and machine.iso_support and not loc_custom:
            parts.append(f" (Group {machine.iso_group}, {machine.iso_support} support)")
        clause = "".join(parts)
    # The ruled sentence rides IN the health line rather than beneath it: the
    # ruling is about what that one line reads, and a reader who stops after it
    # has to have seen both zones. The full stop comes from the template, which
    # already punctuates this string -- hence `_basis_clause`'s missing one.
    if clause is not None and basis is not None:
        clause = f"{clause}. {basis}"
    return {
        "zone_clause": clause,
        "rms": rms,
        "axis": axis,
        "zone": zone,
        "group": machine.iso_group,
        "support": machine.iso_support,
        # Contract §2 `iso_assumed`, and §5 rule 3: this flag, and nothing else,
        # gates the word "assumed". Absent means nobody told us either way,
        # which prints nothing -- the safe direction, and the one the old
        # `getattr(machine, "iso_group_assumed", False)` reached by accident.
        # That attribute exists on no model in src/, so the flag could never
        # print at all. `group_source` / `support_source` are deliberately NOT
        # read: the contract (§2, §6) says they are not on the wire.
        "assumed": bool(iso_assumed),
        # Item 4, one sentence BENEATH the health line -- its own paragraph,
        # because it is a second claim (about the fault screen) and not more of
        # the severity sentence. Withheld on a not-assessable reading: there is
        # no health line under which to put it, and no severity judgement was
        # made for a gate to have governed.
        "gate_note": (_GATE_NOTE if (result is not None and _custom_basis(result)
                                     and not _not_assessable(result)) else None),
    }


def _fault_positions(fault: str, roster: list[dict[str, Any]]) -> list[str]:
    """Every measurement point that committed this fault, in roster order.

    Item 8: the same fault at more than one location is named ONCE with every
    position. A route report that prints "bearing outer-race fault" four times
    has told the reader four things where there was one.
    """
    return [loc["label"] for loc in roster if fault in _loc_fault_ids(loc)]


def _sheet_fault(
    committed: list[Finding], result: AnalysisResult | None, case: Case | None,
    roster: list[dict[str, Any]], *, worst: dict[str, Any] | None = None,
    names: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """The fault, where it is on the machine, and the evidence in one sentence.

    The sentence is `_signature_sentence(..., with_orders=True)` — the same
    string the Diagnosis lead carries, so the fault Hz, its shaft order and the
    harmonic found are stated identically on page 1 and in the evidence.
    """
    if not committed:
        # Session REPORTFIX-1. The worst point may have ridden the wire alone,
        # which carries `committed_fault` as a MODEL ID and nothing else -- no
        # frequency, no order, no confidence. Name the fault and where it is,
        # and say nothing further: a report may not print a number no tool
        # result produced, and an invented Hz is exactly that.
        ids = _loc_fault_ids(worst) if worst is not None else []
        if not ids:
            return None
        return {
            "label": _fault_label(ids[0]),
            "confidence": None,
            "axis": _loc_axis(worst) if worst is not None else None,
            "positions": _fault_positions(ids[0], roster),
            "evidence": None,
        }
    finding = committed[0]
    return {
        "label": _fault_label(finding.fault),
        "confidence": finding.confidence,
        "axis": finding.evidence.get("axis"),
        "positions": _fault_positions(finding.fault, roster),
        "evidence": _signature_sentence(finding, result, case, with_orders=True, names=names),
    }


def _sheet_recommendation(result: AnalysisResult) -> dict[str, str] | None:
    """REC-1's three named parts, laid out — this is the session F-5 named.

    `CorrectiveRecommendation.action` / `.timing` / `.reassess` have existed
    since REC-1 and nothing has ever rendered them; `_recommendation_texts`
    keeps only `.text`. The sheet prints the three parts under their own
    heading and the Recommendations section still prints `.text`, so the two
    cannot come to say different things — the parts ARE ordered spans of that
    text, which `tests/test_rec1_recommendations.py` pins for every fault id.
    """
    zone = result.iso.iso_zone if result.iso is not None else None
    recs = corrective_recommendations_for_findings(result.findings, zone)
    if not recs:
        return None
    rec = recs[0]
    return {"action": rec.action, "timing": rec.timing,
            "reassess": rec.reassess, "text": rec.text}


def location_roster(locations: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """The per-location Evidence roster, in ROSTER ORDER — Session REPORT-3 item 8.

    A route is walked in an order and the report reads in that order; sorting by
    severity would be a ranking `pdm_core` did not make, and it would put the
    reader somewhere other than where they stood.

    `None` for the single-location product — which is every job today, until
    INTAKE-2 lands the intake that produces a roster. That is what makes a
    one-location report byte-identical to what it was apart from page 1: the
    macros below render NOTHING when this is None, so no section, no heading and
    no blank line appears (the R2-BUILD rule).

    A roster of ONE is also None, deliberately: a machine measured at one point
    is the single-location product wearing a list, and printing "Evidence —
    Motor DE" above the only evidence there is would be furniture, not
    information.
    """
    roster = list(locations or [])
    if len(roster) < 2:
        return None
    rows: list[dict[str, Any]] = []
    for loc in roster:
        result = _loc_result(loc)
        rows.append({
            "label": loc["label"],
            "zone": _loc_zone(loc),
            # Session REPORT-4 (item 1). The roster printed "ISO Zone {letter}"
            # twice per point, unconditionally, while `_sheet_health` two
            # hundred lines up already branched on this very key
            # (`loc_custom`, :996) — so a route whose points carry plant limits
            # contradicted its own page 1. The contract's per-location
            # `zone_basis` is the authority here exactly as `Reading.zone_basis`
            # is for the machine-level line; `_zone_label` is its twin.
            "zone_label": ("Zone" if loc.get("zone_basis") == "custom" else "ISO Zone"),
            "rms": _loc_rms(loc),
            "axis": _loc_axis(loc),
            # The location's own call, in the report's own words. Named per
            # location here AND once on page 1 with every position it was seen
            # at: the sheet answers "what is wrong with this machine", the
            # roster answers "where".
            "committed": [_fault_label(f) for f in _loc_fault_ids(loc)] or None,
            # Session REPORTFIX-1: the evidence TABLE and the figures need the
            # live objects, which the wire does not carry. A wire-only point
            # renders the compact section -- label, zone, overall, committed
            # call -- and no empty furniture below it (the R2-BUILD rule).
            "evidence_rows": _evidence_rows(result) if result is not None else None,
            "charts": loc.get("charts"),
            # Why a non-ok point carries no numbers, in the contract's own words.
            # Without it the point would render as a heading with nothing under
            # it, which reads as a point that was fine rather than one that was
            # never assessed (contract §5 rule 1).
            "not_diagnosed": _loc_not_diagnosed(loc),
        })
    return rows


def _sheet_figure(charts: ChartSet | None, result: AnalysisResult) -> Any | None:
    """The one spectrum figure page 1 carries: the channel the call was made on.

    Session REPORT-3 (item 1) — "one spectrum figure with the fault markers".
    It is the CHARTS-2 two-panel, unchanged and re-used rather than re-rendered:
    a second, shorter variant would print its labels below the 8 pt floor
    CHARTS-2 measured against the stylesheet, and that floor is the one
    guarantee that module exists to make. The page-1 budget is met by tightening
    the sheet's own SPACING instead (report.css `.sheet`).

    The channel chosen is the one carrying the committed finding, falling back
    to the first channel with a figure — a healthy report has no committed axis
    and still deserves its spectrum on page 1.
    """
    if charts is None:
        return None
    if charts.sheet is not None:
        return charts.sheet
    # A manifest built before this session, or by a caller that only has the
    # appendix figures: show the full-height one rather than no figure at all.
    committed = [f for f in result.findings if f.fault != "no_significant_findings"]
    axis = committed[0].evidence.get("axis") if committed else None
    for channel in charts.channels:
        if channel.axis == axis and channel.figures:
            return channel.figures[0]
    for channel in charts.channels:
        if channel.figures:
            return channel.figures[0]
    return None


def _dq_notes(result: AnalysisResult) -> list[str]:
    return [
        f"{c.name}: {c.status.upper()} — {c.reason}"
        for c in result.quality_gate.checks
        if c.status in ("warn", "fail") and c.reason
    ]


def _limitations(result: AnalysisResult) -> list[str]:
    notes: list[str] = []
    if result.zscore is None:
        notes.append(
            "Statistical (z-score) anomaly detection was not run: it requires a streaming "
            "baseline, which a single-file analysis does not carry."
        )
    if result.isolation_forest is not None and result.isolation_forest.status == "not_enough_history":
        notes.append(
            "Multivariate anomaly detection (Isolation Forest) declined: insufficient history."
        )
    if result.quality_gate.overall == "warn":
        notes.append(
            "The reading carried data-quality warnings (see Data Quality); findings should be "
            "read with that caveat."
        )
    for f in result.findings:
        if f.confidence == "low":
            notes.append(
                f"'{_fault_label(f.fault)}' is a low-confidence finding — treat as provisional "
                "pending the recommended follow-up."
            )
    return notes


#: Session REPORT-2 (item 5). The block's heading, in both renderers' words.
FMAX_ADEQUACY_HEADING = "Frequency range — what Fmax can evaluate"

#: The order the bearing frequencies are listed in, everywhere the report lists them.
_BEARING_FREQ_KEYS = ("BPFO", "BPFI", "BSF", "FTF")


def fmax_adequacy_context(result: AnalysisResult, case: Case | None) -> dict[str, Any] | None:
    """Session REPORT-2 (item 5) -- up to which harmonic each computed fault
    frequency is inside the measured range.

    A CAT analyst reviewing the outreach sample said an Fmax of 500 Hz cannot
    evaluate the bearing harmonics. The report never said what its Fmax COULD
    evaluate; now it does, from two things it already holds: the span of the
    measured spectrum (`Spectrum.fmax_hz`, else its top bin -- the same rule the
    parameters table's Fmax row uses) and `rca.bearing_freqs`. One line per
    computed frequency, each assembled here as ONE string so the markdown and
    HTML twins carry identical wording (the REPORT-NA precedent):

        BPFO 107.03 Hz (3.57×): visible to 4×; 5× and above are beyond Fmax 500 Hz.

    `floor(fmax / f)` and nothing else. With several channels the smallest span
    is used and named as such. Without a bearing geometry the block states that
    the reach was not assessed, rather than disappearing. Without a spectrum
    there is no range to speak of and the block is None.
    """
    spectra = _channel_spectra(case)
    spans: list[float] = []
    for _axis, series in sorted(spectra.items()):
        for spectrum, _roles in series:
            span = spectrum.fmax_hz or (spectrum.freq_hz[-1] if spectrum.freq_hz else None)
            if span:
                spans.append(float(span))
    if not spans:
        return None
    fmax = min(spans)
    differs = max(spans) != fmax
    where = " on the shortest channel" if differs else ""
    rca = result.rca
    shaft = rca.shaft_freq_hz if rca is not None else 0.0
    if rca is None or rca.bearing_freqs is None:
        return {
            "heading": FMAX_ADEQUACY_HEADING,
            "fmax_hz": fmax,
            "intro": f"The measured spectrum reaches Fmax {fmax:g} Hz{where}.",
            "lines": [],
            "absence": (
                "No bearing geometry was supplied, so no fault frequency was computed and "
                "the reach of this range against a bearing's harmonic series is not assessed."
            ),
        }
    intro = (
        f"The measured spectrum reaches Fmax {fmax:g} Hz{where}. For each fault frequency "
        "computed from the supplied geometry, the harmonics inside that range are the only "
        "ones this analysis could see:"
    )
    lines: list[str] = []
    for key in _BEARING_FREQ_KEYS:
        freq = getattr(rca.bearing_freqs, key, None)
        if not freq:
            continue
        n = int(fmax // float(freq))
        named = f"{key} {fmt_hz_order(float(freq), shaft)}"
        if n == 0:
            line = f"{named}: not inside the measured range at all; even 1× is beyond Fmax {fmax:g} Hz."
        else:
            line = f"{named}: visible to {n}×; {n + 1}× and above are beyond Fmax {fmax:g} Hz."
        if n < 3:
            line += " The 1×–3× series this analysis screens is not fully inside the measured range."
        lines.append(line)
    return {
        "heading": FMAX_ADEQUACY_HEADING,
        "fmax_hz": fmax,
        "intro": intro,
        "lines": lines,
        "absence": None,
    }


def _order_cell(freq_hz: Any, shaft_hz: float) -> str:
    """`"3.57×"` for an evidence-table order column; "—" when either is absent."""
    if freq_hz is None or not shaft_hz or shaft_hz <= 0:
        return "—"
    return f"{float(freq_hz) / shaft_hz:.2f}×"


#: The damage-stage box's zone line, as `pdm_core/staging.py:252` mints it.
#: Matched as a PREFIX, so the letter (and anything a future staging session
#: appends) travels with it.
_STAGE_ZONE_PREFIX = "ISO zone at the time of measurement: "


def _stage_zone_line_on_custom_basis(line: str, result: AnalysisResult) -> str:
    """Session REPORT-4, item 1 — the damage-stage box's zone line, re-attributed.

    `pdm_core/staging.py:252` appends `"ISO zone at the time of measurement: B"`
    to the stage evidence. On a custom basis that letter is not ISO's: it is the
    zone the analyst's own 5 / 8 / 12 mm/s limits produced, and ISO 20816-3 would
    have said something else — D, on the reading this was measured against. The
    box is the last place on the page that still signed the standard's name to
    the plant's number after the health line and the banner were fixed.

    Remapped HERE, at presentation, and not at the mint. `pdm_core` is latched
    for this session, but that is not the only reason: `StageEstimate.evidence`
    is a list of display strings that the staging layer does not otherwise read,
    and `_stage_with_orders` has annotated those same strings at render time
    since REPORT-2 — so this is the established seam, not a workaround for one.
    `analysis.json` keeps pdm_core's own wording, untouched.

    Left alone on the ISO basis, where the sentence is true as written.
    """
    if not line.startswith(_STAGE_ZONE_PREFIX) or not _custom_basis(result):
        return line
    zone = line[len(_STAGE_ZONE_PREFIX):]
    basis = _CUSTOM_BASIS_PHRASE
    would_be = result.iso.iso_zone_would_be if result.iso is not None else None
    if would_be and would_be != zone:
        return f"Zone at the time of measurement: {zone} ({basis}; ISO would give {would_be})"
    return f"Zone at the time of measurement: {zone} ({basis})"


def _stage_with_orders(stage: StageEstimate, shaft_hz: float,
                       result: AnalysisResult | None = None) -> StageEstimate:
    """The damage-stage evidence lines with the shaft order beside each fault
    frequency (Session REPORT-2, item 3), and — since Session REPORT-4 — the zone
    line attributed to whoever actually set the boundary. `StageEstimate` is a
    frozen pdm_core dataclass whose strings name frequencies in Hz alone and name
    ISO unconditionally; the report layer fixes both at presentation time. ONE
    helper, called at every site that computes a stage for rendering, so the
    deterministic and drafted paths stay byte-identical.

    `result` is optional only so a caller that has no basis to apply (there is
    none in the tree) still gets the orders pass it always got."""
    lines = [annotate_orders(line, shaft_hz) for line in stage.evidence]
    if result is not None:
        lines = [_stage_zone_line_on_custom_basis(line, result) for line in lines]
    return dataclasses.replace(stage, evidence=lines)


def _shaft_hz(result: AnalysisResult) -> float:
    return result.rca.shaft_freq_hz if result.rca is not None else 0.0


def _evidence_rows(result: AnalysisResult) -> list[dict]:
    rows: list[dict] = []
    shaft = _shaft_hz(result)
    for f in result.findings:
        ev = f.evidence
        if ev.get("freq_hz") is None and ev.get("expected_hz") is None:
            continue
        rows.append(
            {
                "label": _fault_label(f.fault),
                # Not rendered in either document. It is here so a graphic about
                # this row can pick the series the row's own DETECTOR read — the
                # bearing family reads the envelope, the 1x-family reads
                # raw/velocity. The markdown template names its columns one by
                # one and never iterates this dict, so report.md is unaffected.
                "fault": f.fault,
                "axis": ev.get("axis") or "—",
                "observed": ev.get("freq_hz"),
                "computed": ev.get("expected_hz"),
                # Session REPORT-2 (item 3): the shaft order beside each Hz, as a
                # formatted string ("3.57×"); "—" without a shaft rate or a value.
                "observed_order": _order_cell(ev.get("freq_hz"), shaft),
                "computed_order": _order_cell(ev.get("expected_hz"), shaft),
                "harmonic": "yes" if ev.get("harmonic_present") else "—",
                "sidebands": ", ".join(f"{s:.1f}" for s in ev["sidebands"]) if ev.get("sidebands") else "—",
            }
        )
    return rows


def _checked_for(result: AnalysisResult) -> list[str]:
    """What a clean reading was actually screened for, so a no-findings report can
    say what it ruled out instead of just asserting nothing was found. Derived from
    what the analysis actually ran -- never a boilerplate list."""
    checked: list[str] = []
    rca = result.rca
    if rca is not None and rca.bearing_specs_present and rca.bearing_freqs is not None:
        bf = rca.bearing_freqs
        shaft = rca.shaft_freq_hz
        checked.append(
            "bearing fault frequencies computed from the configured geometry and matched "
            f"against the measured spectrum (BPFO {fmt_hz_order(bf.BPFO, shaft)}, "
            f"BPFI {fmt_hz_order(bf.BPFI, shaft)}, BSF {fmt_hz_order(bf.BSF, shaft)}, "
            f"FTF {fmt_hz_order(bf.FTF, shaft)})"
        )
    if rca is not None and rca.status == "ok":
        checked.append(
            "the 1x/2x shaft-order family screened for imbalance, the misalignment family, "
            "and mechanical looseness"
        )
    if result.trend is not None:
        checked.append("the vibration trend across the reading history")
    else:
        checked.append("no reading history was available, so no trend could be assessed")
    return checked


# ─────────────────────────────────────────────────────────────────────────
# Session R3-DIFF (item 2) — no-geometry honesty.
#
# Without bearing geometry there are no fault frequencies to compute, so the
# bearing detector cannot run at all. That is a MISSING INSTRUMENT, not a clean
# result — but a reading with no committed fault rendered the same
# "parameters within normal range" paragraph either way, and an analyst reading
# it had no way to tell "we looked and it is fine" from "we could not look".
#
# When the spectrum carries strong periodicity that is NOT at a shaft order,
# something in that machine is repeating at a rate this analysis cannot name.
# With geometry it would have been matched or excluded; without it, the only
# honest report says so and says what to supply.
#
# "Strong" reuses the amplitude floor — the same constant the figure draws and
# the same one item 1a made the evidence bar — so this section can only ever
# name a peak the reader can see standing above the line.
# ─────────────────────────────────────────────────────────────────────────

#: Integer shaft orders a peak is tested against before it counts as "unmatched".
#: Reaches past _HARMONIC_MULTIPLES' 5x because this test is the inverse of the
#: detectors': they ask "is this a shaft order I model?", this asks "could this
#: plausibly be ANY shaft order?" — and a wider net here means fewer peaks
#: claimed as unexplained, which is the conservative direction.
_SHAFT_ORDER_CANDIDATES: tuple[float, ...] = (0.5, 1.5, 2.5) + tuple(float(k) for k in range(1, 13))

#: Harmonics of the unmatched fundamental worth naming, if they are present.
_UNMATCHED_HARMONIC_ORDERS: tuple[int, ...] = (2, 3, 4)


def _claimed_frequencies(result: AnalysisResult, machine: MachineMeta | None) -> list[float]:
    """Every frequency a finding in THIS report already accounts for.

    Session UNEXPLAINED-PEAK. Narrower than the detectors' `known_expected`
    (bearing_rca.detect_resonance) on purpose: bearing fault frequencies are
    absent by construction here — `unmatched_periodicity` only runs when no
    geometry was supplied — so what remains is what the report actually
    COMMITTED to, plus the two derived frequencies a machine can carry without
    a bearing model. A peak inside any of these bands is spoken for, and this
    section has nothing to add about it.
    """
    claimed: list[float] = []
    for finding in result.findings:
        freq = finding.evidence.get("freq_hz")
        if freq:
            claimed.append(float(freq))
    if machine is not None:
        if machine.belt is not None and machine.belt.freq_hz:
            claimed.append(machine.belt.freq_hz)
            claimed.append(machine.belt.freq_hz * 2)
        if machine.blades is not None and result.rca is not None:
            claimed.append(machine.blades * result.rca.shaft_freq_hz)
    return claimed


def _is_a_shaft_order(freq: float, shaft: float, tolerance: float) -> bool:
    return any(
        shaft * mult > 0 and abs(freq - shaft * mult) / (shaft * mult) <= tolerance
        for mult in _SHAFT_ORDER_CANDIDATES
    )


def unmatched_periodicity(
    result: AnalysisResult,
    case: Case | None,
    thresholds: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Strong, non-shaft-order periodicity in a reading with NO bearing geometry.

    Returns None — and the report is unchanged — whenever the question does not
    apply: geometry WAS supplied (the bearing detector ran, so an unmatched tone
    is its business, not this section's), the RCA did not run, no spectrum was
    captured, no profile amplitude floor exists to define "strong", or every
    floor-clearing peak sits on a shaft order.

    Reads the spectra through the SAME adapter the pipeline calls
    (`peaks_from_spectrum`) with the same profile constants, so it reports the
    peaks the analysis itself saw rather than a second opinion computed
    differently.
    """
    rca = result.rca
    if rca is None or rca.status != "ok" or rca.bearing_specs_present:
        return None
    if case is None or not case.spectra or rca.shaft_freq_hz <= 0:
        return None
    rca_cfg = (thresholds or {}).get("rca", {})
    floor_min = rca_cfg.get("floor_min")
    if floor_min is None:
        return None  # no floor configured -> no definition of "strong" -> say nothing

    tolerance = rca_cfg.get("tolerance_pct", 5.0) / 100.0
    peak_set = peaks_from_spectrum(
        case.spectra,
        rca.rpm,
        {},
        max_peaks_per_axis=rca_cfg.get("spectrum_max_peaks_per_axis", 3),
        prominence=rca_cfg.get("spectrum_peak_prominence"),
    )
    means = peak_set.axis_mean_amp
    if not means:
        return None

    shaft = rca.shaft_freq_hz
    strong = [
        p for p in peak_set.peaks
        if p.amplitude is not None and _clears_evidence_floor(p, means, floor_min)
    ]
    unmatched = [p for p in strong if not _is_a_shaft_order(p.freq, shaft, tolerance)]
    if not unmatched:
        return None

    # Rank by prominence over the axis's OWN broadband bed, so axes with
    # different overall levels are compared fairly.
    def prominence(peak) -> float:
        mean = means.get(peak.axis) or 0.0
        return (peak.amplitude / mean) if mean > 0 else 0.0

    fundamental = max(unmatched, key=prominence)
    harmonics = []
    for order in _UNMATCHED_HARMONIC_ORDERS:
        target = fundamental.freq * order
        hit = next(
            (p for p in strong
             if p.axis == fundamental.axis
             and target > 0
             and abs(p.freq - target) / target <= tolerance),
            None,
        )
        if hit is not None:
            harmonics.append({"order": order, "freq_hz": round(hit.freq, 1)})

    kinds = {s.kind for s in case.spectra.values()}
    kind_word = "envelope" if kinds == {"envelope"} else "spectrum"
    freq_hz = round(fundamental.freq, 1)
    order = fundamental.freq / shaft
    floor_multiple = round(prominence(fundamental), 1)
    if harmonics:
        harmonic_clause = ", harmonics at " + ", ".join(
            f"{h['freq_hz']} Hz ({h['order']}×)" for h in harmonics
        )
    else:
        harmonic_clause = ", no harmonic of it above the floor"
    # Assembled here rather than in the template so the wording is one testable
    # string and the rendered markdown is one clean paragraph rather than a
    # column of Jinja-wrapped fragments.
    sentence = (
        f"Strong unmatched {kind_word} periodicity at {freq_hz} Hz "
        f"({order:.2f}× shaft, axis {fundamental.axis}) at {floor_multiple}× the broadband "
        f"floor{harmonic_clause} — supply bearing geometry to identify."
    )

    # ── Session UNEXPLAINED-PEAK ─────────────────────────────────────────
    # Everything above is unchanged, including `sentence`: the no-findings
    # branch that has rendered it since R3-DIFF must stay byte-identical, and
    # tests/test_no_geometry_honesty.py pins it.
    #
    # What is new is the case that branch never covered. When a finding DOES
    # commit, this section was computed and thrown away — all four templates
    # gated it on `no_findings`. On the HIST-2 sample that meant a report
    # committing `imbalance` on a 0.9 line while saying nothing at all about
    # the 1.4 line beside it, which is the loudest thing in the spectrum and
    # 1.56× the line the report does commit to.
    #
    # The trigger is deliberately the narrowest one that covers that case: the
    # LOUDEST peak on the channel is claimed by nothing. A report whose
    # committed finding already owns the loudest line is untouched.
    claimed = _claimed_frequencies(result, case.machine)
    unclaimed = not any(_within(fundamental.freq, c, tolerance) for c in claimed)
    loudest = next(
        (p for p in peak_set.peaks if p.axis == fundamental.axis and p.rank == 1), None
    )
    is_loudest = loudest is not None and loudest.freq == fundamental.freq

    committed = _committed_reference(result, peak_set, fundamental)
    outranks = bool(is_loudest and unclaimed and committed is not None)
    if outranks and committed is not None:
        # One sentence, assembled here for the same reason the one above is:
        # the wording is then a single testable string rather than a column of
        # Jinja-wrapped fragments. Every number in it is read off the peak set
        # the analysis itself saw — none is computed a second way.
        sentence += (
            f" It is the loudest line on axis {fundamental.axis}, at "
            f"{committed['ratio']:.2f}× the amplitude of the {committed['freq_hz']} Hz peak "
            f"this report commits to as {committed['label']}. No committed or considered "
            "finding accounts for it."
        )

    return {
        "sentence": sentence,
        "kind_word": kind_word,
        "axis": fundamental.axis,
        "freq_hz": freq_hz,
        "order": round(order, 2),
        "shaft_hz": round(shaft, 2),
        "floor_multiple": floor_multiple,
        "harmonics": harmonics,
        "amplitude": fundamental.amplitude,
        "is_loudest_on_axis": is_loudest,
        "committed": committed,
        "outranks_committed": outranks,
    }


def _committed_reference(
    result: AnalysisResult, peak_set: Any, fundamental: Any
) -> dict[str, Any] | None:
    """The committed finding this report is asking the reader to compare against.

    Its amplitude is NOT on the `FaultMatch` — the model carries frequencies and
    velocities, not spectral amplitude — so it is read out of the same peak set
    the unexplained line came from. Same array, same peak picker, one comparison
    an analyst can redo by eye against the spectrum figure.
    """
    for finding in result.findings:
        freq = finding.evidence.get("freq_hz")
        axis = finding.evidence.get("axis")
        if not freq or axis != fundamental.axis:
            continue
        peak = next(
            (p for p in peak_set.peaks
             if p.axis == axis and p.amplitude and abs(p.freq - float(freq)) < 1e-6),
            None,
        )
        if peak is None or not peak.amplitude or not fundamental.amplitude:
            continue
        return {
            "label": _fault_label(finding.fault),
            "freq_hz": round(peak.freq, 1),
            "amplitude": peak.amplitude,
            "ratio": fundamental.amplitude / peak.amplitude,
        }
    return None


# ─────────────────────────────────────────────────────────────────────────
# Session R2-BUILD — the cause section.
#
# A vibration reading identifies a FAULT. It does not identify the CAUSE that
# produced that fault, and it never can: the evidence that separates
# contamination from a bad fit from stray shaft current is oil analysis, thermal
# measurement and dismounted visual inspection. batch1 is built from
# dismounted-bearing damage atlases and carries no cited vibration observation
# with any frequency content at all (knowledge/review/b03_queue.md §A-0).
#
# So this section is a HYPOTHESIS LIST, and every design decision below exists
# to stop it reading as a finding:
#
#   * it renders only when a bearing fault was actually committed;
#   * its heading says "hypotheses for analyst confirmation" in the heading
#     itself, not in a footnote a reader can skip;
#   * each cause's evidence is SPLIT by who can collect it, so an analyst can
#     see at a glance that most of what would settle this was never measured;
#   * an observation the library marks `inferred: true` renders `(our reasoning)`
#     where a citation would go, rather than rendering bare beside cited
#     siblings. Bare-beside-cited is exactly the adjacency failure R2-CITECHECK
#     found in the YAML, and it recurs in a rendered list if nothing marks it.
#
# Nothing here derives, weighs or ranks anything from the measurement. The order
# is the library's own (knowledge/loader.lookup_causes), the words are the
# library's own, and the split is a lookup on the observation's `stream` field.
# ─────────────────────────────────────────────────────────────────────────

#: Streams the analyst has to go and collect: this analysis performed none of
#: them. Everything not listed here is evidence the pipeline itself works from.
_FIELDWORK_STREAMS: frozenset[str] = frozenset({"oil", "thermal", "visual"})


def _committed_bearing_faults(result: AnalysisResult) -> list[str]:
    """Committed bearing faults, in pdm_core's own findings order."""
    return [f.fault for f in result.findings if f.fault in BEARING_FAULT_FAMILIES]


#: Session REPORT-4 (item 5). What an inferred observation prints where a
#: citation would go. It was `[inferred]`, which is a lint marker's voice, not an
#: analyst's: a reader who has not been told what it means reads a bracketed
#: lowercase word as machinery showing through. It says the same thing in the
#: report's own words instead, and the explanatory sentence above the cause list
#: names this exact string. ONE constant, because the templates also TEST against
#: it to set the CSS class on the slot — two spellings would silently lose the
#: styling that keeps an uncited observation visually distinct from a cited one.
INFERRED_CITATION_SLOT = "(our reasoning)"


def _citation_slot(observation: Any) -> str:
    """What goes where a citation goes. An inferred observation gets
    `(our reasoning)` rather than nothing -- see the module note above."""
    if observation.inferred:
        return INFERRED_CITATION_SLOT
    return " ".join(c.rendered() for c in observation.citations)


def cause_section(result: AnalysisResult, *, stage: str = "any") -> dict[str, Any] | None:
    """The presentation-ready cause block, or None when there is nothing to say.

    None (i.e. the whole section disappears) when no bearing fault was
    committed, or when the library returns no entry for the ones that were. The
    template renders `{% if causes %}` on exactly this.

    `history_checked` decides which bucket a `history`-stream observation lands
    in. A history observation ("balance records", "alignment record", "which ring
    rotates") is a question about the machine's record rather than a lab test, so
    it is only honest to file it under evidence this analysis had when the
    analysis actually carried a reading history to read it against -- i.e. when a
    trend was computed. Without one it is fieldwork like any other, and is
    listed as such.
    """
    faults = _committed_bearing_faults(result)
    if not faults:
        return None
    causes = lookup_causes_for(faults, stage)
    if not causes:
        return None

    history_checked = result.trend is not None
    rendered: list[dict[str, Any]] = []
    for cause in causes:
        checked: list[dict[str, str]] = []
        collect: list[dict[str, str]] = []
        for observation in cause.discriminating_evidence:
            is_fieldwork = observation.stream in _FIELDWORK_STREAMS or (
                observation.stream == "history" and not history_checked
            )
            item = {
                "text": " ".join(observation.observation.split()),
                "stream": observation.stream,
                "citation": _citation_slot(observation),
                # The FLAG, not a string comparison against the slot's wording.
                # Both templates style an uncited observation differently from a
                # cited one, and they used to decide that by testing
                # `item.citation == '[inferred]'` — so REPORT-4's rewording of
                # that slot would have silently dropped the styling on every
                # inferred row while every text pin stayed green.
                "inferred": bool(observation.inferred),
            }
            if is_fieldwork:
                collect.append({**item, "how": " ".join(observation.how_to_collect.split())})
            else:
                checked.append(item)
        rendered.append(
            {
                "name": " ".join(cause.cause.split()),
                "cause_id": cause.cause_id,
                "mechanism": " ".join(cause.mechanism.split()),
                "citations": " ".join(c.rendered() for c in cause.citations),
                "consider": " ".join(cause.consider.split()) if cause.consider else None,
                "checked": checked,
                "collect": collect,
            }
        )
    return {
        "fault_labels": [_fault_label(f) for f in faults],
        "history_checked": history_checked,
        "causes": rendered,
    }


# ─────────────────────────────────────────────────────────────────────────
# Session REPORT-NA — the coverage roster.
#
# A fault family this analysis cannot assess used to be simply absent from the
# report, and absence reads as a clean screen. The roster below makes the
# boundary explicit: every family on the auditor's list that this analysis did
# NOT assess appears in the report as *not assessed*, with its reason class —
# "no detector for this family" or "requires an input not provided: <field>".
#
# The roster is keyed, row for row, to outputs/FAULT_COVERAGE_2026-08-31.md §3
# (each entry's `coverage_row` names its row there). Sessions that make a
# family assessable flip its entry HERE, in one place, and update the audit
# document in the same commit (ROADMAP §(d).0 common law #9).
#
#   * `no_detector` rows are STATIC — no code path names the family at all.
#   * `input_absent` rows are COMPUTED from fields that exist today
#     (machine.bearing, machine.belt.freq_hz, machine.blades, machine.coupled):
#     the detector exists, and the row appears exactly when its input is absent.
#   * `caveat` rows name a blind spot of a family that WAS assessed — they are
#     rendered as caveats, never as "not assessed".
#
# Every rendered line is assembled here as ONE string (the
# unmatched_periodicity precedent), so the markdown and HTML documents carry
# identical wording by construction and a test can assert the exact sentence.
# ─────────────────────────────────────────────────────────────────────────

#: Row kinds. The reason class the report prints follows the kind.
_COV_NO_DETECTOR = "no_detector"
_COV_INPUT_ABSENT = "input_absent"
_COV_CAVEAT = "caveat"

#: The whirl/cage ambiguity band, in shaft orders (operator ruling D-14).
#: FTF computes at ≈0.38–0.42× shaft, and no fractional-order screening exists,
#: so a committed cage call whose primary peak sits in this band cannot be
#: distinguished from oil whirl. Session SUBHARM removes this caveat when the
#: 0.38–0.48× family lands.
_WHIRL_BAND_LO = 0.38
_WHIRL_BAND_HI = 0.48

#: The roster. `family` is the label the report prints; `coverage_row` is the
#: FAULT_COVERAGE_2026-08-31.md §3 row it is keyed to. `input_absent` entries
#: carry `field` (printed in the reason) and `requires` (dispatched against
#: _COVERAGE_INPUT_PRESENT). `caveat` entries carry `applies` (dispatched
#: against the result at build time).
COVERAGE_ROSTER: tuple[dict[str, Any], ...] = (
    {
        "family": "Imbalance — couple (sub-type)",
        "kind": _COV_NO_DETECTOR,
        "coverage_row": "Imbalance — couple",
        "detail": "Distinguishing couple from static imbalance requires cross-bearing phase, "
                  "which this analysis does not measure; imbalance is committed without "
                  "sub-typing.",
    },
    {
        "family": "Imbalance — overhung rotor (sub-type)",
        "kind": _COV_NO_DETECTOR,
        "coverage_row": "Imbalance — overhung",
        "detail": "Requires rotor-configuration knowledge and phase; an overhung rotor's "
                  "axial 1× signature is read by the misalignment screen instead.",
    },
    {
        "family": "Sleeve-bearing oil whirl / whip",
        "kind": _COV_NO_DETECTOR,
        "coverage_row": "Sleeve bearing oil whirl / whip",
        "detail": "No fractional-order (0.38–0.48× shaft) screening exists in this analysis.",
    },
    {
        "family": "Rotor bar faults",
        "kind": _COV_NO_DETECTOR,
        "coverage_row": "Rotor bar faults",
        "geometry_on_file": "rotor_bar_geometry",
        "detail": "Requires pole-pass sideband analysis with pole count and line frequency, "
                  "none of which this analysis carries.",
    },
    {
        "family": "Stator faults (2× line frequency)",
        "kind": _COV_NO_DETECTOR,
        "coverage_row": "Stator faults",
        "geometry_on_file": "line_frequency",
        "detail": "Requires a line-frequency input and a 2×LF check, neither of which exists.",
    },
    {
        "family": "Eccentricity",
        "kind": _COV_NO_DETECTOR,
        "coverage_row": "Eccentricity",
        "detail": "Requires the horizontal-vs-vertical phase relationship, which this analysis "
                  "does not measure; an eccentricity 1× is read by the imbalance screen.",
    },
    {
        "family": "VFD 2× line-frequency faults",
        "kind": _COV_NO_DETECTOR,
        "coverage_row": "VFD 2×LF",
        "geometry_on_file": "line_frequency",
        "detail": "Requires a line-frequency input, which this analysis does not carry.",
    },
    {
        "family": "VFD carrier-frequency artifacts",
        "kind": _COV_NO_DETECTOR,
        "coverage_row": "VFD carrier artifacts",
        "geometry_on_file": "drive_type",
        "detail": "Carrier frequency is not captured, and no detector identifies a carrier "
                  "tone as such.",
    },
    {
        "family": "Gearmesh wear / gear sidebands / hunting tooth",
        "kind": _COV_NO_DETECTOR,
        "coverage_row": "Gearmesh + sidebands + hunting tooth",
        "geometry_on_file": "gear_teeth",
        "detail": "No gear-mesh analysis exists in this version.",
    },
    {
        "family": "Cavitation / recirculation (broadband)",
        "kind": _COV_NO_DETECTOR,
        "coverage_row": "Cavitation / recirculation (broadband)",
        "detail": "No broadband energy metric is diagnosed; only discrete peaks are assessed.",
    },
    {
        "family": "Soft foot",
        "kind": _COV_NO_DETECTOR,
        "coverage_row": "Soft foot",
        "detail": "Requires phase across the feet or a controlled foot-bolt test, neither of "
                  "which is a measurement this analysis performs.",
    },
    {
        "family": "Beat frequencies",
        "kind": _COV_NO_DETECTOR,
        "coverage_row": "Beat frequencies",
        "detail": "Requires close-tone resolution or time-waveform amplitude-modulation "
                  "analysis, neither of which this analysis performs.",
    },
    {
        "family": "Rolling-element bearing faults (BPFO / BPFI / BSF / FTF)",
        "kind": _COV_INPUT_ABSENT,
        "coverage_row": "Rolling-element bearing, stages 1–4",
        "requires": "bearing_geometry",
        # Session INTAKE-HONEST: input-absent rows name the exact upload-form
        # field that supplies the input, in the form's own words, so the
        # analyst reading the row knows what to type next time — or learns
        # the form cannot collect it yet.
        "field": "bearing geometry — the “Bearing model” field (`bearing_model`, "
                 "under More options) on the upload form",
        "detail": "Without bearing geometry the fault frequencies cannot be computed, so the "
                  "bearing screen never ran.",
    },
    {
        "family": "Belt / pulley faults",
        "kind": _COV_INPUT_ABSENT,
        "coverage_row": "Belt / pulley",
        "requires": "belt_frequency",
        "field": "the belt drive geometry — the “Drive pulley Ø”, “Driven pulley Ø” and "
                 "“Centre distance” fields (under More options) on the upload form, from "
                 "which the belt frequency is computed",
        "detail": "Without the belt fundamental no belt frequency can be matched.",
    },
    {
        "family": "Vane / blade pass",
        "kind": _COV_INPUT_ABSENT,
        "coverage_row": "Vane pass / blade pass",
        "requires": "blade_count",
        # No backtick span here on purpose: `md_inline` turns one into a
        # <code> element, `_visible()` replaces tags with spaces, and the
        # markdown/HTML roster-line mirror check in tests/test_report_na.py
        # then sees two different strings for one line.
        "field": "the blade / vane count — the “Blade / vane count” field "
                 "(under More options) on the upload form",
        "detail": "Without a blade count the blade-pass frequency cannot be computed.",
    },
    {
        "family": "Bent shaft",
        "kind": _COV_INPUT_ABSENT,
        "coverage_row": "Bent shaft",
        "requires": "uncoupled_declared",
        "field": "the coupling state — the “Coupling” field (under More options) on the "
                 "upload form, set to “Not coupled”",
        "detail": "The coupled/uncoupled state was not provided and defaults to coupled, so "
                  "the bent-shaft (uncoupled) branch cannot engage; axial 1×/2× evidence is "
                  "read as misalignment.",
    },
    {
        "family": "Rolling-element bearing faults — axial presentation",
        "kind": _COV_CAVEAT,
        "coverage_row": "Rolling-element bearing, stages 1–4",
        "applies": "bearing_screen_ran",
        "detail": "Bearing fault matching reads radial axes only; a thrust-loaded bearing "
                  "fault presenting on the axial channel is not assessed.",
    },
    {
        # Session GEOM-B. The branch commits on a dominant axial 1×, which is
        # the signature of a mid-span bend AND of a bend at the shaft end — so
        # the finding names the fault and stops there. Until PHASE (ROADMAP §S17)
        # gives this analysis 1× phase at both bearings there is no predicate
        # that separates them, and the roster says so rather than letting the
        # silence read as "the location was determined and simply not printed".
        "family": "Bent shaft — bend location",
        "kind": _COV_CAVEAT,
        "coverage_row": "Bent shaft",
        "applies": "bent_shaft_committed",
        "detail": "A bent shaft is committed, but WHERE the shaft is bent — mid-span versus "
                  "at the shaft end — is not assessed: both present as a dominant axial 1×. "
                  "Separating them requires 1× phase read at both bearings, which this "
                  "analysis does not measure.",
    },
    {
        # Operator ruling D-14. Removed by Session SUBHARM.
        "family": "Bearing cage fault (FTF)",
        "kind": _COV_CAVEAT,
        "coverage_row": "Sleeve bearing oil whirl / whip",
        "applies": "cage_whirl_band",
        "detail": "The committed cage finding's primary peak at {freq_hz} Hz sits between "
                  "0.38× and 0.48× of shaft speed: cage defect or oil whirl — not "
                  "distinguished by this analysis.",
    },
)

#: `input_absent` dispatch: True means the input IS present (the family was
#: assessable), so the row does NOT render. `machine.coupled` defaults True and
#: is treated as "provided" only when it is explicitly False — the one state
#: that engages the bent-shaft branch.
_COVERAGE_INPUT_PRESENT: dict[str, Any] = {
    "bearing_geometry": lambda m: m.bearing is not None,
    "belt_frequency": lambda m: m.belt is not None,
    "blade_count": lambda m: m.blades is not None,
    "uncoupled_declared": lambda m: m.coupled is False,
}


# ─────────────────────────────────────────────────────────────────────────
# Session GEOM-A — "geometry on file, awaiting detector".
#
# GEOM-A captures gear tooth counts, rotor-bar and pole counts, line frequency
# and drive type. None of them has a detector yet (SIDEBAND is where they start
# firing), so their families stay `no_detector` and stay NOT ASSESSED — the
# reason class does not soften because a number was typed in. What DOES change
# is that the report can now distinguish "we have never been told" from "you
# told us and we cannot use it yet", and the second sentence is owed to an
# analyst who took the trouble to fill the field in.
#
# The note names no numbers: the values live in Analysis parameters, and a
# second copy here is a second place for them to drift.
# ─────────────────────────────────────────────────────────────────────────
_GEOMETRY_ON_FILE_NOTE = "Geometry on file — awaiting a detector: "


def _geom_gear_teeth(m: MachineMeta) -> str | None:
    if m.gear_teeth_driving is None and m.gear_teeth_driven is None:
        return None
    return "the gear tooth counts you supplied are recorded in Analysis parameters."


def _geom_rotor_bar(m: MachineMeta) -> str | None:
    have = [label for label, value in (("the rotor-bar count", m.rotor_bars),
                                       ("the pole count", m.poles),
                                       ("the line frequency", m.line_freq_hz))
            if value is not None]
    if not have:
        return None
    return f"{_join_and(have)} you supplied {'is' if len(have) == 1 else 'are'} recorded in Analysis parameters."


def _geom_line_frequency(m: MachineMeta) -> str | None:
    if m.line_freq_hz is None:
        return None
    return "the line frequency you supplied is recorded in Analysis parameters."


def _geom_drive_type(m: MachineMeta) -> str | None:
    if m.drive_type is None:
        return None
    return "the drive type you supplied is recorded in Analysis parameters."


_COVERAGE_GEOMETRY_ON_FILE: dict[str, Any] = {
    "gear_teeth": _geom_gear_teeth,
    "rotor_bar_geometry": _geom_rotor_bar,
    "line_frequency": _geom_line_frequency,
    "drive_type": _geom_drive_type,
}


def _join_and(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _bearing_screen_ran(result: AnalysisResult) -> bool:
    rca = result.rca
    return rca is not None and rca.status == "ok" and rca.bearing_specs_present


def _cage_peak_in_whirl_band(result: AnalysisResult) -> float | None:
    """Observed frequency of a committed `bearing_cage` finding whose primary
    peak sits inside the 0.38–0.48× shaft band, else None. Both values are tool
    results (the finding's evidence and rca.shaft_freq_hz); this only divides
    one by the other, the unmatched_periodicity precedent."""
    rca = result.rca
    if rca is None or rca.shaft_freq_hz <= 0:
        return None
    for f in result.findings:
        if f.fault != "bearing_cage":
            continue
        freq = f.evidence.get("freq_hz")
        if freq is None:
            continue
        order = freq / rca.shaft_freq_hz
        if _WHIRL_BAND_LO <= order <= _WHIRL_BAND_HI:
            return float(freq)
    return None


def _bent_shaft_committed(result: AnalysisResult) -> bool:
    """Session GEOM-B: was a bent shaft actually committed on this reading? The
    bend-location caveat is a caveat on an ASSESSED family, so it renders only
    where there is a bent-shaft finding to qualify."""
    return any(f.fault == "bent_shaft" for f in result.findings)


_COVERAGE_PREAMBLE = (
    "Not every fault family is within this analysis's reach, and absence from the findings "
    "above is not evidence of a family's absence. The families below were NOT assessed on "
    "this reading — each line states the reason class: no detector exists for the family, "
    "or a required input was not provided."
)
_COVERAGE_PREAMBLE_GATE_FAIL = (
    "The data-quality gate failed, so NO fault family was assessed on this reading (see "
    "Diagnosis). Independent of that, the families below are outside this analysis's "
    "coverage even on a passing reading — each line states the reason class: no detector "
    "exists for the family, or a required input was not provided."
)
_COVERAGE_PREAMBLE_NO_SCREEN = (
    "Fault-family screening did not complete on this reading, so NO fault family was "
    "assessed. Independent of that, the families below are outside this analysis's "
    "coverage — each line states the reason class: no detector exists for the family, or "
    "a required input was not provided."
)


def coverage_context(result: AnalysisResult, machine: MachineMeta) -> dict[str, Any]:
    """The presentation-ready coverage section: a preamble plus one assembled
    line per row that applies to THIS reading.

    Static `no_detector` rows always render. `input_absent` rows render exactly
    when their field is absent — an input that was supplied means the family was
    assessed, and a roster that named it anyway would be false modesty. `caveat`
    rows render only when their condition holds on this result; they are worded
    as caveats on an assessed family, never as "not assessed".
    """
    rows: list[dict[str, str]] = []
    for entry in COVERAGE_ROSTER:
        kind = entry["kind"]
        detail = entry["detail"]
        if kind == _COV_NO_DETECTOR:
            line = (f"**{entry['family']}** — not assessed — no detector for this family. "
                    f"{detail}")
            geometry_key = entry.get("geometry_on_file")
            if geometry_key is not None:
                note = _COVERAGE_GEOMETRY_ON_FILE[geometry_key](machine)
                if note is not None:
                    line = f"{line} {_GEOMETRY_ON_FILE_NOTE}{note}"
        elif kind == _COV_INPUT_ABSENT:
            if _COVERAGE_INPUT_PRESENT[entry["requires"]](machine):
                continue
            if entry["requires"] == "uncoupled_declared" and machine.coupled_stated:
                # Session GEOM-A. The analyst DID answer, and said coupled. The
                # family is still not assessed -- but "not provided" would be
                # false, and a report that calls a supplied answer missing
                # teaches the analyst their answer did not arrive.
                line = (f"**{entry['family']}** — not assessed — this machine was declared "
                        f"COUPLED, so the bent-shaft (uncoupled) branch does not apply; axial "
                        f"1×/2× evidence is read as misalignment.")
            else:
                line = (f"**{entry['family']}** — not assessed — requires an input not "
                        f"provided: {entry['field']}. {detail}")
        else:  # _COV_CAVEAT
            if entry["applies"] == "bearing_screen_ran":
                if not _bearing_screen_ran(result):
                    continue
            elif entry["applies"] == "cage_whirl_band":
                freq = _cage_peak_in_whirl_band(result)
                if freq is None:
                    continue
                detail = detail.format(freq_hz=f"{freq:.1f}")
            elif entry["applies"] == "bent_shaft_committed":
                if not _bent_shaft_committed(result):
                    continue
            line = f"**{entry['family']}** — caveat. {detail}"
        rows.append({"family": entry["family"], "kind": kind, "line": line})

    if result.quality_gate.overall == "fail":
        preamble = _COVERAGE_PREAMBLE_GATE_FAIL
    elif result.rca is None or result.rca.status != "ok":
        preamble = _COVERAGE_PREAMBLE_NO_SCREEN
    else:
        preamble = _COVERAGE_PREAMBLE
    return {"preamble": preamble, "rows": rows}


def staging_profile(profile: str | None) -> dict[str, Any]:
    """Resolve config/staging.json to one named profile, for pdm_core.staging.

    The loading lives here rather than in staging.py because pdm_core is pure --
    no I/O anywhere in that package -- so the caller supplies the config, the
    same way resolved thresholds are passed in.

    A profile with no entry (`streaming`, the frozen NCD path, which has none by
    design) returns {} and classify_bearing_stage falls through to its
    documented defaults. Never raises: a missing or malformed staging.json
    degrades to defaults rather than taking down report rendering.
    """
    try:
        profiles = load_config("staging").get("profiles", {})
    except (FileNotFoundError, ValueError):
        return {}
    resolved = profiles.get(profile or "route")
    return resolved if isinstance(resolved, dict) else {}


def _history_provenance(
    result: AnalysisResult, case: Case | None, thresholds: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Where this trend's readings came from, when that changes what the report
    may claim about them — or None, which is every path but one.

    Session HIST-1. `history_source` is stamped on the Case by the webapp when
    the points arrived from the analyst's own browser
    (`webapp/assembly.py::attach_client_history`). We keep no copy of them and
    cannot check them against the files they came from, so the trend section
    says so rather than presenting them as ours.

    `needed` is `trend.min_days` — the point count `compute_trend` refuses below
    — read from the resolved profile rather than written into a template, per
    the config doctrine. None when the caller supplied no thresholds: the report
    then still names the provenance and simply does not invent a floor.

    Deliberately gated on `result.trend`: the note belongs to the trend section
    and has nowhere to render without one.
    """
    if case is None or case.history_source != "analyst_supplied" or result.trend is None:
        return None
    needed = None
    if thresholds:
        needed = (thresholds.get("trend") or {}).get("min_days")
    return {"n_points": len(case.history or []), "needed": needed}


def _recommendation_texts(result: AnalysisResult) -> list[str]:
    """The corrective recommendations as the templates render them (Session REC-1).

    One helper rather than two call sites, because the deterministic report and
    the drafted report's spliced block must carry the SAME sentences: the
    drafted path calls this too (`_drafted_evidence_block`), so the two can no
    longer drift apart the way the Recommendations heading itself did
    (outputs/PARTC_2026-09-11.md E2 item 5).

    The ISO zone is passed through because two fault ids word their timing from
    it; it is read as TEXT, never as a severity — see
    `pdm_core.recommendations._ZONE_TIMING`.
    """
    zone = result.iso.iso_zone if result.iso is not None else None
    return [r.text for r in corrective_recommendations_for_findings(result.findings, zone)]


def build_context(
    result: AnalysisResult,
    machine: MachineMeta,
    *,
    charts: ChartSet | None = None,
    case: Case | None = None,
    thresholds: dict[str, Any] | None = None,
    profile: str | None = None,
    comparison: ComparisonResult | None = None,
    locations: list[dict[str, Any]] | None = None,
    iso_assumed: bool | None = None,
    #: Session REPORT-4 (item 6). The caller's own report notes, read for ONE
    #: thing: the channels-measured line, which is the only place a declared
    #: measurement direction exists in this process. None everywhere a direction
    #: was never declared, which is every CLI and NCD reading — and that is what
    #: keeps those documents byte-identical.
    notes: Sequence[str] | None = None,
) -> dict:
    """Assemble the flattened, presentation-ready context the template renders.

    `charts` is the already-written figure manifest (see report/charts.py);
    None means no figures are embedded and the status badge falls back to its
    text form. `case`/`thresholds`/`profile` only feed the Analysis-parameters
    table with values the caller already has — everything they cannot supply
    is reported as "not recorded", never guessed.
    """
    axis_names = declared_axis_names(notes)
    diagnosis = []
    shaft = _shaft_hz(result)
    # Hoisted above the loop by Session REPORT-4 (item 7) so a finding can carry
    # its own damage stage. Its own comment, on where it used to sit, is below.
    stage = _stage_with_orders(classify_bearing_stage(result, staging_profile(profile)),
                               shaft, result)
    for f in result.findings:
        diagnosis.append(
            {
                "label": _fault_label(f.fault),
                "severity": _display_severity(f.severity),
                "confidence": f.confidence,
                # Session REPORT-4 (item 7). What the finding heading prints
                # beside the fault name, in place of `severity: info`.
                "tag": _finding_tag(f, stage),
                # Session REPORT-2 (item 4): the analyst-order lead paragraph,
                # with orders; None when the finding names no frequency.
                "lead": diagnosis_lead(f, result, case, first=(f is result.findings[0]),
                                       names=axis_names),
                # Session REPORT-2 (item 3): pdm_core's sentence, with the shaft
                # order beside every fault frequency it names. Same string in
                # both twins (this dict feeds both renderers).
                "reason": annotate_orders(f.reason, shaft),
                "factors": f.evidence.get("confidence_factors", []),
            }
        )
    differential = []
    if result.rca is not None:
        for d in result.rca.differential:
            differential.append(
                {"label": _fault_label(d.fault), "confidence": d.confidence, "adjudication": d.adjudication}
            )
    not_assessable = _not_assessable(result)
    # Session D: a clean reading gets its own explicit shape rather than being
    # rendered as a fault row labelled "No significant findings". RUN v5 found the
    # drafting model inventing a diagnosis on a healthy machine; part of the root
    # cause was that a no-findings result had no distinct, legitimate place to
    # land, so "write the Diagnosis section" read as "name a fault".
    no_findings = [f.fault for f in result.findings] == ["no_significant_findings"]
    # Session R1 — damage stage. Derived at render time from the SAME pure
    # function the drafted-report consistency check calls on the SAME
    # AnalysisResult, so the deterministic and drafted paths agree by
    # construction rather than by two goldens being kept in sync by hand.
    # Nothing is stored on the AnalysisResult, so no model or pipeline change
    # was needed. Session R2-BUILD reuses the same estimate to key the cause
    # lookup, so the stage a report PRINTS and the stage it LOOKS UP with can
    # never be two different stages.
    # Session R3-DIFF (item 2). Computed BEFORE the clean-phrasing decision below,
    # because it can veto it: a reading with no committed fault but unexplained
    # periodicity and no geometry to explain it with is not a clean bill.
    unmatched = unmatched_periodicity(result, case, thresholds)
    return {
        "no_findings": no_findings,
        "unmatched_periodicity": unmatched,
        # Session H — evidence layer.
        #
        # Session REPORTFIX-1: on a ROUTE this is the MACHINE's verdict, not
        # location 1's. Measured on FIXTURE-1's four-point set, before the fix:
        # the badge read "ISO ZONE A — GOOD · no fault signature identified"
        # directly above a health line reading "ISO Zone D — unacceptable ...
        # Bearing outer-race fault (BPFO) at Compressor DE". The document's own
        # subject is a healthy point, so a verdict taken from it is a clean bill
        # for a machine with a Zone D bearing fault on it -- the exact Part C
        # failure class Phase 7B found, in its most quotable form.
        "status_line": status_text(_machine_lead(result, case, locations)[0]),
        "charts": charts,
        "analysis_parameters": analysis_parameters(
            result, case=case, thresholds=thresholds, profile=profile, axis_names=axis_names
        ),
        # Session REPORT-2 (item 5) — which harmonic of each computed fault
        # frequency the measured range reaches. Rendered once by both twins;
        # nulled for the model's reference report (render_markdown below) and
        # spliced deterministically on the drafted path, the REPORT-NA way.
        "fmax_adequacy": fmax_adequacy_context(result, case),
        "checked_for": _checked_for(result) if no_findings else [],
        "machine": machine,
        "result": result,
        "iso": result.iso,
        # Session LIMITS-1b, fix round (close-out F-3). The same predicate the
        # health line, the zone label and the Machine Details row already read,
        # lifted into the context so the template sites branch on ONE
        # expression rather than five copies of `iso and iso.zone_basis ==
        # "custom"` -- which is the drift `_CUSTOM_BASIS_PHRASE` exists to
        # stop, one layer up. False whenever `result.iso` is None, so a
        # trend-section site needs no second None guard.
        "custom_basis": _custom_basis(result),
        "gate": result.quality_gate,
        "trend": result.trend,
        "insufficient": result.quality_gate.overall == "fail",
        # S12FIX: the fault screen crashed. The STATEMENT differs from a
        # gate-fail (the data was fine, the analysis was not), but every verdict
        # surface is suppressed identically — hence the second flag, which the
        # templates use for suppression so the two reasons cannot drift apart.
        "analysis_failed": _analysis_failed(result),
        "no_diagnosis": result.quality_gate.overall == "fail" or _analysis_failed(result),
        # Session A: when the reading's ISO zone is not_assessable, the report
        # renders a Severity & Coverage block and drops all Zone A–D language.
        "not_assessable": not_assessable,
        "not_assessable_reason": result.iso.not_assessable_reason if result.iso else None,
        "history_ran": result.trend is not None,
        # Session HIST-1 — set only when the trend's readings came from the
        # analyst's own browser. It is read off the CASE, which every renderer
        # already receives, so no signature in this module and no kwarg in
        # webapp/worker.py had to move for it.
        "history_provenance": _history_provenance(result, case, thresholds),
        # Session REPORTFIX-1 — the machine's, not location 1's. Measured on
        # FIXTURE-1's four points before the fix, this read "Synthetic
        # Compressor Train 01 is in ISO Zone A with no fault signature
        # identified. Overall vibration is within acceptable limits; continue
        # routine monitoring." on a machine carrying a Zone D outer-race fault:
        # a clean bill, naming the machine, in a section a reader trusts.
        "executive_summary": _executive_summary(*_machine_lead(result, case, locations),
                                               names=axis_names),
        # Session REPORT-3 (item 1) — page 1, the fault sheet. Present in
        # BOTH twins' context; the markdown renders it as an additive
        # leading section and the HTML page renders it as page 1 proper,
        # because pagination is a property of the PDF and the PDF is built
        # from the HTML.
        "fault_sheet": fault_sheet_context(result, machine, case, locations=locations,
                                           iso_assumed=iso_assumed, names=axis_names),
        # Session REPORT-3 (item 8) — the per-location Evidence roster, in the
        # order the route was walked. None on every single-location job, which
        # is every job until INTAKE-2 lands, and that is what keeps those
        # documents byte-identical apart from page 1.
        "locations": location_roster(locations),
        "dq_notes": _dq_notes(result),
        # Session REPORT-4 (item 6). A CALLABLE in the context, so the two
        # templates ask the same function the Python sites ask rather than each
        # re-deriving "what do we call this channel" from a letter.
        "axis_names": axis_names,
        "axis_phrase": (lambda axis: axis_phrase(axis, axis_names)),
        "diagnosis": diagnosis,
        "differential": differential,
        "damage_stage": stage,
        # Session R2-BUILD — the operator-approved cause layer, rendered from
        # the same macro on both paths. None when no bearing fault was
        # committed, which is what makes the whole section disappear rather
        # than appear empty.
        "causes": cause_section(result, stage=stage.stage if stage.determinable else "any"),
        "cause_heading": CAUSE_HEADING,
        # Session REPORT-NA — the coverage roster. Always present: the static
        # no-detector rows exist on every reading, so the section never
        # disappears, gate-fail included.
        "coverage": coverage_context(result, machine),
        # Session HIST-2 — the Before/After comparison, when this report is one
        # half of a compare-mode job. None on every ordinary single-file report,
        # so the section renders NOTHING and every existing document is
        # byte-identical. Arrives fully computed from pdm_core.compare; nothing
        # in the report layer derives a delta.
        "comparison": comparison,
        "evidence_rows": _evidence_rows(result),
        "measurements": result.recommended_measurements,
        # Session REC-1 — the corrective recommendations, each in the analyst's
        # three-part shape (action -> timing -> reassessment). Still a list of
        # STRINGS here, so `default_survey.md.j2` and `survey.html.j2` render it
        # exactly as they always have; the three named parts ride on the objects
        # for a later session that wants to lay them out.
        "recommendations": _recommendation_texts(result),
        "limitations": _limitations(result),
    }


def render_markdown(
    result: AnalysisResult,
    machine: MachineMeta,
    *,
    charts: ChartSet | None = None,
    case: Case | None = None,
    thresholds: dict[str, Any] | None = None,
    profile: str | None = None,
    include_causes: bool = True,
    #: Session REPORT-4 (item 6). The caller's report notes — read only for the
    #: channels-measured line. The webapp inserts its notes into report.md as
    #: TEXT after this render (`worker.markdown_with_notes`) and so does not
    #: pass them here yet; until it does this stays None and every markdown
    #: document is byte-identical. See the close-out's findings.
    notes: Sequence[str] | None = None,
    comparison: ComparisonResult | None = None,
    locations: list[dict[str, Any]] | None = None,
    iso_assumed: bool | None = None,
) -> str:
    """`include_causes=False` renders everything EXCEPT the cause hypotheses.

    Session R3-DIFF (item 3). One caller wants that: agent/loop.py, which hands
    this markdown to the drafting model as the reference report. The cause
    section is 90% of that prompt on a bearing-fault case (30,030 of 33,329
    characters, 11 causes with their full sourced mechanisms), and it does not
    belong there for two independent reasons:

      * The model cannot improve it. The section is spliced into the drafted
        report deterministically afterwards, from the same lookup, so whatever
        the model writes is discarded — it is asked to reproduce 156 lines whose
        only possible correct output is the input.
      * Trying makes the job fail. agent/consistency.py's check_cause_language
        requires every mechanism a draft writes up to appear VERBATIM. A model
        handed the text paraphrases it, the check refuses the draft, the retry
        paraphrases differently, and the job degrades to "Drafted narrative
        unavailable" — which is precisely what production showed on 3 of 3
        cause-bearing reports and 0 of 2 without.

    Every consistency contract stays armed. Removing the section from the PROMPT
    removes the temptation, not the check: a draft that writes cause language
    anyway is still refused, and tests/test_draft_failure_logging.py pins that.

    Session REPORT-NA: `include_causes=False` also drops the coverage roster,
    for the first of the same two reasons — the section is spliced into the
    drafted report deterministically afterwards, from the same builder, so the
    model is never shown it and never writes it. No consistency check pins
    coverage language, so keeping it out of the prompt is what keeps the
    rendered section deterministic rather than a paraphrase.

    Session HIST-2: `include_causes=False` drops the Before/after comparison for
    the SAME reason, and it is the load-bearing half of "the LLM never computes
    a delta". The model is never shown a computed delta, so it cannot narrate
    one that disagrees with the arithmetic — a stronger guarantee than checking
    a narration afterwards, and the reason no consistency contract had to be
    touched to get it.
    """
    context = build_context(
        result, machine, charts=charts, case=case, thresholds=thresholds, profile=profile,
        comparison=comparison, locations=locations, iso_assumed=iso_assumed, notes=notes,
    )
    template = _markdown_environment(context.get("axis_names")).get_template(
        "default_survey.md.j2")
    if not include_causes:
        context["causes"] = None
        context["coverage"] = None
        context["comparison"] = None
        # Session REPORT-2: the Fmax-adequacy block is spliced deterministically
        # on the drafted path (no consistency check pins its language), so the
        # model is never shown it and never writes one.
        context["fmax_adequacy"] = None
    return template.render(**context)


# ─────────────────────────────────────────────────────────────────────────
# The v2 HTML page — a SECOND renderer over the same build_context()
# ─────────────────────────────────────────────────────────────────────────
#
# Session V2-WIRE (WIRING.md slice W2). `render_markdown` is untouched, which is
# the load-bearing property of this whole change: `report.md` stays
# byte-identical, so the markdown goldens do not move and the agent keeps the
# exact reference report it has always been handed. The PDF an analyst
# downloads is rendered from the HTML instead (slice W4).

#: Gate statuses a display may summarise. Everything else must appear in the
#: rendered page's visible text with its NAME, its STATUS and its REASON IN
#: FULL, inside the data-quality section and above the roster. A non-PASS check
#: may never be hidden, summarised, folded into a count, or demoted. Computed
#: and asserted on every fixture by tests/test_report_html.py.
SUMMARISABLE: frozenset[str] = frozenset({"pass", "not_applicable"})

#: Which zone a machine would cross into next. There is no "next" past D.
_ZONE_NEXT: dict[str, str] = {"A": "B", "B": "C", "C": "D"}


def _machine_rows(result: AnalysisResult, machine: MachineMeta, *,
                  not_assessable: bool,
                  names: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """The Machine Details table, as rows.

    This reproduces `default_survey.md.j2`'s table — including every one of its
    own `—` defaults and its `Not assessable — …` phrasing, which STILL SHOWS
    THE MEASURED NUMBER, labelled as implausible, rather than hiding it. The two
    must not drift, so `tests/test_report_html.py` parses the rendered markdown
    table and asserts it equals this list, row for row.
    """
    iso = result.iso
    rows: list[tuple[str, str]] = [
        ("Machine", machine.name),
        ("Type", machine.type or "—"),
        ("Location", machine.location or "—"),
        # Session REPORT-4 (item 5): no "Sensor / MAC" row. `machine.mac` is an
        # INTERNAL key, not a sensor identity an analyst can act on — on the
        # upload lanes it is the alias with a lane prefix bolted on
        # (`adapters/uploads/tabular.py:96` -> "UPLOAD-SPEC-Compressor"), which
        # is this codebase talking to itself in a document somebody signs. The
        # machine is already named one row above; the sensor is described by the
        # Analysis Parameters table, which states what the acquisition actually
        # declared. Dropping the row takes the "UPLOAD-SPEC-" string out of both
        # documents, which is the only place it ever rendered.
        ("ISO group / support",
         f"{machine.iso_group if machine.iso_group is not None else '—'}"
         f" / {machine.iso_support if machine.iso_support is not None else '—'}"),
    ]
    if result.rca is not None and result.rca.rpm:
        rows.append(("Running speed", f"{result.rca.rpm} rpm"))
    if iso is not None and not not_assessable:
        rows.append(("Overall vibration",
                     f"{iso.severity_rms:.2f} mm/s ({axis_phrase(iso.dominant_axis, names)}), "
                     f"{_zone_label(result)} {iso.iso_zone}"))
    elif not_assessable:
        tail = ""
        if iso is not None and iso.severity_rms is not None:
            tail = f" (measured {iso.severity_rms:.2f} mm/s — outside the plausible range)"
        rows.append(("Overall vibration",
                     f"Not assessable — {iso.not_assessable_reason if iso else ''}{tail}"))
    if machine.bearing is not None:
        # Session GEOM-A: the bearing is named WITH the point it sits at when
        # the analyst gave one -- "6206 (at Motor DE)". A bearing number with no
        # location is a machine-wide claim, and on a machine with four of them
        # that is the wrong claim.
        bearing_label = machine.bearing.model or "(geometry supplied)"
        if machine.location:
            bearing_label = f"{bearing_label} (at {machine.location})"
        rows.append(("Bearing", bearing_label))
    return rows


def _gate_counts_line(checks: list[Any]) -> str:
    """`13 checks passed · 3 not applicable to this reading (a, b, c).`

    The not-applicable rows are NAMED, not merely counted, so "not applicable"
    can never be read as "not run".
    """
    passed = [c for c in checks if str(c.status).lower() == "pass"]
    na = [c for c in checks if str(c.status).lower() == "not_applicable"]
    parts = [f"{len(passed)} checks passed"]
    if na:
        parts.append(f"{len(na)} not applicable to this reading "
                     f"({', '.join(c.name for c in na)})")
    return " · ".join(parts) + "."


def _iso_gate_crossref(result: AnalysisResult) -> Markup:
    """`iso_zone_elevated`, repeated beside the severity statement.

    It is a severity fact filed under data quality, and an analyst reading the
    zone should not have to reach the data-quality section to learn the gate
    raised it. The NOT_APPLICABLE case carries a reason too (`ISO zone not
    assessable -- velocity not measured`) and is surfaced for the same reason.
    Purely additive: the data-quality section is unchanged by it.
    """
    check = next((c for c in result.quality_gate.checks if c.name == "iso_zone_elevated"), None)
    if check is None:
        return Markup("")
    status = str(check.status).lower()
    if status == "pass" or (status == "not_applicable" and not check.reason):
        return Markup("")
    reason = f" — {md_inline(check.reason)}" if check.reason else ""
    return Markup(
        f'<p class="xref {escape(status)}"><span class="mono">iso_zone_elevated: '
        f'{escape(status.upper().replace("_", " "))}</span>{reason}'
        ' <span class="muted">· data-quality gate, repeated here because it is a '
        'severity fact</span></p>'
    )


def _markdown_environment(axis_names: dict[str, str] | None = None) -> Environment:
    """The markdown environment — autoescape OFF (the output IS markdown).

    Session HIST-2 factored this out of its three call sites for one reason: the
    comparison macro needs `verdict_word`/`repair_word`, and three hand-built
    environments are three chances for one of them to be missing. Behaviour is
    otherwise byte-identical to what each site built inline.
    """
    return _with_report_globals(Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=(), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    ), axis_names)


def _with_report_globals(env: Environment, axis_names: dict[str, str] | None = None) -> Environment:
    """The verdict vocabularies, imported from the module that ASSIGNS them
    (`pdm_core.compare`) rather than restated here. Two renderers means two
    chances to word the same verdict differently; this is the one import that
    makes that impossible.

    Session REPORT-4 (item 6) adds `axis_phrase` here, as a GLOBAL, and the
    reason is LIMITS-1c F-4: every `{% import %}` of the two macro files in this
    directory is a plain import without `with context`, so a macro cannot see
    the caller's variables. Putting the callable in the render context reaches
    the top-level templates and silently resolves to Undefined inside
    `fault_sheet` and the roster macros — which is where most axis mentions
    actually live. A global is visible in both, and these environments are built
    fresh on every render (see the two builders above), so binding a per-render
    value to one cannot leak into the next.

    The default is the identity-ish fallback: with no declared directions it
    returns "y-axis", exactly what the templates printed before."""
    env.globals["verdict_word"] = verdict_word
    env.globals["repair_word"] = repair_word
    env.globals["axis_phrase"] = (lambda axis: axis_phrase(axis, axis_names))
    return env


def _html_environment(axis_names: dict[str, str] | None = None) -> Environment:
    """The HTML environment — autoescape ON.

    The markdown environment deliberately has it off; this one must have it on,
    because the report carries analyst-facing prose and reference locators and a
    `<` in either has to render as a `<`. Prose that carries the report's inline
    markdown goes through `md_inline`, which escapes first and exactly once.
    """
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(default=True, default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["md_inline"] = md_inline
    env.globals["zone_colour"] = _zone_colour
    return _with_report_globals(env, axis_names)


_ZONE_CSS_COLOUR = {"A": "var(--zA)", "B": "var(--zB)", "C": "var(--zC)", "D": "var(--zD)"}


def _zone_colour(zone: str) -> str:
    return _ZONE_CSS_COLOUR.get(str(zone), "var(--mut)")


def _status_colour(result: AnalysisResult) -> str:
    if result.quality_gate.overall == "fail":
        return "var(--mut)"
    if result.iso is None or result.iso.iso_zone == "not_assessable":
        return "var(--mut)"
    return _zone_colour(result.iso.iso_zone)


def render_html(
    result: AnalysisResult,
    machine: MachineMeta,
    *,
    charts: ChartSet | None = None,
    case: Case | None = None,
    thresholds: dict[str, Any] | None = None,
    profile: str | None = None,
    notes: list[str] | None = None,
    deletion_footer: str | None = None,
    comparison: ComparisonResult | None = None,
    #: The machine's measurement points, in roster order —
    #: `docs/contracts/machine_result.md` §3, plus the optional live `result` /
    #: `case` / `charts` a caller may hold (Session REPORTFIX-1 reconciled the
    #: shape REPORT-3 invented to the contract that landed after it). None is
    #: the single-location product, which renders exactly what it rendered
    #: before.
    locations: list[dict[str, Any]] | None = None,
    iso_assumed: bool | None = None,
) -> str:
    """The v2 survey page, rendered from the SAME context as `render_markdown`.

    `notes` are the webapp's own insertions (the units-conversion note, the
    "Channels measured" line, the coverage note) — first-class context rather
    than a regex inserted into rendered text. `deletion_footer` is the webapp's
    provenance line. Both are None on the CLI path, where neither applies.
    """
    context = _html_context(
        result, machine, charts=charts, case=case, thresholds=thresholds, profile=profile,
        notes=notes, deletion_footer=deletion_footer, comparison=comparison,
        locations=locations, iso_assumed=iso_assumed,
    )
    # `ctx` is the context itself, so a macro that needs many of its keys at
    # once (the severity/diagnosis card pair reads eight) can take one argument
    # instead of eight positional ones that a caller could silently misorder.
    context["ctx"] = context
    return _html_environment(context.get("axis_names")).get_template(
        "survey.html.j2").render(**context)


def _html_context(
    result: AnalysisResult,
    machine: MachineMeta,
    *,
    charts: ChartSet | None,
    case: Case | None,
    thresholds: dict[str, Any] | None,
    profile: str | None,
    notes: list[str] | None,
    deletion_footer: str | None,
    comparison: ComparisonResult | None = None,
    #: The machine's measurement points, in roster order —
    #: `docs/contracts/machine_result.md` §3, plus the optional live `result` /
    #: `case` / `charts` a caller may hold (Session REPORTFIX-1 reconciled the
    #: shape REPORT-3 invented to the contract that landed after it). None is
    #: the single-location product, which renders exactly what it rendered
    #: before.
    locations: list[dict[str, Any]] | None = None,
    iso_assumed: bool | None = None,
) -> dict[str, Any]:
    """`build_context()` plus the handful of presentation values only the HTML
    page needs. Shared by the deterministic and the drafted renderer, so the
    chrome on both is built from identical inputs."""
    context = build_context(
        result, machine, charts=charts, case=case, thresholds=thresholds, profile=profile,
        comparison=comparison, locations=locations, iso_assumed=iso_assumed, notes=notes,
    )
    zone = result.iso.iso_zone if result.iso is not None else None
    # Session REPORT-3: one computation, read by the severity strip's margin
    # sentence AND by the fault sheet's escalation trigger, so the two can never
    # name different boundaries.
    next_zone, next_boundary = _next_boundary(result)
    checks = list(result.quality_gate.checks)
    context.update(
        stylesheet=Markup(stylesheet()),
        status_colour=_status_colour(_machine_lead(result, case, locations)[0]),
        zone_word=zone_word(zone),
        next_zone=next_zone,
        # Session REPORT-3 — the one spectrum figure page 1 carries.
        sheet_figure=_sheet_figure(charts, result),
        next_boundary=next_boundary,
        iso_gate_crossref=_iso_gate_crossref(result),
        machine_rows=_machine_rows(result, machine,
                                   not_assessable=bool(context["not_assessable"]),
                                   names=context.get("axis_names")),
        gate_attention=[c for c in checks if str(c.status).lower() not in SUMMARISABLE],
        gate_counts=_gate_counts_line(checks),
        notes=list(notes or []),
        deletion_footer=deletion_footer,
        # Each of these renders NOTHING when the analysis did not produce what it
        # depicts — no arrays, no tolerance, no geometry, no figure. Empty is a
        # legitimate state, not a failure: a fixture with no measured series
        # draws nothing rather than inventing one to fill a container.
        evidence_graphics=Markup(
            evidence_insets(result, context["evidence_rows"], case=case, thresholds=thresholds)
            + bearing_map(result, case=case, thresholds=thresholds)
        ),
        stage_graphic=stage_ladder(context["damage_stage"]),
        cause_illustration=cause_illustration,
    )
    return context


def _narrative_html(markdown_text: str) -> Markup | None:
    """The model's markdown narrative, as HTML — or None if it cannot be.

    `markdown` is in the `[pdf]` extra alongside weasyprint, so in practice a
    machine that can render the v2 page can also convert this. When it cannot,
    the caller falls back to the markdown document rather than dropping the
    narrative, because the narrative IS the drafted report.

    `_blank_line_before_blocks` runs first for the same reason it runs on the
    markdown path: neither engine accepts a list that interrupts a paragraph
    without a blank line, and the model writes them that way.

    The narrative arrives already stripped of the document's own furniture — see
    `strip_document_furniture`.
    """
    try:
        import markdown as md_lib  # type: ignore
    except ImportError:
        return None
    return Markup(md_lib.markdown(
        _blank_line_before_blocks(markdown_text), extensions=["tables"]
    ))


#: The narrative's own copies of blocks the DOCUMENT owns.
#:
#: The model is told to open with the report title (`check_title_line` requires
#: it) and, until Session TFIX, was told to close with the DRAFT footer. Both
#: were right when the narrative WAS the document. They are wrong now that it is
#: set inside one: the masthead carries the title and the page carries the
#: footer, so the draft's copies land in the middle of the report.
_FURNITURE_HEADING = re.compile(r"^#{1,6}\s*review\s*&(?:amp;)?\s*approval\b", re.I)
_FURNITURE_FOOTER = re.compile(r"^\s*(?:---\s*)?DRAFT\s*[—-]{1,2}\s*prepared by automated analysis", re.I)


def strip_document_furniture(markdown_text: str) -> str:
    """The model's narrative, with the document's own furniture removed.

    Two edits, both DISPLAY-ONLY — the checks run on the draft text, not on
    this, so nothing they guarantee is weakened:

      * the leading TITLE line, because the masthead already carries it. It was
        printing the document's title twice on page one, the second time in the
        middle of the page;
      * everything from the draft's own `## Review & Approval` heading (or its
        DRAFT footer, whichever comes first) to the end. That block put a
        SIGNATURE and a footer mid-document, with the damage stage, the figures
        and eleven cause sections rendered after them — a report that appears to
        end and then carries on for sixteen pages.

    Only a TRAILING block is removed, never a section from the middle: the
    narrative is the model's and this is not licence to edit it. The
    deterministic signature and footer render at the end of the page, so the
    reader loses nothing.
    """
    lines = markdown_text.strip().split("\n")
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    for i, line in enumerate(lines):
        if _FURNITURE_HEADING.match(line) or _FURNITURE_FOOTER.match(line):
            lines = lines[:i]
            break
    # a `---` rule left dangling by the cut is furniture too
    while lines and lines[-1].strip() in ("", "---"):
        lines.pop()
    return "\n".join(lines).strip()


def render_drafted_html(
    narrative_markdown: str,
    result: AnalysisResult,
    machine: MachineMeta,
    *,
    charts: ChartSet | None = None,
    case: Case | None = None,
    thresholds: dict[str, Any] | None = None,
    profile: str | None = None,
    notes: list[str] | None = None,
    deletion_footer: str | None = None,
    comparison: ComparisonResult | None = None,
    #: Session REPORTFIX-1 — the drafted page takes the roster too. REPORT-3
    #: threaded `locations` through the four deterministic renderers and not
    #: through this one, so a multi-location job that reached the model rendered
    #: a page 1 with no roster on it: the machine's worst point unnamed on
    #: exactly the jobs that got the fuller document.
    locations: list[dict[str, Any]] | None = None,
    iso_assumed: bool | None = None,
) -> str | None:
    """The v2 page with the model's narrative in place of the deterministic
    prose. None when the narrative cannot be converted — see `_narrative_html`.

    Session V2-WIRE (WIRING.md slice W5). The evidence layer is rendered from
    the SAME AnalysisResult, through the SAME macros, as the deterministic page,
    and the sections the draft already wrote are suppressed by the SAME flags
    the markdown appendix has used since Session H — so the two drafted
    documents carry the same blocks by construction rather than by two
    templates being kept in step by hand.
    """
    # Session REPORT-3 (item 4) — PARTC F-10. The analyst-order lead, spliced
    # into the model's Diagnosis section before anything else reads the
    # narrative, so the want_* probes below see the document the page will
    # actually render.
    narrative_markdown = splice_diagnosis_lead(narrative_markdown, result, case)
    body = strip_document_furniture(narrative_markdown)
    narrative = _narrative_html(body)
    if narrative is None:
        return None
    context = _html_context(
        result, machine, charts=charts, case=case, thresholds=thresholds, profile=profile,
        notes=notes, deletion_footer=deletion_footer, comparison=comparison,
        locations=locations, iso_assumed=iso_assumed,
    )
    # The want_* flags are computed from the STRIPPED narrative, not the raw
    # draft. Reading the raw one would let a signature block that has just been
    # removed go on suppressing the deterministic signature — and the report
    # would end with no signature at all, which is the one block it exists to
    # carry.
    lowered = body.lower()
    stage = _stage_with_orders(classify_bearing_stage(result, staging_profile(profile)),
                               _shaft_hz(result), result)
    context.update(
        narrative=narrative,
        want_parameters="## analysis parameters" not in lowered,
        want_signature="reviewed and approved by" not in lowered,
        want_causes=CAUSE_HEADING.lower() not in lowered,
        # New in Session TFIX. `want_parameters`, `want_causes` and
        # `want_signature` existed; stage was missed, so the section rendered
        # twice on every drafted report where the model wrote its own — once as
        # the model's prose and once deterministically, under the same heading.
        want_stage="## damage stage estimate" not in lowered,
        # Session REC-1. `recommendations` is already in the context — it comes
        # from build_context, shared with the deterministic page — but nothing
        # rendered it here, so a draft that wrote no Recommendations heading
        # produced a page with no Recommendations section at all. The probe is
        # the heading, not the word: "## recommended follow-up measurements"
        # does not contain "## recommendations".
        want_recommendations="## recommendations" not in lowered,
        # Session REPORT-3 (item 4) — the twin of drafted_evidence.md.j2's
        # guard, and the same byte-for-byte probe: the order COLUMNS, not the
        # "Evidence" heading a model writes routinely. `evidence_rows` is
        # already in the context (it comes from build_context, shared with the
        # deterministic page); only the flag is new.
        want_evidence_table=not ("computed (×)" in lowered and "observed (×)" in lowered),
        damage_stage=stage,
        causes=cause_section(result, stage=stage.stage if stage.determinable else "any"),
    )
    context["ctx"] = context
    return _html_environment(context.get("axis_names")).get_template(
        "drafted.html.j2").render(**context)


# ─────────────────────────────────────────────────────────────────────────
# The four public document entry points below are each `@serializes_render`.
#
# That decorator is where the ONE render lock is actually taken (Session
# RENDER-SERIAL, PROD_READINESS §7 F-1): a document holds it from its first
# figure to its last byte of PDF, so no two worker threads are ever inside
# matplotlib or weasyprint — or one inside each — at the same time. Both
# libraries segfault under concurrency, and a native segfault kills uvicorn
# rather than the job.
#
# It has to be at all four, and it has to be non-reentrant to be honest about
# what it costs: `render_report` calls `render_charts` AND `render_pdf`, while
# `render_pdf` and `render_drafted_pdf` are ALSO called directly by
# `webapp/worker.py`. The thread-local guard in `render_lock` turns the inner
# acquisitions into pass-throughs, so the whole call graph enters the lock
# exactly once per document — pinned as a number in tests/test_render_serial.py.
# ─────────────────────────────────────────────────────────────────────────


@serializes_render
def render_report(
    result: AnalysisResult,
    machine: MachineMeta,
    out_dir: Path,
    *,
    pdf: bool = False,
    case: Case | None = None,
    thresholds: dict[str, Any] | None = None,
    profile: str | None = None,
    figures: bool = True,
    notes: list[str] | None = None,
    deletion_footer: str | None = None,
    comparison: ComparisonResult | None = None,
    #: Session REPORT-3 (item 8) — the machine's measurement points, in roster
    #: order. See `build_context`. None is every job today.
    locations: list[dict[str, Any]] | None = None,
    iso_assumed: bool | None = None,
) -> dict[str, Any]:
    """Write report.md, analysis.json and (if `pdf`) report.pdf into out_dir.

    TWO documents, ONE context. `report.md` is rendered by `render_markdown`
    and is unchanged by this session; the PDF is rendered from the v2 HTML page
    (`render_html`), which reads the SAME `build_context()`. That is what lets
    the analyst's document be a designed page while the agent's reference
    report stays exactly the markdown it has always been.

    `notes` and `deletion_footer` are the webapp's own insertions and are
    first-class context on the HTML side — the markdown side keeps its existing
    placement (see SESSION_V2WIRE.md). Both are None on the CLI path, where
    neither applies.

    Returns the written paths plus, under `"charts"`, the figure manifest — so
    a caller that has to re-render the PDF later does not have to redraw every
    figure to do it.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    chart_set = (
        render_charts(result, machine, out_dir, case=case, thresholds=thresholds,
                      fault_labels=FAULT_LABELS)
        if figures
        else None
    )
    markdown_text = render_markdown(
        result, machine, charts=chart_set, case=case, thresholds=thresholds, profile=profile,
        comparison=comparison, locations=locations, iso_assumed=iso_assumed,
    )
    md_path = out_dir / "report.md"
    md_path.write_text(markdown_text)
    written: dict[str, Any] = {
        "markdown": md_path,
        "analysis_json": write_analysis_json(result, out_dir),
        "charts": chart_set,
    }

    if pdf:
        pdf_path = render_pdf(
            result, machine, out_dir, charts=chart_set, case=case, thresholds=thresholds,
            profile=profile, notes=notes, deletion_footer=deletion_footer,
            markdown_text=markdown_text, comparison=comparison, locations=locations,
            iso_assumed=iso_assumed,
        )
        if pdf_path is not None:
            written["pdf"] = pdf_path

    return written


@serializes_render
def render_pdf(
    result: AnalysisResult,
    machine: MachineMeta,
    out_dir: Path,
    *,
    charts: ChartSet | None = None,
    case: Case | None = None,
    thresholds: dict[str, Any] | None = None,
    profile: str | None = None,
    notes: list[str] | None = None,
    deletion_footer: str | None = None,
    markdown_text: str | None = None,
    comparison: ComparisonResult | None = None,
    #: Session REPORT-3 (item 8) — see `build_context`. None is every job today.
    locations: list[dict[str, Any]] | None = None,
    iso_assumed: bool | None = None,
) -> Path | None:
    """Render the v2 page for this result and write it as report.pdf.

    **Signature change, Session V2-WIRE.** This used to take already-rendered
    MARKDOWN TEXT, because the PDF was a conversion of the markdown report. The
    PDF is now rendered from the analysis itself, so the caller passes the
    analysis rather than a string it has been doing text surgery on. The
    webapp's notes arrive here as data, in `notes`.

    `charts` is the manifest `render_report` already produced. Pass it when you
    have it: without it every figure is redrawn, which is the slowest part of a
    job. Redrawing is still the right DEFAULT, though — a caller that forgot
    would otherwise get a silently figureless report, including one with no
    status badge.
    """
    if charts is None:
        charts = render_charts(result, machine, Path(out_dir), case=case,
                               thresholds=thresholds, fault_labels=FAULT_LABELS)
    html = render_html(
        result, machine, charts=charts, case=case, thresholds=thresholds, profile=profile,
        notes=notes, deletion_footer=deletion_footer, comparison=comparison,
        locations=locations, iso_assumed=iso_assumed,
    )
    pdf_path = _pdf_from_html(html, Path(out_dir))
    if pdf_path is not None:
        return pdf_path

    # No weasyprint here. Fall back to the MARKDOWN document through
    # pandoc+tectonic — the document this product shipped before v2, and the one
    # the suite exercises on any machine without weasyprint. That is a
    # deliberate choice over converting the v2 HTML: `pandoc --from html` was
    # tried and fails on this document outright, and a fallback should be a
    # thing we already validate, not a second novel artifact. The notes reach it
    # the way they always have — the caller passes the text it already wrote.
    text = markdown_text if markdown_text is not None else render_markdown(
        result, machine, charts=charts, case=case, thresholds=thresholds, profile=profile,
        comparison=comparison, locations=locations, iso_assumed=iso_assumed,
    )
    return _pdf_from_markdown(text, Path(out_dir))


# Longest first: a draft that reproduces the template's footer writes a rule
# above it, and the evidence appendix belongs ABOVE that rule, not between it
# and the footer line.
_DRAFT_FOOTER_ANCHORS = ("\n---\nDRAFT", "\nDRAFT")


def _drafted_evidence_block(
    result: AnalysisResult,
    *,
    charts: ChartSet | None,
    case: Case | None,
    thresholds: dict[str, Any] | None,
    profile: str | None,
    draft_text: str,
    machine: MachineMeta | None = None,
) -> str:
    """The deterministic evidence appendix for an agent-drafted narrative.

    The model drafts prose; it never draws a figure, never fills in an
    acquisition parameter, and never signs anything. Those blocks are rendered
    here from the AnalysisResult and spliced in, so a drafted report carries
    the same evidence as the deterministic one. Blocks the draft already wrote
    (it is shown the deterministic report as its reference structure) are
    suppressed rather than duplicated.
    """
    template = _markdown_environment().get_template("drafted_evidence.md.j2")
    lowered = draft_text.lower()
    stage = _stage_with_orders(classify_bearing_stage(result, staging_profile(profile)),
                               _shaft_hz(result), result)
    return template.render(
        charts=charts,
        # No `axis_names` here, and it is not an oversight. This appendix renders
        # from hand-built kwargs rather than from `build_context()` (LIMITS-1c §5
        # records the same shape), and it is the MARKDOWN drafted lane, which the
        # webapp never hands its notes to — so there is no declared direction in
        # scope to pass. The row is simply absent, exactly as on every reading
        # that declared no direction, and the letters stay. The close-out's
        # findings name the one-line webapp change that lights both up together.
        analysis_parameters=analysis_parameters(
            result, case=case, thresholds=thresholds, profile=profile,
        ),
        # Session REPORT-2: no want_* flag -- the reference report the model is
        # handed omits the block (render_markdown include_causes=False), so there
        # is no draft copy to suppress. Spliced deterministically on every drafted
        # report, exactly as the coverage roster is.
        fmax_adequacy=fmax_adequacy_context(result, case),
        want_parameters="## analysis parameters" not in lowered,
        want_signature="reviewed and approved by" not in lowered,
        # Session HIST-1. The one place a context value has to be derived again
        # rather than read from build_context(): this appendix renders its
        # template directly. The `case` it needs is already in hand, and the
        # drafted HTML page needs nothing at all -- render_drafted_html shares
        # _html_context, and so build_context, with the deterministic page.
        history_provenance=_history_provenance(result, case, thresholds),
        # Session LIMITS-1c, item 1. Same reason as `history_provenance` above:
        # this appendix renders its template directly rather than through
        # build_context(), so the one flag its provenance note branches on has
        # to be derived again here. The drafted HTML page needs nothing --
        # render_drafted_html shares _html_context, and so build_context.
        custom_basis=_custom_basis(result),
        # Session R1: same pure function, same AnalysisResult as the
        # deterministic path, so the stage block is byte-identical there. The
        # model never authors stage language -- it is rendered here and
        # separately asserted by agent.consistency.check_stage_language.
        damage_stage=stage,
        # Session R2-BUILD: same again for the cause section. The model never
        # authors it; it is rendered from the same lookup, through the same
        # macro, and separately asserted by agent.consistency.check_cause_language.
        # `want_causes` suppresses it only when the draft already reproduced the
        # heading, exactly as want_parameters/want_signature do -- and in that
        # case the consistency check has already proved the draft's own version
        # is a subset of this lookup.
        causes=cause_section(result, stage=stage.stage if stage.determinable else "any"),
        cause_heading=CAUSE_HEADING,
        want_causes=CAUSE_HEADING.lower() not in lowered,
        # Session REPORT-NA: the coverage roster is spliced deterministically,
        # never model-written — the reference report the model is handed omits
        # it (render_markdown include_causes=False), so no want_* flag exists
        # for it: there is no draft copy to suppress in favour of.
        coverage=coverage_context(result, machine) if machine is not None else None,
        # Session TFIX: the stage had no suppression flag, so a draft that wrote
        # its own "## Damage Stage Estimate" got a second one appended under the
        # same heading.
        want_stage="## damage stage estimate" not in lowered,
        # Session REC-1 — the corrective recommendations, from the same helper
        # the deterministic report's context uses, so the two documents carry
        # the same sentences by construction. Suppressed when the draft wrote
        # its own heading, exactly as want_stage is.
        recommendations=_recommendation_texts(result),
        want_recommendations="## recommendations" not in lowered,
        # Session REPORT-3 (item 4) — the other half of PARTC F-10. The drafted
        # report's evidence table was the model's own and had no Computed (×) /
        # Observed (×) columns, so the orders REPORT-2 put beside every
        # frequency reached the drafted document only through deterministic
        # splices elsewhere. This is the deterministic table, from the same
        # `_evidence_rows` and the same macro the deterministic report renders,
        # so the two are byte-identical.
        evidence_rows=_evidence_rows(result),
        # BYTE-FOR-BYTE, not by heading: "## Evidence" is a heading the model
        # writes routinely, so probing for it would suppress this table on
        # exactly the drafts that need it (which is what PARTC read). The
        # question is whether the ORDER COLUMNS are already there.
        want_evidence_table=not ("computed (×)" in lowered and "observed (×)" in lowered),
    ).rstrip()


#: Session REPORT-3 (item 4) — the drafted narrative's Diagnosis heading, at any
#: level the model might have written it at.
_DIAGNOSIS_HEADING_RE = re.compile(r"(?im)^(#{1,4}[ \t]+Diagnosis[ \t]*)$")


def splice_diagnosis_lead(narrative: str, result: AnalysisResult,
                          case: Case | None = None) -> str:
    """Put the analyst-order lead into a model-written narrative — PARTC F-10.

    REPORT-2 built `diagnosis_lead` so the Diagnosis section opens the way an
    analyst writes one: zone and level, then the dominant frequency with its
    order and harmonic, then the bearing it matches, then the conclusion. Part A
    then read a re-minted drafted sample and found the drafted Diagnosis section
    running the OTHER WAY — committed call first, zone last — and carrying no
    order at all. The model had been handed the lead in its reference report and
    simply had not transcribed it, and nothing required it to.

    So it is spliced deterministically, after the draft. Two departures from the
    six existing `want_*` guards, both deliberate and both the brief's:

      * the probe is **byte-for-byte**, not the heading. Every existing guard
        asks "did the draft write `## Recommendations`?" — which is right for a
        whole section the model may own. This is a PARAGRAPH inside a section
        the model always writes, so the only honest question is whether this
        exact sentence is already there.
      * with no Diagnosis heading to insert under, the lead gets its own
        section rather than being dropped. A drafted report with no analyst-
        order lead is the defect; silently skipping it on an unusual draft
        would leave the defect for exactly the drafts nobody looked at.

    Called from BOTH `write_drafted_report` (for report.md) and
    `render_drafted_html` (for the PDF). It has to be both: the PDF is rendered
    from the un-spliced narrative — `write_drafted_report` passes
    `narrative_only` on purpose — so a splice in only one of them would put the
    lead in one document and not the other.
    """
    committed = [f for f in result.findings if f.fault != "no_significant_findings"]
    if not committed:
        return narrative
    lead = diagnosis_lead(committed[0], result, case, first=True)
    if not lead or lead in narrative:
        return narrative
    match = _DIAGNOSIS_HEADING_RE.search(narrative)
    if match is not None:
        end = match.end()
        return narrative[:end] + "\n\n" + lead + narrative[end:]
    # No Diagnosis heading to insert under, so the lead gets its own section --
    # BEFORE the trailing DRAFT footer, the same anchors `splice_comparison_
    # markdown` and `_splice_drafted_evidence` use. Appending past the footer
    # ends the document on the lead, which is what the first cut of this did
    # and what tests/test_report_charts.py::TestDraftedSplice caught.
    section = "## Diagnosis\n\n" + lead + "\n"
    body = narrative.rstrip()
    for anchor in _DRAFT_FOOTER_ANCHORS:
        idx = body.rfind(anchor)
        if idx != -1:
            return body[:idx] + "\n\n" + section + body[idx:] + "\n"
    return body + "\n\n" + section


#: The comparison section's own heading, in both renderers' words. Used to make
#: the markdown splice below idempotent.
COMPARISON_HEADING = "## Before / after comparison"


def comparison_markdown(comparison: ComparisonResult | None) -> str:
    """The Before/after comparison as a standalone markdown block, from the SAME
    macro `default_survey.md.j2` calls — so the section a compare-mode report
    carries is byte-identical whichever path wrote the document."""
    if comparison is None:
        return ""
    template = _markdown_environment().from_string(
        '{% import "_evidence.md.j2" as ev %}{{ ev.comparison_section(comparison) }}'
    )
    return template.render(comparison=comparison).strip()


def splice_comparison_markdown(text: str, comparison: ComparisonResult | None) -> str:
    """Insert the comparison into an ALREADY-WRITTEN report markdown, above the
    trailing DRAFT footer.

    Session HIST-2, and it exists for exactly one path: a DRAFTED webapp job's
    `report.md` is written inside `agent/loop.py`, which this session does not
    touch (`agent/**` is out of scope for HIST-2). The webapp rewrites that file
    anyway to insert its own notes, so the comparison joins it there.

    Idempotent by heading, the `want_*` pattern: a document that already carries
    the section — a deterministic report, where `render_markdown` rendered it
    from the same macro — is returned unchanged rather than given a second copy.
    That is what lets the caller apply this on every path without knowing which
    one produced the text.
    """
    block = comparison_markdown(comparison)
    if not block or COMPARISON_HEADING.lower() in text.lower():
        return text
    body = text.rstrip()
    for footer in _DRAFT_FOOTER_ANCHORS:
        idx = body.rfind(footer)
        if idx != -1:
            return body[:idx] + "\n\n" + block + "\n" + body[idx:] + "\n"
    return body + "\n\n" + block + "\n"


def _splice_drafted_evidence(draft_text: str, badge: str, block: str) -> str:
    """Badge directly under the title line; the evidence appendix immediately
    before the trailing DRAFT footer (or at the end when the draft has none)."""
    text = draft_text.rstrip()
    if badge:
        lines = text.split("\n")
        # check_title_line has already proven line 0 is the report title.
        text = "\n".join([lines[0], "", badge.strip(), *lines[1:]])
    if not block:
        return text + "\n"
    for anchor in _DRAFT_FOOTER_ANCHORS:
        idx = text.rfind(anchor)
        if idx != -1:
            return text[:idx] + "\n\n" + block + "\n" + text[idx:] + "\n"
    return text + "\n\n" + block + "\n"


@serializes_render
def write_drafted_report(
    markdown_text: str,
    result: AnalysisResult,
    out_dir: Path,
    *,
    pdf: bool = False,
    machine: MachineMeta | None = None,
    case: Case | None = None,
    thresholds: dict[str, Any] | None = None,
    profile: str | None = None,
    figures: bool = True,
) -> dict[str, Path]:
    """Write an agent-drafted report.md (+ report.pdf, + analysis.json) into
    out_dir. Mirrors render_report's file-writing contract exactly, but takes
    already-rendered narrative text instead of running the Jinja2 template --
    the Phase 4 agent path's markdown comes from the model's drafting call,
    not the deterministic template.

    Session H: the deterministic evidence layer (status badge, figures,
    analysis parameters, signature block) is rendered here and spliced into the
    narrative. It needs `machine` to render the figures; without it the drafted
    report is written exactly as it was before Session H.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    narrative_only = markdown_text
    chart_set = None
    if figures and machine is not None:
        # Session REPORT-3 (item 4) — the same splice the drafted PAGE does,
        # applied to the document this function writes. Inside this branch, and
        # not above it, because Session H's contract is that a call with no
        # machine writes the draft UNTOUCHED, and the analyst-order lead is part
        # of the deterministic evidence layer like every other block here.
        #
        # Both `markdown_text` and `narrative_only`: the second is what the PDF
        # renderer is handed, so splicing one would put the lead in report.md
        # and leave it out of the PDF.
        markdown_text = splice_diagnosis_lead(markdown_text, result, case)
        narrative_only = markdown_text
        chart_set = render_charts(
            result, machine, out_dir, case=case, thresholds=thresholds, fault_labels=FAULT_LABELS
        )
        block = _drafted_evidence_block(
            result, charts=chart_set, case=case, thresholds=thresholds,
            profile=profile, draft_text=markdown_text, machine=machine,
        )
        badge = _render_badge_markdown(chart_set, result)
        markdown_text = _splice_drafted_evidence(markdown_text, badge, block)

    md_path = out_dir / "report.md"
    md_path.write_text(markdown_text)
    written: dict[str, Any] = {
        "markdown": md_path,
        "analysis_json": write_analysis_json(result, out_dir),
        "charts": chart_set,
        # The narrative WITHOUT the spliced appendix — the drafted PDF renders
        # that appendix itself, from the same result, through the same macros.
        "narrative": narrative_only,
    }

    if pdf:
        pdf_path = render_drafted_pdf(
            narrative_only, result, out_dir, machine=machine, charts=chart_set, case=case,
            thresholds=thresholds, profile=profile, markdown_text=markdown_text,
        )
        if pdf_path is not None:
            written["pdf"] = pdf_path

    return written


@serializes_render
def render_drafted_pdf(
    narrative_markdown: str,
    result: AnalysisResult,
    out_dir: Path,
    *,
    machine: MachineMeta | None = None,
    charts: ChartSet | None = None,
    case: Case | None = None,
    thresholds: dict[str, Any] | None = None,
    profile: str | None = None,
    notes: list[str] | None = None,
    deletion_footer: str | None = None,
    markdown_text: str | None = None,
    comparison: ComparisonResult | None = None,
    locations: list[dict[str, Any]] | None = None,
    iso_assumed: bool | None = None,
) -> Path | None:
    """Render an agent-drafted report to PDF: the v2 page, model prose inside.

    Falls back to the markdown document — the one this product shipped before
    v2 — when the v2 page cannot be produced here, for the same reason and by
    the same rule as `render_pdf`. `markdown_text` is the already-spliced
    drafted markdown, so the fallback issues the document the caller wrote
    rather than re-deriving one.
    """
    out_dir = Path(out_dir)
    if machine is not None:
        html = render_drafted_html(
            narrative_markdown, result, machine, charts=charts, case=case,
            thresholds=thresholds, profile=profile, notes=notes,
            deletion_footer=deletion_footer, comparison=comparison,
            locations=locations, iso_assumed=iso_assumed,
        )
        if html is not None:
            pdf_path = _pdf_from_html(html, out_dir)
            if pdf_path is not None:
                return pdf_path
    if markdown_text is None:
        return None
    return _pdf_from_markdown(markdown_text, out_dir)


def _render_badge_markdown(charts: ChartSet | None, result: AnalysisResult) -> str:
    if charts is not None and charts.status is not None:
        return f"![{charts.status.alt}]({charts.status.rel_path})"
    return f"**STATUS — {status_text(result)}**"


def write_analysis_json(result: AnalysisResult, out_dir: Path) -> Path:
    path = Path(out_dir) / "analysis.json"
    path.write_text(result.model_dump_json(indent=2))
    return path


# F1 (design/report_v2_proto/COMPARISON.md): neither PDF engine accepts a list
# or an ATX heading that interrupts a paragraph without a blank line — Python-
# Markdown does not implement that CommonMark allowance and pandoc's `markdown`
# dialect keeps `blank_before_header` on. The templates emit both, so 66 of 69
# bullets on the bearing report fold into run-on prose and `## Damage Stage
# Estimate` prints as literal text. Normalise the copy handed to the renderer;
# `report.md` itself is untouched and stays byte-identical.
_MD_LIST_START = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_MD_HEADING = re.compile(r"^#{1,6}\s")


def _blank_line_before_blocks(markdown_text: str) -> str:
    out: list[str] = []
    in_fence = in_list = False
    for line in markdown_text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            is_list, is_head = bool(_MD_LIST_START.match(line)), bool(_MD_HEADING.match(line))
            if out and out[-1].strip() and (is_head or (is_list and not in_list)):
                out.append("")
            in_list = False if is_head else (is_list or (in_list and bool(line.strip())))
        out.append(line)
    return "\n".join(out)


class FigurelessPdfRefused(RuntimeError):
    """The pandoc/tectonic fallback would have produced a PDF with no figures.

    Session REPORT-3 (item 10), closing CHARTS-2 F-6. Since that session every
    figure is an SVG, and `pandoc --pdf-engine=tectonic` cannot place one: it
    drops the image and renders the document around it. The old code PRINTED a
    notice and returned the path, so the caller got back a report.pdf that
    looked complete and had no evidence in it — the worst of the three
    outcomes, because a missing PDF is noticed and a figure-less one is not.

    **Nothing in production takes this path.** `deploy/setup_server.sh:49-50`
    installs weasyprint's native deps and says in its own comment that
    pandoc/tectonic is "the documented fallback, not installed here"; this
    laptop has both, which is why the fallback is reachable to test at all.

    No new `ERROR_TAXONOMY` row (wire law #5): `webapp/worker.py` has no
    try/except around its render calls, so this reaches `app.py::_run_guarded`
    and becomes the already-ratified `internal_error` — the same mapping
    Session RENDER-PROC made for `RenderChildCrashed`, for the same reason.

    It NARROWS the old behaviour rather than replacing it: a document with no
    figures to lose still renders through pandoc exactly as it did.
    """


#: Any image reference in the markdown a pandoc render would silently drop.
_SVG_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\.svg\)")


def _pandoc_to_pdf(source: Path, out_dir: Path, pdf_path: Path, *, fmt: str) -> Path | None:
    """The fallback engine: pandoc + tectonic, from `fmt`."""
    import shutil
    import subprocess

    if shutil.which("pandoc") is None:
        print("PDF requested but neither weasyprint nor pandoc is available — wrote markdown only.")
        return None
    try:
        subprocess.run(
            [
                "pandoc", str(source), "--from", fmt, "-o", str(pdf_path),
                "--pdf-engine=tectonic",
                # pandoc resolves image paths against the CWD, not the source
                # file -- point it at the report directory so charts/*.png resolve.
                f"--resource-path={out_dir}",
                "-V", "geometry:margin=0.75in",
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        return pdf_path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"PDF requested but pandoc rendering failed ({exc}) — wrote markdown only.")
        return None
    finally:
        source.unlink(missing_ok=True)


def _pdf_from_html(html: str, out_dir: Path) -> Path | None:
    """Render the v2 page to PDF with weasyprint, or return None.

    Session V2-WIRE (WIRING.md slice W4). weasyprint applies `report.css`,
    including its `@media print` block, so the PDF is the document the design
    describes. This is what production runs.

    There is deliberately NO pandoc fallback FROM HTML. One was written and
    measured: `pandoc --from html | tectonic` fails outright on this document
    (tectonic exit 43), and even had it succeeded it would have dropped the CSS
    and every inline SVG, producing a novel, untested artifact. The caller falls
    back to the MARKDOWN document instead — see `render_pdf`.
    """
    out_dir = Path(out_dir)
    pdf_path = out_dir / "report.pdf"
    # Session RENDER-PROC: weasyprint runs in a CHILD process. It faulted here
    # twice -- DB-1's SIGSEGV inside `write_pdf` during a GC pass, and EMAIL-1
    # F-6's SIGBUS inside `FontConfiguration.__init__` while another thread was
    # collecting -- and a native fault kills the interpreter, so on the droplet
    # uvicorn and every in-flight upload go down together. F-6 also settles why
    # the render lock could not close it: that stack has `render_lock.py:105`
    # IN it. The lock was held. A lock serializes renders, not the GC of the
    # cairo/pango objects a render leaves behind.
    #
    # `engine_absent` (no weasyprint, or its native libs unreachable) still
    # returns None, which is what the caller's markdown fallback reads. A child
    # CRASH raises instead -- see `render_proc.RenderChildCrashed`.
    try:
        reply = render_proc.run_child("pdf", {"html": html, "out_dir": str(out_dir)})
    finally:
        # A child killed mid-write leaves its scratch file behind, and a
        # finished job directory is pinned to hold nothing but the report.
        (out_dir / "report.pdf.part").unlink(missing_ok=True)
    if reply["status"] != "ok":
        return None
    return pdf_path


def _pdf_from_markdown(markdown_text: str, out_dir: Path) -> Path | None:
    """The pre-v2 chain, markdown in.

    Still used by the agent-drafted path, whose document is model-written
    markdown rather than a render of `build_context()`. See SESSION_V2WIRE.md
    for why that path was left on this renderer pending an operator decision.
    """
    out_dir = Path(out_dir)
    pdf_path = out_dir / "report.pdf"
    markdown_text = _blank_line_before_blocks(markdown_text)

    # markdown -> HTML happens HERE, in the parent: it is pure Python (the same
    # `markdown` package `_narrative_html` already imports in this process) and
    # keeping it here means there is exactly ONE child op for PDFs, taking a
    # finished HTML string, rather than two that both know this wrapper.
    html_doc = None
    try:
        import markdown as md_lib  # type: ignore

        html_body = md_lib.markdown(markdown_text, extensions=["tables"])
        html_doc = (
            "<html><head><meta charset='utf-8'><style>" + _PDF_CSS + "</style></head>"
            f"<body>{html_body}</body></html>"
        )
    except ImportError:
        pass

    if html_doc is not None:
        # Same child, same crash semantics as `_pdf_from_html`. A missing
        # weasyprint still falls through to pandoc below; only a crash raises.
        if _pdf_from_html(html_doc, out_dir) is not None:
            return pdf_path

    # Session REPORT-3 (item 10) — refuse rather than ship the evidence-free
    # document. See `FigurelessPdfRefused`. Checked BEFORE the file is written,
    # so a refusal leaves nothing behind.
    figures = _SVG_IMAGE_RE.findall(markdown_text)
    if figures:
        raise FigurelessPdfRefused(
            f"refusing to render this report through pandoc: it references "
            f"{len(figures)} SVG figure(s) that pandoc cannot place, and the PDF "
            f"would be produced with none of them. This document needs weasyprint "
            f"(the `[pdf]` extra, plus its native pango/cairo libraries)."
        )

    md_path = out_dir / "_report_for_pdf.md"
    md_path.write_text(markdown_text)
    return _pandoc_to_pdf(md_path, out_dir, pdf_path, fmt="markdown")
