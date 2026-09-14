"""Session INTAKE-2 — the browser and the server agree about the ISO group.

`isoGroupFromKw` in `app.js` and `group_from_rated_kw` in
`adapters/uploads/common.py` answer the same question, and they have to give the
same answer: the page shows one and the analysis uses the other, and two answers
to *"which ISO 20816-3 row is this machine on"* is two different severity
thresholds on one reading. GEOM-1 built the same twin diff for the bearing label
(`tests/test_geom1_geometry.py`) for the same reason and this follows it: slice
the function out of the SHIPPED file, run it in node, diff against Python.

It also diffs the THIRD copy of `MEMORY_FIELDS` — the one in
`tests/js/machine_store_tests.js`. `tests/test_db1_schema.py` already diffs
`app.js` against `db/models.py::CARD_FIELDS`, but nothing watched the node
suite's own literal, so that copy could fall behind and its `deepStrictEqual`
would keep passing against a stale list.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from vib_agent.adapters.uploads.common import (
    ISO_GROUP_1_MIN_KW,
    ISO_GROUP_2_MIN_KW,
    group_from_rated_kw,
)

_ROOT = Path(__file__).resolve().parents[1]
_APP_JS = _ROOT / "src" / "vib_agent" / "webapp" / "static" / "app.js"
_STORE_TESTS = _ROOT / "tests" / "js" / "machine_store_tests.js"


def _memory_fields(js: str, needle: str) -> tuple[str, ...]:
    block = js.partition(needle)[2].partition("]")[0]
    assert block, f"{needle} is gone"
    block = re.sub(r"//[^\n]*", "", block)
    return tuple(re.findall(r"'([^']+)'", block))


class TestTheGroupDerivationAgrees:

    #: Chosen for the shapes that make two implementations of one threshold
    #: disagree: both sides of both boundaries, the boundaries themselves, a
    #: blank, a zero, a negative, and a non-number.
    CASES = [
        0.5, 7.5, 14.9,
        ISO_GROUP_2_MIN_KW, ISO_GROUP_2_MIN_KW + 0.1,
        90.0, 299.9,
        ISO_GROUP_1_MIN_KW, ISO_GROUP_1_MIN_KW + 0.01, ISO_GROUP_1_MIN_KW + 0.1,
        5000.0,
    ]

    @pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
    def test_the_two_implementations_give_the_same_group(self, tmp_path):
        js = _APP_JS.read_text()
        consts = re.search(r"const ISO_GROUP_2_MIN_KW[\s\S]*?const ISO_GROUP_1_MIN_KW[^\n]*\n", js)
        fn = re.search(r"function isoGroupFromKw\([\s\S]*?\n}\n", js)
        assert consts and fn, "the ISO group helper is gone from app.js"
        script = tmp_path / "iso.js"
        script.write_text(
            consts.group(0) + fn.group(0)
            + "const cases = JSON.parse(process.argv[2]);\n"
            + "console.log(JSON.stringify(cases.map((c) => isoGroupFromKw(c))));\n"
        )
        out = subprocess.run(
            [shutil.which("node"), str(script), json.dumps(self.CASES)],
            capture_output=True, text=True, timeout=60,
        )
        assert out.returncode == 0, f"{out.stdout}\n{out.stderr}"
        from_js = json.loads(out.stdout)
        from_py = [group_from_rated_kw(kw)[0] for kw in self.CASES]
        assert from_js == from_py, list(zip(self.CASES, from_js, from_py))

    @pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
    def test_the_boundary_constants_are_the_same_numbers(self):
        """The functions could agree on the cases above and still hold
        different constants, which would only diverge on a value nobody
        tested."""
        js = _APP_JS.read_text()
        for name, value in (("ISO_GROUP_2_MIN_KW", ISO_GROUP_2_MIN_KW),
                            ("ISO_GROUP_1_MIN_KW", ISO_GROUP_1_MIN_KW)):
            found = re.search(rf"const {name} = ([0-9.]+);", js)
            assert found, f"{name} is gone from app.js"
            assert float(found.group(1)) == value, (
                f"{name} is {found.group(1)} in app.js and {value} in Python"
            )

    @pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
    def test_nothing_usable_is_not_a_group(self, tmp_path):
        """'' means "we were told nothing" and must never be read as a group --
        it is what leaves the analyst's own select alone."""
        js = _APP_JS.read_text()
        consts = re.search(r"const ISO_GROUP_2_MIN_KW[\s\S]*?const ISO_GROUP_1_MIN_KW[^\n]*\n", js)
        fn = re.search(r"function isoGroupFromKw\([\s\S]*?\n}\n", js)
        script = tmp_path / "iso_blank.js"
        script.write_text(
            consts.group(0) + fn.group(0)
            + "const cases = ['', '   ', '0', '-5', 'abc', null, undefined];\n"
            + "console.log(JSON.stringify(cases.map((c) => isoGroupFromKw(c))));\n"
        )
        out = subprocess.run([shutil.which("node"), str(script)],
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, f"{out.stdout}\n{out.stderr}"
        assert json.loads(out.stdout) == [""] * 7


class TestTheThirdCopyOfTheCardCannotDrift:
    """`app.js::MEMORY_FIELDS` vs the node suite's own literal.

    Two copies were already diffed (app.js ↔ db/models.py). This is the third,
    and before this test it was the one nobody watched.
    """

    def test_the_node_suites_card_list_is_the_shipped_one(self):
        shipped = _memory_fields(_APP_JS.read_text(), "const MEMORY_FIELDS = [")
        in_node = _memory_fields(_STORE_TESTS.read_text(), "const CARD_FIELDS = [")
        assert in_node == shipped, (
            "tests/js/machine_store_tests.js holds a stale copy of MEMORY_FIELDS; "
            "its deepStrictEqual would pass against the wrong list"
        )

    def test_the_new_fields_are_actually_in_it(self):
        """Non-vacuity: the diff above passes trivially if both lists are
        empty or if the partition found nothing."""
        shipped = _memory_fields(_APP_JS.read_text(), "const MEMORY_FIELDS = [")
        assert len(shipped) > 20
        for name in ("machine_type", "rated_kw", "driven_rpm"):
            assert name in shipped, f"{name} is not remembered with the machine"
