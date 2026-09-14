"""INTAKE-HARDEN — the adversarial intake corpus, its truth table, and the
immune tests that prove a wrong recipe cannot produce a Case.

No LLM, no network: the one webapp test here runs against a fake client.
Four questions this file answers:

  1. Do the committed fixture bytes still match their generator? (If git or an
     editor normalised a CRLF or ate a BOM, half this corpus tests nothing.)
  2. For every fixture, does the CORRECT recipe land on the right physics, or
     refuse cleanly? Those are the only two acceptable outcomes.
  3. For every plausibly-WRONG recipe, does something refuse? A wrong recipe
     that produces a Case the verification gate accepts is a silently wrong
     diagnosis, which is the worst thing this product can do.
  4. Does a refusal reach the analyst as the couldn't-interpret funnel, and
     never as a 5xx or a stuck job?
"""

from __future__ import annotations

import pytest

from tests.intake_adversarial_corpus import (
    BPFO_HZ,
    BY_NAME,
    CORPUS,
    FIXTURE_DIR,
    RPM,
    SHAFT_HZ,
    write_corpus,
)
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.adapters.uploads.recipe import RecipeExecutionError, execute_recipe
from vib_agent.adapters.uploads.sample import NotTextError, read_sample
from vib_agent.adapters.uploads.verify import failed, verify_case
from vib_agent.config import load_config


@pytest.fixture(scope="module")
def bearings_cfg():
    return load_config("bearings")


@pytest.fixture(scope="module")
def tolerances():
    return load_config("webapp")["inference"]


def _form(**over):
    base = dict(machine_alias="Intake Probe", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor",
                bearing_model="6206")
    base.update(over)
    return UploadForm(**base)


def _run(name: str, recipe, bearings_cfg):
    return execute_recipe(FIXTURE_DIR / name, recipe, _form(), bearings_cfg=bearings_cfg)


# ══════════════════════════════════════════════════════════════════════════
# 1 · The fixtures are the bytes the generator says they are
# ══════════════════════════════════════════════════════════════════════════
def test_committed_fixtures_match_the_generator(tmp_path):
    """The corpus is committed so its BYTES are proved to survive a checkout.
    If git normalised a line ending or an editor stripped a BOM, the encoding
    half of this suite would pass for the wrong reason — so compare bytes."""
    regenerated = write_corpus(tmp_path)
    for item in CORPUS:
        committed = FIXTURE_DIR / item.name
        assert committed.exists(), f"{item.name} missing — run scripts/gen_intake_corpus.py"
        assert committed.read_bytes() == regenerated[item.name].read_bytes(), (
            f"{item.name} on disk differs from the generator — run "
            "scripts/gen_intake_corpus.py (and check .gitattributes is still '* -text')")


def test_the_hard_bytes_really_are_on_disk():
    """Named explicitly, because these are the ones a normalising checkout would
    quietly destroy while every other test kept passing."""
    assert (FIXTURE_DIR / "utf16le_bom.txt").read_bytes()[:2] == b"\xff\xfe"
    assert (FIXTURE_DIR / "utf16be_bom.txt").read_bytes()[:2] == b"\xfe\xff"
    assert (FIXTURE_DIR / "utf8_bom_nohdr.txt").read_bytes()[:3] == b"\xef\xbb\xbf"
    bare_cr = (FIXTURE_DIR / "bare_cr.dat").read_bytes()
    assert b"\r" in bare_cr and b"\n" not in bare_cr, "bare-CR fixture gained a LF"
    assert b"\r\n" in (FIXTURE_DIR / "crlf.txt").read_bytes()
    assert b"\xb0" in (FIXTURE_DIR / "cp1252_degrees.txt").read_bytes()  # cp1252 degree sign
    assert b"\x00" in (FIXTURE_DIR / "binary_head.txt").read_bytes()


def test_corpus_covers_the_declared_hazards():
    assert len(CORPUS) >= 20
    assert {c.name.rsplit(".", 1)[1] for c in CORPUS} == {"txt", "dat", "asc"}
    # every file states what it is there to break, and no two share a name
    assert all(item.hazard for item in CORPUS)
    assert len({item.name for item in CORPUS}) == len(CORPUS)
    # every file is small enough to read in a diff
    for item in CORPUS:
        assert len(item.data) < 32_768, f"{item.name} is too big for a fixture"


@pytest.mark.parametrize("item", CORPUS, ids=[i.name for i in CORPUS])
def test_every_fixture_declares_a_coherent_expectation(item):
    """The corpus is only evidence if each entry commits to an outcome up front:
    a Case, a clean refusal, or a file that never reaches inference at all."""
    assert item.outcome in ("case", "refuse", "not_text")
    assert item.gate in ("pass", "fail", "n/a")
    if item.outcome != "case":
        assert item.gate == "n/a", f"{item.name}: no Case, so no gate verdict to give"
    if item.gate == "fail":
        assert item.outcome == "case", f"{item.name}: the gate only judges a Case"


# ══════════════════════════════════════════════════════════════════════════
# 2 · Truth table — the CORRECT recipe, per fixture
# ══════════════════════════════════════════════════════════════════════════
class TestTruthTable:
    """Extends tests/test_recipe.py::TestExecuteCorpus's pattern: execute the
    hand-authored correct recipe and assert the physics that comes back."""

    @pytest.mark.parametrize("item", CORPUS, ids=[i.name for i in CORPUS])
    def test_sample_stage_matches_the_declared_outcome(self, item):
        """Whatever else happens, a file that is not text must never reach an
        inference pass, and a file that IS text must always reach one."""
        try:
            sample = read_sample(FIXTURE_DIR / item.name)
        except NotTextError:
            assert item.outcome == "not_text", (
                f"{item.name}: refused at the sample stage but the corpus expects "
                f"'{item.outcome}' — a readable export is being turned away")
            return
        assert item.outcome != "not_text", f"{item.name}: binary content reached inference"
        assert sample.strip(), f"{item.name}: sampled to nothing"

    @pytest.mark.parametrize("item", CORPUS, ids=[i.name for i in CORPUS])
    def test_correct_recipe_lands_on_the_right_physics_or_refuses(self, item, bearings_cfg):
        if item.outcome in ("refuse", "not_text"):
            with pytest.raises(RecipeExecutionError):
                _run(item.name, item.recipe, bearings_cfg)
            return

        case, kind, note = _run(item.name, item.recipe, bearings_cfg)
        assert kind == f"inferred_{item.recipe.kind}"

        spectrum = case.spectra["y"]
        assert len(spectrum.freq_hz) >= 400, (
            f"{item.name}: only {len(spectrum.freq_hz)} of 501 rows survived")

        if item.severity_expected:
            assert case.sensor_data.y_velocity_mm_sec is not None
            assert "severity" in (case.validation_scope or [])
        else:
            assert case.sensor_data.y_velocity_mm_sec is None
            assert case.validation_scope == ["rca"]
            assert "severity" in note.lower()

    @pytest.mark.parametrize(
        "item", [i for i in CORPUS if i.outcome == "case" and i.gate == "pass"],
        ids=[i.name for i in CORPUS if i.outcome == "case" and i.gate == "pass"])
    def test_a_correctly_read_spectrum_recovers_1x_and_bpfo(self, item, bearings_cfg):
        """The proof that encoding, line endings, decimal marks, digit grouping
        and the x-axis unit were ALL handled: whatever the file looked like, the
        shaft line comes back at 30 Hz and BPFO at 107 Hz."""
        case, _, _ = _run(item.name, item.recipe, bearings_cfg)
        spectrum = case.spectra["y"]
        pairs = list(zip(spectrum.freq_hz, spectrum.amplitude))

        low = [p for p in pairs if 0 < p[0] <= 45]
        assert abs(max(low, key=lambda p: p[1])[0] - SHAFT_HZ) < 1.0, \
            f"{item.name}: 1x did not land at {SHAFT_HZ} Hz"

        band = [p for p in pairs if 90 <= p[0] <= 125]
        assert abs(max(band, key=lambda p: p[1])[0] - BPFO_HZ) < 1.0, \
            f"{item.name}: BPFO did not land at {BPFO_HZ} Hz"

    @pytest.mark.parametrize("item", [i for i in CORPUS if i.outcome == "case"],
                             ids=[i.name for i in CORPUS if i.outcome == "case"])
    def test_the_gate_says_what_the_corpus_says_it_will(self, item, bearings_cfg, tolerances):
        case, _, _ = _run(item.name, item.recipe, bearings_cfg)
        failures = failed(verify_case(case, rpm=RPM, tolerances=tolerances))
        if item.gate == "pass":
            assert failures == [], (f"{item.name}: correctly-read file refused by the gate "
                                    f"({[c.name for c in failures]})")
        else:
            assert failures, f"{item.name}: {item.hazard} — the gate accepted it"

    def test_row_level_accounting_on_the_lossy_shapes(self, bearings_cfg):
        """Rows are dropped for exactly one reason each, and the count says so.
        A silently short read is how the BOM defect hid for a whole session."""
        expected = {
            "truncated.txt": 500,        # the partial final row, and nothing else
            "fixed_width.txt": 498,      # three over-range rows merged into one field
            "comments_blanks.dat": 501,  # comments and blanks cost nothing
            "binary_tail.txt": 501,      # the blob is dropped, every text row survives
            "utf8_bom_nohdr.txt": 501,   # the BOM no longer eats datum one
            "utf16le_bom.txt": 501,
        }
        for name, count in expected.items():
            case, _, _ = _run(name, BY_NAME[name].recipe, bearings_cfg)
            assert len(case.spectra["y"].freq_hz) == count, name


# ══════════════════════════════════════════════════════════════════════════
# 3 · Immune tests — plausibly WRONG recipes must never produce a Case
# ══════════════════════════════════════════════════════════════════════════

# (fixture, why a model would plausibly emit this, recipe overrides)
WRONG_RECIPES: tuple[tuple[str, str, dict], ...] = (
    ("decimal_comma_cpm.txt", "the comma read as a thousands mark, not a decimal",
     dict(decimal_mark="dot", thousands_separator="comma")),
    ("decimal_comma_cpm.txt", "a CPM axis read as Hz (60x inflation)",
     dict(x_unit="hz")),
    ("thousands_eu_dot.txt", "EU dot-grouping read as a US decimal point",
     dict(decimal_mark="dot")),
    ("thousands_eu_dot.txt", "the grouping dot deleted as a thousands mark",
     dict(decimal_mark="dot", thousands_separator="comma")),
    ("thousands_space.txt", "the grouping space taken for a column delimiter",
     dict(delimiter="whitespace", thousands_separator="none")),
    ("scientific.txt", "US scientific notation read with a comma decimal mark",
     dict(decimal_mark="comma")),
    ("scientific_eu.asc", "EU scientific notation read with a dot decimal mark",
     dict(decimal_mark="dot")),
    ("orders_no_speed.txt", "a shaft-order axis read as Hz",
     dict(x_unit="hz")),
    ("orders_no_speed.txt", "a shaft-order axis read as CPM",
     dict(x_unit="cpm")),
    ("crlf.txt", "an Hz axis read as CPM (60x compression)",
     dict(x_unit="cpm")),
    ("crlf.txt", "the column roles swapped — amplitude read as frequency",
     dict(columns=["amplitude", "x"])),
    ("crlf.txt", "a US file read with European decimals",
     dict(decimal_mark="comma")),
    ("numeric_preamble.dat", "skip_rows off by one — the preamble read as data",
     dict(skip_rows=0)),
    ("fixed_width.txt", "a comma delimiter on a whitespace-aligned file",
     dict(delimiter="comma")),
    ("two_blocks.txt", "over-skipping into the middle of the first block",
     dict(skip_rows=2)),
    ("db_units.txt", "dB amplitudes declared as mm/s velocity",
     dict(amplitude_unit="mm_s")),
    ("db_units.txt", "dB amplitudes declared as in/s velocity",
     dict(amplitude_unit="in_s")),
    ("comments_blanks.dat", "the wrong column index on a two-column file",
     dict(columns=["amplitude", "x"])),
    ("cp1252_degrees.txt", "the wrong delimiter on a semicolon file",
     dict(delimiter="comma")),
)


class TestImmuneToWrongRecipes:
    @pytest.mark.parametrize(
        "name,why,over", WRONG_RECIPES,
        ids=[f"{n}--{w[:40]}" for n, w, _ in WRONG_RECIPES])
    def test_no_wrong_recipe_survives(self, name, why, over, bearings_cfg, tolerances):
        """The single most important assertion in this file.

        A wrong recipe has exactly three acceptable fates: the closed schema
        rejects it, the executor refuses it, or the verification gate fails it.
        The fourth outcome — a Case the gate accepts, carrying numbers that are
        not what the file says — is a confidently wrong diagnosis handed to an
        analyst, and it is what this whole corpus exists to make impossible.
        """
        item = BY_NAME[name]
        try:
            wrong = item.recipe.model_copy(update=over)
        except Exception:
            return  # the closed schema refused it — the earliest and best fate

        try:
            case, _, _ = _run(name, wrong, bearings_cfg)
        except RecipeExecutionError:
            return  # the executor refused it
        except Exception as exc:  # noqa: BLE001 — any other exception is the finding
            pytest.fail(f"{name} ({why}): crashed with {type(exc).__name__} instead of "
                        f"refusing cleanly — an analyst would see a 5xx, not the funnel")

        failures = failed(verify_case(case, rpm=RPM, tolerances=tolerances))
        assert failures, (
            f"{name} ({why}): SILENTLY WRONG — the recipe is wrong, the executor produced a "
            f"Case, and the gate accepted it. overall={case.sensor_data.y_velocity_mm_sec}, "
            f"axis={case.spectra['y'].freq_hz[0]:.2f}-{case.spectra['y'].freq_hz[-1]:.2f} Hz")

    def test_a_refusal_message_never_quotes_the_file(self, bearings_cfg):
        """Refusal text reaches the analyst through the funnel. It must name the
        problem without quoting the file — an instrument export carries a
        machine name, a route, an operator, a plant."""
        with pytest.raises(RecipeExecutionError) as excinfo:
            _run("prose_report.txt", BY_NAME["prose_report.txt"].recipe, bearings_cfg)
        message = str(excinfo.value)
        assert message
        for leak in ("12-P-101", "MONTHLY", "Traceback", "/", "\\"):
            assert leak not in message, f"refusal message leaked {leak!r}: {message}"

    def test_an_uncatchable_unit_claim_is_still_declared_to_the_analyst(self):
        """The honest limit of this gate, asserted rather than left implicit.

        Acceleration in m/s2 declared as mm/s velocity is numerically
        indistinguishable — the numbers are the same size, the spectrum is the
        same shape, the overall is 3.9 mm/s and entirely plausible. No data-side
        check can catch it, and none is pretended. What must not happen is that
        the wrong claim is made SILENTLY: the confirm card is built from the
        recipe's own vocabulary and states the unit and its severity
        consequence, which is the analyst's chance to correct it.
        """
        wrong = BY_NAME["accel_ms2.asc"].recipe.model_copy(update={"amplitude_unit": "mm_s"})
        described = wrong.describe(form_rpm=RPM)
        assert described["amplitude_label"] == "mm/s"
        assert described["severity_available"] is True
        assert "mm/s" in described["headline"] and "RMS" in described["headline"]

        honest = BY_NAME["accel_ms2.asc"].recipe.describe(form_rpm=RPM)
        assert honest["severity_available"] is False
        assert "m/s²" in honest["amplitude_label"]


# ══════════════════════════════════════════════════════════════════════════
# 4 · The funnel — a refusal is a friendly card, never a 5xx or a stuck job
# ══════════════════════════════════════════════════════════════════════════
class TestRefusalsSurfaceAsTheFunnel:
    """Through the live app, keyless, with a fake client. The point is not that
    these files fail — it is HOW they fail: a terminal job state carrying an
    analyst-safe message, and a 2xx on every request along the way."""

    @pytest.mark.parametrize("name,why", [
        ("binary_head.txt", "binary bytes wearing a .txt suffix"),
        ("prose_report.txt", "a narrative report that is not a data export"),
    ])
    def test_an_unreadable_upload_ends_in_the_couldnt_interpret_card(self, name, why, tmp_path):
        from fastapi.testclient import TestClient

        from tests.test_inference_webapp import _app, _await_confirm, _post

        app = _app(None, tmp_path=tmp_path)          # keyless: inference unavailable
        with TestClient(app) as client:
            response = _post(client, FIXTURE_DIR / name)
            assert response.status_code == 202, f"{why}: {response.status_code}"
            data = _await_confirm(client, response.json()["job_id"])

        assert data["state"] == "error", f"{why}: job ended {data['state']}"
        message = data["safe_message"]
        assert message, f"{why}: no message for the analyst"
        for leak in ("Traceback", "vib_agent", ".py", "MONTHLY"):
            assert leak not in message, f"{why}: internals leaked into {message!r}"

    def test_a_misread_file_becomes_an_insufficient_data_report_not_an_error(self, tmp_path):
        """The distinction the product is built on: `error` is for an upload
        that could not be READ. A file that parsed cleanly but whose
        interpretation its own data contradicts gets a REPORT naming what went
        wrong — never a diagnosis on a misread file, and never a dead end.
        """
        from fastapi.testclient import TestClient

        from tests.test_inference_webapp import _app, _await_confirm, _post, _recipe_reply
        from tests.fake_anthropic import FakeAnthropicClient
        from tests.test_webapp_e2e import _poll_until_terminal

        # a CPM axis read as Hz — the inflation error the new gate rule catches
        wrong = BY_NAME["decimal_comma_cpm.txt"].recipe.model_copy(update={"x_unit": "hz"})
        fake = FakeAnthropicClient(responses=[_recipe_reply(wrong)])
        app = _app(fake, tmp_path=tmp_path)
        with TestClient(app) as client:
            response = _post(client, FIXTURE_DIR / "decimal_comma_cpm.txt")
            assert response.status_code == 202
            job_id = response.json()["job_id"]

            paused = _await_confirm(client, job_id)
            assert paused["state"] == "awaiting_confirm", paused
            assert client.post(f"/api/jobs/{job_id}/confirm", json={}).status_code == 202
            data = _poll_until_terminal(client, job_id)

        assert data["state"] == "gate_fail", (
            f"a misread file must reach the insufficient-data path, not `error`: {data}")

        # ...and the analyst is told WHICH check refused, in the gate's own words.
        reasons = " ".join(data["gate_summary"]["reasons"])
        assert "coarser than the running speed" in reasons
        assert "60x error" in reasons, "the reason must name the likely cause"

        # the interpretation that was refused is still on the card, so the
        # analyst can see the claim ("mm/s RMS spectrum", x axis "Hz") that the
        # file's own data contradicted
        assert data["interpretation"]["x_axis"] == "Hz"
