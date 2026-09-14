"""Session INTAKEFIX-1 — the ISO 20816-3 group boundaries live in config (INTAKE-2 F-2).

INTAKE-2 shipped `ISO_GROUP_2_MIN_KW` / `ISO_GROUP_1_MIN_KW` as literals in
`adapters/uploads/common.py` and wrote its own finding against them: they are
tunables, and CLAUDE.md says *"All tunable constants live in `config/*.json` —
never hardcode them. Code reads whatever the config contains."* They were in code
only because `config/**` was outside `scope/intake2.txt`.

TWO HALVES, AND ONLY ONE OF THEM IS CLOSED HERE.

  * **The location is fixed.** `config/iso20816_3.json` holds them and this
    module reads it.
  * **The SOURCE is not.** `references/INDEX.md` still has no ISO 20816-3 row, so
    each entry carries `"source": "unverified - no indexed reference"` and
    `_verify` is still `true`. The brief was explicit: *"Never invent or adjust a
    boundary."* These tests assert the values are the ones INTAKE-2 shipped, to
    the digit, so a future edit to this config is a deliberate act with a red
    test in front of it rather than a number that drifted in a JSON file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vib_agent.adapters.uploads import common
from vib_agent.config import load_config

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _ROOT / "config" / "iso20816_3.json"

#: The values INTAKE-2 shipped, restated here rather than read from the config,
#: because a test that reads its subject cannot detect a change to it.
_GROUP_2_MIN_KW = 15.0
_GROUP_1_MIN_KW = 300.0


class TestTheConfigExistsAndHoldsWhatItSaysItHolds:

    def test_the_file_is_where_load_config_looks(self):
        assert _CONFIG.is_file()
        assert load_config("iso20816_3")["groups"]

    def test_the_boundaries_are_the_ones_intake_2_shipped(self):
        """Moved, not adjusted. A different number here is a change to how every
        machine's severity is judged, and it needs its own brief."""
        groups = load_config("iso20816_3")["groups"]
        assert groups["group_2"]["min_kw"] == _GROUP_2_MIN_KW
        assert groups["group_1"]["min_kw"] == _GROUP_1_MIN_KW

    @pytest.mark.parametrize("group", ("group_1", "group_2"))
    def test_every_entry_names_its_source(self, group):
        entry = load_config("iso20816_3")["groups"][group]
        assert entry["source"].strip(), f"{group} has no source field"

    @pytest.mark.parametrize("group", ("group_1", "group_2"))
    def test_the_source_is_still_honest_about_being_unindexed(self, group):
        """When the operator indexes ISO 20816-3, this test is what they meet:
        replace the source with its INDEX number and drop `_verify`, in the same
        edit, so the two cannot disagree."""
        entry = load_config("iso20816_3")["groups"][group]
        indexed = not entry.get("_verify", False)
        unverified = "unverified" in entry["source"].lower()
        assert indexed != unverified, (
            f"{group}: `_verify` and `source` disagree — a boundary is either "
            "traceable to references/INDEX.md or it is not"
        )

    def test_the_standard_is_still_not_in_the_reference_index(self):
        """The measured fact the two tests above are written around. Delete this
        test in the commit that indexes the standard — it is a reminder with an
        expiry date, not a rule."""
        index = (_ROOT / "references" / "INDEX.md").read_text()
        assert "20816" not in index, (
            "ISO 20816-3 is in references/INDEX.md now — cite it in "
            "config/iso20816_3.json, drop the _verify markers, and delete this test"
        )


class TestTheCodeReadsTheConfigRatherThanRepeatingIt:

    def test_the_constants_are_the_configs_values(self):
        groups = load_config("iso20816_3")["groups"]
        assert common.ISO_GROUP_2_MIN_KW == groups["group_2"]["min_kw"]
        assert common.ISO_GROUP_1_MIN_KW == groups["group_1"]["min_kw"]

    def test_no_boundary_is_written_as_a_literal_in_the_module(self):
        """Non-vacuity for the test above: if the numbers were still declared in
        code, comparing them to a config that happened to agree would prove
        nothing. `15.0` and `300.0` must not appear as assignments."""
        body = (_ROOT / "src" / "vib_agent" / "adapters" / "uploads"
                / "common.py").read_text()
        assert "ISO_GROUP_2_MIN_KW = 15.0" not in body
        assert "ISO_GROUP_1_MIN_KW = 300.0" not in body
        assert 'load_config("iso20816_3")' in body

    def test_the_verify_marker_survived_the_move(self):
        body = (_ROOT / "src" / "vib_agent" / "adapters" / "uploads"
                / "common.py").read_text()
        assert "# VERIFY" in body, (
            "the marker says these numbers are not traceable to an indexed "
            "source; moving them to config did not make them traceable"
        )


class TestClassificationIsUnchangedByTheMove:
    """The only thing that actually matters: no machine changes group."""

    @pytest.mark.parametrize("rated_kw,group,has_note", [
        (7.5, "2", True),      # below scope — closest row, and say so
        (14.9, "2", True),
        (15.0, "2", False),    # the floor itself is IN scope
        (90.0, "2", False),
        (300.0, "2", False),   # the Group 1 bound is strict: 300 is Group 2
        (300.1, "1", False),
        (900.0, "1", False),
    ])
    def test_group_from_rated_kw(self, rated_kw, group, has_note):
        got, note = common.group_from_rated_kw(rated_kw)
        assert got == group
        assert (note is not None) == has_note

    def test_the_below_scope_note_names_the_configs_own_floor(self):
        """The note says "15 kW". If the config's floor moved and the sentence
        did not, the report would cite a threshold the code no longer uses."""
        floor = load_config("iso20816_3")["groups"]["group_2"]["min_kw"]
        assert f"{floor:g} kW" in common.ISO_BELOW_SCOPE_NOTE
