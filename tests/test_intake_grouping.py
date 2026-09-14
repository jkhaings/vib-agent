"""INTAKE-HARDEN, fix 2 — a declared thousands separator must actually group.

The defect this closes is the most dangerous kind the intake path can have: not
a crash, not a refusal, but a Case full of numbers that are not what the file
says, in the right shape, that the verification gate accepts.

A German export writes an amplitude as `0,78`. Read with thousands="comma" —
which is exactly what a model shown `1800,00` in the frequency column would
plausibly conclude — the comma is deleted and 0.78 becomes 78. Every bin scales
by 100, the spectrum keeps its shape, the 1x line stays where it was, and the
overall velocity comes out at 258,535 mm/s. The gate's `max_amplitude` was 1e6,
so it said nothing. That is an ISO Zone D severity claim on a healthy machine.

The rule is arithmetic, not calibration: a thousands separator groups digits in
threes. `1,234,567` does. `0,78` does not. `1,23456` does not. So the separator
in those files is not a thousands separator, and the recipe is wrong.
"""

from __future__ import annotations

import pytest

from tests.intake_adversarial_corpus import BY_NAME, FIXTURE_DIR, RPM
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.adapters.uploads.recipe import (
    RecipeExecutionError,
    _groups_in_threes,
    _to_float,
)
from vib_agent.adapters.uploads.recipe import execute_recipe
from vib_agent.adapters.uploads.verify import failed, verify_case
from vib_agent.config import load_config


@pytest.fixture(scope="module")
def bearings_cfg():
    return load_config("bearings")


@pytest.fixture(scope="module")
def tolerances():
    return load_config("webapp")["inference"]


def _form():
    return UploadForm(machine_alias="Grouping", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor",
                      bearing_model="6206")


def _run(name, recipe, bearings_cfg):
    return execute_recipe(FIXTURE_DIR / name, recipe, _form(), bearings_cfg=bearings_cfg)


class TestGroupingRule:
    @pytest.mark.parametrize("text,sep,ok", [
        ("1,234", ",", True),
        ("1,234,567", ",", True),
        ("999,999", ",", True),
        ("-1,234", ",", True),
        ("1234", ",", True),          # no separator at all — nothing to judge
        ("0,78", ",", False),         # two digits: a DECIMAL comma
        ("1,23456", ",", False),      # five digits
        ("1,2", ",", False),
        ("1234,567", ",", False),     # head too long
        ("1.800", ".", True),
        ("1.23", ".", False),
        ("1 800", " ", True),
    ])
    def test_threes_or_nothing(self, text, sep, ok):
        assert _groups_in_threes(text, sep) is ok

    def test_the_hundredfold_error_now_raises(self):
        """0,78 read as a thousands group is 78. This is the whole bug, in one
        assertion."""
        assert _to_float("0,78", "comma") == 0.78
        with pytest.raises(ValueError):
            _to_float("0,78", "dot", "comma")

    def test_the_thousandfold_error_the_other_way_also_raises(self):
        """With decimal_mark='comma' the dot is a grouping mark by definition,
        so a US file read as European turns 1.23 into 123."""
        assert _to_float("1.23", "dot") == 1.23
        with pytest.raises(ValueError):
            _to_float("1.23", "comma")

    def test_legitimate_grouping_still_reads(self):
        assert _to_float("1,800.00", "dot", "comma") == 1800.0
        assert _to_float("1.800,00", "comma") == 1800.0
        assert _to_float("1 800,00", "comma", "space") == 1800.0
        assert _to_float("1’800.00", "dot", "apostrophe") == 1800.0
        assert _to_float("1'800.00", "dot", "apostrophe") == 1800.0

    def test_scientific_notation_is_unharmed(self):
        assert _to_float("3.000000E+01", "dot") == 30.0
        assert _to_float("3,000000E+01", "comma") == 30.0
        assert _to_float("1.5e-3", "dot") == 0.0015
        # ...but a US exponent read as European is refused, not silently rescaled
        with pytest.raises(ValueError):
            _to_float("3.000000E+01", "comma")


class TestTheDangerousFilesRefuse:
    def test_a_decimal_comma_file_read_as_thousands_refuses(self, bearings_cfg):
        wrong = BY_NAME["decimal_comma_cpm.txt"].recipe.model_copy(
            update={"decimal_mark": "dot", "thousands_separator": "comma"})
        with pytest.raises(RecipeExecutionError) as excinfo:
            _run("decimal_comma_cpm.txt", wrong, bearings_cfg)
        assert "thousands-separator" in str(excinfo.value)

    def test_the_refusal_names_the_convention_not_just_the_row_count(self, bearings_cfg):
        """An analyst who is told 'too few numeric rows' re-exports blindly. One
        told the decimal convention was wrong knows what to change."""
        wrong = BY_NAME["scientific.txt"].recipe.model_copy(update={"decimal_mark": "comma"})
        with pytest.raises(RecipeExecutionError) as excinfo:
            _run("scientific.txt", wrong, bearings_cfg)
        message = str(excinfo.value)
        assert "decimal" in message and "convention" in message
        assert "0.0000" not in message      # and it quotes nothing from the file

    def test_a_us_file_read_as_european_refuses(self, bearings_cfg):
        wrong = BY_NAME["crlf.txt"].recipe.model_copy(update={"decimal_mark": "comma"})
        with pytest.raises(RecipeExecutionError):
            _run("crlf.txt", wrong, bearings_cfg)


class TestTheCorrectRecipesAreUntouched:
    @pytest.mark.parametrize("name", [
        "decimal_comma_cpm.txt", "thousands_eu_dot.txt", "thousands_space.txt",
        "scientific.txt", "scientific_eu.asc", "crlf.txt", "fixed_width.txt",
    ])
    def test_every_numeric_convention_in_the_corpus_still_reads(self, name, bearings_cfg,
                                                                tolerances):
        case, _, _ = _run(name, BY_NAME[name].recipe, bearings_cfg)
        assert failed(verify_case(case, rpm=RPM, tolerances=tolerances)) == []
        spectrum = case.spectra["y"]
        low = [(f, a) for f, a in zip(spectrum.freq_hz, spectrum.amplitude) if 0 < f <= 45]
        assert abs(max(low, key=lambda p: p[1])[0] - RPM / 60.0) < 1.0

    def test_the_session_g2_thousands_fixture_still_reads(self, bearings_cfg):
        """The US-style fixture this rule could most plausibly have broken."""
        from tests.inference_corpus import ALL_CORPUS, write_all

        import tempfile
        from pathlib import Path

        item = next(i for i in ALL_CORPUS if i.name == "thousands.txt")
        with tempfile.TemporaryDirectory() as tmp:
            write_all(Path(tmp))
            case, _, _ = execute_recipe(Path(tmp) / item.name, item.recipe, _form(),
                                        bearings_cfg=bearings_cfg)
        spectrum = case.spectra["y"]
        low = [(f, a) for f, a in zip(spectrum.freq_hz, spectrum.amplitude) if 0 < f <= 45]
        assert abs(max(low, key=lambda p: p[1])[0] - 30.0) < 1.0
