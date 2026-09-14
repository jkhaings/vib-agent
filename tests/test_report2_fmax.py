"""Session REPORT-2 (item 5) — the report says up to which harmonic each computed
fault frequency is inside the measured range.

A CAT analyst reviewing the outreach sample said an Fmax of 500 Hz cannot evaluate
the bearing harmonics. The report never said what its Fmax COULD evaluate. Now a
"Frequency range — what Fmax can evaluate" block, built once in
`generate.fmax_adequacy_context` from the spectrum's span and `rca.bearing_freqs`,
prints one line per computed frequency:

    BPFO 107.03 Hz (3.57×): visible to 4×; 5× and above are beyond Fmax 500 Hz.

Pinned with the 500 Hz sample and the 2 kHz case the generator's new `fmax` hook
builds, on both document twins, on the drafted path (a deterministic splice the
model never sees), through the 2 kHz CSVs via the upload path, and with a stated
absence when no bearing geometry was supplied.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from tests.test_report_html import visible_text
from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report import generate
from vib_agent.report.generate import (
    FMAX_ADEQUACY_HEADING,
    fmax_adequacy_context,
    render_drafted_html,
    render_html,
    render_markdown,
    write_drafted_report,
)
from vib_agent.synth.generator import make_case
from vib_agent.webapp import assembly as A

_REPO = Path(__file__).resolve().parents[1]
_TRACKED = _REPO / "outputs" / "demo_package" / "bpfo_synthetic_fmax2000"

_spec = importlib.util.spec_from_file_location(
    "_vib_make_sample_csv_report2_fmax", _REPO / "scripts" / "make_sample_csv.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

NARRATIVE = "# Vibration Survey Report — Synthetic Compressor 01\n\nJust a narrative.\n"

# The four lines on the 500 Hz sample: floor(500 / f) for the 6206 at 1800 rpm.
SAMPLE_LINES = (
    "BPFO 107.03 Hz (3.57×): visible to 4×; 5× and above are beyond Fmax 500 Hz.",
    "BPFI 162.97 Hz (5.43×): visible to 3×; 4× and above are beyond Fmax 500 Hz.",
    "BSF 69.30 Hz (2.31×): visible to 7×; 8× and above are beyond Fmax 500 Hz.",
    "FTF 11.89 Hz (0.40×): visible to 42×; 43× and above are beyond Fmax 500 Hz.",
)


@pytest.fixture(scope="module")
def cfg() -> dict:
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
        "bearings": load_config("bearings"),
    }


def _analyse(case, cfg):
    return run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                        rules=cfg["rules"])


def _case(cfg, name="bpfo", **kw):
    return make_case(name, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1, **kw)


def _md_section(md: str) -> str:
    m = re.search(rf"## {re.escape(FMAX_ADEQUACY_HEADING)}\n(.*?)(?=\n## )", md, re.S)
    assert m, "the Fmax-adequacy section is missing from the markdown report"
    # The words, not the surrounding template's blank lines: the deterministic
    # document keeps a second blank line before Data Quality that the drafted
    # appendix's neighbour does not.
    return m.group(1).strip()


class TestTheFiveHundredHertzSample:
    def test_the_four_lines(self, cfg):
        case = _case(cfg)
        fa = fmax_adequacy_context(_analyse(case, cfg), case)
        assert fa["fmax_hz"] == 500.0
        assert fa["absence"] is None
        assert tuple(fa["lines"]) == SAMPLE_LINES
        assert fa["intro"].startswith("The measured spectrum reaches Fmax 500 Hz.")

    def test_both_documents_carry_them_word_for_word(self, cfg):
        case = _case(cfg)
        result = _analyse(case, cfg)
        md = render_markdown(result, case.machine, case=case, thresholds=cfg["thresholds"], profile="route")
        html = visible_text(render_html(result, case.machine, case=case, thresholds=cfg["thresholds"], profile="route"))
        assert f"## {FMAX_ADEQUACY_HEADING}" in md
        for line in SAMPLE_LINES:
            assert f"- {line}" in md
            assert line in html
        assert FMAX_ADEQUACY_HEADING in html

    def test_it_sits_beside_the_analysis_parameters_in_the_markdown(self, cfg):
        case = _case(cfg)
        md = render_markdown(_analyse(case, cfg), case.machine, case=case,
                             thresholds=cfg["thresholds"], profile="route")
        params = md.index("## Analysis Parameters")
        block = md.index(f"## {FMAX_ADEQUACY_HEADING}")
        nxt = md.index("## Data Quality")
        assert params < block < nxt


class TestTheTwoKilohertzCase:
    def test_the_generator_case_reaches_eighteen_times_bpfo(self, cfg):
        case = _case(cfg, fmax=2000.0, lines=8000)
        fa = fmax_adequacy_context(_analyse(case, cfg), case)
        assert fa["fmax_hz"] == 2000.0
        assert fa["lines"][0] == (
            "BPFO 107.03 Hz (3.57×): visible to 18×; 19× and above are beyond Fmax 2000 Hz.")
        assert fa["lines"][1].startswith("BPFI 162.97 Hz (5.43×): visible to 12×;")
        assert not any("not fully inside" in line for line in fa["lines"])

    def test_the_tracked_csvs_say_the_same_through_the_upload_path(self, cfg):
        """The README beside the CSVs promises "visible to 18×"; this is that promise
        read back through the lane the operator will actually use."""
        parsed = []
        form = UploadForm(machine_alias="Synthetic Compressor 01", rpm=S.RPM, iso_group="2",
                          iso_support="rigid", machine_type="motor", bearing_model="6206")
        for stem, _axis in S.FILES:
            case, kind, note = parse_upload(_TRACKED / f"{stem}.csv", form, bearings_cfg=cfg["bearings"])
            parsed.append(A.ParsedChannel(stem, False, case, kind, note))
        merged = A.merge_channels(parsed, [], iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                                  rules=cfg["rules"], speed_tolerance_pct=5.0).case
        md = render_markdown(_analyse(merged, cfg), merged.machine, case=merged,
                             thresholds=cfg["thresholds"], profile="route")
        assert "- BPFO 107.03 Hz (3.57×): visible to 18×; 19× and above are beyond Fmax 2000 Hz." in md


class TestTheShortRangeWording:
    def test_a_range_that_misses_the_screened_series_says_so(self, cfg):
        case = _case(cfg, fmax=300.0, lines=1200)  # BPFI 162.97 → floor(300/163) = 1
        fa = fmax_adequacy_context(_analyse(case, cfg), case)
        bpfi = fa["lines"][1]
        assert bpfi.startswith("BPFI 162.97 Hz (5.43×): visible to 1×; 2× and above are beyond Fmax 300 Hz.")
        assert bpfi.endswith("The 1×–3× series this analysis screens is not fully inside the measured range.")

    def test_no_geometry_is_a_stated_absence_not_silence(self, cfg):
        case = _case(cfg, "imbalance")  # a machine with no bearing
        result = _analyse(case, cfg)
        assert result.rca.bearing_freqs is None
        fa = fmax_adequacy_context(result, case)
        assert fa["lines"] == []
        assert fa["absence"].startswith("No bearing geometry was supplied")
        md = render_markdown(result, case.machine, case=case, thresholds=cfg["thresholds"], profile="route")
        html = visible_text(render_html(result, case.machine, case=case, thresholds=cfg["thresholds"], profile="route"))
        assert fa["absence"] in md and fa["absence"] in html

    def test_no_spectrum_means_no_block(self, cfg):
        case = _case(cfg)
        result = _analyse(case, cfg)
        assert fmax_adequacy_context(result, None) is None
        md = render_markdown(result, case.machine)  # no case → no spectrum span to speak of
        assert FMAX_ADEQUACY_HEADING not in md


class TestTheDraftedPath:
    def test_the_reference_report_handed_to_the_model_omits_the_block(self, cfg):
        case = _case(cfg)
        reference = render_markdown(_analyse(case, cfg), case.machine, case=case,
                                    thresholds=cfg["thresholds"], include_causes=False)
        assert FMAX_ADEQUACY_HEADING not in reference

    def test_drafted_markdown_carries_the_block_verbatim(self, tmp_path, cfg):
        pytest.importorskip("matplotlib")
        case = _case(cfg)
        result = _analyse(case, cfg)
        written = write_drafted_report(NARRATIVE, result, tmp_path, machine=case.machine,
                                       case=case, thresholds=cfg["thresholds"], figures=True)
        drafted = written["markdown"].read_text()
        deterministic = render_markdown(result, case.machine, case=case, thresholds=cfg["thresholds"])
        assert _md_section(drafted) == _md_section(deterministic)

    def test_drafted_html_carries_the_block(self, cfg):
        pytest.importorskip("markdown")
        case = _case(cfg)
        result = _analyse(case, cfg)
        html = render_drafted_html(NARRATIVE, result, case.machine, case=case, thresholds=cfg["thresholds"])
        assert html is not None
        text = visible_text(html)
        assert FMAX_ADEQUACY_HEADING in text and SAMPLE_LINES[0] in text


class TestNegativeControl:
    def test_nulling_the_context_removes_the_block_from_both_documents(self, cfg, monkeypatch):
        """Proves the pins above read the wiring, not a coincidence of wording."""
        case = _case(cfg)
        result = _analyse(case, cfg)
        monkeypatch.setattr(generate, "fmax_adequacy_context", lambda result, case: None)
        md = render_markdown(result, case.machine, case=case, thresholds=cfg["thresholds"], profile="route")
        html = visible_text(render_html(result, case.machine, case=case, thresholds=cfg["thresholds"], profile="route"))
        assert FMAX_ADEQUACY_HEADING not in md and FMAX_ADEQUACY_HEADING not in html
        assert SAMPLE_LINES[0] not in md and SAMPLE_LINES[0] not in html
