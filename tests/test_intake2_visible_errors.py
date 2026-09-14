"""Session INTAKE-2 — PARTC F-8: a refused upload says so.

**The defect, as PART-C photographed it** (`07_geometry_422_silent_ui.jpg`): a
`POST /api/jobs` that returned 422 produced *no visible error at all*. The
previous run's "Accepted" panel stayed on screen with its elapsed counter still
incrementing, and `get_page_text` found no error content anywhere on the page.
PART-C named `app.js:4685-4704` as the code that should have rendered it and
recorded that why it did not was never traced.

**It is two independent causes, and both are fixed:**

1. **The previous job's poll loop was never cancelled.** `pollJob` advances a
   chain of `setTimeout(poll, …)` calls and kept no handle on them, so a second
   submit could not stop the first loop — which calls `show()` on every tick and
   therefore repainted its own "Accepted" card over the error card within a
   second. The page really did have no error text on it by the time a human
   looked.
2. **FastAPI's own 422 carries a LIST, not a string.** There was no
   `RequestValidationError` handler, so the framework answered with
   `{"detail": [ {...} ]}`, and `app.js`'s branch
   `if (res.status >= 500 || typeof detail !== 'string')` routes anything
   non-string to a generic transient card. The server's account of the problem
   was discarded before it could be shown.

**This file pins the SERVER half**, which is what the brief asks for: every
non-2xx answer from `POST /api/jobs` carries a `detail` that is a string, so the
shipped browser branch cannot swallow it. The browser half — the panel clearing
and the clock stopping — is verified by the operator's real-browser pass, and the
close-out says so rather than claiming a check that a test client cannot make.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient
from tests.test_webapp_e2e import _webapp_cfg
from vib_agent.webapp.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_APP_JS = _ROOT / "src" / "vib_agent" / "webapp" / "static" / "app.js"
_RPM = 1800.0


def _csv_bytes(n=400, fmax=200.0) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["freq_hz", "amplitude"])
    for i in range(n):
        freq = i * fmax / n
        writer.writerow([freq, 0.4 if abs(freq - _RPM / 60.0) < 0.3 else 0.001])
    return buf.getvalue().encode()


def _form(**over):
    data = {"invite_code": "demo-code", "machine_alias": "TestPump", "rpm": str(_RPM),
            "iso_group": "2", "iso_support": "rigid", "machine_type": "pump"}
    data.update({k: str(v) for k, v in over.items()})
    return data


@pytest.fixture
def client():
    app = create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"},
                     anthropic_client_factory=lambda: FakeAnthropicClient(responses=[]))
    with TestClient(app) as handle:
        yield handle


def _post(client, *, files=None, **over):
    if files is None:
        files = {"file": ("upload.csv", _csv_bytes(), "text/csv")}
    return client.post("/api/jobs", files=files, data=_form(**over))


class TestEveryRefusalIsAStringTheBrowserCanRender:
    """The contract `app.js:4700` depends on, asserted across the 4xx table.

    `typeof detail !== 'string'` is a real branch in the shipped browser: when
    it is taken, the analyst gets a generic card instead of the reason. So a
    refusal whose detail is not a string is, in effect, a silent one.
    """

    #: Each row: a description, the overrides, and the status expected. Every
    #: one of these is reachable from the form as shipped.
    CASES = [
        ("no machine type", {"machine_type": ""}, 422),
        ("an invented machine type", {"machine_type": "reactor"}, 422),
        ("a non-numeric speed", {"rpm": "quite fast"}, 422),
        ("a non-numeric rated power", {"rated_kw": "big"}, 422),
        ("a negative rated power", {"rated_kw": "-5"}, 422),
        ("an unknown mounting", {"mounting": "welded"}, 422),
        ("a bad window type", {"window_type": "triangular"}, 422),
        ("both bearing model and geometry", {"bearing_model": "6206",
                                             "bearing_n_balls": "9"}, 422),
        ("an unknown bearing", {"bearing_model": "SKF 32222 J2"}, 422),
        ("a bad invite code", {"invite_code": "nope"}, 401),
    ]

    @pytest.mark.parametrize("label,over,status",
                             CASES, ids=[c[0] for c in CASES])
    def test_the_detail_is_a_string(self, client, label, over, status):
        res = _post(client, **over)
        assert res.status_code == status, f"{label}: {res.status_code} {res.text}"
        detail = res.json().get("detail")
        assert isinstance(detail, str), (
            f"{label}: detail is {type(detail).__name__}, which app.js discards "
            f"in favour of a generic card — {detail!r}"
        )
        assert detail.strip(), f"{label}: the detail is empty"

    def test_an_unsupported_format_is_a_string_too(self, client):
        res = _post(client, files={"file": ("upload.exe", b"MZ\x00\x00", "application/octet-stream")})
        assert res.status_code == 415, res.text
        assert isinstance(res.json()["detail"], str)

    def test_the_framework_422_no_longer_returns_a_list(self, client):
        """The one that regressed the whole feature.

        Before this session a type-coercion failure produced FastAPI's default
        body — a list of error objects. That is the shape the browser cannot
        render, and it is the shape a missing required field used to produce.
        """
        res = client.post("/api/jobs",
                          files={"file": ("upload.csv", _csv_bytes(), "text/csv")},
                          data={"invite_code": "demo-code", "machine_alias": "P",
                                "rpm": "not-a-number", "iso_group": "2",
                                "iso_support": "rigid", "machine_type": "pump"})
        assert res.status_code == 422
        body = res.json()
        assert isinstance(body["detail"], str), body
        assert not isinstance(body["detail"], list)

    def test_the_framework_message_names_no_internal_field_path(self, client):
        """It must not send an analyst looking for `body -> wav_sensitivity`.

        The `_*_422` helpers own the per-field wording for every control an
        analyst can see (GEOM-1's rule for the eight bearing messages); this
        handler covers the shapes that never reached them, so it names the part
        of the FORM to look at and no internal path.
        """
        res = client.post("/api/jobs",
                          files={"file": ("upload.csv", _csv_bytes(), "text/csv")},
                          data={"invite_code": "demo-code", "machine_alias": "P",
                                "rpm": "not-a-number", "iso_group": "2",
                                "iso_support": "rigid", "machine_type": "pump"})
        detail = res.json()["detail"]
        for leak in ("body", "->", "value_error", "type_error", "pydantic",
                     "wav_sensitivity", "iso_support"):
            assert leak not in detail, f"the refusal leaks {leak!r}: {detail}"

    def test_a_missing_required_field_does_not_crash_the_handler(self, client):
        """No `machine_alias` at all — the framework refuses before the handler
        body runs, which is exactly the path that used to produce a list."""
        res = client.post("/api/jobs",
                          files={"file": ("upload.csv", _csv_bytes(), "text/csv")},
                          data={"invite_code": "demo-code", "rpm": str(_RPM),
                                "iso_group": "2", "iso_support": "rigid",
                                "machine_type": "pump"})
        assert res.status_code == 422
        assert isinstance(res.json()["detail"], str)

    def test_a_refusal_never_returns_a_job_id(self, client):
        """Nothing may keep ticking, which starts with nothing to tick for."""
        for _label, over, _status in self.CASES:
            res = _post(client, **over)
            assert "job_id" not in res.json(), f"{over} created a job"


class TestTheBrowserRetiresASupersededRun:
    """The browser half, pinned at the source.

    A `TestClient` cannot run `app.js`, and the node harness does not drive the
    submit path, so what is checkable here is the SHAPE: the generation counter
    exists, the failure paths retire the previous run, and every paint in the
    poll loop is behind the check. The behaviour itself is the operator's
    real-browser pass — the standing rule, and the close-out says it plainly.
    """

    def test_the_generation_counter_exists(self):
        js = _APP_JS.read_text()
        assert "let pollGeneration = 0;" in js
        assert "function retirePolling()" in js

    def test_retiring_also_stops_the_clock(self):
        """A refused run must not leave an elapsed counter running — that is
        half of what PART-C saw."""
        js = _APP_JS.read_text()
        body = js.partition("function retirePolling()")[2].partition("\n}")[0]
        assert "pollGeneration += 1" in body
        assert "runStartMs = null" in body

    def test_the_poll_loop_takes_a_generation_and_checks_it(self):
        js = _APP_JS.read_text()
        loop = js.partition("function pollJob(jobId)")[2]
        assert "const myGeneration = pollGeneration;" in loop
        assert "const superseded = () =>" in loop
        # Checked before the fetch and again after it: a tick can be superseded
        # while its request is in flight, and it is the PAINT that must not
        # happen.
        assert loop.count("if (superseded()) return;") >= 3, (
            "the supersede check is missing from a path that paints"
        )

    #: Where the submit handler's own POST is. Both tests below anchor here,
    #: because `if (!res.ok) {` and `show(` both also occur earlier in the file
    #: inside `pollJob` -- searching from the top finds those and measures the
    #: wrong lines.
    _ANCHOR = "fetch('/api/jobs', { method: 'POST'"

    def test_the_non_2xx_path_retires_before_it_paints(self):
        """The 422/415/401 path PART-C actually hit.

        Measured by POSITION, not by membership: `retirePolling()` has to run
        BEFORE the first card is painted, or the old loop's next tick repaints
        over it anyway -- which is the whole defect.
        """
        js = _APP_JS.read_text()
        branch = js.index("if (!res.ok) {", js.index(self._ANCHOR))
        # Searched from the BRANCH, not from the anchor: the offline `catch` sits
        # between the two and has its own `retirePolling()`, so searching from
        # the anchor finds THAT one and this passes for the wrong reason --
        # measured, not supposed. The negative control that deletes this
        # branch's call stayed GREEN until this line was fixed.
        retire = js.index("retirePolling();", branch)
        first_paint = js.index("show(", branch)
        assert retire < first_paint, (
            "the non-2xx path paints a card before retiring the previous run, "
            "so the old poll loop repaints over it (PARTC F-8)"
        )

    def test_the_offline_path_retires_before_it_paints(self):
        """`fetch` threw -- the request never landed.

        Its own test rather than a parametrize case, because it is a differently
        shaped branch: having only one of the two leaves the stale panel up for
        the other case, which is how a defect like this survives a session of
        testing.
        """
        js = _APP_JS.read_text()
        paint = js.index("show(offlineCard());", js.index(self._ANCHOR))
        catch = js.rindex("catch (err) {", 0, paint)
        assert "retirePolling();" in js[catch:paint], (
            "the offline path paints before it retires the previous run"
        )
