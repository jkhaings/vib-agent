"""Session FIXTURE-1 -- the multi-location route sample.

`scripts/make_multi_sample.py` writes one compressor train at FOUR measurement
points, three directions each, with a BPFO planted at Compressor DE and nowhere
else -- plus a 13th file that is deliberately NOT a fault.

Every fixture before this one was a single machine at a single point, so nothing
exercised the judgement a route actually demands: which point is the fault at. The
claims pinned here are the ones the README beside the files makes, and each is a
claim about the PRODUCT rather than about an array:

  * the 13 files exist, each declaring its type and unit -- PART-C section D point 2
    found the public sample printing an amplitude with no unit stated anywhere, and
    "declared" here means declared in the two places the intake actually reads;
  * the BPFO tone is present at Compressor DE and absent from all nine other files,
    with the axial one deliberately weak (sub-floor) and the radial ones deliberately
    not;
  * the ski-slope file's overall exceeds its healthy twin's by the intended factor,
    and it commits no bearing fault -- it trips the quality gate's own `ski_slope`
    check instead, which is the honest answer;
  * every one of the 13 is accepted by the intake over HTTP, status 202.

The byte-identity pin is what keeps the committed files and the script from drifting
apart, on `tests/test_report2_sample_csv.py`'s precedent.
"""

from __future__ import annotations

import importlib.util
import math
import statistics
import traceback
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient
from tests.test_webapp_e2e import _webapp_cfg
from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report.charts import _amplitude_floor_value
from vib_agent.webapp import assembly as A
from vib_agent.webapp.app import create_app

_REPO = Path(__file__).resolve().parents[1]

# scripts/ is not a package; load the module by path (tests/test_report2_sample_csv.py
# precedent). The module deliberately uses NamedTuple rather than dataclass because a
# dataclass cannot be built under this idiom -- see the comment on `Point`.
_spec = importlib.util.spec_from_file_location(
    "_vib_make_multi_sample_fixture1", _REPO / "scripts" / "make_multi_sample.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

TRACKED = _REPO / "outputs" / "demo_package" / "multi_location"
SIBLING = _REPO / "outputs" / "demo_package" / "bpfo_synthetic_fmax2000"

CODE, LABEL = "demo-code", "engineer-1"

#: TIDY-2 F-1 / DOCS-1 F-4. This file posts 13 jobs; without a double every one of
#: them would construct the real SDK. Copied rather than imported for the reason
#: given in tests/test_sec2_headers.py -- the canonical helpers sit behind a
#: module-scope `importorskip("sqlalchemy")`, and nothing here needs the [db] extra.
DUMMY_ANTHROPIC_KEY = "test-placeholder-not-a-real-key"


@pytest.fixture(autouse=True)
def no_real_anthropic_client(monkeypatch):
    import anthropic

    constructed: list[str] = []

    def _tripwire(self, *args, **kwargs):
        constructed.append("".join(traceback.format_stack(limit=12)))
        raise RuntimeError("TIDY-2 F-1: the real anthropic.Anthropic was constructed")

    monkeypatch.setattr(anthropic.Anthropic, "__init__", _tripwire)
    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY_ANTHROPIC_KEY)
    yield constructed
    assert not constructed, (
        f"the real anthropic.Anthropic was constructed {len(constructed)} time(s). "
        f"The first construction:\n{constructed[0]}"
    )


@pytest.fixture(scope="module")
def cfg() -> dict:
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
        "bearings": load_config("bearings"),
    }


def _form() -> UploadForm:
    # `machine_type` is REQUIRED on the wire since Session INTAKE-2 (PARTC F-2 --
    # the upload lane used to record "motor" for everything). The value comes from
    # the generator's own `MACHINE_TYPE`, so the fixture and the form agree about
    # what this machine is rather than the test asserting it separately.
    return UploadForm(machine_alias=S.MACHINE_ALIAS, rpm=S.RPM, iso_group=S.ISO_GROUP,
                      iso_support=S.ISO_SUPPORT, bearing_model=S.BEARING_MODEL,
                      machine_type=S.MACHINE_TYPE)


def _parse(directory: Path, stem: str, cfg: dict):
    case, _kind, _note = parse_upload(directory / f"{stem}.csv", _form(),
                                      bearings_cfg=cfg["bearings"])
    return case


def _trio(directory: Path, point, cfg: dict):
    """One measurement point's three files through the real assembly path."""
    parsed = []
    for suffix, direction, _axis, _label in S.DIRECTIONS:
        case, kind, note = parse_upload(directory / f"{point.key}_{suffix}.csv", _form(),
                                        bearings_cfg=cfg["bearings"])
        parsed.append(A.ParsedChannel(direction, False, case, kind, note))
    return A.merge_channels(parsed, [], iso_table=cfg["iso_table"],
                            thresholds=cfg["thresholds"], rules=cfg["rules"],
                            speed_tolerance_pct=5.0)


def _at(case, freq: float) -> float:
    spec = case.spectra["y"]
    return next(a for f, a in zip(spec.freq_hz, spec.amplitude) if abs(f - freq) < 1e-9)


def _floor(case, cfg: dict) -> float:
    value = _amplitude_floor_value(case.spectra["y"], cfg["thresholds"])
    assert value is not None
    return value


# ── the files on disk ────────────────────────────────────────────────────


class TestTheTrackedFilesAreTheScriptsOutput:
    def test_thirteen_csvs_and_a_readme(self, tmp_path):
        written = S.write_sample(tmp_path)
        assert len(S.FILES) == 13
        assert sorted(p.name for p in written) == sorted(
            [f"{f.stem}.csv" for f in S.FILES] + ["README.md"])
        assert len({f.stem for f in S.FILES}) == 13  # no stem written twice

    @pytest.mark.parametrize("sample", S.FILES, ids=lambda s: s.stem)
    def test_every_csv_has_the_shape_the_spectrum_lane_reads(self, sample):
        lines = (TRACKED / f"{sample.stem}.csv").read_text().splitlines()
        assert lines[0] == "freq_hz,amplitude"
        assert len(lines) == S.LINES + 2  # header + 12,800 bins + the closing Fmax bin
        assert lines[-1].startswith("2000,")

    def test_the_tracked_files_are_byte_identical_to_a_fresh_run(self, tmp_path):
        """Determinism, and that what is committed is what the script writes."""
        S.write_sample(tmp_path)
        for name in [f"{f.stem}.csv" for f in S.FILES] + ["README.md"]:
            assert (tmp_path / name).read_bytes() == (TRACKED / name).read_bytes(), name

    def test_the_single_point_sample_is_a_sibling_and_is_untouched(self):
        assert S.OUT.parent == SIBLING.parent and S.OUT != SIBLING
        assert (SIBLING / "radial_h.csv").exists()
        assert not (S.OUT / "radial_h.csv").exists()
        assert not (S.OUT / "report.pdf").exists()  # the PDFs are the operator's


class TestEveryFileDeclaresItsTypeAndUnit:
    """PART-C section D point 2: the public sample states a spectrum type and no unit,
    anywhere. Nothing in this set may be readable that way."""

    @pytest.mark.parametrize("sample", S.FILES, ids=lambda s: s.stem)
    def test_the_manifest_declares_both_on_every_file(self, sample):
        assert sample.quantity == "velocity spectrum"
        assert sample.unit == "mm/s RMS"

    @pytest.mark.parametrize("sample", S.FILES, ids=lambda s: s.stem)
    def test_the_readme_renders_the_declaration_on_every_files_own_row(self, sample):
        rows = [ln for ln in (TRACKED / "README.md").read_text().splitlines()
                if ln.startswith(f"| `{sample.stem}.csv` |")]
        assert len(rows) == 1, f"{sample.stem} has {len(rows)} rows in the README table"
        assert f"{sample.quantity}, {sample.unit}" in rows[0]
        assert sample.point_label in rows[0] and f"`{sample.direction}`" in rows[0]

    def test_the_form_values_the_readme_states_are_the_ones_the_intake_takes(self):
        """The declaration is only real if these are the field VALUES, not prose."""
        assert (S.VELOCITY_UNIT_FORM, S.DETECTION_FORM, S.MODE_FORM) == (
            "mm_s", "rms", "spectrum")
        readme = (TRACKED / "README.md").read_text()
        for value in (S.VELOCITY_UNIT_FORM, S.DETECTION_FORM, S.MODE_FORM):
            assert value in readme


# ── the fault, and where it is not ───────────────────────────────────────


class TestTheBpfoIsAtCompressorDeOnly:
    def test_the_radial_files_carry_it_well_above_the_evidence_floor(self, cfg):
        for suffix in ("h", "v"):
            case = _parse(TRACKED, f"compressor_de_{suffix}", cfg)
            at_bpfo, floor = _at(case, S.BPFO_BIN_HZ), _floor(case, cfg)
            assert at_bpfo > floor, suffix
            assert at_bpfo / floor > 10, f"{suffix}: {at_bpfo / floor:.1f}x -- not 'clearly'"

    def test_the_axial_file_carries_it_weakly_below_the_floor(self, cfg):
        """'A weak' has to mean something measurable: above the noise so an analyst
        sees it, below the evidence floor so no detector commits on it."""
        case = _parse(TRACKED, "compressor_de_a", cfg)
        at_bpfo, floor = _at(case, S.BPFO_BIN_HZ), _floor(case, cfg)
        median = statistics.median(case.spectra["y"].amplitude)
        assert at_bpfo > 3 * median, "the weak axial tone is lost in the noise"
        assert at_bpfo < floor, "the weak axial tone reached the evidence floor"

    @pytest.mark.parametrize(
        "sample",
        [f for f in S.FILES if f.point_key != "compressor_de"],
        ids=lambda s: s.stem,
    )
    def test_no_other_file_carries_it_at_all(self, sample, cfg):
        case = _parse(TRACKED, sample.stem, cfg)
        assert _at(case, S.BPFO_BIN_HZ) < _floor(case, cfg), sample.stem

    def test_the_bpfo_line_quantises_to_the_twelve_thousand_eight_hundred_line_bin(self):
        """107.1875 Hz, not the 107.25 Hz the 8,000-line sibling reports. A pin that
        carried the sibling's literal would pass on the wrong file forever."""
        assert S.BPFO_BIN_HZ == pytest.approx(107.1875)
        assert round(S.BPFO_HZ / S.FMAX_HZ * S.LINES) == 686


class TestEachPointCommitsWhatTheReadmeClaims:
    """The set is only useful if the PRODUCT reaches these calls, not just the arrays."""

    @pytest.mark.parametrize(
        "key,zone,findings",
        [("motor_de", "A", ["no_significant_findings"]),
         ("motor_nde", "B", ["no_significant_findings"]),
         ("compressor_de", "D", ["bearing_outer_race"]),
         ("compressor_nde", "A", ["no_significant_findings"])],
    )
    def test_the_trio_lands_the_declared_zone_and_call(self, key, zone, findings, cfg):
        point = next(p for p in S.POINTS if p.key == key)
        outcome = _trio(TRACKED, point, cfg)
        assert outcome.fail_closed_checks == []
        assert outcome.channel_summary["speed_warning"] is None
        assert all(c["status"] == "ok" for c in outcome.channel_summary["channels"])
        result = run_analysis(outcome.case, iso_table=cfg["iso_table"],
                              thresholds=cfg["thresholds"], rules=cfg["rules"])
        assert result.quality_gate.overall != "fail"
        assert result.iso.iso_zone == zone
        assert result.iso.severity_rms == pytest.approx(point.overalls["h"], abs=1e-3)
        assert [f.fault for f in result.findings] == findings
        assert S.iso_zone(max(point.overalls.values()), cfg["iso_table"]) == zone

    def test_only_compressor_de_commits_a_bearing_fault(self, cfg):
        committed = {}
        for point in S.POINTS:
            result = run_analysis(_trio(TRACKED, point, cfg).case,
                                  iso_table=cfg["iso_table"],
                                  thresholds=cfg["thresholds"], rules=cfg["rules"])
            committed[point.key] = [m.fault for m in (result.rca.primary_findings
                                                      if result.rca else [])]
        assert committed == {"motor_de": [], "motor_nde": [],
                             "compressor_de": ["bearing_outer_race"],
                             "compressor_nde": []}

    def test_the_committed_match_is_the_bearing_the_form_declares(self, cfg):
        result = run_analysis(
            _trio(TRACKED, next(p for p in S.POINTS if p.key == "compressor_de"), cfg).case,
            iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], rules=cfg["rules"])
        match = result.rca.primary_findings[0]
        assert (match.axis, match.freq_hz) == ("y", pytest.approx(107.19, abs=0.01))
        assert match.expected_hz == pytest.approx(107.03, abs=0.01)  # 6206 geometry
        assert result.rca.differential == []


# ── the two things a CSV cannot carry ────────────────────────────────────


class TestTheAddedRunningSpeedLine:
    """Exactly as loud as it has to be, on all 13 -- REPORT-2's sandwich, re-measured
    at 12,800 lines."""

    @pytest.mark.parametrize("sample", S.FILES, ids=lambda s: s.stem)
    def test_present_above_the_intake_floor_but_below_the_evidence_floor(self, sample, cfg):
        case = _parse(TRACKED, sample.stem, cfg)
        spec = case.spectra["y"]
        at_30 = _at(case, S.SHAFT_LINE_HZ)
        # assembly.py::_has_shaft_content -- a peak counts when it stands >=3x the median
        assert at_30 >= A._SPEED_PRESENCE_RATIO * statistics.median(spec.amplitude)
        # charts.py::_amplitude_floor_value -- the route profile's evidence floor
        assert at_30 < _floor(case, cfg)

    def test_the_ratios_the_readme_quotes_are_the_ratios_the_files_have(self, cfg):
        """The README states these bands in prose. An earlier draft said the 1x line
        stood "7.7-9.3x the median", which conflated it with the axial BPFO's 9.3x --
        a false number in a deliverable, and nothing would have caught it. The bands
        are module constants now, rendered into the README and asserted here, so the
        prose and the data cannot part company."""
        machine_files = [f for f in S.FILES if not f.ski_slope]
        low, high = S.SHAFT_LINE_MEDIAN_BAND
        for sample in machine_files:
            case = _parse(TRACKED, sample.stem, cfg)
            ratio = _at(case, S.SHAFT_LINE_HZ) / statistics.median(case.spectra["y"].amplitude)
            assert low <= ratio <= high, f"{sample.stem}: 1x/median {ratio:.2f}"
        ski = _parse(TRACKED, S.SKI_SLOPE_STEM, cfg)
        assert (_at(ski, S.SHAFT_LINE_HZ) / statistics.median(ski.spectra["y"].amplitude)
                == pytest.approx(S.SKI_SLOPE_SHAFT_LINE_MEDIAN, abs=0.05))
        for sample in S.FILES:
            case = _parse(TRACKED, sample.stem, cfg)
            assert (_at(case, S.SHAFT_LINE_HZ) / _floor(case, cfg)
                    < S.SHAFT_LINE_FLOOR_CEILING), sample.stem

        axial = _parse(TRACKED, "compressor_de_a", cfg)
        at_bpfo = _at(axial, S.BPFO_BIN_HZ)
        assert (at_bpfo / statistics.median(axial.spectra["y"].amplitude)
                == pytest.approx(S.AXIAL_BPFO_MEDIAN_RATIO, abs=0.05))
        assert at_bpfo / _floor(axial, cfg) == pytest.approx(S.AXIAL_BPFO_FLOOR_RATIO, abs=0.005)
        low, high = S.RADIAL_BPFO_FLOOR_BAND
        for suffix in ("h", "v"):
            case = _parse(TRACKED, f"compressor_de_{suffix}", cfg)
            ratio = _at(case, S.BPFO_BIN_HZ) / _floor(case, cfg)
            assert low <= ratio <= high, f"{suffix}: bpfo/floor {ratio:.2f}"

    def test_thirty_hertz_lands_exactly_on_a_bin(self):
        """192 bins per shaft order, against the intake's floor of 2."""
        assert S.SHAFT_LINE_HZ / (S.FMAX_HZ / S.LINES) == 192.0

    def test_without_it_a_trio_would_be_refused(self, tmp_path, cfg, monkeypatch):
        """The negative control on the README's reason for adding it at all."""
        monkeypatch.setattr(S, "SHAFT_LINE_AMP", 0.0)
        S.write_sample(tmp_path)
        outcome = _trio(tmp_path, next(p for p in S.POINTS if p.key == "compressor_de"), cfg)
        assert [c.name for c in outcome.fail_closed_checks] == ["cross_channel_speed_agreement"]


class TestTheSkiSlopeFile:
    """The one file in the set that is not a fault."""

    def test_its_overall_exceeds_the_healthy_twin_by_the_intended_factor(self, cfg):
        healthy = _parse(TRACKED, f"{S.SKI_SLOPE_POINT}_{S.SKI_SLOPE_DIRECTION}", cfg)
        ski = _parse(TRACKED, S.SKI_SLOPE_STEM, cfg)
        true_overall = next(p for p in S.POINTS if p.key == S.SKI_SLOPE_POINT
                            ).overalls[S.SKI_SLOPE_DIRECTION]
        assert healthy.sensor_data.y_velocity_mm_sec == pytest.approx(true_overall, abs=1e-3)
        assert ski.sensor_data.y_velocity_mm_sec == pytest.approx(
            S.SKI_SLOPE_TARGET_MM_S, abs=1e-3)
        assert (ski.sensor_data.y_velocity_mm_sec / healthy.sensor_data.y_velocity_mm_sec
                == pytest.approx(S.SKI_SLOPE_TARGET_MM_S / true_overall, rel=1e-6))

    def test_the_added_component_is_strictly_decreasing_so_it_is_never_a_tone(self):
        """What makes it a slope and not a fault: it can hold no local maximum.

        Asserted on the rows the script produces, not on the round-tripped file. The
        CSV carries six significant figures of amplitude, and out in the tail the
        artifact is smaller than that -- so a difference taken across the file is
        rounding noise, and a strict-monotonicity assertion on it fails for a reason
        that has nothing to do with the shape.
        """
        rows = S.build_rows()
        healthy = rows[f"{S.SKI_SLOPE_POINT}_{S.SKI_SLOPE_DIRECTION}"]
        ski = rows[S.SKI_SLOPE_STEM]
        added = [b - a for (_f, a), (_g, b) in zip(healthy, ski)]
        assert all(b <= a for a, b in zip(added, added[1:]))
        assert added[0] > 0  # and it is actually there
        shape = S.ski_slope_shape([i * S.FMAX_HZ / S.LINES for i in range(64)])
        assert all(b < a for a, b in zip(shape, shape[1:]))

    def test_the_script_refuses_a_slope_that_is_not_monotone(self, monkeypatch):
        """Non-vacuity for the guard: the claim above is enforced at write time, so
        no future edit to the shape can quietly ship a slope with a bump in it."""
        # One bump, at the second bin. A FLAT shape would not do: the guard forbids a
        # RISE, and flat is not a rise -- getting that wrong is how a non-vacuity
        # control ends up proving nothing.
        monkeypatch.setattr(
            S, "ski_slope_shape",
            lambda freqs: [2.0 if i == 1 else 1.0 for i in range(len(freqs))])
        rows = [(i * 0.15625, 0.001) for i in range(S.LINES + 1)]
        with pytest.raises(ValueError, match="monotone"):
            S.ski_slope_rows(rows)

    def test_the_machine_content_is_the_healthy_files_own(self):
        """Not a second machine and not a louder one. The healthy array is carried
        through untouched and the artifact is added on top -- there is no second
        rescale, so the only difference between the two files IS the artifact."""
        rows = S.build_rows()
        healthy = rows[f"{S.SKI_SLOPE_POINT}_{S.SKI_SLOPE_DIRECTION}"]
        ski = rows[S.SKI_SLOPE_STEM]
        added = [b - a for (_f, a), (_g, b) in zip(healthy, ski)]
        shape = S.ski_slope_shape([f for f, _ in healthy])
        scale = added[0] / shape[0]
        assert all(x == pytest.approx(scale * g, rel=1e-9) for x, g in zip(added, shape))
        assert math.sqrt(sum(a * a for _f, a in healthy)) == pytest.approx(
            next(p for p in S.POINTS if p.key == S.SKI_SLOPE_POINT
                 ).overalls[S.SKI_SLOPE_DIRECTION], rel=1e-9)

    def test_it_commits_no_bearing_fault_and_trips_the_gates_own_ski_slope_check(self, cfg):
        """The honest answer to this file is 'your data is bad', not a diagnosis.

        It is not silent -- the detector reports `possible_resonance`, which is a fair
        reading of a broadband low-frequency hump. What it must never do is name a
        bearing defect on a point whose bearing is fine.
        """
        result = run_analysis(_parse(TRACKED, S.SKI_SLOPE_STEM, cfg),
                              iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                              rules=cfg["rules"])
        committed = [m.fault for m in (result.rca.primary_findings if result.rca else [])]
        assert not any(f.startswith("bearing_") for f in committed), committed
        assert not any(f.fault.startswith("bearing_") for f in result.findings)
        tripped = {c.name for c in result.quality_gate.checks if c.status == "warn"}
        assert "ski_slope" in tripped
        # and the healthy twin does not trip it -- otherwise the pin proves nothing
        twin = run_analysis(_parse(TRACKED, f"{S.SKI_SLOPE_POINT}_{S.SKI_SLOPE_DIRECTION}", cfg),
                            iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                            rules=cfg["rules"])
        assert "ski_slope" not in {c.name for c in twin.quality_gate.checks
                                   if c.status == "warn"}

    def test_its_name_cannot_be_mistaken_for_a_fault_case(self):
        """Once uploaded the filename is the only label that travels with the file."""
        assert "not_a_fault" in S.SKI_SLOPE_STEM and "artifact" in S.SKI_SLOPE_STEM
        assert "NOT A FAULT" in S.SKI_SLOPE_PLANTED


# ── over HTTP ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    app = create_app(
        webapp_cfg=_webapp_cfg(), invite_codes={CODE: LABEL},
        contact_email="ops@example.test",
        # Without a double every one of the 13 posts below would construct the real
        # SDK -- DOCS-1 F-4's class, which Session FIXTURE-1 also closed in
        # tests/test_sec2_headers.py.
        anthropic_client_factory=lambda: FakeAnthropicClient(responses=[]),
    )
    with TestClient(app) as client:
        yield client


class TestTheIntakeAcceptsEveryFile:
    @pytest.mark.parametrize("sample", S.FILES, ids=lambda s: s.stem)
    def test_every_file_is_accepted_by_the_live_intake(self, sample, client):
        """A 202 over HTTP, not a direct adapter call: the webapp stores every upload
        as `upload.csv`, so filename-coupled logic only fails on this path (the
        standing rule the CWRU nameless-upload bug wrote)."""
        response = client.post(
            "/api/jobs",
            files={"file": ("upload.csv",
                            (TRACKED / f"{sample.stem}.csv").read_bytes(), "text/csv")},
            data={"invite_code": CODE, "machine_alias": S.MACHINE_ALIAS,
                  "rpm": str(S.RPM), "iso_group": S.ISO_GROUP,
                  "iso_support": S.ISO_SUPPORT, "bearing_model": S.BEARING_MODEL,
                  # Session INTAKE-2: required, and taken from the generator.
                  "machine_type": S.MACHINE_TYPE,
                  "velocity_unit": S.VELOCITY_UNIT_FORM,
                  "detection_type": S.DETECTION_FORM, "mode": S.MODE_FORM,
                  "measurement_location": sample.point_label},
        )
        assert response.status_code == 202, response.text
        assert response.json()["job_id"]
