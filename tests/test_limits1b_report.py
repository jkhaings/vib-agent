"""LIMITS-1b — the report prints BOTH zones when a machine has its own limits.

LIMITS-1a taught the classifier to judge a machine against a limit somebody set
and to record what ISO 20816-3 would have said about the same millimetres per
second (`Reading.zone_basis`, `Reading.iso_zone_would_be`). Nothing read either
field, so the document went on signing ISO's name to whatever
`resolve_thresholds` returned. Measured at this branch's base commit, on a
machine carrying a plant limit of 5.0 / 8.0 / 12.0 mm/s:

    **Health:** ISO Zone B — acceptable per ISO 20816-3: overall 5.20 mm/s RMS
    on the y-axis, above the 5 mm/s Zone A/B boundary (Group 2, rigid support).

ISO 20816-3 would have called that same reading **Zone D, unacceptable**. That
is LIMITS-1a F-3, and it is the failure the operator's ruling exists to stop:

    a custom zone must not hide a real problem.

THE FIXTURE, and why this one. The seeded `bpfo` case carries the repo's 5.20
mm/s trio — `severity_rms` 5.20 on `y` — on a machine that is ISO group 2 on
rigid support, whose boundaries are 1.4 / 2.8 / 4.5, so **ISO says Zone D**.
That headroom above the top ISO boundary is what lets one reading be walked
through C, B and D by moving only the limits, exactly as
`tests/test_limits1_zones.py` does it one layer down. `iso_zone_would_be` is
therefore `D` in all three, which is the point: the would-give clause is a fixed
second opinion the custom letter is read against, and the Zone B case is the one
where a plant limit would otherwise have reported a Zone D machine as
acceptable.

This file renders the product; it never asserts a boundary. `test_limits1_zones`
owns the arithmetic.
"""

from __future__ import annotations

import re

import pytest

from vib_agent.agent.consistency import check_zone_language
from vib_agent.config import load_config, load_thresholds
from vib_agent.models import MachineThresholds
from vib_agent.pdm_core.iso_classify import mark_not_assessable
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import render_html, render_report
from vib_agent.synth.generator import make_case

from tests.test_r3_fault_sheet import squash, visible_text

#: The same three boundary sets LIMITS-1a walked this reading through, by name,
#: so the two files cannot come to disagree about which limit means which zone.
TO_ZONE_C = MachineThresholds(ab=2.0, bc=5.0, cd=8.0)
TO_ZONE_B = MachineThresholds(ab=5.0, bc=8.0, cd=12.0)
TO_ZONE_D = MachineThresholds(ab=1.0, bc=2.0, cd=3.0)

#: The ruled sentence's own phrase, and item 4's sentence, quoted once.
BASIS_PHRASE = "machine-specific limits"
GATE_SENTENCE = "Machine-specific limits also govern the 1× severity gate for this machine."


@pytest.fixture(scope="module")
def cfg():
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
    }


def _analysed(cfg, limit=None, *, case_name="bpfo", drop_iso=False, withhold=False):
    """One seeded case, optionally carrying a plant limit, optionally with no
    ISO row to fall back on, optionally withheld."""
    case = make_case(case_name, iso_table=cfg["iso_table"],
                     thresholds=cfg["thresholds"], seed=1)
    update = {}
    if limit is not None:
        update["thresholds"] = limit
    if drop_iso:
        update["iso_group"] = None
        update["iso_support"] = None
    if update:
        case = case.model_copy(update={"machine": case.machine.model_copy(update=update)})
    result = run_analysis(case, iso_table=cfg["iso_table"],
                          thresholds=cfg["thresholds"], rules=cfg["rules"])
    if withhold:
        result = result.model_copy(
            update={"iso": mark_not_assessable(result.iso, "velocity outside the plausible range")}
        )
    return case, result


#: state -> how to build it. Rendered ONCE per module; six documents is enough
#: weasyprint for one file.
STATES = {
    "iso": dict(limit=None),
    "custom_C": dict(limit=TO_ZONE_C),
    "custom_B": dict(limit=TO_ZONE_B),
    "custom_D": dict(limit=TO_ZONE_D),
    "custom_no_iso_row": dict(limit=TO_ZONE_B, drop_iso=True),
    "custom_withheld": dict(limit=TO_ZONE_B, withhold=True),
    # An ISO-basis reading that is NOT Zone D, so the escalation trigger has a
    # next boundary to name. `bpfo` on the ISO table is Zone D, which is the top
    # of the scale and has no next boundary at all (`_ZONE_NEXT` stops at C).
    "iso_zone_c": dict(limit=None, case_name="belt_fault"),
}


@pytest.fixture(scope="module")
def analyses(cfg):
    return {name: _analysed(cfg, **kw) for name, kw in STATES.items()}


@pytest.fixture(scope="module")
def custom_result(analyses):
    """The Zone B custom reading — the one a plant limit would have called
    acceptable while ISO 20816-3 called the same millimetres per second D."""
    return analyses["custom_B"][1]


@pytest.fixture(scope="module")
def iso_result(analyses):
    return analyses["iso"][1]


@pytest.fixture(scope="module")
def docs(cfg, analyses):
    """`{state: {"md": ..., "html": ...}}` — no PDF engine needed."""
    out = {}
    for name, (case, result) in analyses.items():
        out[name] = {
            "md": _md(cfg, case, result),
            "html": render_html(result, case.machine, case=case,
                                thresholds=cfg["thresholds"], profile="route"),
        }
    return out


def _md(cfg, case, result):
    from vib_agent.report.generate import render_markdown
    return render_markdown(result, case.machine, case=case,
                           thresholds=cfg["thresholds"], profile="route")


@pytest.fixture(scope="module")
def page1(cfg, analyses, tmp_path_factory):
    """`{state: page-1 text}`, read out of the rendered PDF — "page 1" in the
    pins below means the page an analyst actually sees first, not a section."""
    pytest.importorskip("weasyprint")
    pypdf = pytest.importorskip("pypdf")
    pages = {}
    for name, (case, result) in analyses.items():
        out = tmp_path_factory.mktemp(f"l1b_{name}")
        written = render_report(result, case.machine, out, pdf=True, case=case,
                                thresholds=cfg["thresholds"], profile="route")
        assert "pdf" in written, "no PDF engine reached — these pins need one"
        pages[name] = squash(pypdf.PdfReader(str(written["pdf"])).pages[0].extract_text())
    return pages


# ── (a) the ruled sentence, on a machine ISO can still answer for ─────────


class TestBothZonesPrint:
    """The ruling of record: the machine's own letter AND what ISO would give."""

    @pytest.mark.parametrize("state,zone,limits", [
        ("custom_C", "C", "2 / 5 / 8"),
        ("custom_B", "B", "5 / 8 / 12"),
        ("custom_D", "D", "1 / 2 / 3"),
    ])
    def test_page_one_carries_the_ruled_sentence(self, page1, state, zone, limits):
        text = page1[state]
        assert BASIS_PHRASE in text, text[:400]
        assert f"Judged against {BASIS_PHRASE} ({limits} mm/s RMS)" in text
        assert "ISO 20816-3 Group 2 rigid support would give Zone D" in text
        assert f"Zone {zone} —" in text

    @pytest.mark.parametrize("state,zone", [
        ("custom_C", "C"), ("custom_B", "B"), ("custom_D", "D"),
    ])
    def test_the_letter_is_the_machines_own(self, analyses, page1, state, zone):
        _, result = analyses[state]
        assert result.iso.iso_zone == zone
        assert result.iso.iso_zone_would_be == "D"
        # The health line leads with the machine's letter, not ISO's.
        assert re.search(rf"Health\.?\s+Zone {zone} —", page1[state]), page1[state][:400]

    def test_the_case_the_ruling_exists_for(self, analyses, page1):
        """A plant limit that calls a Zone D machine acceptable still prints D.

        This is the whole product claim in one assertion: `custom_B` is the
        reading whose own limits say "acceptable" while ISO 20816-3 says
        "unacceptable", and page 1 has to carry both or the custom zone has
        hidden a real problem.
        """
        _, result = analyses["custom_B"]
        assert result.iso.iso_zone == "B" and result.iso.iso_zone_would_be == "D"
        text = page1["custom_B"]
        assert "Zone B — acceptable" in text
        assert "would give Zone D" in text


# ── (b) the machine ISO has no row for ───────────────────────────────────


class TestWhenIsoHasNoZoneForThisMachine:
    """A plant sets its own limit precisely for a machine the table misses."""

    def test_page_one_says_so_and_promises_nothing(self, analyses, page1):
        _, result = analyses["custom_no_iso_row"]
        assert result.iso.zone_basis == "custom"
        assert result.iso.iso_zone_would_be is None
        text = page1["custom_no_iso_row"]
        assert f"Judged against {BASIS_PHRASE} (5 / 8 / 12 mm/s RMS); " \
               "ISO 20816-3 has no zone for this machine" in text
        assert "would give" not in text

    def test_no_half_built_group_parenthetical(self, docs):
        """The machine has no group and no support, so neither may be cited."""
        md = docs["custom_no_iso_row"]["md"]
        health = next(l for l in md.splitlines() if l.startswith("**Health:**"))
        assert "Group" not in health, health
        assert "support" not in health, health


# ── (c) ISO is named only where ISO did the work ─────────────────────────


class TestIsoIsNeverSignedToAPlantNumber:
    """F-3. `per ISO 20816-3` over a number ISO did not set is the defect."""

    @pytest.mark.parametrize("state", ["custom_C", "custom_B", "custom_D",
                                       "custom_no_iso_row"])
    @pytest.mark.parametrize("fmt", ["md", "html"])
    def test_no_rendered_page_says_per_iso_20816_3(self, docs, state, fmt):
        rendered = docs[state][fmt]
        text = visible_text(rendered) if fmt == "html" else rendered
        assert "per ISO 20816-3" not in text, \
            next((l for l in text.splitlines() if "per ISO 20816-3" in l), "")

    @pytest.mark.parametrize("fmt", ["md", "html"])
    def test_the_iso_basis_still_says_it(self, docs, fmt):
        """The other half of the branch: nothing was removed from the ISO path."""
        rendered = docs["iso"][fmt]
        text = visible_text(rendered) if fmt == "html" else rendered
        assert "per ISO 20816-3" in text

    def test_the_machine_details_row_follows_the_basis(self, docs):
        """`_machine_rows` and `default_survey.md.j2` are pinned twins
        (tests/test_report_html.py), so the row moves in both or in neither."""
        assert re.search(r"\| Overall vibration \| 5\.20 mm/s \(y-axis\), Zone B \|",
                         docs["custom_B"]["md"])
        assert re.search(r"\| Overall vibration \| 5\.20 mm/s \(y-axis\), ISO Zone D \|",
                         docs["iso"]["md"])

    def test_the_escalation_trigger_does_not_either(self, analyses, docs):
        """`_next_boundary` reads th_ab/th_bc/th_cd — the plant's numbers here.

        Read against a Zone C reading on EACH basis, because Zone D has no next
        boundary and so prints no boundary clause at all: the ISO-basis `bpfo`
        fixture would have proved nothing either way.
        """
        assert analyses["custom_C"][1].iso.iso_zone == "C"
        assert analyses["iso_zone_c"][1].iso.iso_zone == "C"
        assert analyses["iso_zone_c"][1].iso.zone_basis == "iso"
        assert "the next zone boundary (8 mm/s)" in docs["custom_C"]["md"]
        assert "next ISO zone boundary" not in docs["custom_C"]["md"]
        assert "the next ISO zone boundary (4.5 mm/s)" in docs["iso_zone_c"]["md"]


# ── (d) a withheld reading gets neither ──────────────────────────────────


class TestAWithheldReadingPrintsNoZoneAtAll:
    """`mark_not_assessable` withholds `iso_zone_would_be` for a reason
    (SESSION_LIMITS1.md §5.2); the health line must not reopen that door.

    The zone-clause path was already correct. The WIRE-ONLY fallback was not:
    measured at the base commit, a withheld reading whose velocity was present
    but implausible rendered

        **Health:** ISO Zone not_assessable —  per ISO 20816-3: overall 5.20
        mm/s RMS on the y-axis (Group 2, rigid support).

    a zone verdict and a severity figure for a reading whose zone was
    deliberately withheld. That is REPORTFIX-1's, not this session's, and it is
    fixed here because item 1 requires this state to print no zone clause.
    """

    def test_neither_the_zone_nor_the_iso_clause(self, analyses, docs, page1):
        _, result = analyses["custom_withheld"]
        assert result.iso.iso_zone == "not_assessable"
        assert result.iso.zone_basis == "custom"   # provenance survives, §5.2
        assert result.iso.iso_zone_would_be is None
        md = docs["custom_withheld"]["md"]
        health = next(l for l in md.splitlines() if l.startswith("**Health:**"))
        assert health == ("**Health:** ISO severity not assessable on this reading — "
                          "overall vibration is not rated.")
        assert "not_assessable" not in page1["custom_withheld"]
        assert "would give" not in page1["custom_withheld"]
        assert BASIS_PHRASE not in page1["custom_withheld"]

    def test_the_gate_sentence_is_withheld_too(self, docs):
        """There is no health line to put it beneath, and no severity
        judgement for a gate to have governed."""
        assert GATE_SENTENCE not in docs["custom_withheld"]["md"]
        assert GATE_SENTENCE not in visible_text(docs["custom_withheld"]["html"])


# ── (e) F-2, said out loud ───────────────────────────────────────────────


class TestTheGateSentence:
    """SESSION_LIMITS1.md F-2: a plant limit moves a DIAGNOSIS, not just a
    letter — `one_x_severity_min_zone` is a zone letter and `pipeline.py`
    already feeds the resolved boundaries into `run_rca`."""

    @pytest.mark.parametrize("state", ["custom_C", "custom_B", "custom_D"])
    def test_present_on_every_custom_basis(self, docs, page1, state):
        assert GATE_SENTENCE in docs[state]["md"]
        assert GATE_SENTENCE in visible_text(docs[state]["html"])
        assert squash(GATE_SENTENCE) in page1[state]

    def test_absent_on_the_iso_basis(self, docs, page1):
        assert GATE_SENTENCE not in docs["iso"]["md"]
        assert GATE_SENTENCE not in visible_text(docs["iso"]["html"])
        assert "govern the 1" not in page1["iso"]

    def test_it_sits_beneath_the_health_line_not_inside_it(self, docs):
        """Item 4's word is *beneath*: a second claim, its own paragraph."""
        lines = docs["custom_B"]["md"].splitlines()
        health = next(i for i, l in enumerate(lines) if l.startswith("**Health:**"))
        gate = next(i for i, l in enumerate(lines) if GATE_SENTENCE in l)
        assert gate > health
        assert GATE_SENTENCE not in lines[health]


# ── (f)'s in-file half. The PROOF is the two-worktree byte diff recorded in
#     outputs/SESSION_LIMITS1B.md §; this is the cheap standing guard. ──────


class TestTheIsoPathDidNotMove:
    def test_the_health_line_is_the_shipped_sentence(self, docs):
        health = next(l for l in docs["iso"]["md"].splitlines()
                      if l.startswith("**Health:**"))
        assert health == (
            "**Health:** ISO Zone D — unacceptable per ISO 20816-3: overall 5.20 mm/s RMS "
            "on the y-axis, above the 4.5 mm/s Zone C/D boundary (Group 2, rigid support)."
        )

    def test_nothing_custom_leaks_onto_it(self, docs):
        for fmt in ("md", "html"):
            text = docs["iso"][fmt]
            assert BASIS_PHRASE not in text
            assert "would give Zone" not in text
            assert "Judged against" not in text


# ── (g) the consistency check ────────────────────────────────────────────


class TestCheckZoneLanguageOnACustomBasis:
    """Item 3. The check was a no-op for every assessable reading; it now
    refuses the two things a drafting model will do with a custom zone."""

    def test_the_reports_own_prose_passes_on_both_bases(self, docs, custom_result, iso_result):
        """The strongest form of "no false positives": the document this
        session emits must satisfy the check this session tightened."""
        assert check_zone_language(docs["custom_B"]["md"], custom_result) == []
        assert check_zone_language(docs["iso"]["md"], iso_result) == []

    def test_correct_custom_wording_passes(self, custom_result):
        text = ("**Health:** Zone B — acceptable per machine-specific limits: overall 5.20 "
                "mm/s RMS on the y-axis. Judged against machine-specific limits "
                "(5 / 8 / 12 mm/s RMS) — ISO 20816-3 Group 2 rigid support would give Zone D.")
        assert check_zone_language(text, custom_result) == []

    def test_per_iso_over_a_custom_number_is_refused(self, custom_result):
        text = "Zone B — acceptable per ISO 20816-3: overall 5.20 mm/s RMS on the y-axis."
        mismatches = check_zone_language(text, custom_result)
        assert mismatches, "a plant number attributed to the standard must not publish"
        assert any("machine-specific limits" in m for m in mismatches)

    def test_the_would_give_clause_is_not_read_as_a_verdict(self, custom_result):
        """It names D on a Zone B machine, legitimately, and must not trip
        the letter rule — this is the false positive that would degrade every
        correct custom report."""
        assert check_zone_language(
            "ISO 20816-3 Group 2 rigid support would give Zone D.", custom_result) == []

    @pytest.mark.parametrize("frame", [
        "The machine is in ISO Zone {}.",
        "Overall vibration places this machine in Zone {}.",
        "**Health:** Zone {} — acceptable.",
    ])
    def test_a_wrong_letter_fails_on_either_basis(self, custom_result, iso_result, frame):
        assert check_zone_language(frame.format("A"), custom_result), frame
        assert check_zone_language(frame.format("A"), iso_result), frame

    @pytest.mark.parametrize("state,letter", [("custom_B", "B"), ("iso", "D")])
    def test_the_right_letter_passes_on_either_basis(self, analyses, state, letter):
        result = analyses[state][1]
        assert check_zone_language(
            f"Overall vibration places this machine in Zone {letter}.", result) == []

    def test_the_not_assessable_rule_is_untouched(self, analyses):
        """Never weaken: rule 1 still refuses any zone language at all."""
        result = analyses["custom_withheld"][1]
        assert check_zone_language("The machine is in ISO Zone A.", result)
        assert check_zone_language("Operating in Zone C at this time.", result)
        assert check_zone_language("ISO severity unrated per ISO 20816.", result) == []

    @pytest.mark.parametrize("innocent", [
        "above the 4.5 mm/s Zone C/D boundary",
        "ISO zone at the time of measurement: D",
        "iso_zone_elevated: WARN — ISO zone D",
        "Re-measure before the scheduled window if the overall crosses the next zone boundary.",
        "Map both load zones and record which ring carries them before cleaning.",
    ])
    def test_the_scan_does_not_reach_non_verdict_prose(self, iso_result, innocent):
        """Precision, measured against strings the real Zone D render contains
        — read against that render's own result, which is the only pairing in
        which they are innocent. A false positive here costs a retry and then a
        DEGRADE on a correct report, which is worse than the miss it prevents.
        """
        assert check_zone_language(innocent, iso_result) == []

    def test_the_gate_line_is_still_held_to_the_letter(self, custom_result):
        """The one string above that is NOT basis-neutral, and must not be.

        `iso_zone_elevated: WARN — ISO zone C` is the quality gate naming the
        zone, and the gate agrees with the classifier on a custom basis
        (measured: a custom Zone C reading renders "ISO zone C"). Held to the
        letter like every other frame — a gate line naming D over a Zone B
        analysis is a contradiction inside one document, not innocent prose.
        """
        assert custom_result.iso.iso_zone == "B"
        assert check_zone_language("iso_zone_elevated: WARN — ISO zone B", custom_result) == []
        assert check_zone_language("iso_zone_elevated: WARN — ISO zone D", custom_result)
