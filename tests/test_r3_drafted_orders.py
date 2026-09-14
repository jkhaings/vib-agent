"""Session REPORT-3 items 3 and 4 — the drafted report finally carries the orders.

PARTC's Part A re-minted the public sample with a key, read all 23 pages, and
filed **F-10**:

    The drafted report does not carry REPORT-2's analyst-order diagnosis lead
    or its Computed (×) / Observed (×) columns. [...] A keyless re-render of
    the reference report the model receives contains the lead with
    "107.25 Hz (3.58×)" / "107.03 Hz (3.57×)" and both order columns. The model
    was handed them and did not transcribe them, and nothing required it to.

Both halves are spliced deterministically here, and both are verified the way
the brief requires — **the keyless re-render and the stand-in client**. Nothing
in this file can reach the network: `_drafted_evidence_block`,
`write_drafted_report` and `render_drafted_html` take a narrative string and no
client at all, and the one end-to-end test drives `run_agent_analysis` against
`tests.fake_anthropic.FakeAnthropicClient`.

Item 3 — the Diagnosis section in the analyst's order, zone first and the
conclusion last — is pinned here too, on BOTH documents, because the splice is
what makes it true of the drafted one.
"""

from __future__ import annotations

import re

import pytest

from tests.fake_anthropic import FakeAnthropicClient, build_consistent_echo, draft_message
from tests.test_r3_fault_sheet import cfg, squash, visible_text  # noqa: F401  (fixture)

from vib_agent.agent.loop import run_agent_analysis
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import (
    _drafted_evidence_block,
    diagnosis_lead,
    render_drafted_html,
    render_html,
    render_markdown,
    splice_diagnosis_lead,
    write_drafted_report,
)
from vib_agent.synth.generator import make_case

#: A narrative in the shape Part A read: a Diagnosis section that opens with the
#: committed call and puts the zone last, and an evidence table with no order
#: columns. This is the document F-10 was filed against.
DRAFT = """# Vibration Survey Report — Synthetic Compressor 01

## Executive Summary

The machine is in ISO Zone D at 5.20 mm/s RMS.

## Diagnosis

Committed diagnosis: Bearing outer-race fault (BPFO), high confidence. The
dominant line is at 107.25 Hz on the y-axis, matching the computed BPFO for
bearing 6206 at 107.03 Hz. This places the machine firmly in ISO Zone D.

| Finding | Axis | Computed (Hz) | Observed (Hz) |
|---|---|---|---|
| Bearing outer-race fault (BPFO) | y | 107.03 | 107.25 |

---
DRAFT — prepared by automated analysis, pending analyst review.
"""


def _analyse(cfg, name="bpfo"):
    case = make_case(name, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1)
    return case, run_analysis(case, iso_table=cfg["iso_table"],
                              thresholds=cfg["thresholds"], rules=cfg["rules"])


def in_analyst_order(text: str) -> bool:
    """Zone, then the dominant frequency, then the bearing, then the committed
    call — each strictly after the one before. REPORT-2's own helper, applied to
    the Diagnosis section rather than to the summary."""
    marks = ("ISO Zone", "dominated by", "bearing", "Committed diagnosis:")
    positions = [text.find(m) for m in marks]
    return all(p >= 0 for p in positions) and positions == sorted(positions)


class TestTheAnalystOrderLeadReachesTheDraftedNarrative:
    """F-10, first half. Keyless: a narrative string in, a narrative string out."""

    def test_the_lead_is_inserted_under_the_models_diagnosis_heading(self, cfg):
        case, result = _analyse(cfg)
        spliced = splice_diagnosis_lead(DRAFT, result, case)
        lead = diagnosis_lead(result.findings[0], result, case, first=True)
        assert lead in spliced
        # Directly under the heading, ahead of the model's own prose.
        assert spliced.index(lead) < spliced.index("Committed diagnosis: Bearing outer-race")
        assert spliced.index("## Diagnosis") < spliced.index(lead)

    def test_the_lead_carries_both_orders(self, cfg):
        case, result = _analyse(cfg)
        spliced = splice_diagnosis_lead(DRAFT, result, case)
        assert "107.25 Hz (3.58×)" in spliced
        assert "107.03 Hz (3.57×)" in spliced

    def test_the_section_then_reads_in_the_analysts_order(self, cfg):
        """Item 3. The model's own section ran the other way — committed call
        first, zone last (PARTC Part A, p.2-3). Measured here both ways."""
        case, result = _analyse(cfg)
        section = DRAFT[DRAFT.index("## Diagnosis"):DRAFT.index("---")]
        assert not in_analyst_order(section), "the fixture must be the WRONG order"
        spliced = splice_diagnosis_lead(DRAFT, result, case)
        after = spliced[spliced.index("## Diagnosis"):spliced.index("---")]
        assert in_analyst_order(after)

    def test_a_draft_that_already_has_it_is_left_alone_byte_for_byte(self, cfg):
        """The suppression the brief asked for, and it is BYTE-for-byte rather
        than the heading probe the other six guards use — see the helper's
        docstring for why a paragraph cannot be guarded by a heading."""
        case, result = _analyse(cfg)
        once = splice_diagnosis_lead(DRAFT, result, case)
        assert splice_diagnosis_lead(once, result, case) == once
        lead = diagnosis_lead(result.findings[0], result, case, first=True)
        assert once.count(lead) == 1

    def test_a_draft_with_no_diagnosis_heading_still_gets_the_lead(self, cfg):
        case, result = _analyse(cfg)
        headless = DRAFT.replace("## Diagnosis", "## What we found")
        spliced = splice_diagnosis_lead(headless, result, case)
        assert "## Diagnosis" in spliced
        assert diagnosis_lead(result.findings[0], result, case, first=True) in spliced

    def test_the_headless_fallback_lands_before_the_draft_footer(self, cfg):
        """The first cut appended past it, and the document ended on the lead
        instead of on "DRAFT — prepared by automated analysis". Caught by
        tests/test_report_charts.py::TestDraftedSplice, pinned here at the
        source so it cannot come back quietly."""
        case, result = _analyse(cfg)
        headless = DRAFT.replace("## Diagnosis", "## What we found")
        spliced = splice_diagnosis_lead(headless, result, case)
        assert spliced.rstrip().endswith(
            "DRAFT — prepared by automated analysis, pending analyst review.")
        assert spliced.index("## Diagnosis") < spliced.index("DRAFT — prepared")

    def test_a_reading_with_nothing_committed_is_untouched(self, cfg):
        _, result = _analyse(cfg, "healthy")
        assert splice_diagnosis_lead(DRAFT, result, None) == DRAFT


class TestTheOrderColumnsReachTheDraftedAppendix:
    """F-10, second half — the keyless re-render of the appendix itself."""

    def _block(self, cfg, draft=DRAFT):
        case, result = _analyse(cfg)
        return case, result, _drafted_evidence_block(
            result, charts=None, case=case, thresholds=cfg["thresholds"],
            profile="route", draft_text=draft, machine=case.machine,
        )

    def test_the_deterministic_table_is_spliced_with_both_order_columns(self, cfg):
        _, _, block = self._block(cfg)
        assert "Computed (×)" in block and "Observed (×)" in block
        assert "3.57" in block and "3.58" in block

    def test_it_is_byte_identical_to_the_deterministic_report_s_own(self, cfg):
        """Same macro, same `_evidence_rows` — so the two documents cannot come
        to word the evidence differently (the REC-1 discipline)."""
        case, result, block = self._block(cfg)
        md = render_markdown(result, case.machine, case=case,
                             thresholds=cfg["thresholds"], profile="route")

        def section(doc):
            m = re.search(r"^## Evidence$(.*?)(?=^## |\Z)", doc, re.S | re.M)
            return ("## Evidence" + m.group(1)).rstrip() if m else None

        assert section(block) is not None
        assert section(block) == section(md)

    def test_a_draft_that_already_has_the_order_columns_is_not_given_a_second_table(self, cfg):
        already = DRAFT.replace(
            "| Finding | Axis | Computed (Hz) | Observed (Hz) |",
            "| Finding | Axis | Computed (Hz) | Computed (×) | Observed (Hz) | Observed (×) |")
        _, _, block = self._block(cfg, already)
        assert "## Evidence" not in block

    def test_the_probe_is_the_columns_and_not_the_heading(self, cfg):
        """The fixture writes "## Evidence"-free prose but a model routinely
        writes that heading. A heading probe would suppress the table on exactly
        the drafts PARTC read — so this pins that it does not."""
        heading_only = DRAFT.replace("## Diagnosis", "## Diagnosis\n\n## Evidence\n")
        _, _, block = self._block(cfg, heading_only)
        assert "Computed (×)" in block


class TestBothHalvesOnTheRealDraftedPath:
    """End to end through `run_agent_analysis` against the stand-in client.

    Zero API calls: the client is handed in, and `FakeAnthropicClient` raises
    the moment it is asked for a response it was not given.
    """

    def test_report_md_carries_the_lead_and_the_order_columns(self, tmp_path, cfg):
        pytest.importorskip("matplotlib")
        case, result = _analyse(cfg)
        client = FakeAnthropicClient(
            [draft_message(DRAFT, build_consistent_echo(result))]
        )
        run_agent_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                           rules=cfg["rules"], out_dir=tmp_path, pdf=False, client=client)
        drafted = (tmp_path / "report.md").read_text()
        assert "107.25 Hz (3.58×)" in drafted
        assert "107.03 Hz (3.57×)" in drafted
        assert "Computed (×)" in drafted and "Observed (×)" in drafted
        section = drafted[drafted.index("## Diagnosis"):]
        assert in_analyst_order(section[:section.index("\n## ", 1)])

    def test_the_drafted_page_carries_them_too(self, tmp_path, cfg):
        """The PDF is rendered from this page, not from report.md — which is
        why the splice had to be applied in both places."""
        pytest.importorskip("markdown")
        case, result = _analyse(cfg)
        html = render_drafted_html(DRAFT, result, case.machine, case=case,
                                   thresholds=cfg["thresholds"], profile="route")
        assert html is not None
        text = visible_text(html)
        assert "107.25 Hz (3.58×)" in text
        assert "Computed (×)" in text and "Observed (×)" in text


class TestTheDeterministicDiagnosisSectionToo:
    """Item 3 on the document that always had it — pinned so it cannot regress."""

    @pytest.mark.parametrize("renderer", ["markdown", "html"])
    def test_zone_first_conclusion_last(self, renderer, tmp_path, cfg):
        case, result = _analyse(cfg)
        kw = dict(case=case, thresholds=cfg["thresholds"], profile="route")
        doc = (render_markdown(result, case.machine, **kw) if renderer == "markdown"
               else visible_text(render_html(result, case.machine, **kw)))
        start = doc.index("Overall vibration places this machine in")
        assert in_analyst_order(doc[start:start + 900])
