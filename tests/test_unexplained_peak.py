"""Session UNEXPLAINED-PEAK — the report names the loudest line when no finding
claims it.

The reproducing case is the HIST-2 sample pair. On `before.csv` — 1800 rpm, no
bearing model — the spectrum carries exactly three lines:

    107.0 Hz at 1.4     the loudest line, and a BPFO for a bearing nobody named
     30.0 Hz at 0.9     1x shaft
    214.0 Hz at 0.6     2x of 107

The analysis commits `imbalance` on the 30 Hz line and calls it "dominant on
radial axis y". Before this session the number 107 appeared NOWHERE in the
rendered report: `unmatched_periodicity()` computed the right sentence, and all
four templates gated it on `no_findings`, so a committed finding threw it away.
A report that commits to a line 1.56x quieter than the one it stays silent about
is not a report an analyst can act on.

The fixture is re-minted here rather than read from `outputs/hist2_sample/`:
that directory is untracked (`.gitignore:4` `outputs/*`), so a test reading it
would pass on the machine that minted it and fail everywhere else.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import render_html, render_markdown, unmatched_periodicity

RPM = 1800.0
SHAFT_HZ = RPM / 60.0  # 30 Hz
UNCLAIMED_HZ = 107.0   # 3.57x shaft — not an integer order, not a half order
MARKER = "Unexplained dominant peak"


@pytest.fixture(scope="module")
def cfg():
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
        "bearings": load_config("bearings"),
    }


def _spectrum_csv(path: Path, peaks: dict[float, float], *, n: int = 800,
                  fmax: float = 400.0, floor: float = 0.001) -> Path:
    """The hist2_sample grid: 0.5 Hz spacing to 400 Hz, so the 2x at 214 Hz fits."""
    freqs = [i * fmax / n for i in range(n)]
    amp = [floor] * n
    for hz, a in peaks.items():
        amp[min(range(n), key=lambda i: abs(freqs[i] - hz))] = a
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["freq_hz", "amplitude"])
        for f, a in zip(freqs, amp):
            w.writerow([f, a])
    return path


#: `before.csv`, exactly.
_BEFORE = {SHAFT_HZ: 0.9, UNCLAIMED_HZ: 1.4, 2 * UNCLAIMED_HZ: 0.6}
#: The same machine with the 1x as the loudest line — the control.
_ONE_X_LOUDEST = {SHAFT_HZ: 1.4, UNCLAIMED_HZ: 0.5}


def _analyse(tmp_path, cfg, peaks, *, bearing_model=None, name="upload.csv"):
    form = dict(machine_alias="H2", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor")
    if bearing_model:
        form["bearing_model"] = bearing_model
    path = _spectrum_csv(tmp_path / name, peaks)
    case, _kind, _note = parse_upload(path, UploadForm(**form), bearings_cfg=cfg["bearings"])
    result = run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                          rules=cfg["rules"])
    return case, result


def _documents(case, result, cfg) -> tuple[str, str]:
    """Both halves of the mirrored pair, from one context (common law #7)."""
    kw = dict(case=case, thresholds=cfg["thresholds"], profile="route")
    return (render_markdown(result, case.machine, **kw),
            render_html(result, case.machine, **kw))


# ── the defect ───────────────────────────────────────────────────────────


class TestTheLoudestLineIsNamed:
    def test_the_committed_finding_is_the_quieter_line(self, tmp_path, cfg):
        """The premise, asserted rather than assumed: this fixture really does
        commit on a line that is not the loudest one."""
        _case, result = _analyse(tmp_path, cfg, _BEFORE)
        assert [f.fault for f in result.findings] == ["imbalance"]
        assert result.findings[0].evidence["freq_hz"] == pytest.approx(SHAFT_HZ, abs=0.5)

    def test_both_documents_name_the_unclaimed_line(self, tmp_path, cfg):
        case, result = _analyse(tmp_path, cfg, _BEFORE)
        md, html = _documents(case, result, cfg)
        for doc, label in ((md, "markdown"), (html, "html")):
            assert "107.0" in doc, f"{label}: the loudest line is not named"

    def test_both_documents_state_its_ratio_to_the_committed_peak(self, tmp_path, cfg):
        """1.4 / 0.9 = 1.56x. The ratio is the whole point: it is what tells an
        analyst the report committed to the quieter of the two."""
        case, result = _analyse(tmp_path, cfg, _BEFORE)
        md, html = _documents(case, result, cfg)
        for doc, label in ((md, "markdown"), (html, "html")):
            assert "1.56" in doc, f"{label}: no ratio to the committed peak"

    def test_the_line_reaches_the_findings_section(self, tmp_path, cfg):
        """Placement is load-bearing. An analyst reading the Diagnosis section
        must not have to scroll to a later section to learn that the loudest
        line in the spectrum is unexplained."""
        case, result = _analyse(tmp_path, cfg, _BEFORE)
        md, _html = _documents(case, result, cfg)
        diagnosis = md.split("## Diagnosis", 1)[1].split("\n## ", 1)[0]
        assert "107.0" in diagnosis, diagnosis


# ── the two ways it must stay silent ─────────────────────────────────────


class TestSilentWhereTheQuestionIsAnswered:
    def test_geometry_claims_the_line_and_nothing_is_added(self, tmp_path, cfg):
        """With a 6205 the 107 Hz line IS the computed BPFO, the bearing screen
        commits it, and there is nothing unexplained left to say."""
        case, result = _analyse(tmp_path, cfg, _BEFORE, bearing_model="6205")
        assert "bearing_outer_race" in [f.fault for f in result.findings]
        assert unmatched_periodicity(result, case, cfg["thresholds"]) is None
        md, html = _documents(case, result, cfg)
        for doc, label in ((md, "markdown"), (html, "html")):
            assert MARKER not in doc, f"{label}: section fired with geometry supplied"

    def test_when_the_committed_finding_owns_the_loudest_line(self, tmp_path, cfg):
        """The operator's chosen trigger: the line appears only when the LOUDEST
        peak on the channel is claimed by nothing. Here the 1x is loudest and
        imbalance commits it, so the report is unchanged."""
        case, result = _analyse(tmp_path, cfg, _ONE_X_LOUDEST)
        assert "imbalance" in [f.fault for f in result.findings]
        up = unmatched_periodicity(result, case, cfg["thresholds"])
        assert up is None or not up["outranks_committed"], up
        md, _html = _documents(case, result, cfg)
        diagnosis = md.split("## Diagnosis", 1)[1].split("\n## ", 1)[0]
        assert MARKER not in diagnosis, diagnosis


# ── the branch this session must not disturb ─────────────────────────────


class TestTheNoFindingsSentenceIsUnchanged:
    """`tests/test_no_geometry_honesty.py` pins the sentence the no-findings
    branch has printed since R3-DIFF, and this session did NOT edit that file.
    This is the local restatement of the contract that lets it stay green: the
    new clause is APPENDED to that sentence, never woven into it."""

    def test_the_original_sentence_survives_intact_inside_the_new_one(self, tmp_path, cfg):
        case, result = _analyse(tmp_path, cfg, _BEFORE)
        up = unmatched_periodicity(result, case, cfg["thresholds"])
        assert up is not None and up["outranks_committed"]
        original_tail = "— supply bearing geometry to identify."
        assert original_tail in up["sentence"]
        # Everything the R3-DIFF branch prints is a PREFIX of what this one
        # prints. If a future edit rewords the first half instead of appending
        # to it, this fails here rather than in test_no_geometry_honesty.
        head, _sep, tail = up["sentence"].partition(original_tail)
        assert head.startswith("Strong unmatched spectrum periodicity at 107.0 Hz")
        assert tail.strip().startswith("It is the loudest line")

    def test_the_committed_reference_is_read_not_invented(self, tmp_path, cfg):
        """The ratio's denominator is the committed peak's amplitude, which is
        not on the FaultMatch — it is read from the same peak set the numerator
        came from, so an analyst can redo the comparison against the figure."""
        case, result = _analyse(tmp_path, cfg, _BEFORE)
        up = unmatched_periodicity(result, case, cfg["thresholds"])
        committed = up["committed"]
        assert committed["label"] == "Rotor imbalance"
        assert committed["freq_hz"] == pytest.approx(SHAFT_HZ, abs=0.5)
        assert committed["amplitude"] == pytest.approx(0.9, abs=1e-9)
        assert up["amplitude"] == pytest.approx(1.4, abs=1e-9)
        assert committed["ratio"] == pytest.approx(1.4 / 0.9, rel=1e-9)
