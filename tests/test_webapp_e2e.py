"""End-to-end tests for the Phase 6 webapp (webapp/app.py), driven through
FastAPI's TestClient. A fake Anthropic client (tests/fake_anthropic.py) is
injected everywhere a job might reach the drafting stage -- zero real API
calls anywhere in this suite.
"""

from __future__ import annotations

import csv
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient, build_consistent_echo, draft_message
from vib_agent.agent.consistency import TITLE_TEMPLATE
from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.webapp.app import create_app

RPM = 1800.0
_DEFAULT_WEBAPP_CFG = load_config("webapp")


def _webapp_cfg(**overrides):
    cfg = dict(_DEFAULT_WEBAPP_CFG)
    # Every per-IP budget is effectively unlimited for tests that are not ABOUT
    # the limiter. They share one test-client IP and poll far faster than a
    # browser does, so a real cap here produces flakes that look like product
    # failures -- which is exactly the confusion hotfix-net was fixing for
    # analysts, and the test harness deserves the same treatment.
    #
    # The limiter's real behaviour is pinned ONLY by the tests that set caps
    # explicitly: tests/test_hardening.py and
    # tests/test_poll_resilience.py::TestStatusPollBudget. Nothing about limits
    # is asserted by accident, from a default, anywhere else.
    cfg["ip_requests_per_minute"] = 1_000_000
    cfg["ip_job_posts_per_hour"] = 1_000_000
    cfg["ip_status_requests_per_minute"] = 1_000_000
    # `job_ttl_minutes` is deliberately NOT pinned here, and that is a finding
    # rather than an omission. The flake this file's deadline constant exists for
    # looks like an expiry -- a slow job, a poll that gives up -- so raising the
    # test TTL is the obvious fix to reach for. It is the wrong one twice over:
    # the shipped TTL is 60 MINUTES against jobs measured in tens of SECONDS, so
    # expiry was never the mechanism; and TestDownloadSemantics genuinely depends
    # on this default being the product's own value (it ages a job by 61*60s and
    # asserts the expiry card says "60 minutes"), so overriding it here breaks
    # two correct tests to fix a race they are not in. The real deadline is
    # _JOB_WALLCLOCK_DEADLINE_S below.
    cfg.update(overrides)  # explicit test overrides (e.g. tiny caps) still win
    return cfg


class TestAnalysisProfileWiring:
    """Session D: the upload path must analyse on the `route` profile. Uploads are
    third-party/route-collected data; `streaming` is the NCD sensor pipeline's frozen
    production profile. This was a silent product defect — app.py called
    load_thresholds() with no argument, which resolves `active_profile` (streaming),
    so every route-calibrated detector guard was inactive in the shipped service.
    These tests pin the wiring so it cannot regress by omission again.
    """

    def test_webapp_config_declares_route(self):
        assert load_config("webapp")["analysis_profile"] == "route"

    def test_app_state_thresholds_are_the_route_profile(self):
        app = create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "engineer-1"})
        thresholds = app.state.vib.thresholds
        route = load_thresholds("route")
        assert thresholds == route
        # and it is genuinely NOT streaming — the guards must be present
        assert thresholds["rca"].get("epsilon_sync") is not None
        assert thresholds["rca"].get("floor_min") is not None
        assert thresholds["rca"].get("imbalance_radial_dominance") is not None

    def test_streaming_profile_is_untouched_and_still_selectable(self):
        streaming = load_thresholds("streaming")
        # The NCD pipeline's profile stays frozen: none of the route-only guards leak in.
        assert streaming["rca"].get("epsilon_sync") is None
        assert streaming["rca"].get("floor_min") is None
        assert streaming["rca"].get("imbalance_radial_dominance") is None

    def test_profile_is_read_from_config_not_hardcoded(self):
        app = create_app(
            webapp_cfg=_webapp_cfg(analysis_profile="streaming"),
            invite_codes={"demo-code": "engineer-1"},
        )
        assert app.state.vib.thresholds == load_thresholds("streaming")


def _spectrum_csv(path: Path, *, peak_hz: float = 107.03, n: int = 400, fmax: float = 200.0) -> None:
    freqs = [i * fmax / n for i in range(n)]
    amps = [0.001] * n
    idx = min(range(n), key=lambda i: abs(freqs[i] - peak_hz))
    amps[idx] = 0.5
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["freq_hz", "amplitude"])
        for fr, a in zip(freqs, amps):
            w.writerow([fr, a])


def _off_csv(path: Path, *, n: int = 400, fmax: float = 200.0) -> None:
    freqs = [i * fmax / n for i in range(n)]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["freq_hz", "amplitude"])
        for fr in freqs:
            w.writerow([fr, 0.00001])


def _trend_csv(path: Path, *, n: int = 30, start: float = 2.0, end: float = 3.5) -> None:
    now = datetime.now(timezone.utc)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "overall_rms"])
        for i in range(n):
            ts = (now - timedelta(days=(n - i))).isoformat()
            val = start + (end - start) * i / max(n - 1, 1)
            w.writerow([ts, val])


def _consistent_response_for(csv_path: Path, machine_alias: str, **form_kwargs):
    bearings_cfg = load_config("bearings")
    form = UploadForm(machine_alias=machine_alias, rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor", **form_kwargs)
    case, _, _ = parse_upload(csv_path, form, bearings_cfg=bearings_cfg)
    iso_table = load_config("iso_zones")["zones"]
    result = run_analysis(case, iso_table=iso_table, thresholds=load_thresholds(), rules=load_config("next_measurements"))
    title = TITLE_TEMPLATE.format(machine_name=machine_alias)
    narrative = f"{title}\n\nBody.\n\nDRAFT -- prepared by automated analysis, pending analyst review."
    return draft_message(narrative, build_consistent_echo(result))


# The test client polls the same way the browser does, and for the same
# reasons (hotfix-net-t). A helper that reads json()["state"] off whatever came
# back turns a 500 or a 429 into a KeyError three frames deep -- unreadable, and
# it blames the wrong thing.
_POLL_SLEEP_S = 0.2          # never faster than a real client would poll
_MAX_TEST_RETRY_AFTER_S = 2.0  # honour Retry-After, but a test cannot wait 60s

# ── THE RACE, NAMED ───────────────────────────────────────────────────────
# A webapp job in this suite runs a REAL analysis and a REAL PDF render. On this
# machine the PDF path is pandoc + tectonic (weasyprint is installed without its
# native libs -- see CLAUDE.md, Environment), i.e. a LaTeX engine subprocess, and
# `worker_concurrency` is 2 while pytest is running everything else on the same
# cores.
#
# Measured, not guessed. A full-suite run at 39d119c logged these job durations
# in the failures it produced:
#
#     duration_ms=36897.6   outcome=degraded   (test_poll_resilience)
#     duration_ms=64225.2   outcome=degraded   (test_inference_multifile)
#     duration_ms=66960.2   outcome=degraded   (test_webapp_e2e)
#     duration_ms=69428.4   outcome=gate_fail  (test_intake_adversarial)
#
# Every one of those jobs FINISHED, correctly, and was logged doing so. The tests
# failed anyway because the deadline they were waited on was 60s, and because
# test_poll_resilience budgeted the job's own wall clock at 30s. In isolation the
# same jobs take 5-40s, which is why these read as random flakes attached to
# whichever test happened to be running when the machine was busiest --
# accel_g.asc's confirm-card test among them.
#
# So the deadline is a property of the HOST, not of the product, and it is pinned
# here rather than left to inherit whatever seems fast enough. 300s is ~4x the
# measured worst case: a genuinely stuck job still fails the suite, just later; a
# merely slow one no longer reports a product defect that is not there.
#
# This is the second time this deadline has moved for exactly this reason (10s ->
# 60s -> 300s), so it is written down properly this time.
_JOB_WALLCLOCK_DEADLINE_S = 300.0


def _status_once(client: TestClient, job_id: str) -> dict | None:
    """One protocol-honest status poll.

    Returns the decoded status, or None when the server asked us to wait (a 429,
    which this honours before returning). Any OTHER non-200 is an assertion with
    the status and body in it -- a future failure must be readable at a glance.
    """
    response = client.get(f"/api/jobs/{job_id}")
    if response.status_code == 429:
        raw = response.headers.get("Retry-After")
        try:
            wait = float(raw) if raw is not None else _MAX_TEST_RETRY_AFTER_S
        except ValueError:
            wait = _MAX_TEST_RETRY_AFTER_S
        time.sleep(max(0.0, min(wait, _MAX_TEST_RETRY_AFTER_S)))
        return None
    assert response.status_code == 200, (
        f"status poll for job {job_id} returned {response.status_code}: {response.text[:400]}"
    )
    data = response.json()
    assert isinstance(data, dict) and "state" in data, f"status poll returned no state: {data!r}"
    return data


def _poll_until_terminal(client: TestClient, job_id: str, *, timeout_s: float | None = None) -> dict:
    # Deadline is _JOB_WALLCLOCK_DEADLINE_S (env-tunable via
    # VIB_TEST_POLL_TIMEOUT_S) -- see the measured durations recorded beside that
    # constant. The job reaching a terminal state is still asserted; only the
    # patience is host-calibrated.
    if timeout_s is None:
        timeout_s = float(
            os.environ.get("VIB_TEST_POLL_TIMEOUT_S", str(_JOB_WALLCLOCK_DEADLINE_S))
        )
    deadline = time.monotonic() + timeout_s
    data: dict = {"state": "queued"}
    while time.monotonic() < deadline:
        polled = _status_once(client, job_id)
        if polled is None:          # asked to wait; the job is unaffected
            continue
        data = polled
        if data["state"] not in ("queued", "running"):
            return data
        time.sleep(_POLL_SLEEP_S)
    raise AssertionError(f"job {job_id} did not reach a terminal state in time (last: {data})")


def _post_upload(client: TestClient, path: Path, *, invite_code="demo-code", **form_overrides) -> "TestClientResponse":  # noqa: F821
    form = {
        "invite_code": invite_code,
        "machine_alias": "TestPump",
        "rpm": "1800",
        "iso_group": "2",
        "iso_support": "rigid", "machine_type": "motor",
        "bearing_model": "6206",
    }
    form.update(form_overrides)
    with open(path, "rb") as f:
        return client.post("/api/jobs", files={"file": (path.name, f, "text/csv")}, data=form)


class TestHappyPath:
    def test_csv_upload_done_report_downloadable(self, tmp_path):
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        response = _consistent_response_for(csv_path, "TestPump", bearing_model="6206")
        fake_client = FakeAnthropicClient(responses=[response])
        app = create_app(
            webapp_cfg=_webapp_cfg(),
            invite_codes={"demo-code": "engineer-1"},
            anthropic_client_factory=lambda: fake_client,
        )
        with TestClient(app) as client:
            r = _post_upload(client, csv_path)
            assert r.status_code == 202
            job_id = r.json()["job_id"]

            data = _poll_until_terminal(client, job_id)
            assert data["state"] == "done"

            # Bug #2 fix: GET does NOT purge — the report is served idempotently
            # within TTL, so a browser viewer's follow-up/ranged requests all work.
            pdf = client.get(f"/api/jobs/{job_id}/report.pdf")
            assert pdf.status_code == 200
            assert pdf.content[:4] == b"%PDF"

            job = app.state.vib.registry.get(job_id)
            assert job.purged is False
            assert job.job_dir.exists()

            # still fetchable, still the SAME bytes
            again = client.get(f"/api/jobs/{job_id}/report.pdf")
            assert again.status_code == 200
            assert again.content == pdf.content


class TestDownloadSemantics:
    """Bug #2: purge is decoupled from GET. At COMPLETION only report.pdf
    (+ consent-gated trace) remains; GET /report.pdf is idempotent within TTL;
    the TTL sweep removes everything; expired/unknown reports render a friendly
    HTML card, never a raw JSON body."""

    def _done_app(self, tmp_path, *, retain_trace=False):
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        response = _consistent_response_for(csv_path, "TestPump", bearing_model="6206")
        app = create_app(
            webapp_cfg=_webapp_cfg(),
            invite_codes={"demo-code": "engineer-1"},
            retain_traces_enabled=retain_trace,
            anthropic_client_factory=lambda: FakeAnthropicClient(responses=[response]),
        )
        return app, csv_path

    def test_two_sequential_gets_are_identical_bytes(self, tmp_path):
        app, csv_path = self._done_app(tmp_path)
        with TestClient(app) as client:
            job_id = _post_upload(client, csv_path).json()["job_id"]
            assert _poll_until_terminal(client, job_id)["state"] == "done"
            a = client.get(f"/api/jobs/{job_id}/report.pdf")
            b = client.get(f"/api/jobs/{job_id}/report.pdf")
            assert a.status_code == 200 and b.status_code == 200
            assert a.content == b.content and a.content[:4] == b"%PDF"

    def test_get_after_ttl_is_friendly_410_no_json(self, tmp_path):
        app, csv_path = self._done_app(tmp_path)
        with TestClient(app) as client:
            job_id = _post_upload(client, csv_path).json()["job_id"]
            assert _poll_until_terminal(client, job_id)["state"] == "done"
            job = app.state.vib.registry.get(job_id)
            job.created_at -= 61 * 60  # force past TTL, without running the sweep loop
            resp = client.get(f"/api/jobs/{job_id}/report.pdf")
            assert resp.status_code == 410
            assert "text/html" in resp.headers["content-type"]
            body = resp.text
            # the friendly card copy (part 6), not a raw JSON detail body
            assert "This report has been deleted" in body
            assert "60 minutes" in body and "privacy" in body.lower()
            assert "Re-run the analysis" in body
            assert not body.lstrip().startswith("{")
            assert '"detail"' not in body
            # extends the no-internal-identifiers copy check to the report-gone card:
            # it must never leak the job id or any internal token.
            low = body.lower()
            assert job_id not in body
            for tok in ("traceback", "job_dir", "tmp", "rmtree", "purge", "exception"):
                assert tok not in low, f"report-gone card leaks internal token {tok!r}"
            # the lazy purge actually ran, not just the card
            assert job.purged is True
            assert not job.job_dir.exists()

    def test_completion_dir_holds_only_the_report(self, tmp_path):
        app, csv_path = self._done_app(tmp_path)  # retain_trace off
        with TestClient(app) as client:
            job_id = _post_upload(client, csv_path).json()["job_id"]
            assert _poll_until_terminal(client, job_id)["state"] == "done"
            job = app.state.vib.registry.get(job_id)
            names = {p.name for p in job.job_dir.iterdir()}
            assert names == {"report.pdf"}, f"raw/intermediates not purged: {names}"
            assert not any(n.startswith("upload") for n in names)   # raw gone
            assert "report.md" not in names and "analysis.json" not in names  # intermediates gone
            assert "trace.jsonl" not in names  # consent off

    def test_completion_keeps_trace_only_when_consented(self, tmp_path):
        app, csv_path = self._done_app(tmp_path, retain_trace=True)
        with TestClient(app) as client:
            job_id = _post_upload(client, csv_path, retain_trace="true").json()["job_id"]
            assert _poll_until_terminal(client, job_id)["state"] == "done"
            job = app.state.vib.registry.get(job_id)
            names = {p.name for p in job.job_dir.iterdir()}
            assert names == {"report.pdf", "trace.jsonl"}, names

    def test_ttl_sweep_removes_everything(self, tmp_path):
        app, csv_path = self._done_app(tmp_path)
        with TestClient(app) as client:
            job_id = _post_upload(client, csv_path).json()["job_id"]
            assert _poll_until_terminal(client, job_id)["state"] == "done"
            job = app.state.vib.registry.get(job_id)
            job.created_at -= 61 * 60
            # S7: sweep_expired now returns (expired, stranded). A `done` job past
            # its TTL is a genuine expiry and is reclaimed exactly as before.
            removed, stranded = app.state.vib.registry.sweep_expired()
            assert job_id in removed and stranded == []
            assert not job.job_dir.exists()
            assert app.state.vib.registry.get(job_id) is None

    def test_unknown_job_report_is_friendly_html_not_json(self, tmp_path):
        app, _ = self._done_app(tmp_path)
        with TestClient(app) as client:
            resp = client.get("/api/jobs/deadbeef/report.pdf")
            assert resp.status_code == 404
            assert "text/html" in resp.headers["content-type"]
            assert "This report has been deleted" in resp.text
            assert '"detail"' not in resp.text

    def test_not_ready_report_is_friendly_html_not_json(self, tmp_path):
        app, _ = self._done_app(tmp_path)
        with TestClient(app) as client:
            # a queued job that never ran -- created directly, no worker involved
            job = app.state.vib.registry.create(code_label="t", file_size=1)
            resp = client.get(f"/api/jobs/{job.id}/report.pdf")
            assert resp.status_code == 409
            assert "text/html" in resp.headers["content-type"]
            assert "still being prepared" in resp.text
            assert '"detail"' not in resp.text

    def test_render_failure_keeps_markdown_and_shows_honest_card(self, tmp_path, monkeypatch):
        # PDF render is best-effort (pandoc timeout / no engine -> writes markdown
        # only). The job must NOT destroy report.md, must not point pdf_path at a
        # missing file, and the card must say "could not be generated" -- never the
        # false "has been deleted".
        # Patch the two ENGINES, not the two render entry points: the worker
        # renders a deterministic report and a drafted one through different
        # functions, and this scenario is "no PDF engine is available here",
        # which is a property of the engines. Session V2-WIRE.
        monkeypatch.setattr("vib_agent.report.generate._pdf_from_html", lambda *a, **k: None)
        monkeypatch.setattr("vib_agent.report.generate._pdf_from_markdown", lambda *a, **k: None)
        app, csv_path = self._done_app(tmp_path)
        with TestClient(app) as client:
            job_id = _post_upload(client, csv_path).json()["job_id"]
            assert _poll_until_terminal(client, job_id)["state"] == "done"
            job = app.state.vib.registry.get(job_id)
            assert job.pdf_path is None
            names = {p.name for p in job.job_dir.iterdir()}
            assert "report.md" in names  # the report content survives
            assert not any(n.startswith("upload") for n in names)  # raw still gone
            resp = client.get(f"/api/jobs/{job_id}/report.pdf")
            assert resp.status_code == 404
            assert "could not be generated" in resp.text
            assert "has been deleted" not in resp.text
            assert '"detail"' not in resp.text

    def test_xlsx_upload_happy_path(self, tmp_path):
        import openpyxl

        xlsx_path = tmp_path / "spec.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["freq_hz", "amplitude"])
        n, fmax = 400, 200.0
        for i in range(n):
            freq = i * fmax / n
            amp = 0.5 if abs(freq - 107.03) < 0.3 else 0.001
            ws.append([freq, amp])
        wb.save(xlsx_path)

        response = _consistent_response_for(xlsx_path, "TestPump", bearing_model="6206")
        fake_client = FakeAnthropicClient(responses=[response])
        app = create_app(
            webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "engineer-1"},
            anthropic_client_factory=lambda: fake_client,
        )
        with TestClient(app) as client:
            with open(xlsx_path, "rb") as f:
                r = client.post(
                    "/api/jobs",
                    files={"file": ("spec.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    data={
                        "invite_code": "demo-code", "machine_alias": "TestPump", "rpm": "1800",
                        "iso_group": "2", "iso_support": "rigid", "machine_type": "motor", "bearing_model": "6206",
                    },
                )
            job_id = r.json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            assert data["state"] == "done"

    def test_unscaled_wav_done_with_severity_unavailable_note(self, tmp_path):
        import numpy as np
        from scipy.io import wavfile

        fs = 12000.0
        n = int(fs * 1.0)
        t = np.arange(n) / fs
        signal = np.sin(2 * np.pi * 3000 * t) * (1 + 0.5 * np.sin(2 * np.pi * 107.03 * t))
        signal += 0.01 * np.random.default_rng(1).standard_normal(n)
        norm = signal / np.max(np.abs(signal)) * 0.8
        wav_path = tmp_path / "wave.wav"
        wavfile.write(str(wav_path), 12000, (norm * 32767).astype(np.int16))

        bearings_cfg = load_config("bearings")
        form = UploadForm(machine_alias="WavPump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor", bearing_model="6206")
        case, _, _ = parse_upload(wav_path, form, bearings_cfg=bearings_cfg)
        # Build the echo from a result computed the SAME way the worker does
        # (with rules) -- an unscaled WAV is a not_assessable reading, so the
        # worker now emits the velocity-coverage follow-up; the echo must carry it
        # too or the consistency check will (correctly) flag a mismatch.
        result = run_analysis(
            case, iso_table=load_config("iso_zones")["zones"], thresholds=load_thresholds(),
            rules=load_config("next_measurements"),
        )
        title = TITLE_TEMPLATE.format(machine_name="WavPump")
        narrative = f"{title}\n\nBody.\n\nDRAFT -- prepared by automated analysis, pending analyst review."
        fake_client = FakeAnthropicClient(responses=[draft_message(narrative, build_consistent_echo(result))])

        app = create_app(
            webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "engineer-1"},
            anthropic_client_factory=lambda: fake_client,
        )
        with TestClient(app) as client:
            with open(wav_path, "rb") as f:
                r = client.post(
                    "/api/jobs", files={"file": ("wave.wav", f, "audio/wav")},
                    data={
                        "invite_code": "demo-code", "machine_alias": "WavPump", "rpm": "1800",
                        "iso_group": "2", "iso_support": "rigid", "machine_type": "motor", "bearing_model": "6206",
                    },
                )
            job_id = r.json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            assert data["state"] == "done"
            pdf = client.get(f"/api/jobs/{job_id}/report.pdf")
            assert pdf.status_code == 200


class TestGateFail:
    def test_machine_off_gate_fails_llm_never_invoked(self, tmp_path):
        csv_path = tmp_path / "off.csv"
        _off_csv(csv_path)
        fake_client = FakeAnthropicClient(responses=[])  # must never be called
        app = create_app(
            webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "engineer-1"},
            anthropic_client_factory=lambda: fake_client,
        )
        with TestClient(app) as client:
            r = _post_upload(client, csv_path, bearing_model="")
            job_id = r.json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            assert data["state"] == "gate_fail"
            assert fake_client.messages.calls == []
            pdf = client.get(f"/api/jobs/{job_id}/report.pdf")
            assert pdf.status_code == 200
            # completion-time cleanup applies to gate_fail too: only the report remains
            job = app.state.vib.registry.get(job_id)
            assert {p.name for p in job.job_dir.iterdir()} == {"report.pdf"}


class TestSpendGuard:
    def test_pre_exhausted_budget_degrades_without_calling_llm(self, tmp_path):
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        fake_client = FakeAnthropicClient(responses=[])  # must never be called
        app = create_app(
            webapp_cfg=_webapp_cfg(daily_token_budget=1),
            invite_codes={"demo-code": "engineer-1"},
            anthropic_client_factory=lambda: fake_client,
        )
        app.state.vib.spend_guard.record(1_000_000)  # pre-exhaust
        with TestClient(app) as client:
            r = _post_upload(client, csv_path)
            job_id = r.json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            assert data["state"] == "degraded"
            assert data["degraded_reason"] == "spend_budget"
            assert fake_client.messages.calls == []
            pdf = client.get(f"/api/jobs/{job_id}/report.pdf")
            assert pdf.status_code == 200
            assert b"unavailable" in pdf.content or pdf.content[:4] == b"%PDF"
            # completion-time cleanup applies to degraded too: only the report remains
            job = app.state.vib.registry.get(job_id)
            assert {p.name for p in job.job_dir.iterdir()} == {"report.pdf"}


class TestDraftFailure:
    def test_inconsistent_draft_twice_degrades(self, tmp_path):
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        bad_echo = (
            '<<<ECHO_START>>>\n{"zone": "Z", "faults": [], "recommended_measurements": []}\n<<<ECHO_END>>>'
        )
        title = TITLE_TEMPLATE.format(machine_name="TestPump")
        narrative = f"{title}\n\nBody.\n\nDRAFT -- prepared by automated analysis, pending analyst review."
        fake_client = FakeAnthropicClient(
            responses=[draft_message(narrative, bad_echo), draft_message(narrative, bad_echo)]
        )
        app = create_app(
            webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "engineer-1"},
            anthropic_client_factory=lambda: fake_client,
        )
        with TestClient(app) as client:
            r = _post_upload(client, csv_path)
            job_id = r.json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            assert data["state"] == "degraded"
            assert data["degraded_reason"] == "draft_failure"
            pdf = client.get(f"/api/jobs/{job_id}/report.pdf")
            assert pdf.status_code == 200


class TestSparseTrendRoute:
    def test_monthly_cadence_trend_still_completes(self, tmp_path):
        csv_path = tmp_path / "sparse.csv"
        _trend_csv(csv_path, n=4, start=2.0, end=2.1)
        response = _consistent_response_for(csv_path, "TestPump", mode="trend", bearing_model="")
        fake_client = FakeAnthropicClient(responses=[response])
        app = create_app(
            webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "engineer-1"},
            anthropic_client_factory=lambda: fake_client,
        )
        with TestClient(app) as client:
            r = _post_upload(client, csv_path, mode="trend", bearing_model="")
            job_id = r.json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            assert data["state"] in ("done", "gate_fail")


class TestSecurity4xx:
    def _app(self, **cfg_overrides):
        return create_app(
            webapp_cfg=_webapp_cfg(**cfg_overrides),
            invite_codes={"demo-code": "engineer-1"},
            anthropic_client_factory=lambda: FakeAnthropicClient(responses=[]),
        )

    def test_oversize_returns_413(self, tmp_path):
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        app = self._app(max_upload_bytes=10)
        with TestClient(app) as client:
            r = _post_upload(client, csv_path)
            assert r.status_code == 413

    def test_unsupported_extension_returns_415_with_funnel_text(self, tmp_path):
        bad_path = tmp_path / "scan.exe"
        bad_path.write_bytes(b"\x00\x01\x02")
        app = create_app(
            webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "engineer-1"},
            contact_email="ask@example.com",
            anthropic_client_factory=lambda: FakeAnthropicClient(responses=[]),
        )
        with TestClient(app) as client:
            with open(bad_path, "rb") as f:
                r = client.post(
                    "/api/jobs", files={"file": ("scan.exe", f, "application/octet-stream")},
                    data={
                        "invite_code": "demo-code", "machine_alias": "P", "rpm": "1800",
                        "iso_group": "2", "iso_support": "rigid", "machine_type": "motor",
                    },
                )
            assert r.status_code == 415
            assert "ask@example.com" in r.json()["detail"]

    def test_bad_invite_code_returns_401(self, tmp_path):
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        app = self._app()
        with TestClient(app) as client:
            r = _post_upload(client, csv_path, invite_code="wrong-code")
            assert r.status_code == 401

    def test_rate_limited_returns_429(self, tmp_path):
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        response = _consistent_response_for(csv_path, "TestPump", bearing_model="6206")
        app = create_app(
            webapp_cfg=_webapp_cfg(per_code_daily_jobs=1),
            invite_codes={"demo-code": "engineer-1"},
            anthropic_client_factory=lambda: FakeAnthropicClient(responses=[response]),
        )
        with TestClient(app) as client:
            r1 = _post_upload(client, csv_path)
            assert r1.status_code == 202
            r2 = _post_upload(client, csv_path)
            assert r2.status_code == 429

    def test_queue_full_returns_503(self, tmp_path):
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        app = self._app(queue_depth_max=0)
        with TestClient(app) as client:
            r = _post_upload(client, csv_path)
            assert r.status_code == 503


class TestTtlPurge:
    def test_expired_job_is_purged_and_removed(self, tmp_path):
        """A FINISHED job past its TTL: files deleted, entry dropped, 404.

        S7 narrowed what counts: the job is put into a terminal state first,
        because an expired job that is still `queued`/`running` is not an expiry
        at all -- it is a job that stopped without finishing, and reclaiming it
        the same way made the two indistinguishable at the API. That case is
        pinned in tests/test_terminal_guarantee.py.
        """
        app = create_app(
            webapp_cfg=_webapp_cfg(job_ttl_minutes=60),
            invite_codes={"demo-code": "engineer-1"},
            anthropic_client_factory=lambda: FakeAnthropicClient(responses=[]),
        )
        registry = app.state.vib.registry
        job = registry.create(code_label="engineer-1", file_size=10)
        (job.job_dir / "report.md").write_text("placeholder")
        job.state = "done"
        job.created_at -= 61 * 60  # force it past TTL

        removed, stranded = registry.sweep_expired()
        assert job.id in removed and stranded == []
        assert not job.job_dir.exists()
        assert registry.get(job.id) is None

        with TestClient(app) as client:
            assert client.get(f"/api/jobs/{job.id}").status_code == 404
            assert client.get(f"/api/jobs/{job.id}/report.pdf").status_code == 404


class TestUiV1Surface:
    """Session E (ui-v1): the served pages carry the shared shell, and the
    job-status API exposes the additive read-only summaries the state cards
    render — without any change to the state machine, limits, or parsing.
    """

    def _app(self, fake_client=None):
        return create_app(
            webapp_cfg=_webapp_cfg(),
            invite_codes={"demo-code": "engineer-1"},
            contact_email="ops@example.test",
            anthropic_client_factory=(lambda: fake_client) if fake_client else None,
        )

    # ── shared shell on every served page ─────────────────────────────
    def test_index_carries_shell_and_all_form_fields(self):
        with TestClient(self._app()) as client:
            html = client.get("/").text
        assert 'VIB-AGENT<span>VIBRATION ANALYSIS</span>' in html
        assert 'class="spectrum"' in html                        # signature SVG
        assert '<link rel="stylesheet" href="/style.css">' in html
        assert "{{contact_email}}" not in html                   # placeholder replaced
        assert "ops@example.test" in html
        for field in ("file", "mode", "machine_alias", "rpm", "iso_group", "iso_support",
                      "bearing_model", "velocity_unit", "detection_type", "wav_sensitivity",
                      "invite_code", "retain_trace"):
            assert f'name="{field}"' in html, f"form field {field} missing"

    def test_privacy_and_validation_share_the_shell(self):
        with TestClient(self._app()) as client:
            for path in ("/privacy", "/validation"):
                html = client.get(path).text
                assert path == "/validation" or "{{contact_email}}" not in html
                assert 'class="spectrum"' in html
                assert '/style.css' in html
                assert "VIB-AGENT" in html

    def test_style_and_fonts_served(self):
        with TestClient(self._app()) as client:
            css = client.get("/style.css")
            assert css.status_code == 200 and "text/css" in css.headers["content-type"]
            assert "@font-face" in css.text and "cdn.jsdelivr" not in css.text  # self-hosted
            font = client.get("/fonts/IBMPlexSans-Regular.woff2")
            assert font.status_code == 200 and font.content[:4] == b"wOF2"
            # no path traversal / non-woff2
            assert client.get("/fonts/..%2f..%2fapp.py").status_code == 404
            assert client.get("/fonts/style.css").status_code == 404

    def test_validation_renders_summary_not_hardcoded(self):
        """Session UX-4 re-pointed ONE assertion in this test, and only that
        one. The claim -- the page renders `outputs/validation_summary.md`
        rather than text somebody typed into app.py -- is unchanged and is
        still what the first and third assertions check.

        What moved is the mechanism. `class="doc"` was `<pre class="doc">`,
        the raw monospace dump a stranger read as `cat` output pasted into a
        page (STRANGER U7). It is now generated HTML. Asserting on a section
        heading taken FROM THE FILE is a stronger form of the same claim: a
        hardcoded page would have to reproduce the file's own headings to
        pass, where `class="doc"` only ever proved a wrapper existed.
        """
        summary = (Path(__file__).resolve().parents[1] / "outputs"
                   / "validation_summary.md")
        with TestClient(self._app()) as client:
            html = client.get("/validation").text
        assert "VALIDATION SUMMARY" in html          # from outputs/validation_summary.md
        assert "/sample-report.pdf" in html
        if summary.exists():
            # A section title that exists only in that file, rendered as the
            # heading the file marks it as with its own `-----` underline.
            assert "<h2>BEARING DIAGNOSIS -- CWRU (Case Western Reserve " \
                   "University)</h2>" in html
            assert "<pre" not in html, (
                "the document page is a monospace dump again -- U7"
            )

    def test_sample_report_is_a_pdf(self):
        with TestClient(self._app()) as client:
            r = client.get("/sample-report.pdf")
        assert r.status_code == 200 and r.content[:4] == b"%PDF"

    def test_sample_report_serves_bpfo_synthetic_bytes(self):
        """Session SROUTE. The route and scripts/make_sample.py disagreed about which
        file was the public sample -- app.py served the old CWRU demo while the script
        wrote (and announced) the synthetic one. Nothing checked, so it went unnoticed.

        Pin the identity, not just the shape: a 200 with a `%PDF` header passed happily
        throughout that whole period. This asserts the exact file, by bytes, at a path
        spelled out independently of the app so that re-pointing the route breaks this
        test instead of silently redefining what it proves."""
        sample = (
            Path(__file__).resolve().parents[1]
            / "outputs" / "demo_package" / "bpfo_synthetic" / "report.pdf"
        )
        assert sample.exists(), f"the committed sample is missing at {sample}"
        with TestClient(self._app()) as client:
            r = client.get("/sample-report.pdf")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content == sample.read_bytes()

    # ── additive job-status fields ────────────────────────────────────
    def test_done_job_exposes_result_summary(self, tmp_path):
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        fake = FakeAnthropicClient(responses=[_consistent_response_for(csv_path, "TestPump", bearing_model="6206")])
        with TestClient(self._app(fake)) as client:
            job_id = _post_upload(client, csv_path).json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            assert data["state"] == "done"
            rs = data["result_summary"]
            assert set(rs) == {"no_findings", "faults", "severity"}
            assert isinstance(rs["no_findings"], bool)
            for f in rs["faults"]:
                assert set(f) == {"label", "confidence"}  # labelled, never raw ids

    def test_gate_fail_exposes_gate_summary_reasons_and_collect(self, tmp_path):
        csv_path = tmp_path / "off.csv"
        _off_csv(csv_path)  # machine-off -> gate fail, LLM never called
        with TestClient(self._app()) as client:
            job_id = _post_upload(client, csv_path).json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            assert data["state"] == "gate_fail"
            gs = data["gate_summary"]
            assert set(gs) == {"reasons", "collect"}
            assert gs["reasons"] and all(isinstance(r, str) for r in gs["reasons"])
            # plain language only — no raw dict/repr leaking through
            assert not any("{" in r for r in gs["reasons"])

    def test_phase_hint_is_a_display_string_not_a_new_state(self, tmp_path):
        # phase only ever narrows 'running'; the terminal state set is unchanged.
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        fake = FakeAnthropicClient(responses=[_consistent_response_for(csv_path, "TestPump", bearing_model="6206")])
        with TestClient(self._app(fake)) as client:
            job_id = _post_upload(client, csv_path).json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            assert data["state"] == "done"  # unchanged terminal state
            if "phase" in data:
                assert data["phase"] in ("analyzing", "drafting")


class TestErroredJobReportCopy:
    """MERGE-PREP micro-fix: a job that FAILED has no report and never will.

    Observed live on 2026-08-05 (../vib-agent/outputs/live_lane_aug5/): requesting
    the report for the prose-file job returned "Your report is still being
    prepared", which tells the analyst to wait for something that is not coming.
    The refusal was right; the words after it were not.
    """

    def _app(self):
        return create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "engineer-1"},
                          contact_email="ops@example.test")

    def test_errored_job_report_says_the_analysis_failed(self):
        app = self._app()
        with TestClient(app) as client:
            job = app.state.vib.registry.create(code_label="t", file_size=1)
            job.state = "error"
            job.safe_message = "We couldn’t interpret this file’s layout."
            response = client.get(f"/api/jobs/{job.id}/report.pdf")

        assert response.status_code == 409
        assert "text/html" in response.headers["content-type"]
        assert "could not be completed" in response.text
        assert "no report" in response.text
        # the exact wrong words, gone
        assert "still being prepared" not in response.text
        # and it says WHY, in the copy the analyst already saw on the error card
        assert "couldn’t interpret this file’s layout" in response.text
        assert '"detail"' not in response.text

    def test_a_job_still_working_is_unchanged(self):
        """The fix is scoped to `error`. A queued job is genuinely still being
        prepared and must keep saying so."""
        app = self._app()
        with TestClient(app) as client:
            job = app.state.vib.registry.create(code_label="t", file_size=1)
            response = client.get(f"/api/jobs/{job.id}/report.pdf")
        assert response.status_code == 409
        assert "still being prepared" in response.text

    def test_an_errored_job_with_no_message_still_reads_honestly(self):
        app = self._app()
        with TestClient(app) as client:
            job = app.state.vib.registry.create(code_label="t", file_size=1)
            job.state = "error"          # safe_message never set
            response = client.get(f"/api/jobs/{job.id}/report.pdf")
        assert response.status_code == 409
        assert "could not be completed" in response.text
        assert "could not be analysed" in response.text
        assert "None" not in response.text
