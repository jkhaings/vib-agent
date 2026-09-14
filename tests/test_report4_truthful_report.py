"""Session REPORT-4 — the report tells the truth on a custom basis, cites nothing
it did not measure, and carries no internal language.

Every case here is driven from FIXTURE-1's `compressor_de_h.csv` through the SAME
lanes the product uses — `adapters.uploads.parse_upload`, `webapp.assembly.merge_channels`,
`pipeline.run_analysis` — because the defect this session fixed was only ever visible
on that path: the operator's report (master `389fce6`, one channel, ISO group 2 rigid,
machine-specific limits 5 / 8 / 12) opened "ISO ZONE B — ACCEPTABLE" on a reading ISO
20816-3 itself calls Zone D, and cited an axis nobody measured as corroborating evidence.

Both BASES are built from one file by changing three form fields and nothing else, so
"the custom document says X and the ISO document still says Y" is a claim about the
basis and not about two different readings.

No paid calls: nothing here reaches the drafting model. The item-2 checks are driven with
canned narratives, which is what the brief's "watched red on a negative control" requires —
each refusal pin carries the exact sentence from the shipped report beside the honest
sentence that must survive it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.agent.consistency import check_measured_channels
from vib_agent.agent.loop import NOT_MEASURED, mask_unmeasured_axes
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report.charts import analysis_parameters, status_text, trend_legend_labels
from vib_agent.report.generate import (
    axis_phrase,
    declared_axis_names,
    render_html,
    render_markdown,
)
from vib_agent.webapp.assembly import ParsedChannel, merge_channels

CSV = Path("outputs/demo_package/multi_location/compressor_de_h.csv")
PROFILE = "route"

#: The plant's own limits, and the ones that make ISO disagree: 5.20 mm/s is
#: Zone B against 5 / 8 / 12 and Zone D against ISO group 2 rigid (1.4/2.8/4.5).
CUSTOM_LIMITS = (5.0, 8.0, 12.0)

#: The webapp's own note for this job, verbatim from `assembly.channels_measured_note`.
#: It is the ONLY place a declared measurement direction exists in the process.
CHANNELS_NOTE = ("Channels measured: radial – horizontal (y); "
                 "not measured: radial – vertical, axial.")


def _build(limits):
    form = UploadForm(
        machine_alias="Compressor", rpm=1800.0, iso_group="2", iso_support="rigid",
        bearing_model="6206", machine_type="compressor",
        velocity_unit="mm_s", detection_type="rms", mode="spectrum",
        limit_ab=limits[0] if limits else None,
        limit_bc=limits[1] if limits else None,
        limit_cd=limits[2] if limits else None,
    )
    case, kind, conv = parse_upload(CSV, form, bearings_cfg=load_config("bearings"))
    thresholds = load_thresholds(PROFILE)
    iso_table = load_config("iso_zones")["zones"]
    outcome = merge_channels(
        [ParsedChannel(direction="radial_h", assumed=False, case=case,
                       kind=kind, conversion_note=conv)], [],
        iso_table=iso_table, thresholds=thresholds, rules=None,
    )
    result = run_analysis(outcome.case, iso_table=iso_table,
                          thresholds=thresholds, rules=None)
    notes = [n for n in ([outcome.conversion_note or conv, *outcome.report_notes]) if n]
    return outcome.case, result, thresholds, notes


@pytest.fixture(scope="module")
def custom():
    return _build(CUSTOM_LIMITS)


@pytest.fixture(scope="module")
def iso():
    return _build(None)


def _documents(built):
    case, result, thresholds, notes = built
    md = render_markdown(result, case.machine, case=case,
                         thresholds=thresholds, profile=PROFILE)
    html = render_html(result, case.machine, case=case, thresholds=thresholds,
                       profile=PROFILE, notes=notes)
    return md, html


class TestTheFixtureIsTheOperatorsCase:
    """Before any pin asserts what the documents say, prove they are documents
    about the reading the operator actually read. A green suite over the wrong
    case is this session's own standing warning."""

    def test_the_custom_basis_disagrees_with_iso(self, custom, iso):
        _, c_result, _, _ = custom
        _, i_result, _, _ = iso
        assert c_result.iso.zone_basis == "custom"
        assert c_result.iso.iso_zone == "B"
        assert c_result.iso.iso_zone_would_be == "D"
        assert i_result.iso.zone_basis == "iso"
        assert i_result.iso.iso_zone == "D"
        # One reading, two verdicts. If these ever converge the pins below stop
        # discriminating and would pass for the wrong reason.
        assert c_result.iso.severity_rms == pytest.approx(i_result.iso.severity_rms)

    def test_one_channel_was_measured(self, custom):
        case, _, _, notes = custom
        assert CHANNELS_NOTE in notes
        assert declared_axis_names(notes) == {"y": "radial – horizontal"}


# ── P1 ───────────────────────────────────────────────────────────────────────
class TestP1BasisVocabulary:
    """On a custom basis the token "ISO Zone" (any case) appears NOWHERE in
    either document except inside the would-give clause."""

    @pytest.mark.parametrize("doc", (0, 1), ids=("markdown", "html"))
    def test_no_iso_zone_anywhere_on_a_custom_page(self, custom, doc):
        text = _documents(custom)[doc]
        assert "iso zone" not in text.lower()

    @pytest.mark.parametrize("doc", (0, 1), ids=("markdown", "html"))
    def test_the_would_give_clause_is_where_iso_is_named(self, custom, doc):
        text = _documents(custom)[doc]
        assert "ISO 20816-3 Group 2 rigid support would give Zone D" in text

    def test_the_banner_carries_the_iso_zone_when_it_differs(self, custom):
        _, result, _, _ = custom
        assert status_text(result) == (
            "ZONE B — ACCEPTABLE · MACHINE-SPECIFIC LIMITS · ISO 20816-3 WOULD GIVE ZONE D"
        )

    def test_the_damage_stage_box_re_attributes_its_zone(self, custom):
        md, html = _documents(custom)
        line = "Zone at the time of measurement: B (machine-specific limits; ISO would give D)"
        assert line in md
        assert line in html

    def test_the_negative_control_is_the_iso_basis(self, iso):
        """Watched red: on the ISO basis every line above must read the OLD way.
        A fix that made the custom page clean by making the ISO page wrong would
        pass every assertion before this one."""
        _, result, _, _ = iso
        assert status_text(result) == "ISO ZONE D — UNACCEPTABLE · 1 committed finding"
        md, html = _documents(iso)
        assert "ISO zone at the time of measurement: D" in md
        assert "ISO zone at the time of measurement: D" in html
        # and ISO's name IS the right one to sign here
        assert "per ISO 20816-3" in md


# ── P3 ───────────────────────────────────────────────────────────────────────
#: The sentence the shipped report actually carried, on a job with one channel.
OFFENDING_DRAFT = (
    "No shaft-rate sidebands were detected around the BPFO carrier. The axial axis (x) "
    "showed no elevated bearing-frequency content; the axial-to-radial ratio is 0.0, "
    "consistent with a radially loaded outer-race defect."
)

#: The report telling the truth about the same coverage gap. It names the same
#: unmeasured axes in every clause and MUST survive.
HONEST_DRAFT = (
    "Single-axis velocity measurement: Only the y-axis velocity was measured. The x-axis "
    "(axial) and z-axis were not available as velocity channels. The axial-to-radial ratio "
    "could not be computed from velocity data."
)


class TestP3NothingCitedThatWasNotMeasured:
    def test_the_artefact_never_reaches_the_drafter(self, custom):
        """`pdm_core/bearing_rca.py:366` divides `v.get(axial, 0.0)` by the radial
        max, so an axis that was never measured reads 0.0 — indistinguishable
        from a measured, silent one. It is masked at the agent boundary."""
        import json
        _, result, _, _ = custom
        raw = json.loads(result.model_dump_json())
        assert raw["rca"]["axial_radial_ratio"] == 0.0, "the artefact still exists upstream"
        assert raw["rca"]["axial_axis"] == "x"
        masked = mask_unmeasured_axes(json.loads(result.model_dump_json()), ["y"])
        assert masked["rca"]["axial_radial_ratio"] == NOT_MEASURED

    def test_the_mask_is_a_no_op_where_it_should_be(self, custom):
        """Watched red both ways: masking when the axis WAS measured would delete
        a real number, and masking on unknown coverage would delete it on every
        hand-built PeakSet in the tree."""
        import json
        _, result, _, _ = custom
        for axes in (None, [], ["x", "y", "z"]):
            kept = mask_unmeasured_axes(json.loads(result.model_dump_json()), axes)
            assert kept["rca"]["axial_radial_ratio"] == 0.0

    def test_the_offending_sentence_is_refused(self, custom):
        _, result, _, _ = custom
        mismatches = check_measured_channels(OFFENDING_DRAFT, result, ["y"])
        assert mismatches, "the shipped report's own sentence must not pass"
        assert any("was NOT measured" in m for m in mismatches)
        # BOTH halves: the clause after the semicolon is where the fabrication hid.
        assert len(mismatches) == 2

    def test_the_honest_sentence_survives(self, custom):
        """The negative control that matters most. This names every unmeasured
        axis there is, and it is the report being truthful about its coverage."""
        _, result, _, _ = custom
        assert check_measured_channels(HONEST_DRAFT, result, ["y"]) == []

    def test_the_check_is_a_no_op_without_a_coverage_gap(self, custom):
        _, result, _, _ = custom
        assert check_measured_channels(OFFENDING_DRAFT, result, ["x", "y", "z"]) == []
        assert check_measured_channels(OFFENDING_DRAFT, result, None) == []


# ── P6 ───────────────────────────────────────────────────────────────────────
#: Internal language. Every one of these rendered in the report the operator read.
INTERNAL_TOKENS: tuple[str, ...] = (
    "Sensor / MAC", "UPLOAD-SPEC", "operator-approved", "language model",
    "pdm_core", "Layer 5", "[inferred]", "R2-B0.2", "see conversion note",
    "deterministic Python", "severity: info", "Decision:",
)


class TestP6NoInternalLanguage:
    @pytest.mark.parametrize("token", INTERNAL_TOKENS)
    @pytest.mark.parametrize("basis", ("custom", "iso"))
    @pytest.mark.parametrize("doc", (0, 1), ids=("markdown", "html"))
    def test_absent_from_both_documents_on_both_bases(self, custom, iso, basis, doc, token):
        built = custom if basis == "custom" else iso
        assert token not in _documents(built)[doc]

    def test_the_finding_heading_states_confidence_and_stage(self, custom):
        """What replaced "severity: info" — and it is not nothing, which is the
        way this pin could have been satisfied dishonestly."""
        md, html = _documents(custom)
        assert "confidence: high · damage stage: 3 (early)" in md
        assert "confidence: high · damage stage: 3 (early)" in html

    def test_the_inferred_slot_says_it_in_the_reports_own_words(self, custom):
        md, html = _documents(custom)
        assert "(our reasoning)" in md
        assert "(our reasoning)" in html


# ── P7 ───────────────────────────────────────────────────────────────────────
class TestP7AxisNamesFollowTheDeclaredDirection:
    def test_the_document_says_the_direction_not_the_letter(self, custom):
        _, html = _documents(custom)
        assert "on the radial – horizontal" in html
        # The letter survives in EXACTLY one place, in parentheses.
        assert "radial – horizontal (y)" in html
        assert "y-axis" not in html

    def test_the_letter_is_in_the_parameters_table(self, custom):
        case, result, thresholds, notes = custom
        rows = dict(analysis_parameters(result, case=case, thresholds=thresholds,
                                        profile=PROFILE,
                                        axis_names=declared_axis_names(notes)))
        assert rows["Measurement channel"] == "radial – horizontal (y)"

    def test_a_reading_that_declared_nothing_keeps_its_letters(self, custom):
        """Watched red: the gate is "the analyst declared a direction", not "the
        product has an axis". A CLI or NCD reading declared none, and naming one
        would assert something nobody said — so the markdown document, which the
        webapp does not hand its notes to, still prints letters."""
        md, _ = _documents(custom)
        assert "y-axis" in md
        assert "radial – horizontal" not in md
        assert declared_axis_names(None) is None
        assert declared_axis_names(["some unrelated note"]) is None
        assert axis_phrase("y", None) == "y-axis"
        assert axis_phrase("y", {"y": "radial – horizontal"}) == "radial – horizontal"


# ── P8 ───────────────────────────────────────────────────────────────────────
class TestP8ChartsTwoOwedFixes:
    def test_the_parameters_table_agrees_with_page_one(self, custom):
        """INTAKEFIX-1 F-1. The row said "mm/s RMS (converted at upload — see
        conversion note)" three inches under page 1's "Input already in mm/s RMS
        — no conversion applied", i.e. it asserted an event that had not
        happened and pointed at a note this module cannot keep."""
        case, result, thresholds, notes = custom
        rows = dict(analysis_parameters(result, case=case, thresholds=thresholds,
                                        profile=PROFILE))
        assert rows["Spectrum amplitude units"] == "mm/s RMS"
        assert rows["Detection"] == "peak-pick against computed fault frequencies"
        _, html = _documents(custom)
        assert "no conversion applied" in html
        assert "converted at upload" not in html

    def test_the_trend_legend_names_whoever_set_the_boundary(self, custom, iso):
        """LIMITS-1c F-1. These strings are rendered INTO the figure and no pin in
        the tree can read text back out of a chart — so the label builder is the
        seam, and this is the pin that holds it."""
        _, c_result, _, _ = custom
        _, i_result, _, _ = iso
        assert trend_legend_labels(c_result) == (
            "Machine-specific zone boundaries", "Machine-specific alarm limit")
        # Watched red on the negative control: ISO's name where ISO set them.
        assert trend_legend_labels(i_result) == (
            "ISO zone boundaries", "ISO §6.5.2 alarm limit")
