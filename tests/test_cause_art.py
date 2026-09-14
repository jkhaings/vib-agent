"""The cause illustrations — the register, the caption, and the no-fallback rule.

Session V2-WIRE (WIRING.md slice W6c). The prototype's H1 check proved these
properties at build time, outside the suite. This is its port.

The rule that matters most is negative: **a cause with no mapped asset renders
no image element at all.** There is no fallback drawing, because a picture of
the wrong mechanism beside the right words is worse than no picture — a reader
takes it as evidence, which is exactly what the section's own heading
("hypotheses for analyst confirmation") exists to prevent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vib_agent.knowledge import lookup_causes_for
from vib_agent.report import charts as charts_mod
from vib_agent.report.cause_art import ASSETS, DISCLAIMER, INDEX, asset_for, illustration, mapping

_REPO = Path(__file__).resolve().parents[1]
_GENERATOR = _REPO / "design" / "report_v2_proto" / "make_cause_art.py"

#: The operator's ship list for this session. The five drawings NOT on it exist
#: in the prototype and are deliberately not packaged — two are section art with
#: no slot, and three map to no `cause_id` at all because `knowledge/loader.py`
#: restricts the cause section to bearing-damage families.
NOT_SHIPPED = ("bearing-anatomy", "staging-progression", "misalignment-parallel",
               "imbalance", "looseness")


class TestRegister:
    def test_every_row_points_at_a_file_that_exists(self):
        """A dangling row is a silently missing figure. The whole point of a
        register is that it cannot drift from the directory it describes."""
        assert mapping(), "the register parsed to nothing — INDEX.md is missing or its rows moved"
        for cause_id, name in mapping().items():
            assert (ASSETS / name).exists(), f"{cause_id} -> {name} is not in {ASSETS}"

    def test_every_shipped_asset_is_registered(self):
        """The other direction: an asset nobody can reach is dead weight, and an
        UNregistered asset in the directory is the beginning of a second,
        informal mapping."""
        registered = set(mapping().values())
        on_disk = {p.name for p in ASSETS.glob("*.svg")}
        assert on_disk == registered, (
            f"unregistered assets: {sorted(on_disk - registered)}; "
            f"registered but absent: {sorted(registered - on_disk)}"
        )

    def test_no_asset_is_mapped_to_two_causes(self):
        names = list(mapping().values())
        assert len(names) == len(set(names)), "an asset is mapped to more than one cause"

    def test_the_ship_list_is_exactly_what_was_approved(self):
        """Fourteen cause-mapped schematics, and none of the five that were
        drawn but deliberately not shipped."""
        assert len(mapping()) == 14
        for stem in NOT_SHIPPED:
            assert not (ASSETS / f"{stem}.svg").exists(), (
                f"{stem}.svg is not on the ship list but is in the package")

    def test_the_register_covers_the_product_s_own_cause_list(self):
        """The register cannot name a `cause_id` the product does not have, and
        must not miss one it does."""
        book_ids = set()
        for family in ("bearing_outer_race", "bearing_inner_race",
                       "bearing_ball_spin", "bearing_cage"):
            for cause in lookup_causes_for([family]) or []:
                book_ids.add(cause.cause_id)
        assert book_ids, "the cause library returned nothing — the fixture is stale"
        assert set(mapping()) == book_ids, (
            f"register names causes the library does not have: {sorted(set(mapping()) - book_ids)}; "
            f"library causes with no illustration: {sorted(book_ids - set(mapping()))}"
        )


class TestNoFallbackDrawing:
    def test_an_unmapped_cause_renders_no_image_element_at_all(self):
        assert asset_for("a_cause_that_does_not_exist") is None
        assert asset_for(None) is None
        assert illustration({"cause_id": "a_cause_that_does_not_exist"}) == ""
        assert illustration({"cause_id": None}) == ""
        assert illustration({}) == ""

    def test_a_dangling_register_row_raises_rather_than_going_quiet(self, tmp_path, monkeypatch):
        """A missing file must be loud. Degrading to "no image" here would make
        a packaging mistake indistinguishable from a deliberate absence."""
        import vib_agent.report.cause_art as art

        monkeypatch.setattr(art, "ASSETS", tmp_path)
        art.mapping.cache_clear()
        art.asset_for.cache_clear()
        monkeypatch.setattr(art, "INDEX", tmp_path / "INDEX.md")
        (tmp_path / "INDEX.md").write_text("| `outer_race_moisture_corrosion` | `gone.svg` |\n")
        with pytest.raises(FileNotFoundError):
            art.asset_for("outer_race_moisture_corrosion")
        art.mapping.cache_clear()
        art.asset_for.cache_clear()


class TestCaption:
    def test_the_disclaimer_is_on_every_illustration(self):
        for cause_id in mapping():
            html = illustration({"cause_id": cause_id})
            assert html, cause_id
            assert DISCLAIMER in html, f"{cause_id} rendered without its caption"

    @pytest.mark.skipif(not _GENERATOR.exists(), reason="the prototype generator is not present")
    def test_the_renderer_and_the_generator_quote_the_same_words(self):
        """Three modules have to agree on this string — the generator that draws
        the assets, the renderer that captions them, and the register that
        documents it. Drift breaks the build rather than shipping two different
        disclaimers."""
        source = _GENERATOR.read_text()
        match = re.search(r'^DISCLAIMER = "(.+)"$', source, re.M)
        assert match, "the generator no longer declares DISCLAIMER where this test looks"
        assert match.group(1) == DISCLAIMER
        assert DISCLAIMER in INDEX.read_text()


@pytest.mark.skipif(not charts_mod.matplotlib_available(), reason="matplotlib not installed")
class TestOnTheRenderedPage:
    """H1, computed against a real report rather than against the loader."""

    def _bearing_report(self, tmp_path, iso_table, rules):
        from vib_agent.config import load_thresholds
        from vib_agent.pipeline import run_analysis
        from vib_agent.report.generate import FAULT_LABELS, render_html
        from vib_agent.synth.generator import make_case

        thresholds = load_thresholds("route")
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        charts = charts_mod.render_charts(result, case.machine, tmp_path, case=case,
                                          thresholds=thresholds, fault_labels=FAULT_LABELS)
        return result, render_html(result, case.machine, charts=charts, case=case,
                                   thresholds=thresholds, profile="route")

    def test_one_illustration_and_one_caption_per_cause(self, tmp_path, iso_table, rules):
        result, html = self._bearing_report(tmp_path, iso_table, rules)
        drawn = re.findall(r'data-cause-art="([a-z0-9_]+)"', html)
        assert drawn, "the bearing report rendered no illustrations at all"
        assert len(drawn) == len(set(drawn)), "an illustration was rendered twice"
        assert html.count("<div class=\"cause\">") == len(drawn), (
            "a cause on this page has no illustration, or an illustration has no cause")
        assert html.count(DISCLAIMER) == len(drawn), (
            "the caption count does not equal the illustration count")

    def test_every_inline_svg_id_is_unique_across_the_whole_page(self, tmp_path, iso_table, rules):
        """Eleven assets inline eleven SVGs into ONE document, and every asset
        ships the same `id="t"`/`id="d"` pair its `aria-labelledby` points at.
        Duplicated, all eleven resolve to the FIRST asset on the page — so every
        illustration announces the first one's title to a screen reader, and
        `url(#…)` clips resolve to the wrong rectangle. Invisible on screen.
        """
        _, html = self._bearing_report(tmp_path, iso_table, rules)
        ids = re.findall(r'\bid="([A-Za-z0-9_-]+)"', html)
        duplicates = {i for i in ids if ids.count(i) > 1}
        assert not duplicates, f"duplicate ids across the page: {sorted(duplicates)}"

    def test_a_report_with_no_bearing_commit_renders_no_illustrations(
        self, tmp_path, iso_table, rules
    ):
        """No cause section, so no cause art — and no orphan caption either."""
        from vib_agent.config import load_thresholds
        from vib_agent.pipeline import run_analysis
        from vib_agent.report.generate import FAULT_LABELS, render_html
        from vib_agent.synth.generator import make_case

        thresholds = load_thresholds("route")
        case = make_case("imbalance", iso_table=iso_table, thresholds=thresholds, seed=2)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        charts = charts_mod.render_charts(result, case.machine, tmp_path, case=case,
                                          thresholds=thresholds, fault_labels=FAULT_LABELS)
        html = render_html(result, case.machine, charts=charts, case=case,
                           thresholds=thresholds, profile="route")
        assert "data-cause-art" not in html
        assert DISCLAIMER not in html
