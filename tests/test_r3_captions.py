"""Session REPORT-3 item 5 — a caption never denies what the plot shows (PARTC F-11).

    F-11 (pre-existing, all three PDFs) — The Z-axis figure caption reads "No
    peak on this channel matched a computed fault frequency within tolerance."
    But Z's loudest bin is 107.25 Hz at 0.330, the BPFO line [...] The RCA
    records the match on y only, and `_spectrum_caption` renders "no match
    recorded for this axis" as a claim that no peak on this channel matched.

The sentence was true about the RCA's bookkeeping and false as English about
the figure it sat under, on every PDF Part C read. It now says where the match
WAS recorded and names what this channel shows.

Pinned with the trio, as the brief requires — the demo trio through the real
upload lane, GENERATED into tmp_path rather than read from `outputs/`
(law #22) — and on the generator fixture Part C actually measured.
"""

from __future__ import annotations

import pytest

from tests.test_r3_fault_sheet import cfg  # noqa: F401  (fixture)

from vib_agent.config import load_config
from vib_agent.pipeline import run_analysis
from vib_agent.report import charts as charts_mod
from vib_agent.report.charts import _match_seen_on_this_channel
from vib_agent.synth.generator import make_case

pytestmark = pytest.mark.skipif(
    not charts_mod.matplotlib_available(), reason="matplotlib not installed ([pdf] extra)"
)

DENIAL = "No peak on this channel matched a computed fault frequency within tolerance."


def _captions(result, machine, out, case, thresholds):
    charts = charts_mod.render_charts(result, machine, out, case=case, thresholds=thresholds)
    return {ch.axis: ch.figures[0].caption for ch in charts.channels if ch.figures}


def _generator_bpfo(cfg):
    case = make_case("bpfo", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1)
    return case, run_analysis(case, iso_table=cfg["iso_table"],
                              thresholds=cfg["thresholds"], rules=cfg["rules"])


def _demo_trio(tmp_path, cfg):
    """The trio, through the upload lane the live form uses."""
    from scripts.make_sample_csv import write_sample
    from vib_agent.adapters.uploads import parse_upload
    from vib_agent.adapters.uploads.common import UploadForm
    from vib_agent.webapp import assembly

    write_sample(tmp_path)
    bearings = load_config("bearings")
    parsed = []
    for name, direction in (("radial_h.csv", "radial_h"), ("radial_v.csv", "radial_v"),
                            ("axial.csv", "axial")):
        form = UploadForm(machine_alias="Synthetic Compressor 01", rpm=1800.0,
                          iso_group="2", iso_support="rigid", bearing_model="6206")
        case, kind, note = parse_upload(tmp_path / name, form, bearings_cfg=bearings)
        parsed.append(assembly.ParsedChannel(direction, False, case, kind, note))
    outcome = assembly.merge_channels(parsed, [], iso_table=cfg["iso_table"],
                                      thresholds=cfg["thresholds"], rules=cfg["rules"])
    return outcome.case, run_analysis(outcome.case, iso_table=cfg["iso_table"],
                                      thresholds=cfg["thresholds"], rules=cfg["rules"])


class TestTheChannelThatShowsTheToneSaysSo:

    def test_the_generator_sample_z_axis_no_longer_denies_it(self, tmp_path, cfg):
        """The exact figure Part C read, and the exact numbers it measured."""
        case, result = _generator_bpfo(cfg)
        captions = _captions(result, case.machine, tmp_path, case, cfg["thresholds"])
        assert DENIAL not in captions["z"], captions["z"]
        assert "match was recorded on the Y axis" in captions["z"]
        assert "107.25 Hz (3.58×)" in captions["z"]
        assert "0.33" in captions["z"]          # PARTC measured 0.330 on this bin

    def test_the_trio_through_the_upload_lane_too(self, tmp_path, cfg):
        case, result = _demo_trio(tmp_path / "trio", cfg)
        captions = _captions(result, case.machine, tmp_path / "out", case, cfg["thresholds"])
        assert "Matched:" in captions["y"]
        assert DENIAL not in captions["z"], captions["z"]
        assert "recorded on the Y axis" in captions["z"]

    def test_a_channel_that_really_shows_nothing_still_says_so(self, tmp_path, cfg):
        """The other half, and the reason this is not a blanket reword: the
        X axis carries no BPFO line, so the denial is TRUE there and stays."""
        case, result = _generator_bpfo(cfg)
        captions = _captions(result, case.machine, tmp_path, case, cfg["thresholds"])
        assert captions["x"].endswith(DENIAL), captions["x"]

    def test_the_matched_channel_is_unchanged(self, tmp_path, cfg):
        case, result = _generator_bpfo(cfg)
        captions = _captions(result, case.machine, tmp_path, case, cfg["thresholds"])
        assert captions["y"] == ("Envelope spectrum (as supplied), Y axis. Matched: "
                                 "bearing outer race at 107.25 Hz (3.58×).")


class TestThePresenceRuleIsMeasuredNotAssumed:
    """A caption that reported every channel would be as wrong as the denial."""

    def test_the_helper_finds_the_line_on_z_and_not_on_x(self, cfg):
        case, result = _generator_bpfo(cfg)
        tol = (cfg["thresholds"].get("rca") or {}).get("tolerance_pct")
        for axis, expected in (("z", True), ("x", False)):
            spectrum = (case.spectra or {})[axis]
            seen = _match_seen_on_this_channel(result, axis, spectrum, tol)
            assert (seen is not None) is expected, (axis, seen)

    def test_the_axis_that_owns_the_match_is_never_reported_as_elsewhere(self, cfg):
        case, result = _generator_bpfo(cfg)
        tol = (cfg["thresholds"].get("rca") or {}).get("tolerance_pct")
        assert _match_seen_on_this_channel(result, "y", (case.spectra or {})["y"], tol) is None

    def test_a_reading_with_no_committed_match_reports_nothing(self, cfg):
        case = make_case("healthy", iso_table=cfg["iso_table"],
                         thresholds=cfg["thresholds"], seed=1)
        result = run_analysis(case, iso_table=cfg["iso_table"],
                              thresholds=cfg["thresholds"], rules=cfg["rules"])
        tol = (cfg["thresholds"].get("rca") or {}).get("tolerance_pct")
        for axis, spectrum in (case.spectra or {}).items():
            assert _match_seen_on_this_channel(result, axis, spectrum, tol) is None
