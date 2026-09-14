"""Session REPORT-2 (item 4) — the diagnosis paragraph in an analyst's order.

A CAT analyst reviewing the outreach sample wrote the diagnosis as: the overall
level is in the unacceptable zone per ISO 20816-3; the velocity signature is
dominated by 107.25 Hz (3.57× RPM, BPFO) and its harmonics, matching the 6206
bearing, which indicates bearing deterioration. Zone and level first, then the
dominant frequency with its harmonics, then the bearing, then the conclusion.

The deterministic Executive Summary and the lead paragraph of every committed
finding now follow that order (`generate._executive_summary`, `generate.diagnosis_lead`).
Template and deterministic text only; nothing here is drafted.

The one ruled exception: the Executive Summary names frequencies in Hz WITHOUT the
shaft order, because agent/consistency.py::check_numeric_quotes (read-only this
session) refuses any decimal in the drafted summary that is not in the
AnalysisResult, and an order ratio is not. The lead paragraph in the Diagnosis
section, which that check does not scan, carries the orders. Both halves of that
ruling are pinned here, the second by running the real check on a draft that
copies the deterministic summary word for word.
"""

from __future__ import annotations

import re

import pytest

from tests.test_report_html import visible_text
from vib_agent.agent.consistency import check_numeric_quotes, computed_numbers
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report import generate
from vib_agent.report.generate import (
    _executive_summary,
    _zone_clause,
    diagnosis_lead,
    render_html,
    render_markdown,
)
from vib_agent.synth.generator import make_case

_DECIMAL_ORDER = re.compile(r"\d\.\d+×")
_DECIMAL = re.compile(r"(?<![\w.])(\d{1,7}\.\d{1,6})(?![\d.])")

SAMPLE_SUMMARY = (
    "Synthetic Compressor 01 is in ISO Zone D — unacceptable per ISO 20816-3: overall 5.20 mm/s "
    "RMS on the y-axis, above the 4.5 mm/s Zone C/D boundary (Group 2, rigid support). "
    # Session REPORT-3 (item 4) added "(3.58×)" and "(3.57×)" here. REPORT-2 had
    # to leave them out of this ONE paragraph -- F-1 -- because the numeric-quote
    # check would refuse a model that transcribed them. It no longer does.
    "The envelope spectrum (as supplied) is dominated by 107.25 Hz (3.58×) on the y-axis, "
    "with its 2× harmonic present, matching the computed BPFO of bearing 6206 at "
    "107.03 Hz (3.57×). "
    "The committed diagnosis is Bearing outer-race fault (BPFO) (high confidence)."
)
SAMPLE_LEAD = (
    "Overall vibration places this machine in ISO Zone D — unacceptable per ISO 20816-3: overall "
    "5.20 mm/s RMS on the y-axis, above the 4.5 mm/s Zone C/D boundary (Group 2, rigid support). "
    "The envelope spectrum (as supplied) is dominated by 107.25 Hz (3.58×) on the y-axis, with "
    "its 2× harmonic present, matching the computed BPFO of bearing 6206 at 107.03 Hz (3.57×). "
    "Committed diagnosis: Bearing outer-race fault (BPFO), high confidence."
)


@pytest.fixture(scope="module")
def cfg() -> dict:
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
    }


def _case(cfg, name="bpfo"):
    return make_case(name, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1)


def _analyse(case, cfg):
    return run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                        rules=cfg["rules"])


def in_analyst_order(summary: str) -> bool:
    """The pin: zone clause, then the dominant frequency, then the bearing, then the
    committed call — each strictly after the one before."""
    marks = ("ISO Zone", "dominated by", "bearing", "The committed diagnosis is")
    positions = [summary.find(m) for m in marks]
    return all(p >= 0 for p in positions) and positions == sorted(positions)


class TestTheExecutiveSummaryOnTheSample:
    def test_word_for_word(self, cfg):
        case = _case(cfg)
        assert _executive_summary(_analyse(case, cfg), case) == SAMPLE_SUMMARY

    def test_in_the_analysts_order(self, cfg):
        assert in_analyst_order(SAMPLE_SUMMARY)

    def test_reaches_both_documents(self, cfg):
        case = _case(cfg)
        result = _analyse(case, cfg)
        md = render_markdown(result, case.machine, case=case, thresholds=cfg["thresholds"], profile="route")
        html = render_html(result, case.machine, case=case, thresholds=cfg["thresholds"], profile="route")
        assert SAMPLE_SUMMARY in md
        assert SAMPLE_SUMMARY in visible_text(html)

    def test_the_committed_sentence_kept_its_exact_wording(self, cfg):
        """tests/test_session_f2.py pins it; the reorder moved sentences, not words."""
        assert SAMPLE_SUMMARY.endswith(
            "The committed diagnosis is Bearing outer-race fault (BPFO) (high confidence).")


class TestTheRuledExceptionIsClosed:
    """Orders EVERYWHERE, the Executive Summary included — Session REPORT-3 item 4.

    This class used to pin the opposite, and deliberately: REPORT-2 ruled the
    summary an exception because `check_numeric_quotes` would refuse a model
    that copied an order ratio out of it, retry once, and degrade the
    shop-window sample on the second failure. That was measured, not assumed,
    which is why the refusal had a test of its own.

    `computed_numbers` now derives each fault frequency's order with the
    renderer's own `round(f / shaft, 2)`, so the ratio is a value the analysis
    holds. Both directions are pinned below: the order IS in the summary, and a
    draft that copies the summary verbatim is NOT refused. The negative control
    moved with it — an order the analysis did not compute is still refused.
    """

    def test_the_summary_now_carries_the_order(self, cfg):
        case = _case(cfg)
        summary = _executive_summary(_analyse(case, cfg), case)
        assert _DECIMAL_ORDER.search(summary), summary
        assert "107.25 Hz (3.58×)" in summary
        assert "107.03 Hz (3.57×)" in summary

    def test_every_decimal_in_the_summary_is_a_computed_number(self, cfg):
        case = _case(cfg)
        result = _analyse(case, cfg)
        allowed = computed_numbers(result)
        for quoted in _DECIMAL.findall(_executive_summary(result, case)):
            value = float(quoted)
            assert any(abs(value - c) <= 0.5 * 10 ** -len(quoted.split(".")[1]) + 1e-9 for c in allowed), quoted

    def test_a_draft_that_copies_the_summary_passes_the_numeric_quote_check(self, cfg):
        """The real check, on the real sentence: a model that transcribes the
        deterministic summary verbatim is not refused."""
        case = _case(cfg)
        result = _analyse(case, cfg)
        draft = (
            "# Vibration Survey Report — Synthetic Compressor 01\n\n## Executive Summary\n\n"
            f"{_executive_summary(result, case)}\n\n## Diagnosis\n\nAs above.\n"
        )
        assert check_numeric_quotes(draft, result) == []

    def test_an_order_the_analysis_did_not_compute_is_still_refused(self, cfg):
        """The negative control, in its new place. Widening the whitelist must
        not have opened it: 9.99× is not the order of any frequency this
        analysis holds, and a draft that quotes one is refused by name."""
        case = _case(cfg)
        result = _analyse(case, cfg)
        invented = _executive_summary(result, case).replace("(3.58×)", "(9.99×)")
        draft = (
            "# Vibration Survey Report — Synthetic Compressor 01\n\n## Executive Summary\n\n"
            f"{invented}\n\n## Diagnosis\n\nAs above.\n"
        )
        mismatches = check_numeric_quotes(draft, result)
        assert len(mismatches) == 1 and "'9.99'" in mismatches[0]


class TestTheDiagnosisLead:
    def test_word_for_word_with_orders(self, cfg):
        case = _case(cfg)
        result = _analyse(case, cfg)
        assert diagnosis_lead(result.findings[0], result, case, first=True) == SAMPLE_LEAD

    def test_reaches_both_documents_above_the_reason(self, cfg):
        case = _case(cfg)
        result = _analyse(case, cfg)
        md = render_markdown(result, case.machine, case=case, thresholds=cfg["thresholds"], profile="route")
        html = visible_text(render_html(result, case.machine, case=case, thresholds=cfg["thresholds"], profile="route"))
        assert SAMPLE_LEAD in md and SAMPLE_LEAD in html
        for doc in (md, html):
            assert doc.index(SAMPLE_LEAD) < doc.index("Peak at 107.2 Hz (3.57×) on y (rank 1).")

    def test_the_zone_clause_is_one_string_for_both_paragraphs(self, cfg):
        case = _case(cfg)
        clause = _zone_clause(_analyse(case, cfg))
        assert clause in SAMPLE_SUMMARY and clause in SAMPLE_LEAD


class TestOtherReadings:
    def test_a_clean_reading_keeps_its_sentence(self, cfg):
        case = _case(cfg, "healthy")
        result = _analyse(case, cfg)
        assert [f.fault for f in result.findings] == ["no_significant_findings"]
        summary = _executive_summary(result, case)
        assert summary.startswith(f"{case.machine.name} is in ISO Zone")
        assert "with no fault signature identified. Overall vibration is within acceptable limits; continue routine monitoring." in summary
        assert "dominated by" not in summary

    def test_an_unrated_reading_carries_no_zone_language(self, cfg):
        """Session A's contract, restated for the new paragraphs: an acceleration-only
        reading gets no ISO Zone anywhere — not in the summary, not in the lead."""
        case = _case(cfg)
        sd = case.sensor_data.model_copy(update={
            "x_velocity_mm_sec": None, "y_velocity_mm_sec": None, "z_velocity_mm_sec": None})
        case = case.model_copy(update={"sensor_data": sd})
        result = _analyse(case, cfg)
        assert result.iso is not None and result.iso.iso_zone == "not_assessable"
        assert _zone_clause(result) is None
        summary = _executive_summary(result, case)
        lead = diagnosis_lead(result.findings[0], result, case, first=True) or ""
        assert "ISO severity is unrated" in summary
        assert not re.search(r"Zone [A-D]", summary) and not re.search(r"Zone [A-D]", lead)
        assert "is dominated by 107.25 Hz (3.58×)" in lead  # the signature still reads

    def test_a_finding_with_no_frequency_has_no_signature_sentence(self, cfg):
        case = _case(cfg)
        result = _analyse(case, cfg)
        trend_like = result.findings[0].model_copy(update={"fault": "rising_trend", "evidence": {}})
        assert generate._signature_sentence(trend_like, result, case, with_orders=True) is None
        assert diagnosis_lead(trend_like, result, case, first=False) is None


class TestNegativeControl:
    def test_dropping_the_signature_breaks_the_order_pin(self, cfg, monkeypatch):
        case = _case(cfg)
        result = _analyse(case, cfg)
        # `names` is Session REPORT-4 (item 6)'s declared-direction map; the
        # stand-in has to accept it or the patch raises instead of returning None,
        # which would make this negative control pass for the wrong reason.
        monkeypatch.setattr(generate, "_signature_sentence",
                            lambda finding, result, case, *, with_orders, names=None: None)
        summary = _executive_summary(result, case)
        assert "dominated by" not in summary
        assert not in_analyst_order(summary)
        assert summary.endswith("The committed diagnosis is Bearing outer-race fault (BPFO) (high confidence).")
