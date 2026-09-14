"""Session REPORT-3 item 9 — the ground-truth read nobody had done.

FIXTURE-1 built a four-point route sample and closed with this:

    The PDFs for this set are the operator's to render through the live app --
    four jobs, three files each, per the README. That is also the Part C check
    this set is *for*, and it has not been done: nobody has yet read a drafted
    report from these files against the ground truth above.

This is that read, mechanised: every point of the set driven through the real
product path -- `parse_upload` -> `assembly.merge_channels` -> `run_analysis`
-> the renderer -- and compared against what the README says is in the data.

**The expectations are TYPED OUT from the README, not parsed from it.** A
golden derived from the thing it guards guards nothing; that is FIXTURE-1's own
discipline (its F-3, where a README asserted a ratio the data did not have and
nothing would have caught it). The files themselves are GENERATED into
tmp_path by the same script that wrote the tracked copies, so this test reads
nothing that moves (law #22, the CHARTS-2 precedent).

The close-out carries the result as a table, expected vs rendered, one row per
location.
"""

from __future__ import annotations

import pytest

from tests.test_r3_fault_sheet import cfg  # noqa: F401  (fixture)

from vib_agent.config import load_config
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import fault_sheet_context, render_markdown

#: The README's own ground truth, typed out.
#:
#:   | point          | overall H / V / A | zone | planted                    |
#:   | Motor DE       | 1.10 / 0.90 / 0.50 |  A  | nothing - healthy          |
#:   | Motor NDE      | 1.80 / 1.40 / 0.60 |  B  | nothing - healthy          |
#:   | Compressor DE  | 5.20 / 4.80 / 0.50 |  D  | BPFO 107.1875 Hz + 2x      |
#:   | Compressor NDE | 1.20 / 1.00 / 0.55 |  A  | nothing - healthy          |
#:
#: "Expected on upload: Compressor DE commits a bearing outer-race fault (BPFO)
#: at 107.1875 Hz (3.57x) in Zone D; the other three points commit nothing and
#: read Zone A, B and A."
GROUND_TRUTH = {
    "motor_de":       {"label": "Motor DE",       "zone": "A", "overall": 1.10,
                       "committed": [], "bpfo_hz": None},
    "motor_nde":      {"label": "Motor NDE",      "zone": "B", "overall": 1.80,
                       "committed": [], "bpfo_hz": None},
    "compressor_de":  {"label": "Compressor DE",  "zone": "D", "overall": 5.20,
                       "committed": ["bearing_outer_race"], "bpfo_hz": 107.1875},
    "compressor_nde": {"label": "Compressor NDE", "zone": "A", "overall": 1.20,
                       "committed": [], "bpfo_hz": None},
}

#: The 13th file. "NOT A FAULT - a low-frequency integration artifact lifting
#: the overall to 20 mm/s", at a point whose true overall is 1.20.
SKI_SLOPE_FILE = "compressor_nde_h_ski_slope_artifact_not_a_fault.csv"
SKI_SLOPE_POINT = "compressor_nde"


def _point(tmp_path, key, cfg):
    """One point of the route, through the product path the webapp runs."""
    from scripts.make_multi_sample import DIRECTIONS, POINTS, write_sample
    import scripts.make_multi_sample as S
    from vib_agent.adapters.uploads import parse_upload
    from vib_agent.adapters.uploads.common import UploadForm
    from vib_agent.webapp import assembly

    write_sample(tmp_path)
    bearings = load_config("bearings")
    point = next(p for p in POINTS if p.key == key)
    parsed = []
    for suffix, direction, _axis, _label in DIRECTIONS:
        form = UploadForm(machine_alias=S.MACHINE_ALIAS, rpm=S.RPM, iso_group=S.ISO_GROUP,
                          iso_support=S.ISO_SUPPORT, bearing_model=S.BEARING_MODEL)
        case, kind, note = parse_upload(tmp_path / f"{point.key}_{suffix}.csv", form,
                                        bearings_cfg=bearings)
        parsed.append(assembly.ParsedChannel(direction, False, case, kind, note))
    outcome = assembly.merge_channels(parsed, [], iso_table=cfg["iso_table"],
                                      thresholds=cfg["thresholds"], rules=cfg["rules"],
                                      speed_tolerance_pct=5.0)
    return outcome.case, run_analysis(outcome.case, iso_table=cfg["iso_table"],
                                      thresholds=cfg["thresholds"], rules=cfg["rules"])


def _committed(result):
    return [f.fault for f in result.findings if f.fault != "no_significant_findings"]


class TestEveryPointReadsAsTheReadmeSaysItShould:

    @pytest.mark.parametrize("key", sorted(GROUND_TRUTH))
    def test_the_iso_zone(self, key, tmp_path, cfg):
        _, result = _point(tmp_path, key, cfg)
        assert result.iso is not None
        assert result.iso.iso_zone == GROUND_TRUTH[key]["zone"]

    @pytest.mark.parametrize("key", sorted(GROUND_TRUTH))
    def test_the_overall(self, key, tmp_path, cfg):
        """The README states the overall per point; the spectrum lane derives
        severity from the Parseval sum, which is what the files were scaled to."""
        _, result = _point(tmp_path, key, cfg)
        assert result.iso.severity_rms == pytest.approx(GROUND_TRUTH[key]["overall"], abs=0.02)

    @pytest.mark.parametrize("key", sorted(GROUND_TRUTH))
    def test_what_was_committed(self, key, tmp_path, cfg):
        _, result = _point(tmp_path, key, cfg)
        assert _committed(result) == GROUND_TRUTH[key]["committed"]

    def test_the_bpfo_is_at_compressor_de_and_at_the_frequency_the_readme_names(
        self, tmp_path, cfg
    ):
        """The whole point of the set: on a route the diagnostic act is deciding
        WHICH point the fault is at, and a single-point fixture cannot test it."""
        _, result = _point(tmp_path, "compressor_de", cfg)
        match = result.rca.primary_findings[0]
        assert match.fault == "bearing_outer_race"
        assert match.freq_hz == pytest.approx(GROUND_TRUTH["compressor_de"]["bpfo_hz"], abs=0.2)
        assert match.axis == "y"
        assert result.rca.differential == []

    def test_and_nowhere_else(self, tmp_path, cfg):
        for key in ("motor_de", "motor_nde", "compressor_nde"):
            _, result = _point(tmp_path / key, key, cfg)
            assert _committed(result) == [], key


class TestTheSkiSlopeFileIsFlaggedAtTheRightPoint:
    """"NOT A FAULT" -- the file exists to make a severity claim that is
    entirely an artifact of how the data was captured."""

    def _single(self, tmp_path, filename, cfg):
        from scripts.make_multi_sample import write_sample
        import scripts.make_multi_sample as S
        from vib_agent.adapters.uploads import parse_upload
        from vib_agent.adapters.uploads.common import UploadForm

        write_sample(tmp_path)
        form = UploadForm(machine_alias=S.MACHINE_ALIAS, rpm=S.RPM, iso_group=S.ISO_GROUP,
                          iso_support=S.ISO_SUPPORT, bearing_model=S.BEARING_MODEL)
        case, _k, _n = parse_upload(tmp_path / filename, form, bearings_cfg=load_config("bearings"))
        return case, run_analysis(case, iso_table=cfg["iso_table"],
                                  thresholds=cfg["thresholds"], rules=cfg["rules"])

    def test_the_gate_flags_it_and_the_report_says_so_on_page_one(self, tmp_path, cfg):
        case, result = self._single(tmp_path, SKI_SLOPE_FILE, cfg)
        status = {c.name: c.status for c in result.quality_gate.checks}
        assert status["ski_slope"] == "warn"
        assert result.iso.severity_rms == pytest.approx(20.0, abs=0.2)
        # The README's claim is "no bearing fault", and that holds.
        assert not [f for f in _committed(result) if f.startswith("bearing_")]
        # But it is not silent, and THAT is worth writing down rather than
        # asserting away: uploaded on its own, the artifact commits
        # `possible_resonance`. Measured here for the first time -- FIXTURE-1's
        # own pins read the file's ARRAY and its gate verdict, never its
        # committed findings through a single-file job.
        #
        # It is not a wrong call on the evidence: a monotone low-frequency rise
        # IS broadband energy at no shaft order, which is what that detector
        # looks for, and `possible_resonance`'s own recommendation is "confirm
        # with a bump test before any corrective action". It is the strongest
        # argument for item 7 that this session found -- the page-1 caveat and
        # this finding land on the same page, and the caveat is what tells the
        # reader which one to believe first.
        assert _committed(result) == ["possible_resonance"]
        sheet = fault_sheet_context(result, case.machine, case)
        assert sheet["data_quality"] is not None
        md = render_markdown(result, case.machine, case=case,
                             thresholds=cfg["thresholds"], profile="route")
        assert "re-measure before acting on the zone" in md.lower()

    def test_its_healthy_twin_is_the_point_the_readme_says_it_is(self, tmp_path, cfg):
        _, result = self._single(tmp_path, "compressor_nde_h.csv", cfg)
        status = {c.name: c.status for c in result.quality_gate.checks}
        assert status["ski_slope"] == "pass"
        assert result.iso.severity_rms == pytest.approx(1.20, abs=0.02)
        assert GROUND_TRUTH[SKI_SLOPE_POINT]["zone"] == "A"
