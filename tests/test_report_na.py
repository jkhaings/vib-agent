"""Session REPORT-NA — the coverage roster: the report states "not assessed"
for every uncovered fault family; silence is no longer an option.

The roster lives in report/generate.py (COVERAGE_ROSTER), keyed row for row to
outputs/FAULT_COVERAGE_2026-08-31.md §3. Four properties are pinned here:

  * the section renders in BOTH documents (markdown and the v2 HTML page), on
    the deterministic, drafted and gate-fail paths, with identical wording;
  * every capability-absent family from the audit is named, statically;
  * input-absent rows appear EXACTLY when their field is absent — a supplied
    input means the family was assessed, and listing it anyway would be false
    modesty;
  * no not-assessed row ever names a family the analysis DID assess — the
    conjunction proofs run the recipes that actually commit belt, blade-pass
    and bent-shaft and assert their rows disappear.

Plus the D-14 caveat (operator ruling, ROADMAP_2026-08-31.md): a committed
bearing_cage finding whose primary peak sits between 0.38× and 0.48× of shaft
speed prints "cage defect or oil whirl — not distinguished by this analysis."
Session SUBHARM removes that row.
"""

from __future__ import annotations

import re

import pytest

from vib_agent.config import load_thresholds
from vib_agent.models import Finding
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import (
    coverage_context,
    render_html,
    render_markdown,
    write_drafted_report,
)
from vib_agent.synth.generator import make_case

HEADING = "## Coverage — what this analysis did not assess"

#: The capability-absent families, straight off FAULT_COVERAGE_2026-08-31.md §3.
#: Hardcoded here rather than imported from the roster, so a roster edit that
#: silently drops an audited family fails this file instead of passing it.
NO_DETECTOR_FAMILIES = (
    "Imbalance — couple (sub-type)",
    "Imbalance — overhung rotor (sub-type)",
    "Sleeve-bearing oil whirl / whip",
    "Rotor bar faults",
    "Stator faults (2× line frequency)",
    "Eccentricity",
    "VFD 2× line-frequency faults",
    "VFD carrier-frequency artifacts",
    "Gearmesh wear / gear sidebands / hunting tooth",
    "Cavitation / recirculation (broadband)",
    "Soft foot",
    "Beat frequencies",
)

#: Which committed pdm_core fault ids each input-absent row would cover — the
#: disjointness oracle for "no roster row names a family the analysis DID
#: assess".
INPUT_ABSENT_COVERS = {
    "Rolling-element bearing faults (BPFO / BPFI / BSF / FTF)": {
        "bearing_outer_race", "bearing_inner_race", "bearing_ball_spin", "bearing_cage",
    },
    "Belt / pulley faults": {"belt_fault"},
    "Vane / blade pass": {"elevated_blade_pass"},
    "Bent shaft": {"bent_shaft"},
}


@pytest.fixture
def thresholds() -> dict:
    """`route`, named explicitly — the product profile every upload runs
    (the test_report_html precedent; the session-wide fixture resolves
    `active_profile`, which is a default, not a decision)."""
    return load_thresholds("route")


def _analyse(name: str, iso_table, thresholds, rules):
    case = make_case(name, iso_table=iso_table, thresholds=thresholds, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    return case, result


def _md_section(md: str) -> str:
    m = re.search(rf"{re.escape(HEADING)}\n(.*?)(?=\n## )", md, re.S)
    assert m, "the coverage section is missing from the markdown report"
    return m.group(1)


def _visible(html: str) -> str:
    text = re.sub(r"<style\b.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    from html import unescape

    return re.sub(r"\s+", " ", unescape(text)).strip()


def _plain(line: str) -> str:
    """A roster line reduced to the words a reader sees (markers stripped,
    whitespace collapsed) — the same normalisation on both documents."""
    return re.sub(r"\s+", " ", re.sub(r"[*`]", "", line)).strip()


# ── the section renders, everywhere ──────────────────────────────────────


class TestSectionRenders:
    def test_markdown_carries_the_section_after_limitations(
        self, iso_table, thresholds, rules
    ):
        case, result = _analyse("bpfo", iso_table, thresholds, rules)
        md = render_markdown(result, case.machine)
        assert HEADING in md
        assert md.index("## Limitations & Confidence Notes") < md.index(HEADING)
        assert md.index(HEADING) < md.index("## Review & Approval")

    def test_html_carries_the_section_between_limitations_and_data_quality(
        self, iso_table, thresholds, rules
    ):
        case, result = _analyse("bpfo", iso_table, thresholds, rules)
        html = render_html(result, case.machine)
        text = _visible(html)
        title = "Coverage — what this analysis did not assess"
        assert title in text
        limitations = text.index("Limitations & confidence notes")
        coverage = text.index(title)
        data_quality = text.index("Data quality")
        assert limitations < coverage < data_quality

    def test_every_roster_line_is_identical_in_markdown_and_html(
        self, iso_table, thresholds, rules
    ):
        case, result = _analyse("bpfo", iso_table, thresholds, rules)
        coverage = coverage_context(result, case.machine)
        assert coverage["rows"], "the roster rendered no rows at all"
        md = render_markdown(result, case.machine)
        html_text = _visible(render_html(result, case.machine))
        md_text = _plain(md)
        for row in coverage["rows"]:
            line = _plain(row["line"])
            assert line in md_text, f"roster line missing from markdown: {line!r}"
            assert line in html_text, f"roster line missing from HTML: {line!r}"

    def test_gate_fail_report_still_carries_the_section(
        self, iso_table, thresholds, rules
    ):
        case, result = _analyse("machine_off", iso_table, thresholds, rules)
        assert result.quality_gate.overall == "fail", "fixture no longer fails the gate"
        md = render_markdown(result, case.machine)
        assert HEADING in md
        assert "NO fault family was assessed on this reading" in md
        # and the static roster still tells the analyst what the tool cannot
        # do even on a passing reading
        assert "Sleeve-bearing oil whirl / whip" in _md_section(md)


# ── every capability-absent family from the audit is named ───────────────


class TestCapabilityAbsentFamilies:
    def test_every_no_detector_family_is_named_with_its_reason_class(
        self, iso_table, thresholds, rules
    ):
        case, result = _analyse("bpfo", iso_table, thresholds, rules)
        section = _md_section(render_markdown(result, case.machine))
        for family in NO_DETECTOR_FAMILIES:
            line = f"**{family}** — not assessed — no detector for this family."
            assert line in section, f"capability-absent family missing: {family!r}"


# ── input-absent rows appear exactly when their field is absent ──────────


class TestInputAbsentRows:
    @pytest.mark.parametrize(
        "absent_case,present_case,family,field_token",
        [
            # Session INTAKE-HONEST: the field tokens moved from model-speak
            # (`machine.bearing`) to the upload form's own words — the row now
            # tells the analyst what to type, or that the form cannot collect
            # the input yet. The guarantee (row appears iff the input is
            # absent) is unchanged.
            ("healthy", "bpfo",
             "Rolling-element bearing faults (BPFO / BPFI / BSF / FTF)",
             "bearing geometry — the “Bearing model” field (`bearing_model`, "
             "under More options) on the upload form"),
            # Session GEOM-A: these three rows said "the upload form cannot
            # collect this yet" because it could not. It can now — the form
            # collects the blade count, the coupling state and the pulley
            # geometry the belt frequency is derived from — so the rows name
            # their fields in the form's own words, like every other row.
            ("healthy", "belt_fault", "Belt / pulley faults",
             "the belt drive geometry — the “Drive pulley Ø”, “Driven pulley Ø” and "
             "“Centre distance” fields (under More options) on the upload form, from "
             "which the belt frequency is computed"),
            ("healthy", "blade_pass", "Vane / blade pass",
             "the blade / vane count — the “Blade / vane count” field "
             "(under More options) on the upload form"),
            ("healthy", "bent_shaft", "Bent shaft",
             "the coupling state — the “Coupling” field (under More options) on the "
             "upload form, set to “Not coupled”"),
        ],
    )
    def test_row_appears_iff_its_field_is_absent(
        self, absent_case, present_case, family, field_token, iso_table, thresholds, rules
    ):
        case_a, result_a = _analyse(absent_case, iso_table, thresholds, rules)
        section_a = _md_section(render_markdown(result_a, case_a.machine))
        row = f"**{family}** — not assessed — requires an input not provided: {field_token}"
        assert row in section_a, f"{absent_case}: input-absent row missing for {family!r}"

        case_p, result_p = _analyse(present_case, iso_table, thresholds, rules)
        section_p = _md_section(render_markdown(result_p, case_p.machine))
        assert row not in section_p, (
            f"{present_case}: the {family!r} input was supplied, yet the report claims "
            f"it was not"
        )


# ── no roster row ever names a family the analysis DID assess ────────────


class TestNoAssessedFamilyListed:
    @pytest.mark.parametrize(
        "name", ["bpfo", "imbalance", "angular_misalignment", "looseness",
                 "belt_fault", "blade_pass", "bent_shaft"]
    )
    def test_committed_families_never_appear_as_not_assessed(
        self, name, iso_table, thresholds, rules
    ):
        """The conjunction proof: the recipes that commit belt_fault,
        elevated_blade_pass and bent_shaft (fields supplied) must show NO
        not-assessed row covering the fault they committed. Caveat rows are
        exempt by design — a caveat names an assessed family's blind spot."""
        case, result = _analyse(name, iso_table, thresholds, rules)
        committed = {f.fault for f in result.findings}
        assert committed != {"no_significant_findings"}, f"{name}: fixture commits nothing"
        for row in coverage_context(result, case.machine)["rows"]:
            if row["kind"] == "caveat":
                continue
            covers = INPUT_ABSENT_COVERS.get(row["family"], set())
            assert not (covers & committed), (
                f"{name}: roster claims {row['family']!r} was not assessed, but the "
                f"analysis committed {sorted(covers & committed)}"
            )


# ── caveats on assessed families ─────────────────────────────────────────


class TestBearingAxialCaveat:
    def test_radial_only_caveat_renders_when_the_bearing_screen_ran(
        self, iso_table, thresholds, rules
    ):
        case, result = _analyse("bpfo", iso_table, thresholds, rules)
        section = _md_section(render_markdown(result, case.machine))
        assert "Bearing fault matching reads radial axes only" in section
        # worded as a caveat on an assessed family, never as "not assessed"
        assert ("**Rolling-element bearing faults — axial presentation** — caveat."
                in section)

    def test_no_radial_caveat_without_a_bearing_screen(
        self, iso_table, thresholds, rules
    ):
        case, result = _analyse("healthy", iso_table, thresholds, rules)
        section = _md_section(render_markdown(result, case.machine))
        assert "axial presentation" not in section


class TestBendLocationCaveat:
    """Session GEOM-B. `bent_shaft` commits on a dominant axial 1×, which is the
    signature of a mid-span bend AND of a bend at the shaft end. The detector
    used to assert "center bend likely" from a predicate that was the branch
    guard's own call; it now asserts no sub-type, and this row is what stops the
    silence reading as "the location was determined and simply not printed"."""

    REQUIRED = "mid-span versus at the shaft end — is not assessed"

    def test_the_caveat_renders_in_both_documents_when_bent_shaft_commits(
        self, iso_table, thresholds, rules
    ):
        case, result = _analyse("bent_shaft", iso_table, thresholds, rules)
        assert "bent_shaft" in {f.fault for f in result.findings}
        assert self.REQUIRED in _md_section(render_markdown(result, case.machine))
        assert self.REQUIRED in _visible(render_html(result, case.machine))
        # a caveat on an assessed family, never a "not assessed" row
        assert "**Bent shaft — bend location** — caveat." in _md_section(
            render_markdown(result, case.machine)
        )

    def test_no_caveat_where_no_bent_shaft_was_committed(
        self, iso_table, thresholds, rules
    ):
        case, result = _analyse("imbalance", iso_table, thresholds, rules)
        assert "bent_shaft" not in {f.fault for f in result.findings}
        assert self.REQUIRED not in render_markdown(result, case.machine)

    def test_the_finding_and_the_roster_agree(self, iso_table, thresholds, rules):
        """One fact, two places: the finding's own reason and the roster row
        must not be able to drift into disagreeing about what was determined."""
        case, result = _analyse("bent_shaft", iso_table, thresholds, rules)
        reason = next(f for f in result.findings if f.fault == "bent_shaft").reason
        assert "not determined" in reason
        assert "center bend" not in reason.lower()
        assert self.REQUIRED in _md_section(render_markdown(result, case.machine))


class TestCageWhirlCaveat:
    """Operator ruling D-14. Removed by Session SUBHARM."""

    REQUIRED = "cage defect or oil whirl — not distinguished by this analysis."

    def _with_cage(self, iso_table, thresholds, rules, *, order: float):
        case, result = _analyse("bpfo", iso_table, thresholds, rules)
        shaft = result.rca.shaft_freq_hz
        assert shaft > 0
        cage = Finding(
            fault="bearing_cage",
            severity=result.findings[0].severity,
            confidence="medium",
            reason="test cage finding",
            evidence={"freq_hz": shaft * order, "expected_hz": shaft * 0.41,
                      "axis": "y", "confidence_factors": []},
        )
        return case, result.model_copy(update={"findings": [cage]})

    def test_cage_peak_in_the_whirl_band_prints_the_caveat_in_both_documents(
        self, iso_table, thresholds, rules
    ):
        case, result = self._with_cage(iso_table, thresholds, rules, order=0.43)
        md = render_markdown(result, case.machine)
        assert self.REQUIRED in _md_section(md)
        assert self.REQUIRED in _visible(render_html(result, case.machine))

    def test_band_edges_are_inclusive_and_outside_is_silent(
        self, iso_table, thresholds, rules
    ):
        for order, expected in ((0.38, True), (0.48, True), (0.30, False), (0.55, False)):
            case, result = self._with_cage(iso_table, thresholds, rules, order=order)
            section = _md_section(render_markdown(result, case.machine))
            assert (self.REQUIRED in section) is expected, f"order {order}"

    def test_no_cage_finding_means_no_caveat(self, iso_table, thresholds, rules):
        case, result = _analyse("bpfo", iso_table, thresholds, rules)
        assert "bearing_cage" not in {f.fault for f in result.findings}
        assert self.REQUIRED not in render_markdown(result, case.machine)


# ── the drafted path: deterministic splice, never model-written ──────────


class TestDraftedPath:
    NARRATIVE = "# Vibration Survey Report — Synthetic Compressor 01\n\nJust a narrative.\n"

    def test_the_reference_report_handed_to_the_model_omits_the_section(
        self, iso_table, thresholds, rules
    ):
        """What keeps the drafted section deterministic: the model is never
        shown the roster (no consistency check pins coverage language), so it
        never writes one — the splice below is the only copy."""
        case, result = _analyse("bpfo", iso_table, thresholds, rules)
        reference = render_markdown(result, case.machine, include_causes=False)
        assert HEADING not in reference

    def test_drafted_markdown_carries_the_section_verbatim(
        self, tmp_path, iso_table, thresholds, rules
    ):
        pytest.importorskip("matplotlib")
        case, result = _analyse("bpfo", iso_table, thresholds, rules)
        written = write_drafted_report(
            self.NARRATIVE, result, tmp_path, machine=case.machine, case=case,
            thresholds=thresholds, figures=True,
        )
        drafted = written["markdown"].read_text()
        deterministic = render_markdown(result, case.machine)
        assert _md_section(drafted) == _md_section(deterministic), (
            "the drafted report's coverage section differs from the deterministic one — "
            "the splice is no longer verbatim"
        )

    def test_drafted_html_carries_the_section(self, tmp_path, iso_table, thresholds, rules):
        pytest.importorskip("markdown")
        pytest.importorskip("matplotlib")
        from vib_agent.report.generate import render_drafted_html

        case, result = _analyse("bpfo", iso_table, thresholds, rules)
        html = render_drafted_html(
            self.NARRATIVE, result, case.machine, case=case, thresholds=thresholds,
        )
        assert html is not None
        text = _visible(html)
        assert "Coverage — what this analysis did not assess" in text
        assert "Sleeve-bearing oil whirl / whip" in text
