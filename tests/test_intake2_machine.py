"""Session INTAKE-2 — the machine kind, and the ISO row it is classified against.

Two things are pinned here, and the first one is a defect this product shipped
for its whole life.

**PARTC F-2.** `adapters/uploads/common.py:252` hard-coded `type="motor"` on
every upload lane, so a compressor's report read *"Type motor"* on page 1. It is
not cosmetic: `pdm_core/recommendations.py:179` reads `machine.type` back and
asks whether the machine is motor-driven, so a fabricated type silently shaped
the advice an analyst signed. The literal is gone from all five upload-lane
sites; the type is REQUIRED at the form boundary, refused there with a sentence
written for an analyst; and the four `.mat` lanes keep their rig default under
GEOM-1 F-4's precedence (the form's answer wins, otherwise the family default).

**The ISO 20816-3 row.** `config/iso_zones.json` is keyed `f"{group}_{support}"`,
and until this session both halves arrived as raw selects with no provenance. A
report that prints "Group 2" must be able to say WHY, and *"because nobody told
us"* is a different sentence from *"because the analyst rated it at 90 kW"* —
the same distinction `coupled_stated`, `history_source` and `readings.zone_source`
all exist to keep. `resolve_iso_class` settles it in one place with three
sources, and the boundaries are marked VERIFY because `references/INDEX.md` has
no ISO 20816-3 row at all.

The derivation is pinned as a TABLE, on both sides of both boundaries, because a
threshold with one test either side of it is the one shape that cannot tell `>`
from `>=`.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient
from tests.test_webapp_e2e import _poll_until_terminal, _webapp_cfg
from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import (
    ISO_BELOW_SCOPE_NOTE,
    ISO_DEFAULT_GROUP,
    ISO_DEFAULT_SUPPORT,
    ISO_GROUP_1_MIN_KW,
    ISO_GROUP_2_MIN_KW,
    MACHINE_TYPE_UNKNOWN,
    MACHINE_TYPES,
    UploadForm,
    machine_from_form,
    machine_type_from_form,
    resolve_iso_class,
)
from vib_agent.config import load_config
from vib_agent.webapp.app import _machine_422, create_app

_ROOT = Path(__file__).resolve().parents[1]
_UPLOADS = _ROOT / "src" / "vib_agent" / "adapters" / "uploads"
_RPM = 1800.0


def _csv(path, *, n=400, fmax=200.0):
    freqs = [i * fmax / n for i in range(n)]
    amp = [0.001] * n
    amp[min(range(n), key=lambda i: abs(freqs[i] - _RPM / 60.0))] = 0.4
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["freq_hz", "amplitude"])
        for freq, value in zip(freqs, amp):
            writer.writerow([freq, value])
    return path


def _form(**over):
    data = {"invite_code": "demo-code", "machine_alias": "TestPump", "rpm": str(_RPM),
            "iso_group": "2", "iso_support": "rigid", "machine_type": "pump"}
    data.update({k: str(v) for k, v in over.items()})
    return data


@pytest.fixture
def client(tmp_path):
    # The fake Anthropic client, like every other webapp suite here: with no
    # factory the real SDK is constructed with no key and the drafting stage
    # dies with a TypeError instead of taking the designed degrade path.
    # `responses=[]` is GEOM-1's own idiom -- the draft runs out of replies and
    # the job degrades to the deterministic report, which is what these
    # assertions are about. Zero real API calls either way.
    fake = FakeAnthropicClient(responses=[])
    app = create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"},
                     anthropic_client_factory=lambda: fake)
    with TestClient(app) as handle:
        yield handle


def _post(client, path, **over):
    with open(path, "rb") as handle:
        return client.post("/api/jobs", files={"file": (path.name, handle, "text/csv")},
                           data=_form(**over))


# ──────────────────────────────────────────────────────────────────────────
# PARTC F-2
# ──────────────────────────────────────────────────────────────────────────

class TestTheMotorLiteralIsGone:
    """The defect, checked at the source and then through the product."""

    def test_no_upload_lane_hard_codes_a_machine_type(self):
        """The grep PART-C would have caught this with.

        Scoped to `adapters/uploads/` -- the lanes a FORM reaches. The eval
        adapters (`adapters/cwru.py` and friends) hold the same literal for
        their own rigs and no form reaches them; they are out of scope and
        deliberately unmoved.
        """
        offenders = [
            f"{path.name}:{n}"
            for path in sorted(_UPLOADS.glob("*.py"))
            for n, line in enumerate(path.read_text().splitlines(), 1)
            if re.search(r'type\s*=\s*"motor"', line)
        ]
        assert offenders == [], (
            "an upload lane is fabricating a machine type again (PARTC F-2); "
            f"found at {offenders}"
        )

    def test_a_compressor_is_recorded_as_a_compressor(self, tmp_path):
        """The brief's own acceptance: pin it with a compressor."""
        form = UploadForm(machine_alias="Synthetic Compressor 01", rpm=_RPM,
                          iso_group="2", iso_support="rigid",
                          machine_type="compressor")
        # `parse_upload` returns (case, kind, conversion_note).
        case, _kind, _note = parse_upload(_csv(tmp_path / "upload.csv"), form,
                                          bearings_cfg=load_config("bearings"))
        assert case.machine.type == "compressor"

    @pytest.mark.parametrize("kind", MACHINE_TYPES)
    def test_every_offered_type_survives_the_adapter(self, tmp_path, kind):
        """Every option the dropdown offers reaches `MachineMeta` unchanged.

        A dropdown whose values the analysis silently rejects is the drift
        `tests/test_ux2_bearings.py` exists to catch for bearings.
        """
        form = UploadForm(machine_alias="M", rpm=_RPM, iso_group="2",
                          iso_support="rigid", machine_type=kind)
        case, _kind, _note = parse_upload(_csv(tmp_path / "upload.csv"), form,
                                          bearings_cfg=load_config("bearings"))
        assert case.machine.type == kind

    def test_a_stated_type_beats_the_rig_default(self):
        """GEOM-1 F-4's precedence, stated as an assertion.

        The `.mat` lanes pass `default="motor"`; a form that names the machine
        wins, and a form that says nothing gets the rig's own answer. Both
        directions, because only having the first is how a default becomes
        unreachable.
        """
        stated = UploadForm(machine_alias="M", rpm=_RPM, iso_group="2",
                            iso_support="rigid", machine_type="gearbox")
        silent = UploadForm(machine_alias="M", rpm=_RPM, iso_group="2",
                            iso_support="rigid")
        assert machine_type_from_form(stated, default="motor") == "gearbox"
        assert machine_type_from_form(silent, default="motor") == "motor"

    def test_a_product_lane_with_no_type_says_unknown_and_never_motor(self):
        """The fallback is TRUE where the literal was false.

        A caller that never passed the form boundary -- the CLI, a fixture --
        gets "unknown", which reads correctly in a report and is the
        conservative value at recommendations.py:179. What it must never be is
        "motor", because that was the bug.
        """
        form = UploadForm(machine_alias="M", rpm=_RPM, iso_group="2",
                          iso_support="rigid")
        assert machine_type_from_form(form) == MACHINE_TYPE_UNKNOWN
        assert machine_type_from_form(form) != "motor"
        assert MACHINE_TYPE_UNKNOWN not in MACHINE_TYPES, (
            "the absence of an answer must never be an option in the dropdown"
        )

    def test_an_invented_type_is_refused_not_absorbed(self):
        """The control is a `<select>`, so an unknown value is a hand-built
        post and refusing it costs a real analyst nothing."""
        form = UploadForm(machine_alias="M", rpm=_RPM, iso_group="2",
                          iso_support="rigid", machine_type="turboencabulator")
        with pytest.raises(ValueError, match="not a machine type"):
            machine_from_form(form, load_config("bearings"), mac_prefix="X")


class TestTheTypeIsRequiredAtTheFormBoundary:
    """`_machine_422` — free, before a job exists or an allowance is spent."""

    def test_a_missing_type_is_refused_in_analyst_words(self):
        problem = _machine_422({"machine_type": None})
        assert problem is not None
        assert "kind of machine" in problem
        # It names what the analyst must DO, and never a field name they
        # cannot see on the page (GEOM-1's rule for the eight bearing
        # messages).
        assert "machine_type" not in problem

    def test_an_unknown_type_names_the_vocabulary(self):
        problem = _machine_422({"machine_type": "reactor"})
        assert problem is not None
        for kind in MACHINE_TYPES:
            assert kind in problem

    def test_a_stated_type_passes(self):
        assert _machine_422({"machine_type": "compressor"}) is None

    @pytest.mark.parametrize("field,bad", [("rated_kw", 0.0), ("rated_kw", -5.0),
                                           ("driven_rpm", 0.0), ("driven_rpm", -1.0)])
    def test_a_nonpositive_rating_is_refused(self, field, bad):
        problem = _machine_422({"machine_type": "pump", field: bad})
        assert problem is not None and "positive" in problem

    def test_an_unknown_mounting_is_refused(self):
        problem = _machine_422({"machine_type": "pump", "mounting": "welded"})
        assert problem is not None and "mounting" in problem

    def test_the_wire_refuses_a_post_with_no_type(self, client, tmp_path):
        """End to end, and it is a 422 with a STRING detail -- item 7's
        contract. A list-shaped detail is what `app.js:4700` discards."""
        path = _csv(tmp_path / "upload.csv")
        with open(path, "rb") as handle:
            res = client.post("/api/jobs",
                              files={"file": (path.name, handle, "text/csv")},
                              data=_form(machine_type=""))
        assert res.status_code == 422
        assert isinstance(res.json()["detail"], str)
        assert "kind of machine" in res.json()["detail"]

    def test_the_refusal_creates_no_job(self, client, tmp_path):
        """A validation refusal must not cost a job or an allowance."""
        path = _csv(tmp_path / "upload.csv")
        with open(path, "rb") as handle:
            res = client.post("/api/jobs",
                              files={"file": (path.name, handle, "text/csv")},
                              data=_form(machine_type=""))
        assert res.status_code == 422
        assert "job_id" not in res.json()


# ──────────────────────────────────────────────────────────────────────────
# The ISO 20816-3 row
# ──────────────────────────────────────────────────────────────────────────

class TestTheIsoClassIsDerivedAndSaysHow:
    """`resolve_iso_class` — a table, on both sides of both boundaries."""

    @pytest.mark.parametrize("kw,expected", [
        (0.5, "2"),                          # far below the scope floor
        (ISO_GROUP_2_MIN_KW - 0.1, "2"),     # 14.9 — below, still Group 2
        (ISO_GROUP_2_MIN_KW, "2"),           # 15.0 — the floor itself, in scope
        (ISO_GROUP_2_MIN_KW + 0.1, "2"),     # 15.1
        (150.0, "2"),                        # the middle of Group 2
        (ISO_GROUP_1_MIN_KW - 0.1, "2"),     # 299.9 — still Group 2
        (ISO_GROUP_1_MIN_KW, "2"),           # 300.0 — the boundary IS Group 2
        (ISO_GROUP_1_MIN_KW + 0.1, "1"),     # 300.1 — above it, Group 1
        (5000.0, "1"),                       # far above
    ])
    def test_the_group_boundaries(self, kw, expected):
        assert resolve_iso_class(rated_kw=kw).group == expected

    def test_the_boundary_values_are_inclusive_downwards(self):
        """`300.0` is Group 2 and `300.1` is Group 1 -- stated as its own
        assertion because "above 300 kW" is the brief's wording and an
        off-by-one here silently moves every zone boundary on a big machine."""
        assert resolve_iso_class(rated_kw=300.0).group == "2"
        assert resolve_iso_class(rated_kw=300.01).group == "1"

    def test_below_the_scope_floor_is_classified_and_says_so(self):
        """ISO 20816-3 starts at 15 kW. A 7.5 kW machine is classified against
        a table it is not in, so it gets the closest row AND a caveat -- not a
        zone that looks as authoritative as the others, and not a refusal that
        leaves the analyst with nothing."""
        low = resolve_iso_class(rated_kw=7.5)
        assert low.group == "2"
        assert low.note == ISO_BELOW_SCOPE_NOTE
        assert "15 kW" in low.note

    def test_a_machine_inside_the_scope_gets_no_caveat(self):
        assert resolve_iso_class(rated_kw=90.0).note is None
        assert resolve_iso_class(rated_kw=900.0).note is None

    @pytest.mark.parametrize("mounting", ["rigid", "flexible"])
    def test_the_mounting_answer_is_the_support_class(self, mounting):
        assert resolve_iso_class(rated_kw=90.0, mounting=mounting).support == mounting

    def test_nothing_stated_is_group_2_rigid_and_flagged_assumed(self):
        """The brief's default, and the flag that stops it reading as a
        statement."""
        blank = resolve_iso_class()
        assert (blank.group, blank.support) == (ISO_DEFAULT_GROUP, ISO_DEFAULT_SUPPORT)
        assert (blank.group_source, blank.support_source) == ("assumed", "assumed")
        assert blank.assumed is True

    def test_the_assumed_default_is_the_tightest_row(self):
        """Group 2 rigid must be the STRICTEST zone row, or assuming it would
        promote an unrated machine into a kinder zone than it earned. Measured
        against the shipped table, not asserted from memory."""
        zones = load_config("iso_zones")["zones"]
        assumed_ab = zones[f"{ISO_DEFAULT_GROUP}_{ISO_DEFAULT_SUPPORT}"]["ab"]
        assert assumed_ab == min(row["ab"] for row in zones.values()), (
            "the assumed ISO row is no longer the tightest one; assuming it "
            "would now flatter a machine nobody rated"
        )

    def test_a_rating_beats_a_stated_group(self):
        """GEOM-1 F-4's precedence again: a derivation from what the analyst
        measured beats a select they may not have revisited."""
        got = resolve_iso_class(rated_kw=900.0, stated_group="2")
        assert (got.group, got.group_source) == ("1", "rated")

    def test_a_stated_group_is_honoured_when_nothing_was_rated(self):
        """Backward compatibility, as a property rather than as luck: the form
        has always posted these two selects and a post that carries no rating
        must keep meaning exactly what it meant."""
        got = resolve_iso_class(stated_group="1", stated_support="flexible")
        assert (got.group, got.support) == ("1", "flexible")
        assert (got.group_source, got.support_source) == ("stated", "stated")
        assert got.assumed is False

    def test_a_junk_stated_value_falls_back_rather_than_propagating(self):
        got = resolve_iso_class(stated_group="7", stated_support="bolted")
        assert (got.group, got.support) == (ISO_DEFAULT_GROUP, ISO_DEFAULT_SUPPORT)
        assert got.assumed is True

    def test_the_zone_key_is_the_one_the_config_is_keyed_by(self):
        """The derivation is only useful if its output selects a real row."""
        zones = load_config("iso_zones")["zones"]
        for kw in (7.5, 90.0, 900.0):
            for mounting in ("rigid", "flexible"):
                key = resolve_iso_class(rated_kw=kw, mounting=mounting).zone_key
                assert key in zones, f"{key} is not a row of iso_zones.json"

    def test_the_two_halves_carry_their_provenance_independently(self):
        """A rated machine on an unstated mounting is half derived and half
        assumed, and the report needs to be able to say which is which."""
        got = resolve_iso_class(rated_kw=90.0)
        assert got.group_source == "rated"
        assert got.support_source == "assumed"
        assert got.assumed is True


class TestTheDerivationReachesTheAnalysis:
    """Through the wire, not just through the function."""

    def test_a_rated_machine_is_classified_on_the_derived_row(self, client, tmp_path):
        """A 900 kW machine posted with the select still on Group 2 is analysed
        as Group 1 -- the rating is the authority and this is the assertion
        that proves the server is not simply trusting the select."""
        res = _post(client, _csv(tmp_path / "upload.csv"),
                    rated_kw=900.0, mounting="flexible", iso_group="2",
                    iso_support="rigid")
        assert res.status_code == 202, res.text
        body = _poll_until_terminal(client, res.json()["job_id"])
        assert body["state"] != "error", body
        assert body.get("iso_group") == "1"
        assert body.get("iso_support") == "flexible"
        assert body.get("iso_assumed") is False
