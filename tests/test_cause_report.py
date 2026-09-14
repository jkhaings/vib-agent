"""Session R2-BUILD — the cause section as an analyst actually receives it.

The knowledge layer's whole value is that a cause reaches a report only with a
source behind it. That guarantee is enforced in three places and this file
covers the last one: the YAML schema enforces it at authoring time, the
consistency contract enforces it against the drafting model, and these tests
enforce it against the RENDERER — because a macro can lose a citation, mislabel
whose job it is to collect a piece of evidence, or quietly print a reviewer's
private note into a customer document without any of the other layers noticing.

The section is a HYPOTHESIS LIST. Every assertion here is ultimately about that
one claim staying true on the page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vib_agent.config import load_config, load_thresholds
from vib_agent.knowledge import CAUSE_HEADING, lookup_causes_for
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import (
    INFERRED_CITATION_SLOT,
    _drafted_evidence_block,
    cause_section,
    render_markdown,
)
from vib_agent.synth.generator import make_case, make_history

_REPO = Path(__file__).resolve().parents[1]
_PROFILE = "route"


def _analyse(name: str, *, history: bool = False, seed: int = 7):
    iso_table = load_config("iso_zones")["zones"]
    thresholds = load_thresholds(_PROFILE)
    case = make_case(name, iso_table=iso_table, thresholds=thresholds, seed=seed)
    if history:
        case = case.model_copy(
            update={"history": make_history(30, 1.2, 5.2, noise_pct=0.12,
                                            cadence="daily", seed=7)}
        )
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds,
                          rules=load_config("next_measurements"))
    return case, result, thresholds


def _render(name: str, **kw) -> str:
    case, result, thresholds = _analyse(name, **kw)
    return render_markdown(result, case.machine, case=case, thresholds=thresholds,
                           profile=_PROFILE)


def _section(markdown: str) -> str:
    """The cause section only, from its heading to the next top-level one."""
    assert f"## {CAUSE_HEADING}" in markdown
    return markdown.split(f"## {CAUSE_HEADING}", 1)[1].split("\n## ", 1)[0]


@pytest.fixture(scope="module")
def bpfo_markdown() -> str:
    return _render("bpfo")


class TestWhenItRenders:
    def test_it_renders_for_a_committed_bearing_fault(self, bpfo_markdown):
        assert f"## {CAUSE_HEADING}" in bpfo_markdown

    def test_it_renders_for_every_bearing_family_the_generator_can_seed(self):
        for name in ("bpfo", "bpfi"):
            assert f"## {CAUSE_HEADING}" in _render(name), name

    def test_it_is_absent_when_no_bearing_fault_is_committed(self):
        """The library is a BEARING-cause batch. A shaft or drive fault has no
        approved cause knowledge, and the section must disappear entirely
        rather than appear empty or fall back to bearing causes."""
        for name in ("healthy", "imbalance", "looseness", "angular_misalignment", "belt_fault"):
            markdown = _render(name)
            assert CAUSE_HEADING not in markdown, name
            assert "underlying cause" not in markdown.lower(), name

    def test_the_builder_returns_none_rather_than_an_empty_block(self):
        """`{% if causes %}` in the template rests on this: None is what makes
        the whole section vanish instead of leaving a bare heading."""
        _, healthy, _ = _analyse("healthy")
        assert cause_section(healthy) is None
        _, bpfo, _ = _analyse("bpfo")
        assert cause_section(bpfo) is not None

    def test_a_gate_failed_reading_gets_no_cause_section(self):
        """No diagnosis before the gate passes — so there is no committed fault
        to explain, and hypothesising about one would be the exact inversion of
        the insufficient-data contract."""
        case, result, thresholds = _analyse("machine_off")
        assert result.quality_gate.overall == "fail"
        markdown = render_markdown(result, case.machine, case=case,
                                   thresholds=thresholds, profile=_PROFILE)
        assert "Insufficient data" in markdown
        assert CAUSE_HEADING not in markdown
        assert "underlying cause" not in markdown.lower()


class TestItIsFramedAsHypotheses:
    def test_the_heading_itself_says_so(self, bpfo_markdown):
        """In the HEADING, not a footnote — a reader skimming section titles
        must not be able to mistake this for the diagnosis."""
        assert "hypotheses for analyst confirmation" in CAUSE_HEADING
        assert f"## {CAUSE_HEADING}" in bpfo_markdown

    def test_the_preamble_states_the_limit_of_the_measurement(self, bpfo_markdown):
        section = _section(bpfo_markdown)
        assert "**These are hypotheses, not findings.**" in section
        assert "none of them is confirmed by this measurement" in section
        assert "identifies the fault; it does not identify what caused it" in section

    def test_no_wording_presents_a_cause_as_established(self, bpfo_markdown):
        """The forbidden-phrase gate. Every string here would convert a
        candidate into a finding, which is the one thing this section may never
        do. Phrase-level rather than word-level on purpose: the section's own
        framing legitimately contains "confirmation" and "confirm or exclude"."""
        section = _section(bpfo_markdown).lower()
        forbidden = (
            "the root cause is",
            "the root cause of",
            "the underlying cause is",
            "the cause is",
            "the cause was",
            "cause is confirmed",
            "has been confirmed",
            "confirmed cause",
            "cause has been identified",
            "the identified cause",
            "we determined",
            "definitively",
            "conclusively",
            "proves that",
            "demonstrates that the",
        )
        for phrase in forbidden:
            assert phrase not in section, f"the cause section asserts {phrase!r}"

    def test_it_never_claims_the_missing_inspection_was_performed(self, bpfo_markdown):
        section = _section(bpfo_markdown)
        assert "not performed by this analysis" in section
        assert "analyst fieldwork" in section


class TestTheEvidenceSplit:
    def test_oil_thermal_and_visual_land_in_evidence_to_collect(self):
        """The product pillar: every unresolved ambiguity ships with the
        measurement that resolves it, and an analyst must be able to see at a
        glance that this analysis performed none of these."""
        _, result, _ = _analyse("bpfo")
        section = cause_section(result)
        for cause in section["causes"]:
            for item in cause["checked"]:
                assert item["stream"] not in ("oil", "thermal", "visual"), item

    def test_vibration_lands_in_checked_by_this_analysis(self):
        _, result, _ = _analyse("bpfo")
        section = cause_section(result)
        for cause in section["causes"]:
            for item in cause["collect"]:
                assert item["stream"] != "vibration", item

    def test_every_observation_lands_in_exactly_one_bucket(self):
        _, result, _ = _analyse("bpfo")
        section = cause_section(result)
        book = {c.cause_id: c for c in lookup_causes_for(["bearing_outer_race"])}
        for cause in section["causes"]:
            source = book[cause["cause_id"]]
            assert len(cause["checked"]) + len(cause["collect"]) == len(
                source.discriminating_evidence
            ), cause["cause_id"]

    def test_history_is_fieldwork_when_the_analysis_had_no_reading_history(self):
        """A history observation ("the alignment record", "which ring rotates")
        is only honestly "checked by this analysis" when the analysis actually
        carried a history to read it against."""
        _, no_history, _ = _analyse("bpfo")
        assert no_history.trend is None
        section = cause_section(no_history)
        assert section["history_checked"] is False
        streams = {item["stream"] for cause in section["causes"] for item in cause["checked"]}
        assert "history" not in streams

    def test_history_is_checked_when_a_trend_was_computed(self):
        _, with_history, _ = _analyse("bpfo", history=True)
        assert with_history.trend is not None
        section = cause_section(with_history)
        assert section["history_checked"] is True
        streams = {item["stream"] for cause in section["causes"] for item in cause["checked"]}
        assert "history" in streams

    def test_only_collectible_evidence_carries_how_to_collect(self):
        """The resolving measurement is printed exactly where it is actionable."""
        markdown = _render("bpfo")
        section = _section(markdown)
        assert "_How to collect:" in section
        for line in section.splitlines():
            if line.startswith("- ") and "_How to collect:" in line:
                continue
            assert "How to collect" not in line or line.startswith("- ")

    def test_the_bucket_captions_name_who_collects(self, bpfo_markdown):
        section = _section(bpfo_markdown)
        assert "**Checked by this analysis**" in section
        assert "**Evidence to collect**" in section


class TestCitations:
    def test_every_rendered_citation_exists_in_the_library(self, bpfo_markdown):
        """A citation the library does not carry is a fabricated source, which
        is the single failure the reference register exists to prevent."""
        import re

        allowed = {
            cit.rendered()
            for cause in lookup_causes_for(["bearing_outer_race"])
            for cit in list(cause.citations)
            + [c for o in cause.discriminating_evidence for c in o.citations]
        }
        found = set(re.findall(r"\[#\d{1,3}[ab]?[ \t][^\]\n]{1,200}\]", _section(bpfo_markdown)))
        assert found, "no citations rendered at all"
        assert found <= allowed, sorted(found - allowed)

    def test_citations_render_in_the_index_resolvable_form(self, bpfo_markdown):
        section = _section(bpfo_markdown)
        assert "[#01 §5 Damage and actions, Abrasive wear, pp.66-67]" in section
        assert "references/INDEX.md" in section

    def test_an_inferred_observation_renders_without_a_citation(self):
        """Schema v1.2's finding, carried onto the page: an uncited observation
        sitting beside cited ones inherits authority it was never given. In the
        YAML the fix was `inferred: true`; in the report it is a visible
        `(our reasoning)` marker where a citation would go.

        Session REPORT-4 (item 5) renamed that marker from `[inferred]`, a lint
        marker's voice in a document somebody signs. The lookup below finds
        inferred observations BY the marker, so it is re-pointed rather than
        merely re-asserted: keyed on the old string it would match nothing and
        fail on its own "check the lookup" message."""
        _, result, _ = _analyse("bpfo")
        section = cause_section(result)
        items = [item for cause in section["causes"]
                 for item in cause["checked"] + cause["collect"]]
        inferred = [i for i in items if i["citation"] == INFERRED_CITATION_SLOT]
        assert inferred, "no inferred observation rendered — check the lookup"
        for item in inferred:
            assert "#" not in item["citation"]
        # The old marker is gone, not merely unused: a renderer emitting BOTH
        # would satisfy every assertion above.
        assert not [i for i in items if "[inferred]" in i["citation"]]

    def test_the_inferred_marker_is_explained_where_it_appears(self, bpfo_markdown):
        section = _section(bpfo_markdown)
        assert INFERRED_CITATION_SLOT in section
        assert "[inferred]" not in section
        # The explanation moved with the marker it explains: "our own reasoning
        # rather than" became "ours rather than", because "(our reasoning)"
        # already says the first half in the slot itself. Whitespace-normalised,
        # because the new wording wraps mid-phrase in the markdown and the old
        # one happened not to — an assertion that depends on where a line breaks
        # is pinning the formatter, not the sentence.
        assert "ours rather than something a cited source states" in " ".join(section.split())

    def test_every_observation_carries_a_citation_or_the_inferred_marker(self):
        """No third state on the page, exactly as there is none in the schema."""
        _, result, _ = _analyse("bpfo")
        section = cause_section(result)
        for cause in section["causes"]:
            for item in cause["checked"] + cause["collect"]:
                assert item["citation"].strip(), f"{cause['cause_id']}: {item['text'][:60]!r}"


class TestNothingReviewerOnlyLeaks:
    """Read through the AUTHORING model, which is the only one that still HAS
    these fields — the runtime view drops them, and that is precisely the
    property under test. Asserting it against the runtime model would be
    circular."""

    @staticmethod
    def _authored():
        from knowledge.schema import load_causes as load_authored

        return load_authored().entries

    def test_citation_notes_never_reach_the_page(self, bpfo_markdown):
        """A `note` is a reviewer's description of what a source supports,
        written for the review record. Rendering it would put un-reviewed prose
        in front of a customer."""
        section = _section(bpfo_markdown)
        for entry in self._authored():
            notes = list(entry.citations) + [
                c for o in entry.discriminating_evidence for c in (o.citations or [])
            ]
            for citation in notes:
                assert citation.note.strip()[:60] not in section, entry.cause_id

    def test_review_questions_and_disagreements_never_reach_the_page(self, bpfo_markdown):
        section = _section(bpfo_markdown)
        for entry in self._authored():
            for field in (entry.review_question, entry.disagreement):
                if field and field.strip():
                    assert field.strip()[:60] not in section, entry.cause_id

    def test_the_extends_families_justification_never_reaches_the_page(self, bpfo_markdown):
        """"EXTENSION. #02 p.7 describes..." is an argument made to a reviewer
        about why one entry may key to two rings. It is not analyst material."""
        section = _section(bpfo_markdown)
        assert "EXTENSION." not in section
        for entry in self._authored():
            if entry.extends_families:
                assert entry.extends_families.strip()[:60] not in section, entry.cause_id


class TestSessionF2VocabularyHolds:
    """Session F2 removed "RCA" and "root cause" from everything a reader or the
    drafting model receives. The cause layer is a new source of report text and
    a new source of drafting-prompt text, so it is a new way for that vocabulary
    to come back — through a `mechanism`, a `consider` note or an observation
    nobody re-read. Asserted against the LIBRARY, not just today's render, so a
    future entry cannot reintroduce it silently."""

    def test_no_rendered_library_field_carries_the_forbidden_vocabulary(self):
        from vib_agent.knowledge import load_causes as load_runtime

        for entry in load_runtime().entries:
            rendered = [entry.cause, entry.mechanism, entry.consider or ""]
            rendered += [c.locator for c in entry.citations]
            for observation in entry.discriminating_evidence:
                rendered += [observation.observation, observation.how_to_collect]
                rendered += [c.locator for c in observation.citations]
            for text in rendered:
                lowered = text.lower()
                assert "root cause" not in lowered, entry.cause_id
                assert "root-cause" not in lowered, entry.cause_id
                assert "RCA" not in text, entry.cause_id

    def test_the_rendered_section_carries_neither(self, bpfo_markdown):
        section = _section(bpfo_markdown)
        assert "RCA" not in section
        assert "root cause" not in section.lower() and "root-cause" not in section.lower()

    def test_the_agent_tool_payload_carries_neither(self):
        """The other half: the model is handed this JSON, and Session F2's rule
        is about what the model is HANDED, not only what it writes."""
        import json

        from vib_agent.agent.tools import ToolContext, execute_tool

        case, result, _ = _analyse("bpfo")
        raw = execute_tool(
            "lookup_causes",
            {"fault_family": "bearing_outer_race"},
            ctx=ToolContext(result=result, case=case, peak_set=None),
        )
        assert json.loads(raw)["causes"]
        assert "RCA" not in raw
        assert "root cause" not in raw.lower() and "root-cause" not in raw.lower()


class TestTheContentIsTheLibrarys:
    def test_every_looked_up_cause_appears_and_nothing_else_does(self, bpfo_markdown):
        """No silent cap. Truncating shipped knowledge would read as "these are
        the causes" when it was really "the first few"."""
        section = _section(bpfo_markdown)
        expected = lookup_causes_for(["bearing_outer_race"])
        headings = [line[4:].strip() for line in section.splitlines() if line.startswith("### ")]
        assert headings == [" ".join(c.cause.split()) for c in expected]

    def test_the_order_on_the_page_is_the_librarys_order(self, bpfo_markdown):
        """Including the entry whose own `consider` says to weigh it last."""
        section = _section(bpfo_markdown)
        headings = [line[4:].strip() for line in section.splitlines() if line.startswith("### ")]
        assert headings[-1] == "Subsurface fatigue reaching the surface at the end of rating life"

    def test_mechanisms_are_carried_verbatim(self, bpfo_markdown):
        section = _section(bpfo_markdown)
        for cause in lookup_causes_for(["bearing_outer_race"]):
            assert " ".join(cause.mechanism.split()) in section, cause.cause_id

    def test_the_consider_note_is_carried_where_one_exists(self, bpfo_markdown):
        """`consider` is the entry's own disambiguation guidance — "two opposed
        load zones is NOT specific to oval clamping". It is what stops an
        analyst mis-attributing a shared signature, so it is printed rather
        than only used for ordering."""
        section = _section(bpfo_markdown)
        assert "RING IDENTITY" in section
        assert "COLOUR IS NOT A DISCRIMINATOR HERE" in section


class TestOneMacroTwoPaths:
    def test_both_templates_call_the_same_macro(self):
        templates = _REPO / "src/vib_agent/report/templates"
        for name in ("default_survey.md.j2", "drafted_evidence.md.j2"):
            assert "ev.cause_hypotheses(causes, cause_heading)" in (
                templates / name
            ).read_text(), name
        assert "macro cause_hypotheses" in (templates / "_evidence.md.j2").read_text()

    def test_the_rendered_block_is_byte_identical_on_both_paths(self):
        """Stronger than asserting both templates import the macro: it renders
        both and compares. A drafted report must carry exactly the cause
        knowledge the deterministic one does — the model contributes nothing to
        this block and cannot be allowed to."""
        case, result, thresholds = _analyse("bpfo")
        deterministic = _section(
            render_markdown(result, case.machine, case=case, thresholds=thresholds,
                            profile=_PROFILE)
        )
        drafted_block = _drafted_evidence_block(
            result, charts=None, case=case, thresholds=thresholds, profile=_PROFILE,
            draft_text="# Vibration Survey Report — x\n\nprose\n",
        )
        drafted = drafted_block.split(f"## {CAUSE_HEADING}", 1)[1].split("\n## ", 1)[0]
        assert drafted == deterministic

    def test_a_draft_that_already_wrote_the_section_is_not_given_a_second_one(self):
        """Same idiom as want_parameters / want_signature. The consistency
        contract has already proved the draft's own version is a subset of this
        lookup, so suppressing the splice cannot lose knowledge."""
        case, result, thresholds = _analyse("bpfo")
        block = _drafted_evidence_block(
            result, charts=None, case=case, thresholds=thresholds, profile=_PROFILE,
            draft_text=f"# Vibration Survey Report — x\n\n## {CAUSE_HEADING}\n\nprose\n",
        )
        assert CAUSE_HEADING not in block
