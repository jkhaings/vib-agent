"""Session GEOM-A — the readers: what the report does with declared geometry.

Two surfaces, both required by the master-green rule in ROADMAP §S6 ("every
captured field has a same-session reader"):

  * **Analysis parameters** prints every field that was supplied — and prints
    the belt fundamental with the arithmetic it came from, so the one derived
    number in the document can be re-done by hand.
  * **The coverage roster** keeps saying "not assessed" for the families whose
    detectors do not exist yet — a typed-in number does not soften a reason
    class — but stops implying the analyst never supplied the geometry. Those
    two claims are different, and only one of them is true once the field is
    filled in.

Absence is deliberately NOT printed as a parameters row: the roster is the
channel that names what was not supplied, family by family, keyed to
outputs/FAULT_COVERAGE_2026-08-31.md §3. Saying it in both places is one more
place for the two to drift.
"""

from __future__ import annotations

import re

import pytest

from vib_agent.adapters.uploads.common import belt_frequency_hz, belt_length_mm
from vib_agent.config import load_thresholds
from vib_agent.models import BeltSpec, MachineMeta
from vib_agent.pipeline import run_analysis
from vib_agent.report.charts import analysis_parameters, machine_geometry_rows
from vib_agent.report.generate import coverage_context, render_html, render_markdown
from vib_agent.synth.generator import make_case

D1, D2, CENTRES, RPM = 120.0, 200.0, 400.0, 1800.0


@pytest.fixture
def thresholds() -> dict:
    return load_thresholds("route")


def _belt() -> BeltSpec:
    return BeltSpec(
        freq_hz=belt_frequency_hz(D1, D2, CENTRES, RPM),
        drive_pulley_mm=D1, driven_pulley_mm=D2, center_distance_mm=CENTRES,
        belt_length_mm=belt_length_mm(D1, D2, CENTRES),
    )


def _machine(**over) -> MachineMeta:
    base = dict(mac="GEOM-M", name="M", active=True, iso_group="2", iso_support="rigid", machine_type="motor")
    base.update(over)
    return MachineMeta(**base)


FULL = dict(coupled=False, coupled_stated=True, blades=6, gear_teeth_driving=23,
            gear_teeth_driven=91, rotor_bars=44, poles=4, line_freq_hz=60.0,
            drive_type="vfd")


# ══════════════════════════════════════════════════════════════════════════
# 1 · Analysis parameters
# ══════════════════════════════════════════════════════════════════════════
class TestParameterRows:
    def test_nothing_declared_adds_no_rows(self):
        assert machine_geometry_rows(_machine()) == []

    def test_every_declared_field_gets_a_row(self, thresholds):
        rows = dict(machine_geometry_rows(_machine(belt=_belt(), **FULL), thresholds=thresholds))
        assert rows["Coupling"] == "uncoupled (declared)"
        assert rows["Blade / vane count"] == "6 (declared)"
        assert rows["Gear tooth counts"] == "driving 23, driven 91 (declared)"
        assert rows["Rotor bars"] == "44 (declared)"
        assert rows["Motor poles"] == "4 (declared)"
        assert rows["Line frequency"] == "60 Hz (declared)"
        assert rows["Drive type"] == "variable-frequency drive (declared)"

    def test_declared_coupled_reads_differently_from_declared_uncoupled(self):
        coupled = dict(machine_geometry_rows(_machine(coupled=True, coupled_stated=True)))
        assert coupled["Coupling"] == "coupled (declared)"

    def test_an_undeclared_coupling_prints_no_row_at_all(self):
        """`coupled` defaults True. A row saying "coupled" on a machine nobody
        asked about would be a claim the analyst never made."""
        assert "Coupling" not in dict(machine_geometry_rows(_machine()))

    def test_belt_row_prints_the_value_and_the_derivation(self, thresholds):
        rows = dict(machine_geometry_rows(_machine(belt=_belt()), thresholds=thresholds))
        assert rows["Belt fundamental"] == f"{belt_frequency_hz(D1, D2, CENTRES, RPM):.2f} Hz"
        derivation = rows["Belt derivation"]
        # the inputs...
        assert "120 mm" in derivation and "200 mm" in derivation and "400 mm" in derivation
        # ...the formula...
        assert "L = 2C + (π/2)(D1+D2) + (D2−D1)²/(4C)" in derivation
        assert "π·D1·N/(60·L)" in derivation
        # ...the computed length, and the caveat that it is theoretical...
        assert f"{belt_length_mm(D1, D2, CENTRES):.1f} mm" in derivation
        assert "theoretical wrap length" in derivation
        # ...and the tolerance it will be matched at.
        assert "±3%" in derivation

    def test_a_belt_frequency_supplied_directly_says_so(self):
        """A Case JSON may carry `freq_hz` alone, as it always could. The report
        must not imply a derivation that never happened."""
        rows = dict(machine_geometry_rows(_machine(belt=BeltSpec(freq_hz=52.0))))
        assert rows["Belt fundamental"] == "52.00 Hz"
        assert rows["Belt derivation"] == "supplied directly — no pulley geometry recorded"

    def test_rows_reach_the_analysis_parameters_table(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        case.machine = _machine(bearing=case.machine.bearing, belt=_belt(), **FULL)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        params = dict(analysis_parameters(result, case=case, thresholds=thresholds,
                                          profile="route"))
        assert params["Motor poles"] == "4 (declared)"
        assert params["Coupling"] == "uncoupled (declared)"

    def test_the_table_is_unchanged_when_no_geometry_is_declared(
        self, iso_table, thresholds, rules
    ):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        names = [name for name, _ in analysis_parameters(result, case=case,
                                                         thresholds=thresholds, profile="route")]
        for geometry_row in ("Coupling", "Blade / vane count", "Belt fundamental",
                             "Gear tooth counts", "Rotor bars", "Motor poles",
                             "Line frequency", "Drive type"):
            assert geometry_row not in names


# ══════════════════════════════════════════════════════════════════════════
# 2 · The coverage roster
# ══════════════════════════════════════════════════════════════════════════
GEOMETRY_FAMILIES = {
    "Gearmesh wear / gear sidebands / hunting tooth": dict(gear_teeth_driving=23),
    "Rotor bar faults": dict(rotor_bars=44),
    "Stator faults (2× line frequency)": dict(line_freq_hz=60.0),
    "VFD 2× line-frequency faults": dict(line_freq_hz=60.0),
    "VFD carrier-frequency artifacts": dict(drive_type="vfd"),
}

ON_FILE = "Geometry on file — awaiting a detector:"


def _row(rows, family) -> str:
    return next(r["line"] for r in rows if r["family"] == family)


class TestGeometryOnFile:
    @pytest.fixture
    def analysed(self, iso_table, thresholds, rules):
        case = make_case("healthy", iso_table=iso_table, thresholds=thresholds, seed=1)
        return case, run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)

    @pytest.mark.parametrize("family,geometry", sorted(GEOMETRY_FAMILIES.items()))
    def test_without_the_geometry_no_note_appears(self, family, geometry, analysed):
        _case, result = analysed
        rows = coverage_context(result, _machine())["rows"]
        assert ON_FILE not in _row(rows, family)

    @pytest.mark.parametrize("family,geometry", sorted(GEOMETRY_FAMILIES.items()))
    def test_with_the_geometry_the_row_says_so_and_stays_not_assessed(
        self, family, geometry, analysed
    ):
        _case, result = analysed
        line = _row(coverage_context(result, _machine(**geometry))["rows"], family)
        assert ON_FILE in line, line
        # The reason class does not soften: no detector still means not assessed.
        assert line.startswith(f"**{family}** — not assessed — no detector for this family.")

    def test_the_rotor_bar_row_names_only_what_was_supplied(self, analysed):
        _case, result = analysed
        family = "Rotor bar faults"
        one = _row(coverage_context(result, _machine(poles=4))["rows"], family)
        assert "the pole count you supplied is recorded" in one
        assert "rotor-bar count" not in one

        three = _row(coverage_context(
            result, _machine(poles=4, rotor_bars=44, line_freq_hz=60.0))["rows"], family)
        assert "the rotor-bar count, the pole count and the line frequency you supplied are" in three

    def test_the_note_carries_no_numbers(self, analysed):
        """Values live in Analysis parameters. A second copy in the roster is a
        second place for them to drift out of step."""
        _case, result = analysed
        rows = coverage_context(result, _machine(gear_teeth_driving=23, gear_teeth_driven=91,
                                                 rotor_bars=44, poles=4, line_freq_hz=60.0,
                                                 drive_type="vfd"))["rows"]
        for family in GEOMETRY_FAMILIES:
            note = _row(rows, family).partition(ON_FILE)[2]
            assert note and not re.search(r"\d", note), note


class TestBothDocumentsCarryIt:
    """Report law #7: the markdown and the v2 HTML are mirrored pairs. The
    roster line and the parameters row are assembled once and rendered twice,
    so this asserts the mirror rather than two independent strings."""

    def test_markdown_and_html_agree(self, iso_table, thresholds, rules):
        case = make_case("healthy", iso_table=iso_table, thresholds=thresholds, seed=1)
        case.machine = _machine(poles=4, drive_type="vfd", belt=_belt(),
                                coupled=False, coupled_stated=True)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        md = render_markdown(result, case.machine, case=case, thresholds=thresholds,
                             profile="route")
        html = render_html(result, case.machine, case=case, thresholds=thresholds,
                           profile="route")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        for token in ("Motor poles", "variable-frequency drive (declared)",
                      "uncoupled (declared)", "Belt fundamental"):
            assert token in md, f"markdown is missing {token!r}"
            assert token in text, f"the HTML page is missing {token!r}"
        assert ON_FILE in md
        assert "Geometry on file — awaiting a detector:" in text
