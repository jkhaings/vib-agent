"""Session UX-2 — the bearing field offers exactly what we hold geometry for.

`config/bearings.json` is a CLOSED catalogue and
`adapters/uploads/common.py::bearing_spec_from_form` raises for anything else --
inside the parse sandbox, so an unknown model reached the analyst as a failure
about the FILE they uploaded. The field was free text and its own placeholder
invited `SKF 32222 J2`, which is not in the catalogue.

Step 3 of the new intake shows the coverage roster forward, and its headline is
"name a bearing and the BPFO/BPFI/BSF/FTF screen turns on". Over a free-text
field that sentence is false for almost every real bearing. So the field is a
select of what we actually hold -- and these tests are the reason it can stay
true: THREE lists have to agree, and three lists that must agree and are never
diffed is how a UI comes to offer a screen the analysis cannot run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import vib_agent.webapp as webapp_pkg

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = Path(webapp_pkg.__file__).parent / "static"


def _catalogue() -> list[str]:
    return list(json.loads((_ROOT / "config" / "bearings.json").read_text())["bearings"])


def _index() -> str:
    return (_STATIC / "index.html").read_text()


def _form_options() -> list[str]:
    """The `<option value>`s of the upload form's bearing select, in order."""
    select = _index().partition('id="brg"')[2].partition("</select>")[0]
    return [v for v in re.findall(r'<option value="([^"]*)"', select) if v]


def _app_models() -> list[str]:
    block = (_STATIC / "app.js").read_text() \
        .partition("const BEARING_MODELS = [")[2].partition("\n];")[0]
    return re.findall(r"'([^']+)'", block)


def _corpus() -> list[str]:
    """The benchmark-rig entries, from the one place app.js declares them."""
    block = (_STATIC / "app.js").read_text() \
        .partition("function corpusBearings() {")[2].partition("\n}")[0]
    return re.findall(r"'([^']+)'", block)


def _offered() -> list[str]:
    """What the form actually puts in front of a route analyst."""
    return [k for k in _app_models() if k not in _corpus()]


class TestTheThreeListsAgree:
    """Session UX-5 (STRANGER B5) splits ONE list into two claims that were
    previously the same list, because they answer different questions:

      * which bearings we HOLD GEOMETRY FOR — still `config/bearings.json`,
        still exactly what `bearing_spec_from_form` honours, and still what
        `BEARING_MODELS` mirrors. Nothing was removed from it and the brief
        forbids editing it.
      * which bearings we OFFER a route analyst — that list minus three
        benchmark-rig keys, whose underscored dataset names told the stranger
        "the bearing screen was built for the validation corpora and never
        generalised". They are reached by the .mat adapters, which name their
        own rig bearing and never come through this form.

    The drift this class exists to catch is unchanged: a form offering a screen
    the analysis cannot run, or hiding one it can.
    """

    def test_the_upload_form_offers_the_catalogue_minus_the_rigs(self):
        assert _form_options() == _offered(), (
            "the bearing select and config/bearings.json have drifted; the form "
            "is offering a screen the analysis cannot run, or hiding one it can"
        )

    def test_app_js_still_holds_the_whole_catalogue(self):
        """The honoured list is not narrowed — only the offered one is. A
        session that "tidied" the rigs out of here would make the carry-over in
        `cardForForm` unreachable and silently drop a saved card's bearing."""
        assert _app_models() == _catalogue()

    def test_every_rig_is_in_the_catalogue_and_out_of_the_form(self):
        corpus = _corpus()
        assert corpus == ["SKF_6205", "MAFAULDA_ABVT", "MFPT_NICE"], corpus
        for key in corpus:
            assert key in _catalogue(), f"{key} lost its geometry; the benchmarks read it"
            assert key not in _form_options(), f"{key} is offered to a route analyst"

    def test_the_offered_list_is_not_empty(self):
        """The two equalities above would both pass over an empty form."""
        assert len(_offered()) >= 4, _offered()
        assert "6205" in _offered()

    def test_the_catalogue_is_not_empty_and_is_small(self):
        """A sanity floor: if this file ever reads an empty catalogue the two
        equalities above would pass over two empty lists."""
        assert 4 <= len(_catalogue()) <= 40, _catalogue()
        assert "6205" in _catalogue()


class TestTheFieldIsClosed:

    def test_it_is_a_select_and_no_longer_free_text(self):
        html = _index()
        assert '<select name="bearing_model" id="brg">' in html
        assert 'input type="text" name="bearing_model"' not in html
        assert "SKF 32222 J2" not in html, (
            "the placeholder invited a bearing we hold no geometry for, which "
            "reached the analyst as a PARSE_ERROR about their own file"
        )

    def test_the_empty_option_does_not_say_not_stated(self):
        """A bearing we do not hold is not un-stated, it is NOT LISTED, and the
        distinction is load-bearing: one is a question the analyst did not
        answer, the other is a catalogue we do not have.

        Session UX-5 made `Not stated` the single word for a blank field
        (STRANGER C11 -- the form said "Not stated" and the machine card said
        "Not recorded" about the same blank), so the count of selected-empty
        options that carry it went from two to four. It is asserted BY NAME
        here rather than by count, because a count cannot say which controls
        are meant and this class is the one that cares.
        """
        html = _index()
        assert '<option value="" selected>Not listed</option>' in html
        for field in ("coupling", "drive_type", "window_type", "integration"):
            block = html.partition(f'name="{field}"')[2].partition("</select>")[0]
            assert '<option value="" selected>Not stated</option>' in block, field
        assert html.count('<option value="" selected>Not stated</option>') == 4

    def test_the_field_stays_under_more_options(self):
        """Pinned by two other files as well; asserted here because this is the
        session that moved the element."""
        head = _index().partition('<details class="more" id="more-options">')[0]
        assert 'name="bearing_model"' not in head

    def test_the_machine_form_offers_the_same_closed_list(self):
        """One list, two forms. A machine created on the machines page must not
        be able to hold a bearing the upload form would refuse."""
        js = (_STATIC / "app.js").read_text()
        block = js.partition("const EDIT_FIELDS = [")[2].partition("\n];")[0]
        row = block.partition("{ name: 'bearing_model'")[2].partition("},")[0]
        assert "type: 'select'" in row, row
        assert "options: BEARING_OPTIONS" in row, row
        # Built FROM the catalogue and filtered, so the two lists cannot be
        # spelled separately and drift. The filter is the B5 change; the
        # derivation is the claim this line has always protected.
        assert "BEARING_MODELS.filter(" in js and ".map((k) => [k, k])" in js


class TestTheFormSaysWhatTheScreenActuallyDoes:

    def test_the_help_text_does_not_promise_a_screen_for_any_bearing(self):
        html = _index()
        block = html.partition('id="brg"')[2].partition("</div></div>")[0]
        assert "only runs for bearings" in block, block
        assert "Not listed" in block

    def test_the_machine_form_states_the_caveat_that_comes_with_the_screen(self):
        """Roster row 17 (`report/generate.py`): turning the bearing screen on
        brings a caveat WITH it -- matching reads the radial channels, so an
        axial thrust-loaded fault is still not assessed. An unlock that
        promises four frequencies and omits that over-promises exactly where
        the roster is careful."""
        js = (_STATIC / "app.js").read_text()
        row = js.partition("{ name: 'bearing_model'")[2].partition("},")[0]
        assert "radial" in row, row
        assert "axial" in row, row
