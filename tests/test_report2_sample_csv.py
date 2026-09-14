"""Session REPORT-2 — the generator's Fmax hook and the 2 kHz outreach sample CSVs.

A CAT analyst reviewing the outreach sample (`scripts/make_sample.py`,
`make_case("bpfo", seed=1)`) said an Fmax of 500 Hz cannot evaluate the bearing
harmonics and asked for it to be raised. The sample is not a CSV: it is a seeded
generator case whose span was fixed at 500 Hz / 2000 lines inside
`_build_fault_case`. `make_case` now takes `fmax` / `lines`, and
`scripts/make_sample_csv.py` writes the same recipe at 2 kHz in the one shape the web
form's spectrum lane reads, one file per direction, so the operator renders the PDF
through the app.

Two claims are pinned here that the README beside the files makes:

  * the CSVs are the SAME case — same machine, bearing, rpm, seeded tones — and the
    upload + assembly path commits the same call on them (BPFO at 107.25 Hz on y,
    Zone D, empty differential) that the 500 Hz sample gets;
  * the 1× line the export adds is small enough to stay out of every detector's
    evidence (below the route amplitude floor) while satisfying the intake's
    running-speed presence check (≥3× the median) — the reason it had to be added.
"""

from __future__ import annotations

import importlib.util
import statistics
from pathlib import Path

import pytest

from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report.charts import _amplitude_floor_value
from vib_agent.synth.generator import make_case
from vib_agent.webapp import assembly as A

_REPO = Path(__file__).resolve().parents[1]

# scripts/ is not a package; load the module by path (tests/test_ci_summary.py precedent).
_spec = importlib.util.spec_from_file_location(
    "_vib_make_sample_csv_report2", _REPO / "scripts" / "make_sample_csv.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
TRACKED = _REPO / "outputs" / "demo_package" / "bpfo_synthetic_fmax2000"
OLD_SAMPLE = _REPO / "outputs" / "demo_package" / "bpfo_synthetic" / "report.pdf"


@pytest.fixture(scope="module")
def cfg() -> dict:
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
        "bearings": load_config("bearings"),
    }


def _dump_without_ts(case) -> dict:
    """`Case.expected` carries the run's wall-clock stamp; everything else is seeded."""
    d = case.model_dump()
    if d.get("expected"):
        d["expected"].pop("ts", None)
    return d


# ── the hook ─────────────────────────────────────────────────────────────


class TestMakeCaseFmaxHook:
    def test_the_defaults_are_the_old_signature_byte_for_byte(self, cfg):
        """Every seeded case any test, golden or eval case reads is unchanged."""
        before = make_case("bpfo", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1)
        after = make_case("bpfo", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1,
                          fmax=500.0, lines=2000)
        assert before.spectra["y"].fmax_hz == 500.0 and len(before.spectra["y"].freq_hz) == 2000
        assert _dump_without_ts(before) == _dump_without_ts(after)

    def test_two_khz_is_the_same_fault_on_a_wider_span(self, cfg):
        case = make_case("bpfo", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1,
                         fmax=2000.0, lines=8000)
        for axis in ("x", "y", "z"):
            spec = case.spectra[axis]
            assert spec.fmax_hz == 2000.0
            assert len(spec.freq_hz) == 8000
            assert spec.freq_hz[1] - spec.freq_hz[0] == pytest.approx(0.25)  # Δf kept
            assert case.raw_spectra[axis].fmax_hz == 2000.0
        result = run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                              rules=cfg["rules"])
        assert result.iso.iso_zone == "D" and result.iso.severity_rms == pytest.approx(5.2)
        assert [f.fault for f in result.findings] == ["bearing_outer_race"]
        match = result.rca.primary_findings[0]
        assert (match.axis, match.freq_hz, match.expected_hz) == ("y", 107.25, 107.03)
        assert result.rca.differential == []

    def test_a_gate_violation_case_refuses_the_hook_rather_than_ignoring_it(self, cfg):
        with pytest.raises(ValueError, match="fault recipes only"):
            make_case("flat_spectrum", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                      seed=1, fmax=2000.0, lines=8000)
        # the default values are not a refusal
        make_case("flat_spectrum", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1)


# ── the CSV sample ───────────────────────────────────────────────────────


def _form() -> UploadForm:
    return UploadForm(machine_alias="Synthetic Compressor 01", rpm=S.RPM, iso_group="2",
                      iso_support="rigid", machine_type="motor", bearing_model="6206")


def _trio(directory: Path, cfg: dict):
    parsed = []
    for stem, _axis in S.FILES:
        case, kind, note = parse_upload(directory / f"{stem}.csv", _form(),
                                        bearings_cfg=cfg["bearings"])
        parsed.append(A.ParsedChannel(stem, False, case, kind, note))
    return A.merge_channels(parsed, [], iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                            rules=cfg["rules"], speed_tolerance_pct=5.0)


class TestTheTrackedFilesAreTheScriptsOutput:
    def test_the_script_writes_the_three_directions_and_a_readme(self, tmp_path):
        written = S.write_sample(tmp_path)
        assert sorted(p.name for p in written) == sorted(
            ["radial_h.csv", "radial_v.csv", "axial.csv", "README.md"])
        for stem, _axis in S.FILES:
            lines = (tmp_path / f"{stem}.csv").read_text().splitlines()
            assert lines[0] == "freq_hz,amplitude"
            assert len(lines) == S.LINES + 2  # header + 8000 bins + the closing Fmax bin
            assert lines[-1].startswith("2000,")

    def test_the_tracked_files_are_byte_identical_to_a_fresh_run(self, tmp_path):
        """Determinism, and that what is committed is what the script writes."""
        S.write_sample(tmp_path)
        for name in ("radial_h.csv", "radial_v.csv", "axial.csv", "README.md"):
            assert (tmp_path / name).read_bytes() == (TRACKED / name).read_bytes(), name

    def test_the_old_sample_is_a_sibling_and_is_not_written_by_this_script(self):
        old_dir = OLD_SAMPLE.parent  # outputs/demo_package/bpfo_synthetic
        assert S.OUT.parent == old_dir.parent and S.OUT != old_dir
        assert OLD_SAMPLE.exists()
        assert not (S.OUT / "report.pdf").exists()  # the PDF is the operator's, via the app


class TestTheSameFaultThroughTheUploadPath:
    def test_the_trio_commits_the_five_hundred_hertz_samples_call(self, cfg):
        outcome = _trio(TRACKED, cfg)
        assert outcome.fail_closed_checks == []
        assert outcome.channel_summary["speed_warning"] is None
        assert all(c["status"] == "ok" for c in outcome.channel_summary["channels"])
        case = outcome.case
        assert case.spectra["y"].fmax_hz == 2000.0 and len(case.spectra["y"].freq_hz) == S.LINES + 1
        result = run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                              rules=cfg["rules"])
        assert result.quality_gate.overall != "fail"
        assert result.iso.iso_zone == "D"
        assert result.iso.severity_rms == pytest.approx(5.2, abs=1e-3)
        assert result.iso.dominant_axis == "y"
        assert [f.fault for f in result.findings] == ["bearing_outer_race"]
        match = result.rca.primary_findings[0]
        assert (match.axis, match.freq_hz, match.expected_hz) == ("y", 107.25, 107.03)
        assert match.harmonic_present is True
        assert result.rca.differential == []  # exactly the 500 Hz sample's differential

    def test_the_radial_h_file_alone_gives_the_same_call(self, cfg):
        case, _kind, _note = parse_upload(TRACKED / "radial_h.csv", _form(),
                                          bearings_cfg=cfg["bearings"])
        result = run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                              rules=cfg["rules"])
        assert result.iso.iso_zone == "D"
        assert [f.fault for f in result.findings] == ["bearing_outer_race"]
        assert result.rca.primary_findings[0].freq_hz == 107.25

    def test_each_axis_carries_the_recipes_stated_velocity(self, cfg):
        """The spectrum lane derives severity by Parseval; the export scaled each axis
        so that sum IS the recipe's velocity — the number the 500 Hz sample states."""
        base = make_case("bpfo", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1)
        for stem, axis in S.FILES:
            case, _, _ = parse_upload(TRACKED / f"{stem}.csv", _form(), bearings_cfg=cfg["bearings"])
            stated = getattr(base.sensor_data, f"{axis}_velocity_mm_sec")
            assert case.sensor_data.y_velocity_mm_sec == pytest.approx(stated, abs=1e-3), stem


class TestTheAddedRunningSpeedLine:
    """The one thing the export adds, and why it is exactly as loud as it is."""

    @pytest.mark.parametrize("stem,axis", S.FILES)
    def test_present_above_the_intake_floor_but_below_the_evidence_floor(self, stem, axis, cfg):
        case, _, _ = parse_upload(TRACKED / f"{stem}.csv", _form(), bearings_cfg=cfg["bearings"])
        spec = case.spectra["y"]
        at_30 = next(a for f, a in zip(spec.freq_hz, spec.amplitude) if f == S.SHAFT_LINE_HZ)
        # assembly.py::_has_shaft_content — a peak counts when it stands ≥3× the median
        assert at_30 >= A._SPEED_PRESENCE_RATIO * statistics.median(spec.amplitude)
        # charts.py::_amplitude_floor_value — the route profile's evidence floor
        floor = _amplitude_floor_value(spec, cfg["thresholds"])
        assert floor is not None and at_30 < floor
        # and the seeded BPFO line on the radial axes is well above that floor
        if axis in ("y", "z"):
            at_bpfo = next(a for f, a in zip(spec.freq_hz, spec.amplitude) if f == 107.25)
            assert at_bpfo > floor

    def test_without_it_the_trio_would_be_refused(self, tmp_path, cfg, monkeypatch):
        """The negative control on the README's reason: export the recipe with no 1×
        line and the intake fails closed on cross-channel speed agreement."""
        monkeypatch.setattr(S, "SHAFT_LINE_AMP", 0.0)
        S.write_sample(tmp_path)
        outcome = _trio(tmp_path, cfg)
        assert [c.name for c in outcome.fail_closed_checks] == ["cross_channel_speed_agreement"]
