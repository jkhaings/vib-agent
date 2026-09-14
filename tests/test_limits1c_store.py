"""Session LIMITS-1c items 2-4 — the form, the wire, and the browser card.

Three layers, pinned where each one actually lives:

  * the node suite (`tests/js/limits1c_limits_tests.js`) drives the SHIPPED
    `app.js` for the store and the inline refusal;
  * `_thresholds_422` is exercised here, directly, because it is the authority
    — the browser's copy is the immediate half and the server's is the one that
    decides;
  * `thresholds_from_form` -> `MachineMeta.thresholds` is item 3's whole wire,
    one keyword, and is pinned end to end into a real zone.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from vib_agent.adapters.uploads.common import (
    UploadForm,
    machine_from_form,
    thresholds_from_form,
)
from vib_agent.config import load_config, load_thresholds
from vib_agent.models import MachineThresholds
from vib_agent.webapp.app import _thresholds_422

_ROOT = Path(__file__).resolve().parents[1]
_SUITE = _ROOT / "tests" / "js" / "limits1c_limits_tests.js"
_INDEX = _ROOT / "src" / "vib_agent" / "webapp" / "static" / "index.html"


# ── item 4: the browser, in node ─────────────────────────────────────────

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_limits_suite_passes():
    result = subprocess.run([shutil.which("node"), str(_SUITE)],
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    # Non-vacuity: a suite that collected nothing exits 0 too.
    assert re.search(r"\b1[0-9]/1[0-9] passed", result.stdout), result.stdout


# ── item 2: the markup ───────────────────────────────────────────────────

class TestTheFormOffersThem:

    @pytest.fixture(scope="class")
    def markup(self):
        return _INDEX.read_text()

    def test_all_three_inputs_are_there_and_optional(self, markup):
        for name in ("limit_ab", "limit_bc", "limit_cd"):
            assert f'name="{name}"' in markup, name
        # `required` on any of them would make the ISO path unreachable.
        block = markup[markup.index('name="limit_ab"'):markup.index('id="lim-help"')]
        assert "required" not in block

    def test_the_help_line_is_the_ruled_wording(self, markup):
        assert "Leave blank to judge against ISO 20816-3." in markup

    def test_there_is_somewhere_for_the_error_to_go(self, markup):
        assert 'id="lim-error"' in markup
        assert 'class="help stop"' in markup

    def test_iso_group_and_support_are_still_required(self, markup):
        """INTAKEFIX-1 F-3, and the brief: the "would give Zone X" clause needs
        both, and `_iso_fallback` returns None without them."""
        from vib_agent.webapp import app as app_mod
        source = Path(app_mod.__file__).read_text()
        assert "iso_group: str = Form(...)" in source
        assert "iso_support: str = Form(...)" in source


# ── item 2: the refusal, which is MachineThresholds' own ─────────────────

class TestTheServerRefusesABadTrio:

    def test_none_given_is_the_iso_path(self):
        assert _thresholds_422({}) is None
        assert _thresholds_422({"limit_ab": None, "limit_bc": None, "limit_cd": None}) is None

    def test_a_good_trio_passes(self):
        assert _thresholds_422({"limit_ab": 5.0, "limit_bc": 8.0, "limit_cd": 12.0}) is None

    @pytest.mark.parametrize("given,expect", [
        ({"limit_ab": 5.0}, "Zone B/C, Zone C/D are still blank"),
        ({"limit_ab": 5.0, "limit_bc": 8.0}, "Zone C/D is still blank"),
        ({"limit_bc": 8.0}, "Zone A/B, Zone C/D are still blank"),
    ])
    def test_a_partial_trio_names_the_blanks(self, given, expect):
        detail = _thresholds_422(given)
        assert detail is not None and expect in detail, detail
        assert "Leave all three blank to judge against ISO 20816-3." in detail

    @pytest.mark.parametrize("trio,expect", [
        ((0.0, 8.0, 12.0), "Zone A/B must be > 0"),
        ((5.0, 8.0, -1.0), "Zone C/D must be > 0"),
        ((12.0, 8.0, 5.0), "the Zone A/B, B/C and C/D limits must increase"),
        ((5.0, 5.0, 12.0), "the Zone A/B, B/C and C/D limits must increase"),
    ])
    def test_a_bad_trio_is_refused_in_the_models_own_words(self, trio, expect):
        detail = _thresholds_422(dict(zip(("limit_ab", "limit_bc", "limit_cd"), trio)))
        assert detail is not None and expect in detail, detail

    def test_no_refusal_ever_leaks_pydantics_plumbing(self):
        """The near-miss this pin exists for: `str(ValidationError)` renders a
        multi-line report whose LAST line is pydantic's docs URL, and reading it
        that way was measured returning
        "For further information visit https://errors.pydantic.dev/..." to an
        analyst. The message is taken from `.errors()[0]["msg"]` instead."""
        for trio in ((0.0, 8.0, 12.0), (12.0, 8.0, 5.0), (5.0, 5.0, 12.0)):
            detail = _thresholds_422(dict(zip(("limit_ab", "limit_bc", "limit_cd"), trio)))
            for leak in ("pydantic", "http", "Value error", "thresholds.", "validation error"):
                assert leak not in detail, (leak, detail)

    def test_the_refusal_is_not_a_second_copy_of_the_rules(self):
        """Every trio `MachineThresholds` refuses, `_thresholds_422` refuses,
        and every trio it accepts, `_thresholds_422` accepts. Checked over a
        grid rather than asserted in a docstring."""
        values = (-1.0, 0.0, 1.0, 5.0, 8.0, 12.0)
        for ab in values:
            for bc in values:
                for cd in values:
                    try:
                        MachineThresholds(ab=ab, bc=bc, cd=cd)
                        model_ok = True
                    except ValueError:
                        model_ok = False
                    server_ok = _thresholds_422(
                        {"limit_ab": ab, "limit_bc": bc, "limit_cd": cd}) is None
                    assert model_ok == server_ok, (ab, bc, cd, model_ok, server_ok)


# ── item 3: the wire ─────────────────────────────────────────────────────

class TestTheWire:

    @staticmethod
    def _form(**extra):
        return UploadForm(machine_alias="P", rpm=1780.0, iso_group="2",
                          iso_support="rigid", machine_type="pump", **extra)

    def test_no_limits_is_no_thresholds(self):
        assert thresholds_from_form(self._form()) is None

    def test_all_three_becomes_a_machine_thresholds(self):
        got = thresholds_from_form(self._form(limit_ab=5.0, limit_bc=8.0, limit_cd=12.0))
        assert got == MachineThresholds(ab=5.0, bc=8.0, cd=12.0)

    @pytest.mark.parametrize("partial", [
        {"limit_ab": 5.0},
        {"limit_ab": 5.0, "limit_bc": 8.0},
        {"limit_cd": 12.0},
    ])
    def test_a_partial_trio_fails_CLOSED_to_iso(self, partial):
        """Never a half-stated limit. `_thresholds_422` has already refused this
        on the product path; this is the backstop for the lanes that do not come
        through that form."""
        assert thresholds_from_form(self._form(**partial)) is None

    def test_it_reaches_machine_meta(self):
        bearings = load_config("bearings")
        machine = machine_from_form(
            self._form(limit_ab=5.0, limit_bc=8.0, limit_cd=12.0),
            bearings, mac_prefix="web")
        assert machine.thresholds == MachineThresholds(ab=5.0, bc=8.0, cd=12.0)

    def test_and_an_upload_without_one_is_unchanged(self):
        bearings = load_config("bearings")
        assert machine_from_form(self._form(), bearings, mac_prefix="web").thresholds is None

    def test_the_webapp_never_sets_threshold_source(self):
        """LIMITS-1a §3: `pdm_core` stamps it. A webapp that set it too would be
        a second authority on the same question."""
        source = Path(
            __import__("vib_agent.adapters.uploads.common", fromlist=["x"]).__file__
        ).read_text()
        assert "threshold_source=" not in source, (
            "the webapp sets threshold_source; pdm_core is the only authority "
            "on which tier won (LIMITS-1a \u00a73)")

    def test_it_becomes_a_custom_zone_end_to_end(self):
        """The claim the whole session is for: limits on the form change the
        zone letter, and record what ISO would have said."""
        from vib_agent.models import SensorData
        from vib_agent.pdm_core.iso_classify import classify, resolve_thresholds
        bearings = load_config("bearings")
        iso_table = load_config("iso_zones")["zones"]
        machine = machine_from_form(
            self._form(limit_ab=5.0, limit_bc=8.0, limit_cd=12.0),
            bearings, mac_prefix="web")
        resolved = resolve_thresholds(machine, iso_table)
        assert resolved.source == "custom"
        # 5.20 mm/s on y: Zone B against 5/8/12, Zone D against ISO's
        # 1.4/2.8/4.5. The same trio tests/test_limits1_zones.py walks.
        reading = classify(
            SensorData(x_velocity_mm_sec=0.5, y_velocity_mm_sec=5.2,
                       z_velocity_mm_sec=4.8),
            machine, resolved)
        assert reading.iso_zone == "B"
        assert reading.zone_basis == "custom"
        assert reading.iso_zone_would_be == "D"
