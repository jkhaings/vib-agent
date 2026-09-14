"""Session R2-BUILD — lookup_causes(), the one function both the report and the
agent tool read the cause library through.

It is deterministic and pure by contract: same arguments in, same list out, in
the same order, with no I/O beyond the cached book. That matters because the
deterministic report and the drafted report call it separately and their output
is required to be byte-identical — if the ordering were not total, the two paths
could disagree without either being wrong.
"""

from __future__ import annotations

import pytest

from vib_agent.knowledge import (
    BEARING_FAULT_FAMILIES,
    load_causes,
    lookup_causes,
    lookup_causes_for,
)
from vib_agent.knowledge.loader import CauseBook


@pytest.fixture(scope="module")
def book() -> CauseBook:
    return load_causes()


class TestTheFilter:
    def test_a_family_gets_only_entries_keyed_to_it(self, book):
        for entry in lookup_causes("bearing_cage"):
            assert "bearing_cage" in entry.fault_family

    def test_every_bearing_family_returns_something(self):
        for family in BEARING_FAULT_FAMILIES:
            assert lookup_causes(family), f"{family} has no approved causes at all"

    def test_a_non_bearing_family_returns_nothing(self):
        """batch1 is a bearing-cause batch. An imbalance or misalignment call
        has no approved cause knowledge, so the report renders no section — it
        must never fall back to bearing causes."""
        for family in ("imbalance", "angular_misalignment", "mechanical_looseness"):
            assert lookup_causes(family) == []

    def test_an_unknown_family_returns_nothing_rather_than_raising(self):
        assert lookup_causes("bearing_race_wobble") == []

    def test_the_cage_family_is_the_narrow_one(self):
        """One entry, and it is the debris-in-the-cage one. If this grows, the
        b03 queue's proposed cage-wear-from-inadequate-lubrication sibling was
        added — which is an operator decision, not a silent one."""
        assert [e.cause_id for e in lookup_causes("bearing_cage")] == [
            "rolling_element_cage_wear_grooving"
        ]

    def test_the_outer_and_inner_lists_overlap_but_are_not_equal(self):
        outer = {e.cause_id for e in lookup_causes("bearing_outer_race")}
        inner = {e.cause_id for e in lookup_causes("bearing_inner_race")}
        assert outer & inner  # the component-agnostic entries
        assert outer - inner == {"outer_race_housing_fit_creep", "outer_race_oval_clamping_overload"}
        assert inner - outer == {"inner_race_creep_on_shaft_seat", "inner_race_excessive_axial_load"}


class TestTheStageFilter:
    def test_a_stage_independent_entry_matches_every_stage(self):
        """`stage: any` is stage-independent knowledge, which is all of batch1 —
        so a stage-specific request must not silently return nothing."""
        for stage in ("stage_3_early", "stage_3_advanced", "stage_4_suspected", "not_determinable"):
            assert lookup_causes("bearing_outer_race", stage)

    def test_asking_for_any_returns_every_stage(self, book):
        assert len(lookup_causes("bearing_outer_race", "any")) == len(
            lookup_causes("bearing_outer_race", "stage_4_suspected")
        )

    def test_a_stage_specific_entry_is_filtered_out_of_other_stages(self, book):
        """No entry in the shipped book is stage-specific, so this is proved
        against a modified copy rather than left untested until one is."""
        pinned = book.model_copy(deep=True)
        pinned.entries[0].stage = "stage_4_suspected"
        family = pinned.entries[0].fault_family[0]
        ids = {e.cause_id for e in lookup_causes(family, "stage_3_early", book=pinned)}
        assert pinned.entries[0].cause_id not in ids
        ids4 = {e.cause_id for e in lookup_causes(family, "stage_4_suspected", book=pinned)}
        assert pinned.entries[0].cause_id in ids4
        # ...and "any" from the caller still sees it, because the caller is
        # asking for everything rather than for a stage.
        assert pinned.entries[0].cause_id in {
            e.cause_id for e in lookup_causes(family, "any", book=pinned)
        }


class TestTheOrder:
    def test_the_consider_last_entry_sorts_last_for_every_family_it_is_in(self):
        """Its own `consider` reads "LAST. Weigh every other cause in this batch
        before this one" — it is the only entry that concludes nothing was done
        wrong, so offering it first would end the investigation the other
        thirteen exist to run."""
        for family in ("bearing_outer_race", "bearing_inner_race", "bearing_ball_spin"):
            got = lookup_causes(family)
            assert got[-1].cause_id == "rolling_element_subsurface_fatigue_normal_life"

    def test_triangulated_comes_before_single_source(self):
        for family in BEARING_FAULT_FAMILIES:
            ranks = [e.basis_rank() for e in lookup_causes(family) if e.consider_rank() == 0]
            assert ranks == sorted(ranks)

    def test_an_unrecognised_consider_directive_is_neutral_not_guessed_at(self, book):
        """"RING IDENTITY", "COLOUR IS NOT A DISCRIMINATOR HERE" and "TWO
        COLLISIONS" all open a `consider` field, and none of them is an ordering
        instruction. The loader reads exactly FIRST and LAST and treats
        everything else as rank 0 rather than inventing a meaning."""
        by_id = {e.cause_id: e for e in book.entries}
        for cause_id in (
            "outer_race_oval_clamping_overload",
            "outer_race_moisture_corrosion",
            "inner_race_misalignment_edge_loading",
        ):
            assert by_id[cause_id].consider  # it really does carry one
            assert by_id[cause_id].consider_rank() == 0

    def test_a_first_directive_sorts_first(self, book):
        pinned = book.model_copy(deep=True)
        target = next(e for e in pinned.entries if e.cause_id == "inner_race_creep_on_shaft_seat")
        target.consider = "FIRST. Weigh this before anything else."
        assert lookup_causes("bearing_inner_race", book=pinned)[0].cause_id == target.cause_id

    def test_the_final_tie_break_is_authored_order(self, book):
        """Without a total order the deterministic and drafted reports could
        legitimately disagree. Authored order groups by ring and is meaningful,
        so it is the tie-break rather than an id sort."""
        authored = [e.cause_id for e in book.entries]
        tier = [
            e.cause_id
            for e in lookup_causes("bearing_outer_race")
            if e.consider_rank() == 0 and e.basis == "triangulated"
        ]
        assert tier == [cid for cid in authored if cid in set(tier)]


class TestPurity:
    def test_repeated_calls_return_an_identical_ordering(self):
        first = [e.cause_id for e in lookup_causes("bearing_outer_race")]
        for _ in range(5):
            assert [e.cause_id for e in lookup_causes("bearing_outer_race")] == first

    def test_an_explicit_book_is_used_instead_of_the_cached_one(self, book):
        empty = book.model_copy(update={"entries": []})
        assert lookup_causes("bearing_outer_race", book=empty) == []
        assert lookup_causes("bearing_outer_race")  # the real book is untouched

    def test_it_does_not_read_the_reference_library(self, monkeypatch):
        """CLAUDE.md's References doctrine: the citation register is not RAG and
        nothing in the product reads it. A report must not fail to render, or
        change what it says, because a bibliography file moved."""
        import pathlib

        import vib_agent.knowledge.loader as loader

        original = pathlib.Path.read_text
        opened: list[str] = []

        def _watching(self, *a, **kw):  # noqa: ANN001, ANN002, ANN003
            opened.append(str(self))
            return original(self, *a, **kw)

        monkeypatch.setattr(pathlib.Path, "read_text", _watching)
        loader.load_causes.cache_clear()
        try:
            assert lookup_causes("bearing_outer_race")
        finally:
            loader.load_causes.cache_clear()
        assert opened, "the loader read nothing at all — the probe is not wired"
        assert any("causes.yaml" in path for path in opened), opened
        assert not any("references" in path for path in opened), opened
        assert not any("INDEX.md" in path for path in opened), opened


class TestTheMultiFamilyUnion:
    def test_it_de_duplicates_across_committed_families(self):
        merged = lookup_causes_for(["bearing_outer_race", "bearing_inner_race"])
        ids = [e.cause_id for e in merged]
        assert len(ids) == len(set(ids))
        assert set(ids) == {e.cause_id for e in lookup_causes("bearing_outer_race")} | {
            e.cause_id for e in lookup_causes("bearing_inner_race")
        }

    def test_the_consider_last_entry_is_still_last_after_the_union(self):
        """The reason the union re-sorts instead of concatenating: LAST only
        holds within one family's list, so a plain append would drop the
        subsurface-fatigue entry into the middle."""
        merged = lookup_causes_for(["bearing_outer_race", "bearing_inner_race"])
        assert merged[-1].cause_id == "rolling_element_subsurface_fatigue_normal_life"

    def test_a_single_family_union_equals_the_plain_lookup(self):
        assert [e.cause_id for e in lookup_causes_for(["bearing_cage"])] == [
            e.cause_id for e in lookup_causes("bearing_cage")
        ]

    def test_no_families_returns_nothing(self):
        assert lookup_causes_for([]) == []
