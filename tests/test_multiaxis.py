"""Session E — multi-axis upload: assembly-layer units + API/e2e (added in later
sections). This first block covers the pure assembly module (remap / merge /
identity / scope / notes / speed) with no webapp or LLM involved."""

from __future__ import annotations

import math
import pathlib

import pytest

from vib_agent.config import load_config, load_thresholds
from vib_agent.models import Case, MachineMeta, SensorData, Spectrum
from vib_agent.webapp import assembly as A


def _y_spectrum(peaks: list[tuple[float, float]], *, fmax: float = 200.0, n: int = 400,
                kind: str = "velocity") -> Spectrum:
    freqs = [i * fmax / n for i in range(n)]
    amp = [0.001] * n
    for pk_hz, pk_amp in peaks:
        idx = min(range(n), key=lambda i: abs(freqs[i] - pk_hz))
        amp[idx] = pk_amp
    return Spectrum(freq_hz=freqs, amplitude=amp, fmax_hz=fmax, kind=kind)


def _y_case(peaks: list[tuple[float, float]], *, rpm: float = 1800.0,
            scope: tuple[str, ...] = ("zone", "severity", "rca", "trend"),
            raw: bool = False) -> Case:
    """A single-axis 'y' Case shaped like a parsed tabular-spectrum upload:
    velocity = Parseval of the amplitude (the tabular adapter's rule)."""
    spec = _y_spectrum(peaks)
    overall = math.sqrt(sum(a * a for a in spec.amplitude))
    machine = MachineMeta(mac="UPLOAD-SPEC-M", name="M", active=True, type="motor",
                          iso_group="2", iso_support="rigid")
    sd = SensorData(rpm=rpm, y_velocity_mm_sec=overall, y_rms_ACC_G=overall * 0.08)
    return Case(name="M", machine=machine, sensor_data=sd, spectra={"y": spec},
                raw_spectra={"y": spec.model_copy(update={"kind": "velocity"})} if raw else None,
                source="upload", validation_scope=list(scope))


def _parsed(direction: A.Direction, case: Case, *, assumed: bool = False, note: str = "") -> A.ParsedChannel:
    return A.ParsedChannel(direction=direction, assumed=assumed, case=case, kind="tabular_spectrum",
                           conversion_note=note)


@pytest.fixture(scope="module")
def cfg():
    return {"iso_table": load_config("iso_zones")["zones"], "thresholds": load_thresholds("route"),
            "rules": load_config("next_measurements")}


# ── remap ────────────────────────────────────────────────────────────────────
class TestRemap:
    def test_radial_h_is_identity(self):
        c = _y_case([(30.0, 0.5)])
        assert A.remap_channel_case(c, "radial_h") is c

    def test_axial_moves_y_to_x(self):
        c = _y_case([(30.0, 0.5)])
        out = A.remap_channel_case(c, "axial")
        assert set(out.spectra) == {"x"} and out.spectra["x"].amplitude == c.spectra["y"].amplitude
        assert out.sensor_data.x_velocity_mm_sec == c.sensor_data.y_velocity_mm_sec
        assert out.sensor_data.y_velocity_mm_sec is None
        assert out.sensor_data.x_rms_ACC_G == c.sensor_data.y_rms_ACC_G

    def test_radial_v_moves_y_to_z(self):
        c = _y_case([(30.0, 0.5)], raw=True)
        out = A.remap_channel_case(c, "radial_v")
        assert set(out.spectra) == {"z"} and set(out.raw_spectra) == {"z"}
        assert out.sensor_data.z_velocity_mm_sec == c.sensor_data.y_velocity_mm_sec

    def test_non_y_axis_refused(self):
        c = _y_case([(30.0, 0.5)])
        bad = c.model_copy(update={"spectra": {"x": c.spectra["y"]}})
        with pytest.raises(ValueError, match="single-axis"):
            A.remap_channel_case(bad, "axial")


# ── identity fast-path (byte-compat) ──────────────────────────────────────────
class TestIdentityFastPath:
    def test_single_defaulted_returns_case_untouched(self, cfg):
        c = _y_case([(30.0, 0.5)])
        out = A.merge_channels([_parsed("radial_h", c, assumed=True, note="units note")], [],
                               iso_table=cfg["iso_table"], thresholds=cfg["thresholds"])
        assert out.case is c  # same object, not a copy
        assert out.report_notes == [] and out.coverage_note is None
        assert out.conversion_note == "units note"
        assert out.fail_closed_checks == []
        assert out.channel_summary["channels"][0]["assumed"] is True

    def test_single_explicit_direction_adds_notes(self, cfg):
        c = _y_case([(30.0, 0.5)])
        out = A.merge_channels([_parsed("radial_h", c, assumed=False)], [],
                               iso_table=cfg["iso_table"], thresholds=cfg["thresholds"])
        assert out.case is not c  # went through the real merge, not the identity path
        assert any("Channels measured" in n for n in out.report_notes)


# ── merge shape + scope ───────────────────────────────────────────────────────
class TestMergeShape:
    def test_three_channels_populate_all_axes(self, cfg):
        chans = [_parsed("radial_h", _y_case([(30.0, 0.3)])),
                 _parsed("radial_v", _y_case([(30.0, 0.3)])),
                 _parsed("axial", _y_case([(30.0, 0.6)]))]
        out = A.merge_channels(chans, [], iso_table=cfg["iso_table"], thresholds=cfg["thresholds"])
        assert set(out.case.spectra) == {"x", "y", "z"}
        sd = out.case.sensor_data
        assert sd.x_velocity_mm_sec and sd.y_velocity_mm_sec and sd.z_velocity_mm_sec
        assert out.case.machine.axial_axis == "x"

    def test_mixed_scale_scope_union_and_coverage_note(self, cfg):
        rich = _parsed("radial_h", _y_case([(30.0, 0.3)], scope=("zone", "severity", "rca", "trend")))
        rca_only = _parsed("axial", _y_case([(30.0, 0.6)], scope=("rca",)))
        out = A.merge_channels([rich, rca_only], [], iso_table=cfg["iso_table"], thresholds=cfg["thresholds"])
        assert "severity" in out.case.validation_scope  # union keeps it
        assert any("frequency identification only" in n for n in out.report_notes)

    def test_raw_spectra_dropped_when_not_every_channel_has_it(self, cfg):
        with_raw = _parsed("radial_h", _y_case([(30.0, 0.3)], raw=True))
        without = _parsed("axial", _y_case([(30.0, 0.6)], raw=False))
        out = A.merge_channels([with_raw, without], [], iso_table=cfg["iso_table"], thresholds=cfg["thresholds"])
        assert out.case.raw_spectra is None


# ── note copy ─────────────────────────────────────────────────────────────────
class TestNotes:
    def test_channels_measured_lists_present_and_missing(self):
        note = A.channels_measured_note({"radial_h", "radial_v"})
        assert "radial – horizontal (y)" in note and "radial – vertical (z)" in note
        assert "not measured: axial" in note

    def test_next_tier_axial_missing(self):
        assert "axial measurement" in A.next_tier_note({"radial_h", "radial_v"})

    def test_next_tier_all_present_is_none(self):
        assert A.next_tier_note({"radial_h", "radial_v", "axial"}) is None


# ══════════════════════════════════════════════════════════════════════════════
# Diagnosis-level: conjunction differs from any single channel; axial != imbalance
# ══════════════════════════════════════════════════════════════════════════════
def _diagnose(case: Case, cfg) -> list[str]:
    from vib_agent.pipeline import run_analysis
    r = run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], rules=cfg["rules"])
    return [f.fault for f in r.findings]


# Session PDMFIX. These fixtures were written at 0.15 / 0.14 / 0.60 mm/s — every
# axis deep in ISO Zone A (group 2 rigid, ab = 1.4). The 1x-family severity gate
# now refuses to commit ANY 1x pattern that quiet, which is the whole point of
# that ruling, so the conjunction proof had to be lifted above the bar. Every
# amplitude is multiplied by one constant, so each RATIO the proof actually rests
# on is untouched — axial/radial stays 4.0, and the H channel shared by the trio
# and the lone-radial case is still literally the same channel. Only loudness
# moved: radial-h 0.151 -> 1.51 mm/s (Zone B), axial 0.600 -> 6.00 mm/s.
_GATE_SCALE = 10.0


class TestConjunction:
    """The proof of 'analyzed in conjunction': an axial-dominant 1x trio commits a
    misalignment that NO single channel would — while a lone radial channel commits
    imbalance (today's mislabel) and a lone axial channel commits neither."""

    def _kw(self, cfg):
        return dict(iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], rules=cfg["rules"])

    def test_trio_commits_angular_not_imbalance(self, cfg):
        chans = [_parsed("radial_h", _y_case([(30.0, 0.15 * _GATE_SCALE)])),
                 _parsed("radial_v", _y_case([(30.0, 0.14 * _GATE_SCALE)])),
                 _parsed("axial", _y_case([(30.0, 0.60 * _GATE_SCALE)]))]
        out = A.merge_channels(chans, [], **self._kw(cfg))
        faults = _diagnose(out.case, cfg)
        assert "angular_misalignment" in faults
        assert "imbalance" not in faults

    def test_radial_h_alone_commits_imbalance(self, cfg):
        out = A.merge_channels(
            [_parsed("radial_h", _y_case([(30.0, 0.15 * _GATE_SCALE)]))], [], **self._kw(cfg)
        )
        assert "imbalance" in _diagnose(out.case, cfg)

    def test_the_lone_radial_channel_caps_at_medium(self, cfg):
        """Session PDMFIX ruling (2), riding alongside the delta above: the lone
        radial channel still commits, but it can no longer reach `high` on the
        strength of an axial axis it never measured."""
        out = A.merge_channels(
            [_parsed("radial_h", _y_case([(30.0, 0.15 * _GATE_SCALE)]))], [], **self._kw(cfg)
        )
        from vib_agent.pipeline import run_analysis
        result = run_analysis(out.case, iso_table=cfg["iso_table"],
                              thresholds=cfg["thresholds"], rules=cfg["rules"])
        imbalance = next(f for f in result.findings if f.fault == "imbalance")
        assert imbalance.confidence == "medium"

    def test_axial_alone_commits_neither(self, cfg):
        out = A.merge_channels(
            [_parsed("axial", _y_case([(30.0, 0.60 * _GATE_SCALE)]))], [], **self._kw(cfg)
        )
        faults = _diagnose(out.case, cfg)
        assert "imbalance" not in faults and "angular_misalignment" not in faults


class TestAxialNoImbalance:
    """Closes the latent mislabeling bug: an axial-declared single file with a
    dominant 1x must NOT commit imbalance (the axial 1x is a misalignment/bent-shaft
    signature, not radial imbalance)."""

    def test_axial_single_1x_dominant_refuses_imbalance(self, cfg):
        out = A.merge_channels([_parsed("axial", _y_case([(30.0, 0.8)]))], [],
                               iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], rules=cfg["rules"])
        assert out.case.sensor_data.x_velocity_mm_sec and out.case.sensor_data.y_velocity_mm_sec is None
        assert "imbalance" not in _diagnose(out.case, cfg)
        assert any("Channels measured" in n for n in out.report_notes)  # explicit direction -> note present


# ══════════════════════════════════════════════════════════════════════════════
# Cross-file speed agreement (assembly layer)
# ══════════════════════════════════════════════════════════════════════════════
class TestSpeedAgreement:
    def _kw(self, cfg):
        return dict(iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], rules=cfg["rules"])

    def test_disagreement_warns_but_proceeds(self, cfg):
        # 1800 rpm -> 30 Hz shaft. One channel at 30 Hz (agrees), one at 47 Hz (no k*30 within 5%).
        chans = [_parsed("radial_h", _y_case([(30.0, 0.4)], rpm=1800.0)),
                 _parsed("radial_v", _y_case([(47.0, 0.4)], rpm=1800.0))]
        out = A.merge_channels(chans, [], **self._kw(cfg))
        assert out.fail_closed_checks == []  # proceeds
        assert any("Speed check warning" in n for n in out.report_notes)
        assert out.channel_summary["speed_warning"] is not None

    def test_harmonic_dominant_channel_not_flagged(self, cfg):
        # A parallel-misalignment radial channel is legitimately 2x-dominant (60 Hz).
        chans = [_parsed("radial_h", _y_case([(30.0, 0.4)], rpm=1800.0)),
                 _parsed("radial_v", _y_case([(60.0, 0.4)], rpm=1800.0))]
        out = A.merge_channels(chans, [], **self._kw(cfg))
        assert out.fail_closed_checks == []
        assert not any("Speed check warning" in n for n in out.report_notes)
        assert out.channel_summary["speed_warning"] is None

    def test_no_channel_agrees_fails_closed(self, cfg):
        chans = [_parsed("radial_h", _y_case([(47.0, 0.4)], rpm=1800.0)),
                 _parsed("axial", _y_case([(53.0, 0.4)], rpm=1800.0))]
        out = A.merge_channels(chans, [], **self._kw(cfg))
        assert len(out.fail_closed_checks) == 1
        assert out.fail_closed_checks[0].name == "cross_channel_speed_agreement"
        assert "same machine or operating condition" in out.fail_closed_checks[0].reason


# ══════════════════════════════════════════════════════════════════════════════
# Partial gate / unreadable channels (assembly layer)
# ══════════════════════════════════════════════════════════════════════════════
class TestPartialGate:
    def _kw(self, cfg):
        return dict(iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], rules=cfg["rules"])

    def _off_y_case(self):
        # near-zero amplitude -> machine-running gate fails for this channel
        return _y_case([], rpm=1800.0)  # no peaks; noise floor 0.001

    def test_one_channel_gate_fails_others_proceed(self, cfg):
        # a near-dead channel: flatten it to ~0 so machine_running fails
        dead = _y_case([(30.0, 0.0)], rpm=1800.0)
        dead = dead.model_copy(update={"sensor_data": dead.sensor_data.model_copy(
            update={"y_velocity_mm_sec": 1e-6, "y_rms_ACC_G": 1e-7})})
        chans = [_parsed("radial_h", _y_case([(30.0, 0.4)], rpm=1800.0)),
                 _parsed("axial", dead)]
        out = A.merge_channels(chans, [], **self._kw(cfg))
        assert out.fail_closed_checks == []
        statuses = {c["direction"]: c["status"] for c in out.channel_summary["channels"]}
        assert statuses["radial_h"] == "ok" and statuses["axial"] == "gate_fail"
        assert set(out.case.spectra) == {"y"}  # only the passing channel survives
        assert any("Channel status" in n for n in out.report_notes)

    def test_all_channels_gate_fail_returns_fail_closed(self, cfg):
        def dead(direction):
            c = _y_case([(30.0, 0.0)], rpm=1800.0)
            c = c.model_copy(update={"sensor_data": c.sensor_data.model_copy(
                update={"y_velocity_mm_sec": 1e-6, "y_rms_ACC_G": 1e-7})})
            return _parsed(direction, c)
        out = A.merge_channels([dead("radial_h"), dead("axial")], [], **self._kw(cfg))
        assert len(out.fail_closed_checks) == 1
        assert out.fail_closed_checks[0].name == "per_channel_quality"

    def test_unreadable_channel_carried_as_status(self, cfg):
        chans = [_parsed("radial_h", _y_case([(30.0, 0.4)]))]
        unread = [A.UnreadableChannel("axial", "Could not read this file — unexpected layout.")]
        out = A.merge_channels(chans, unread, **self._kw(cfg))
        statuses = {c["direction"]: c["status"] for c in out.channel_summary["channels"]}
        assert statuses["axial"] == "unreadable"
        assert any("unreadable" in n for n in out.report_notes)


# ══════════════════════════════════════════════════════════════════════════════
# Note ordering (pins the reverse-insertion contract) + coverage routing
# ══════════════════════════════════════════════════════════════════════════════
class TestNoteWiring:
    def test_finalize_renders_notes_top_to_bottom(self):
        """The note ORDERING in report.md, on its own.

        Session V2-WIRE split the markdown half of `_finalize_markdown_and_pdf`
        out into `markdown_with_notes`, because the other half now renders a PDF
        from the analysis and needs a result, a case and a chart manifest — none
        of which this property has anything to do with.
        """
        from vib_agent.webapp.worker import markdown_with_notes

        md = ("# R\n\n## Machine Details\n\n| A | B |\n|---|---|\n| x | y |\n\n## Data Quality\n\nbody\n")
        # assembly notes visual order [A, B, C] -> worker passes reversed after conversion
        assembly_notes = ["ALPHA", "BETA", "GAMMA"]
        out = markdown_with_notes(md, ["CONV", *reversed(assembly_notes)], coverage_note="TIER")
        # top-to-bottom within the Machine-Details block: ALPHA, BETA, GAMMA, CONV
        order = [out.index(t) for t in ("ALPHA", "BETA", "GAMMA", "CONV")]
        assert order == sorted(order), out
        assert "_TIER_" in out  # coverage note routed (falls back to MD hook here — no S&C section)


# ══════════════════════════════════════════════════════════════════════════════
# API validation (4xx) + byte-compat + e2e — through the FastAPI TestClient
# ══════════════════════════════════════════════════════════════════════════════
import csv  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from tests.fake_anthropic import FakeAnthropicClient, build_consistent_echo, draft_message  # noqa: E402
from tests.test_webapp_e2e import _off_csv, _poll_until_terminal, _spectrum_csv, _webapp_cfg  # noqa: E402
from vib_agent.adapters.uploads import parse_upload  # noqa: E402
from vib_agent.adapters.uploads.common import UploadForm  # noqa: E402
from vib_agent.agent.consistency import TITLE_TEMPLATE  # noqa: E402
from vib_agent.webapp.app import create_app  # noqa: E402

_RPM = 1800.0


def _axis_csv(path, peaks, *, n=400, fmax=200.0):
    freqs = [i * fmax / n for i in range(n)]
    amp = [0.001] * n
    for pk_hz, pk_amp in peaks:
        idx = min(range(n), key=lambda i: abs(freqs[i] - pk_hz))
        amp[idx] = pk_amp
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["freq_hz", "amplitude"])
        for fr, a in zip(freqs, amp):
            w.writerow([fr, a])


def _app(fake=None):
    return create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"},
                      anthropic_client_factory=(lambda: fake) if fake else None)


def _base_form(**over):
    # `machine_type` is REQUIRED on the wire since Session INTAKE-2 (PARTC F-2):
    # the form refuses a blank rather than recording "motor" for whatever the
    # analyst actually measured. Declared once, here, like every other field
    # this helper supplies.
    form = {"invite_code": "demo-code", "machine_alias": "TestPump", "rpm": str(_RPM),
            "iso_group": "2", "iso_support": "rigid", "bearing_model": "6206",
            "machine_type": "pump"}
    form.update({k: str(v) for k, v in over.items()})
    return form


def _post_multi(client, files_dirs, **form_over):
    """files_dirs: list of (path, direction|None). Posts file/file_2/file_3."""
    names = ["file", "file_2", "file_3"]
    dirs = ["direction", "direction_2", "direction_3"]
    data = _base_form(**form_over)
    fhandles, files = [], {}
    for i, (p, d) in enumerate(files_dirs):
        fo = open(p, "rb")
        fhandles.append(fo)
        files[names[i]] = (p.name, fo, "text/csv")
        if d is not None:
            data[dirs[i]] = d
    try:
        return client.post("/api/jobs", files=files, data=data)
    finally:
        for fo in fhandles:
            fo.close()


def _consistent_multi(files_dirs, alias="TestPump"):
    """A fake-LLM echo consistent with the MERGED case (must carry every committed
    fault, or the consistency check correctly hard-fails)."""
    bearings = load_config("bearings")
    iso = load_config("iso_zones")["zones"]; th = load_thresholds("route"); rules = load_config("next_measurements")
    parsed = []
    for p, d in files_dirs:
        form = UploadForm(machine_alias=alias, rpm=_RPM, iso_group="2", iso_support="rigid", bearing_model="6206")
        case, kind, note = parse_upload(p, form, bearings_cfg=bearings)
        parsed.append(A.ParsedChannel(d, False, case, kind, note))
    from vib_agent.pipeline import run_analysis
    outcome = A.merge_channels(parsed, [], iso_table=iso, thresholds=th, rules=rules)
    result = run_analysis(outcome.case, iso_table=iso, thresholds=th, rules=rules)
    title = TITLE_TEMPLATE.format(machine_name=alias)
    narrative = f"{title}\n\nBody.\n\nDRAFT -- prepared by automated analysis, pending analyst review."
    return draft_message(narrative, build_consistent_echo(result))


class TestApiValidation:
    def test_duplicate_direction_400(self, tmp_path):
        a = tmp_path / "a.csv"; b = tmp_path / "b.csv"
        _axis_csv(a, [(30.0, 0.3)]); _axis_csv(b, [(30.0, 0.3)])
        with TestClient(_app()) as c:
            r = _post_multi(c, [(a, "axial"), (b, "axial")])
            assert r.status_code == 400
            assert "both marked Axial" in r.json()["detail"] and "selector" in r.json()["detail"]

    def test_multi_missing_direction_400(self, tmp_path):
        a = tmp_path / "a.csv"; b = tmp_path / "b.csv"
        _axis_csv(a, [(30.0, 0.3)]); _axis_csv(b, [(30.0, 0.3)])
        with TestClient(_app()) as c:
            r = _post_multi(c, [(a, "axial"), (b, None)])
            assert r.status_code == 400 and "needs a direction" in r.json()["detail"]

    def test_unknown_direction_400(self, tmp_path):
        a = tmp_path / "a.csv"
        _axis_csv(a, [(30.0, 0.3)])
        with TestClient(_app()) as c:
            r = _post_multi(c, [(a, "sideways")])
            assert r.status_code == 400 and "direction" in r.json()["detail"].lower()

    def test_multi_file_trend_mode_400(self, tmp_path):
        a = tmp_path / "a.csv"; b = tmp_path / "b.csv"
        _axis_csv(a, [(30.0, 0.3)]); _axis_csv(b, [(30.0, 0.3)])
        with TestClient(_app()) as c:
            r = _post_multi(c, [(a, "radial_h"), (b, "axial")], mode="trend")
            assert r.status_code == 400 and "single file" in r.json()["detail"]

    def test_old_single_file_post_still_202(self, tmp_path):
        p = tmp_path / "spec.csv"
        _spectrum_csv(p)
        fake = FakeAnthropicClient(responses=[_consistent_multi([(p, "radial_h")])])
        with TestClient(_app(fake)) as c:
            with open(p, "rb") as f:
                r = c.post("/api/jobs", files={"file": (p.name, f, "text/csv")}, data=_base_form())
            assert r.status_code == 202


class TestByteCompatDefaultedSingle:
    def test_defaulted_single_report_has_no_multiaxis_lines(self, tmp_path):
        p = tmp_path / "spec.csv"
        _spectrum_csv(p)
        fake = FakeAnthropicClient(responses=[_consistent_multi([(p, "radial_h")])])
        app = _app(fake)
        with TestClient(app) as c:
            with open(p, "rb") as f:
                r = c.post("/api/jobs", files={"file": (p.name, f, "text/csv")}, data=_base_form())
            job_id = r.json()["job_id"]
            assert _poll_until_terminal(c, job_id)["state"] == "done"
            job = app.state.vib.registry.get(job_id)
            md = (job.job_dir / "report.md").read_text() if (job.job_dir / "report.md").exists() else \
                job.pdf_path.read_bytes().decode("latin-1")
        for banned in ("Channels measured", "Channel status", "Speed check", "would additionally enable"):
            assert banned not in md, f"defaulted single-file report leaked multi-axis line: {banned!r}"

    def test_browser_empty_direction_is_the_identity_path(self, tmp_path):
        """The test above omits the `direction` field. A BROWSER never does.

        `index.html` makes radial-horizontal the EMPTY option value
        (`<option value="" selected>`), and `app.js` substitutes 'radial_h' only
        when nChannels > 1 — so one file from a browser posts `direction=""`, and
        so does every press of the hero's "Run the example analysis" button,
        which sets `direction: ''` explicitly. `_resolve_directions` branches on
        `d is None`; the empty string and an absent field are the same thing
        there ONLY because FastAPI coerces an empty-string value on a
        non-required `Form` field back to that field's default before the handler
        runs (`fastapi/dependencies/utils.py`, the `value == ""` branch).

        So the guard is correct today, but correct by a third-party library's
        coercion rule that is invisible from the file it protects — and until S7
        the only test covering it sent bytes no browser sends. A FastAPI upgrade
        narrowing that rule would turn every single-file upload and every demo
        press into a 400 telling the analyst to choose a direction they already
        chose, and the suite would stay green. This pins the browser's actual
        wire format, at the HTTP boundary: a unit call to
        `_resolve_directions([(f, "")], "spectrum")` would raise 400 and pin the
        wrong thing, because the empty string never gets that far.
        """
        p = tmp_path / "spec.csv"
        _spectrum_csv(p)
        fake = FakeAnthropicClient(responses=[_consistent_multi([(p, "radial_h")])])
        app = _app(fake)
        with TestClient(app) as c:
            with open(p, "rb") as f:
                r = c.post("/api/jobs", files={"file": (p.name, f, "text/csv")},
                           data=_base_form(direction=""))          # <-- THE LINE
            assert r.status_code == 202, r.text
            job_id = r.json()["job_id"]
            assert _poll_until_terminal(c, job_id)["state"] == "done"
            job = app.state.vib.registry.get(job_id)
            md = (job.job_dir / "report.md").read_text() if (job.job_dir / "report.md").exists() \
                else job.pdf_path.read_bytes().decode("latin-1")
        for banned in ("Channels measured", "Channel status", "Speed check",
                       "would additionally enable"):
            assert banned not in md, f"empty-direction upload left the identity path: {banned!r}"


class TestMultiAxisE2E:
    def test_three_file_conjunction_done_and_channels_line(self, tmp_path):
        h = tmp_path / "h.csv"; v = tmp_path / "v.csv"; a = tmp_path / "a.csv"
        _axis_csv(h, [(30.0, 0.15)]); _axis_csv(v, [(30.0, 0.14)]); _axis_csv(a, [(30.0, 0.60)])
        files_dirs = [(h, "radial_h"), (v, "radial_v"), (a, "axial")]
        fake = FakeAnthropicClient(responses=[_consistent_multi(files_dirs)])
        app = _app(fake)
        with TestClient(app) as c:
            r = _post_multi(c, files_dirs)
            assert r.status_code == 202
            job_id = r.json()["job_id"]
            data = _poll_until_terminal(c, job_id)
            assert data["state"] == "done", data
            assert data["channels"]["channels"][2]["direction"] == "axial"
            job = app.state.vib.registry.get(job_id)
            pdf = c.get(f"/api/jobs/{job_id}/report.pdf")
            assert pdf.status_code == 200

    def test_no_speed_agreement_fails_closed(self, tmp_path):
        a = tmp_path / "a.csv"; b = tmp_path / "b.csv"
        _axis_csv(a, [(47.0, 0.4)]); _axis_csv(b, [(53.0, 0.4)])
        files_dirs = [(a, "radial_h"), (b, "axial")]
        fake = FakeAnthropicClient(responses=[])  # LLM must never be called
        app = _app(fake)
        with TestClient(app) as c:
            r = _post_multi(c, files_dirs)
            job_id = r.json()["job_id"]
            data = _poll_until_terminal(c, job_id)
            assert data["state"] == "gate_fail", data
            assert fake.messages.calls == []
            pdf = c.get(f"/api/jobs/{job_id}/report.pdf")
            assert pdf.status_code == 200


class TestUiMeasurements:
    def test_index_exposes_measurement_slots_and_helper(self):
        with TestClient(_app()) as c:
            html = c.get("/").text
        for field in ("file", "file_2", "file_3", "direction", "direction_2", "direction_3", "mode"):
            assert f'name="{field}"' in html, f"form field {field} missing"
        assert "Upload what you measured" in html
        assert "Multi-channel is spectrum-only" in html
        # direction vocabulary present in the selectors
        for val in ("radial_h", "radial_v", "axial"):
            assert f'value="{val}"' in html

    # ── U2 (UX-WIRE) ────────────────────────────────────────────────────
    def test_the_channel_slots_are_first_class_not_buried_under_more_options(self):
        """Slots 2 and 3 used to live inside `<details class="more">`, which is
        also where the bearing model and the WAV scale live. A three-channel
        upload is the product's headline capability and it was filed under
        "More options" — so the form read as one-axis-only unless you went
        looking. All three slots now sit in MEASUREMENTS with slot 1."""
        with TestClient(_app()) as c:
            html = c.get("/").text
        measurements = html.split('<fieldset id="measurements">')[1].split("</fieldset>")[0]
        for field in ("file", "file_2", "file_3", "direction", "direction_2", "direction_3"):
            assert f'name="{field}"' in measurements, f"{field} is not in MEASUREMENTS"
        more = html.split('<details class="more"')[1].split("</details>")[0]
        for field in ("file_2", "file_3", "direction_2", "direction_3"):
            assert f'name="{field}"' not in more, f"{field} is still under More options"
        assert "+ Add a channel" in measurements

    def test_direction_is_not_confusable_with_the_frequency_axis(self):
        """The same confusion U3 fixed on the confirm card, caught here at the
        point the analyst SETS it. `direction` is where the sensor pointed;
        the frequency axis is read from the file and is not editable at all."""
        with TestClient(_app()) as c:
            html = c.get("/").text
        assert "Direction is where the sensor was pointed" in html
        assert "It is not the frequency axis" in html

    def test_slot_one_still_posts_an_empty_direction(self):
        """UXWIRE_PROMPT §10.1, SETTLED and measured: a one-file browser upload
        posts `direction=""`, FastAPI coerces it back to the field default
        before the handler runs, and `app.py`'s `d is None` matches — which is
        what keeps a single-file report byte-identical to the pre-Session-E
        path. Changing this option's value turns every single-file upload and
        every press of the demo button into a 400 telling the analyst to choose
        a direction they already chose. U2 moves position only."""
        with TestClient(_app()) as c:
            html = c.get("/").text
        measurements = html.split('<fieldset id="measurements">')[1].split("</fieldset>")[0]
        slot1 = measurements.split('name="direction"')[1].split("</select>")[0]
        assert '<option value="" selected>' in slot1

    def test_the_oversize_card_says_the_limit_is_per_file(self):
        """The cap is per FILE. The live card never said so, leaving an analyst
        with three channels to conclude the whole submission was too big."""
        js = (pathlib.Path(__file__).resolve().parents[1]
              / "src/vib_agent/webapp/static/app.js").read_text()
        card = js.partition("function sizeCard(")[2].partition("\n}")[0]
        assert "<b>per file</b>" in card
        assert "upload the channels separately" in card
