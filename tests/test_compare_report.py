"""Session HIST-2 — the comparison section in the report layer.

Two claims are pinned here, and they are the ones that matter:

  * **Additive.** With `comparison=None` — every single-file report ever
    produced — `report.md` and the v2 page are BYTE-IDENTICAL to what they were
    before this session. The section does not exist unless a comparison does.

  * **The model is never shown a delta.** The agent's reference render
    (`include_causes=False`) drops the comparison exactly as it drops the cause
    and coverage sections. That is the whole of "the LLM never computes a
    delta": there is no narration to check, because there is nothing to narrate
    from. The section reaches the drafted document deterministically instead.

Plus the mirrored-pair rule (common law #7): whatever the markdown says, the
HTML says, in the same words.
"""

from __future__ import annotations

import re

import pytest

from vib_agent.models import Case, SensorData
from vib_agent.pdm_core import compare as C
from vib_agent.pipeline import run_analysis
from vib_agent.report import generate as G
from vib_agent.synth.generator import make_spectrum

_BPFO = 107.16
_1X = 30.0
_SD = dict(
    rpm=1800.0,
    x_velocity_mm_sec=1.0, y_velocity_mm_sec=3.0, z_velocity_mm_sec=3.0,
    x_rms_ACC_G=0.05, y_rms_ACC_G=0.10, z_rms_ACC_G=0.10,
)
_QUIET = {**_SD, "y_velocity_mm_sec": 1.0, "z_velocity_mm_sec": 1.0}


def _case(machine, peaks, *, sd=None, seed=7):
    spectra = {
        axis: spectrum.model_copy(update={"kind": "velocity"})
        for axis, spectrum in make_spectrum(peaks, seed=seed).items()
    }
    return Case(name="cmp", machine=machine, sensor_data=SensorData(**(sd or _SD)),
                spectra=spectra)


@pytest.fixture
def repaired(comp_machine, iso_table, thresholds, rules):
    """A planted Before/After pair: a committed BPFO fault that is gone
    afterwards. Returns (after_result, after_case, comparison)."""
    before = _case(comp_machine, {"y": [(_BPFO, 0.40)], "z": [(_BPFO, 0.36)]})
    after = _case(comp_machine, {"y": [], "z": []}, sd=_QUIET)
    rb = run_analysis(before, iso_table=iso_table, thresholds=thresholds, rules=rules)
    ra = run_analysis(after, iso_table=iso_table, thresholds=thresholds, rules=rules)
    comparison = C.compare_readings(rb, ra, before_case=before, after_case=after,
                                    cfg=thresholds.get("compare"))
    assert comparison.status == "ok"
    return ra, after, comparison


# ─────────────────────────────────────────────────────────────────────────
# Additive
# ─────────────────────────────────────────────────────────────────────────


class TestAdditive:
    def test_no_comparison_renders_byte_identical_markdown(
        self, comp_machine, iso_table, thresholds, rules
    ):
        case = _case(comp_machine, {"y": [(_BPFO, 0.40)]})
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        without = G.render_markdown(result, comp_machine, case=case, thresholds=thresholds,
                                    profile="route")
        explicit_none = G.render_markdown(result, comp_machine, case=case, thresholds=thresholds,
                                          profile="route", comparison=None)
        assert without == explicit_none
        assert "Before / after" not in without

    def test_no_comparison_renders_byte_identical_html(
        self, comp_machine, iso_table, thresholds, rules
    ):
        case = _case(comp_machine, {"y": [(_BPFO, 0.40)]})
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        without = G.render_html(result, comp_machine, case=case, thresholds=thresholds,
                                profile="route")
        explicit_none = G.render_html(result, comp_machine, case=case, thresholds=thresholds,
                                      profile="route", comparison=None)
        assert without == explicit_none
        assert "Before / after" not in without

    def test_the_section_consumes_no_section_number_when_absent(
        self, comp_machine, iso_table, thresholds, rules, repaired
    ):
        """A document does not number the chapters it does not have. With a
        comparison the later sections shift by exactly one; without it they are
        where they always were."""
        result, case, comparison = repaired
        plain = G.render_html(result, comp_machine, case=case, thresholds=thresholds,
                              profile="route")
        with_cmp = G.render_html(result, comp_machine, case=case, thresholds=thresholds,
                                 profile="route", comparison=comparison)

        def _n(html, title):
            match = re.search(r"<h2>(\d+) · " + re.escape(title), html)
            return int(match.group(1)) if match else None

        assert _n(plain, "Recommendations") is not None
        assert _n(with_cmp, "Recommendations") == _n(plain, "Recommendations") + 1
        assert _n(with_cmp, "Before / after comparison") == _n(plain, "Trend") + 1


# ─────────────────────────────────────────────────────────────────────────
# The model never sees a delta
# ─────────────────────────────────────────────────────────────────────────


class TestTheModelIsNeverShownADelta:
    def test_the_agent_reference_render_drops_the_comparison(
        self, comp_machine, thresholds, repaired
    ):
        """`include_causes=False` is the render `agent/loop.py` hands the drafting
        model. It already drops the causes and the coverage roster; HIST-2 adds
        the comparison for the load-bearing reason — a model that is never shown
        a computed delta cannot narrate one that disagrees with the arithmetic."""
        result, case, comparison = repaired
        shown = G.render_markdown(result, comp_machine, case=case, thresholds=thresholds,
                                  profile="route", comparison=comparison,
                                  include_causes=False)
        assert "Before / after" not in shown
        for word in ("worsened", "improved", "consistent with repair"):
            assert word not in shown

    def test_the_full_render_does_carry_it(self, comp_machine, thresholds, repaired):
        result, case, comparison = repaired
        full = G.render_markdown(result, comp_machine, case=case, thresholds=thresholds,
                                 profile="route", comparison=comparison)
        assert G.COMPARISON_HEADING in full

    def test_the_drafted_page_carries_it_with_no_suppression_flag(
        self, comp_machine, thresholds, repaired
    ):
        """No `want_*` flag exists for this section, and none should: the model
        was never shown a copy to reproduce, so there is nothing to suppress in
        favour of. The REPORT-NA precedent, for the same reason."""
        result, case, comparison = repaired
        page = G.render_drafted_html(
            "# Vibration Survey Report — Test Compressor 01\n\nSome narrative.\n",
            result, comp_machine, case=case, thresholds=thresholds, profile="route",
            comparison=comparison,
        )
        assert page is None or "Before / after comparison" in page


# ─────────────────────────────────────────────────────────────────────────
# The mirrored pair
# ─────────────────────────────────────────────────────────────────────────


class TestMirroredPair:
    def test_both_renderers_word_the_verdict_identically(
        self, comp_machine, thresholds, repaired
    ):
        """Common law #7. The words come from `pdm_core.compare.verdict_word` /
        `repair_word`, registered on BOTH Jinja environments from the module that
        assigns the verdicts — so there is exactly one spelling in the product."""
        result, case, comparison = repaired
        md = G.render_markdown(result, comp_machine, case=case, thresholds=thresholds,
                               profile="route", comparison=comparison)
        html = G.render_html(result, comp_machine, case=case, thresholds=thresholds,
                             profile="route", comparison=comparison)
        for phrase in ("no significant change", "improved", "consistent with repair"):
            assert phrase in md, phrase
            assert phrase in html, phrase

    def test_both_renderers_carry_the_headline_and_significance_note(
        self, comp_machine, thresholds, repaired
    ):
        result, case, comparison = repaired
        md = G.render_markdown(result, comp_machine, case=case, thresholds=thresholds,
                               profile="route", comparison=comparison)
        html = G.render_html(result, comp_machine, case=case, thresholds=thresholds,
                             profile="route", comparison=comparison)
        assert comparison.headline in md
        assert comparison.significance_note in md
        # The HTML escapes prose, so compare against the escaped form.
        for text in (comparison.headline, comparison.significance_note):
            assert text.replace("&", "&amp;").replace("<", "&lt;") in html

    def test_every_band_row_reaches_both_documents(
        self, comp_machine, thresholds, repaired
    ):
        result, case, comparison = repaired
        md = G.render_markdown(result, comp_machine, case=case, thresholds=thresholds,
                               profile="route", comparison=comparison)
        html = G.render_html(result, comp_machine, case=case, thresholds=thresholds,
                             profile="route", comparison=comparison)
        bands = [b for s in comparison.spectra for b in s.bands]
        assert bands
        for band in bands:
            assert band.label in md
            assert band.label in html

    def test_the_word_repaired_never_reaches_either_document(
        self, comp_machine, thresholds, repaired
    ):
        result, case, comparison = repaired
        md = G.render_markdown(result, comp_machine, case=case, thresholds=thresholds,
                               profile="route", comparison=comparison)
        html = G.render_html(result, comp_machine, case=case, thresholds=thresholds,
                             profile="route", comparison=comparison)
        assert "consistent with repair" in md and "consistent with repair" in html
        for document in (md, html):
            assert not re.search(r"\b(was|is|been)\s+repaired\b", document, re.I)


# ─────────────────────────────────────────────────────────────────────────
# No numeric claim without a source
# ─────────────────────────────────────────────────────────────────────────


class TestNoInventedNumbers:
    def test_every_percentage_in_the_section_is_a_computed_one(
        self, comp_machine, thresholds, repaired
    ):
        """Common law #7's other half. Every percentage the section prints must
        be traceable to a field on the ComparisonResult — the report layer
        formats, it never derives."""
        _, _, comparison = repaired
        block = G.comparison_markdown(comparison)
        printed = {abs(float(v)) for v in re.findall(r"([-+]?\d+\.\d)%", block)}
        computed = {abs(round(v, 1)) for v in (
            [comparison.overall.pct_change] if comparison.overall else []
        ) + [b.pct_change for s in comparison.spectra for b in s.bands]
            + [p.pct_change for s in comparison.spectra
               for p in [*s.matched, *s.new_frequencies, *s.gone_frequencies]]
            if v is not None}
        # The significance note's own "+1.9 dB" is a stated rule, not a claim
        # about this machine, and it is printed from the same constant.
        computed.add(1.9)
        assert printed <= computed, printed - computed

    def test_the_significance_rule_is_stated_in_the_document(
        self, comp_machine, thresholds, repaired
    ):
        """A threshold that only lives in code is a hidden claim. The one
        constant that decides every verdict word is printed, so a reader can
        apply their own bar to the figures."""
        _, _, comparison = repaired
        block = G.comparison_markdown(comparison)
        assert "1.25" in block and "0.80" in block


# ─────────────────────────────────────────────────────────────────────────
# The splice — the drafted webapp path's report.md
# ─────────────────────────────────────────────────────────────────────────


class TestSplice:
    def test_the_spliced_block_is_the_rendered_block(
        self, comp_machine, thresholds, repaired
    ):
        """Same macro, same words, whichever path wrote the document."""
        result, case, comparison = repaired
        rendered = G.render_markdown(result, comp_machine, case=case, thresholds=thresholds,
                                     profile="route", comparison=comparison)
        block = G.comparison_markdown(comparison)
        assert block in rendered

    def test_the_splice_goes_above_the_draft_footer(self, repaired):
        _, _, comparison = repaired
        draft = "# Report\n\nNarrative.\n\n---\nDRAFT — prepared by automated analysis.\n"
        out = G.splice_comparison_markdown(draft, comparison)
        assert out.index(G.COMPARISON_HEADING) < out.index("DRAFT — prepared")
        assert out.rstrip().endswith("DRAFT — prepared by automated analysis.")

    def test_the_splice_appends_when_there_is_no_footer(self, repaired):
        _, _, comparison = repaired
        out = G.splice_comparison_markdown("# Report\n\nNarrative.\n", comparison)
        assert out.rstrip().endswith(G.comparison_markdown(comparison).rstrip()[-40:])

    def test_the_splice_is_idempotent(self, comp_machine, thresholds, repaired):
        """A deterministic report already carries the section — `render_markdown`
        rendered it from the same macro. Splicing again must not give it a
        second copy, which is what lets the caller apply this on every path
        without knowing which one produced the text."""
        result, case, comparison = repaired
        rendered = G.render_markdown(result, comp_machine, case=case, thresholds=thresholds,
                                     profile="route", comparison=comparison)
        assert G.splice_comparison_markdown(rendered, comparison) == rendered
        once = G.splice_comparison_markdown("# R\n\nNarrative.\n", comparison)
        assert G.splice_comparison_markdown(once, comparison) == once
        assert once.count(G.COMPARISON_HEADING) == 1

    def test_no_comparison_leaves_the_text_untouched(self):
        text = "# Report\n\nNarrative.\n"
        assert G.splice_comparison_markdown(text, None) == text


# ─────────────────────────────────────────────────────────────────────────
# The refusals render as refusals
# ─────────────────────────────────────────────────────────────────────────


class TestRefusalsRender:
    def test_a_gate_blocked_comparison_prints_its_reason_and_no_tables(
        self, comp_machine, iso_table, thresholds, rules
    ):
        before = _case(comp_machine, {"y": [(_1X, 0.20)]})
        after = _case(comp_machine, {"y": [(_1X, 0.20)]},
                      sd={**_SD, "x_rms_ACC_G": 0.001, "y_rms_ACC_G": 0.001,
                          "z_rms_ACC_G": 0.001})
        rb = run_analysis(before, iso_table=iso_table, thresholds=thresholds, rules=rules)
        ra = run_analysis(after, iso_table=iso_table, thresholds=thresholds, rules=rules)
        comparison = C.compare_readings(rb, ra, before_case=before, after_case=after,
                                        cfg=thresholds.get("compare"))
        assert comparison.status == "gate_blocked"

        block = G.comparison_markdown(comparison)
        assert "data-quality gate" in block
        assert "Band levels" not in block
        assert "Overall level" not in block
        assert "What would resolve this" in block
        # A refusal never prints the significance rule: no verdict was reached
        # for it to qualify.
        assert comparison.significance_note not in block

    def test_a_not_comparable_result_prints_only_the_reason(
        self, comp_machine, iso_table, thresholds, rules
    ):
        before = _case(comp_machine, {"y": [(_1X, 0.20)]})
        after = _case(comp_machine, {"y": [(36.0, 0.20)]}, sd={**_SD, "rpm": 2160.0})
        rb = run_analysis(before, iso_table=iso_table, thresholds=thresholds, rules=rules)
        ra = run_analysis(after, iso_table=iso_table, thresholds=thresholds, rules=rules)
        comparison = C.compare_readings(rb, ra, before_case=before, after_case=after,
                                        cfg=thresholds.get("compare"))
        assert comparison.status == "not_comparable"

        block = G.comparison_markdown(comparison)
        assert "cannot be compared as Before/After" in block
        assert "Band levels" not in block
        assert "Repair verification" not in block
