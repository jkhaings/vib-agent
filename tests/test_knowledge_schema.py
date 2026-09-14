"""Session R2-B0 — validation harness for cause-knowledge candidates.

These are VALIDATION tests, not product tests: nothing in knowledge/ is wired
into pdm_core, agent/, report/ or webapp/. What is being proved is that a
candidate entry cannot carry a citation that does not check out, and cannot key
itself to a fault the product never emits. A plausible mechanism with a bad
citation is the failure mode the whole reference library exists to prevent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from knowledge.schema import (
    BEARING_FAULT_FAMILIES,
    FAULT_FAMILIES,
    STAGES,
    CauseEntry,
    Citation,
    index_doc_numbers,
    load_batch,
)

_REPO = Path(__file__).resolve().parents[1]
BATCH = _REPO / "knowledge" / "candidates" / "batch1_bearing_causes.yaml"


def _entry(**over):
    base = dict(
        cause_id="probe_cause",
        fault_family=["bearing_outer_race"],
        stage="any",
        cause="A probe",
        mechanism="One sentence. Two sentences.",
        discriminating_evidence=[
            dict(
                observation="something",
                stream="vibration",
                how_to_collect="somehow",
                inferred=True,
            )
        ],
        typical_actions=["do the thing"],
        citations=[dict(doc="#01", locator="§5, p.63", note="supports it")],
        basis="single_source",
        review_question="is this right?",
    )
    base.update(over)
    return base


# ── the batch itself ──────────────────────────────────────────────────────


class TestBatchOne:
    def test_the_candidate_file_validates(self):
        batch = load_batch(BATCH)
        assert batch.status == "awaiting_operator_review"
        assert len(batch.entries) >= 10

    def test_it_covers_all_three_bearing_families_asked_for(self):
        families = {f for e in load_batch(BATCH).entries for f in e.fault_family}
        assert {"bearing_outer_race", "bearing_inner_race", "bearing_ball_spin"} <= families

    def test_every_entry_cites_something_on_disk(self):
        """A citation is only worth anything if the document is actually here.

        Observation citations count: under schema v1.2 they are the sourcing
        claim, so a dangling one is the same defect one level down.

        ENVIRONMENT-GATED, NOT WEAKENED. `.gitignore` keeps `references/*` out of
        the repository on purpose — only INDEX.md and GAPS.md are committed (see
        the References section of CLAUDE.md) — so a fresh checkout has zero PDFs
        and this check has nothing to check. It cannot pass there and never
        could: CI run 31757464453 failed on `assert '1' in set()`, an empty
        library, not a bad citation. Where the library IS on disk the assertion
        below runs in full, unchanged. This is the same dataset-absence pattern
        the CWRU/MFPT/wind-turbine/MAFAULDA adapter tests use — the check is
        real, it just requires the data.

        The complementary check that does NOT need the PDFs is
        `Citation._doc_is_a_live_index_entry`, which resolves every `doc` against
        `references/INDEX.md` — and INDEX.md *is* committed. So on a bare
        checkout citations are still validated against the register; what is
        skipped is only the stronger claim that the register matches the files.
        """
        pdfs = list((_REPO / "references").rglob("*.pdf"))
        if not pdfs:
            pytest.skip(
                "references/ holds no PDFs — they are gitignored by policy "
                "(.gitignore: `references/*` with negations for INDEX.md and GAPS.md "
                "only), so a fresh checkout has none. Run this where the library is "
                "on disk; INDEX-resolution of every citation is still enforced by the "
                "schema validator."
            )
        stems = {
            p.name.split("_")[0].lstrip("0") or "0"
            for p in pdfs
            if re.match(r"\d", p.name)
        }
        for entry in load_batch(BATCH).entries:
            cites = list(entry.citations) + [
                c for d in entry.discriminating_evidence for c in (d.citations or [])
            ]
            for c in cites:
                num = c.doc.lstrip("#").lstrip("0")
                assert num in stems, f"{entry.cause_id} cites {c.doc}, which is not a file on disk"

    def test_the_batch_stays_a_review_artifact_after_the_merge(self):
        """SUPERSEDED GUARD, NOT A WEAKENED ONE — read the whole note.

        This test used to assert `not (_REPO / "knowledge" / "causes.yaml")
        .exists()`, under the R2-B0 rule that nothing merged that session. In
        R2-BUILD the operator approved all fourteen entries on record
        (2026-08-13, recorded verbatim in causes.yaml's own `approval` field),
        so that precondition is spent: causes.yaml now exists BY DECISION, and
        an assertion that it does not would be asserting the merge never
        happened.

        What the original test actually protected is not the absence of a file.
        It is that a CANDIDATE BATCH never quietly becomes product knowledge —
        that promotion goes through an operator, and that the review record
        survives the promotion instead of being edited into it. Both halves are
        asserted below, and the second one is new:

          * the batch file stays `awaiting_operator_review` forever. It is the
            record of WHAT WAS REVIEWED, so it must not be back-edited to look
            like the product file;
          * nothing in the product reads the candidates directory. That is the
            real "not merged" property, and it is now checkable because there IS
            a product file to confuse it with.

        The merge itself is asserted in tests/test_causes_yaml.py, which runs
        causes.yaml through this same strict validator and pins the approval
        record. Nothing was relaxed to let the file exist.
        """
        assert load_batch(BATCH).status == "awaiting_operator_review"
        product_source = "\n".join(
            p.read_text() for p in (_REPO / "src/vib_agent").rglob("*.py")
        )
        # Matched on the PATH, not the bare word: "candidate" is ordinary
        # vocabulary in this codebase (rca.differential holds candidates), so a
        # word-level probe fires on correct prose.
        assert not re.search(r"knowledge[/\\]?[\"']?\s*[,/]?\s*[\"']?candidates", product_source), (
            "something under src/vib_agent reads knowledge/candidates/ — the candidate "
            "batches are review artifacts and are never product knowledge; the product "
            "reads knowledge/causes.yaml, which carries an operator approval record."
        )
        assert "batch1_bearing_causes" not in product_source

    def test_judgment_calls_carry_a_review_question(self):
        for entry in load_batch(BATCH).entries:
            if entry.basis != "triangulated":
                assert (entry.review_question or "").strip(), entry.cause_id

    def test_source_disagreements_are_flagged_not_harmonised(self):
        flagged = [e for e in load_batch(BATCH).entries if e.disagreement]
        assert flagged, "no disagreement recorded — sources rarely agree this cleanly"

    def test_related_references_are_reciprocal_and_resolve(self):
        """A one-way cross-reference is a dangling pointer at review time."""
        entries = {e.cause_id: e for e in load_batch(BATCH).entries}
        for cid, entry in entries.items():
            for other in entry.related or []:
                assert other in entries, f"{cid} relates to unknown {other!r}"
                back = entries[other].related or []
                assert cid in back, f"{cid} -> {other} is not reciprocal"

    def test_inferred_observations_are_marked_not_hidden(self):
        """A derived discriminator must not inherit the authority of the
        citation sitting next to it."""
        inferred = [
            d
            for e in load_batch(BATCH).entries
            for d in e.discriminating_evidence
            if d.inferred
        ]
        assert inferred, "nothing marked inferred — check the electrical entry"
        for d in inferred:
            assert d.observation.strip()

    def test_every_multi_family_entry_states_its_justification(self):
        for entry in load_batch(BATCH).entries:
            if len(entry.fault_family) > 1:
                assert "EXTENSION" in (entry.extends_families or ""), entry.cause_id

    def test_the_cage_entry_uses_the_codes_ftf_family_name(self):
        entries = {e.cause_id: e for e in load_batch(BATCH).entries}
        assert entries["rolling_element_cage_wear_grooving"].fault_family == ["bearing_cage"]

    def test_standstill_false_brinelling_is_present_with_its_discriminator(self):
        entries = {e.cause_id: e for e in load_batch(BATCH).entries}
        entry = entries["standstill_vibration_false_brinelling"]
        blob = " ".join(d.observation for d in entry.discriminating_evidence).lower()
        assert "worn away" in blob and "grinding marks" in blob

    def test_mechanisms_are_distilled_not_transcribed(self):
        for entry in load_batch(BATCH).entries:
            assert '"' not in entry.mechanism
            assert len([s for s in re.split(r"[.!?](?:\s|$)", entry.mechanism) if s.strip()]) <= 2


# ── schema v1.2: sourcing lives on the observation ────────────────────────


class TestObservationLevelSourcing:
    """R2-CITECHECK read every cited page and found 15 of 16 vibration/oil/
    thermal observations unsupported: they had been written from general
    engineering knowledge and left beside citations to visual damage atlases,
    where they read as sourced. These tests pin the rule that closed it."""

    def test_every_observation_is_either_cited_or_declared_inferred(self):
        for entry in load_batch(BATCH).entries:
            for d in entry.discriminating_evidence:
                assert bool(d.citations) != d.inferred, (
                    f"{entry.cause_id}: {d.observation[:60]!r} is neither cited nor "
                    "marked inferred, or is claiming both"
                )

    def test_an_observation_with_neither_is_rejected(self):
        with pytest.raises(ValidationError, match=r"citations\[\] or inferred"):
            CauseEntry.model_validate(
                _entry(
                    discriminating_evidence=[
                        dict(observation="x", stream="oil", how_to_collect="y")
                    ]
                )
            )

    def test_an_observation_claiming_both_is_rejected(self):
        """Half-cited is the same laundering one level finer — split it."""
        with pytest.raises(ValidationError, match=r"SPLIT it"):
            CauseEntry.model_validate(
                _entry(
                    discriminating_evidence=[
                        dict(
                            observation="x",
                            stream="oil",
                            how_to_collect="y",
                            inferred=True,
                            citations=[dict(doc="#01", locator="p.63", note="z")],
                        )
                    ]
                )
            )

    def test_an_observation_citation_obeys_every_entry_citation_rule(self):
        """A fabricated doc must not become citable by moving one level down."""
        with pytest.raises(ValidationError, match=r"not a numbered entry"):
            CauseEntry.model_validate(
                _entry(
                    discriminating_evidence=[
                        dict(
                            observation="x",
                            stream="oil",
                            how_to_collect="y",
                            citations=[dict(doc="#99", locator="p.1", note="invented")],
                        )
                    ]
                )
            )

    def test_a_bare_fag_locator_is_rejected_on_an_observation_too(self):
        with pytest.raises(ValidationError, match=r"must state the edition"):
            CauseEntry.model_validate(
                _entry(
                    discriminating_evidence=[
                        dict(
                            observation="x",
                            stream="thermal",
                            how_to_collect="y",
                            citations=[dict(doc="#03", locator="§1.2, p.8", note="z")],
                        )
                    ]
                )
            )

    def test_basis_counts_observation_citations(self):
        """Otherwise an entry could say single_source while resting on three
        documents, just by pushing two of them down a level."""
        with pytest.raises(ValidationError, match=r"single_source"):
            CauseEntry.model_validate(
                _entry(
                    basis="single_source",
                    discriminating_evidence=[
                        dict(
                            observation="x",
                            stream="oil",
                            how_to_collect="y",
                            citations=[dict(doc="#02", locator="p.7", note="z")],
                        )
                    ],
                )
            )

    def test_every_vibration_and_oil_observation_is_accounted_for(self):
        """The stream that exposed the defect. Nothing may go back to being
        implicitly sourced by adjacency."""
        for entry in load_batch(BATCH).entries:
            for d in entry.discriminating_evidence:
                if d.stream in {"vibration", "oil"}:
                    assert d.inferred or d.citations, entry.cause_id


class TestCitecheckFindingsWereApplied:
    """Content pins for the R2-B0.2 truth items — the ones a later edit could
    quietly undo."""

    def _entries(self):
        return {e.cause_id: e for e in load_batch(BATCH).entries}

    def test_reddish_brown_is_gone_from_moisture_corrosion(self):
        """#04 p.8 assigns reddish-brown oxide to FRETTING corrosion — rust
        without water — which is this entry's nearest differential."""
        entry = self._entries()["outer_race_moisture_corrosion"]
        blob = " ".join(d.observation for d in entry.discriminating_evidence).lower()
        assert "reddish" not in blob
        assert "fretting" in (entry.consider or "").lower()

    def test_the_nasa_table_hierarchy_is_stated_at_the_right_level(self):
        """Table 4-5 is Failure Mode | Mechanism | Reason | Cause. Both notes
        collapsed it by one level before R2-B0.2."""
        entries = self._entries()
        elec = next(
            c for c in entries["outer_race_electrical_current_erosion"].citations
            if c.doc == "#43"
        )
        assert "REASON" in elec.note and "Surface Distress" in elec.note
        mis = next(
            c for c in entries["inner_race_misalignment_edge_loading"].citations
            if c.doc == "#43"
        )
        assert "CAUSE" in mis.note and "Excessive Load" in mis.note

    def test_the_cage_entry_is_no_longer_claiming_triangulation(self):
        """#01 p.67 documents a DIFFERENT cause of cage damage — pocket wear from
        inadequate lubrication and vibration — so it was never a second source."""
        entry = self._entries()["rolling_element_cage_wear_grooving"]
        assert entry.basis == "single_source"
        assert {c.doc for c in entry.citations} == {"#02"}
        assert "#01 p.67" in (entry.review_question or "")

    def test_the_fag_locators_state_which_page_numbering_they_use(self):
        """#03's PDF index and printed folio differ by one throughout, and the
        edition on disk is already the unconfirmed earlier one."""
        for entry in load_batch(BATCH).entries:
            cites = list(entry.citations) + [
                c for d in entry.discriminating_evidence for c in (d.citations or [])
            ]
            for c in cites:
                if c.doc == "#03":
                    assert "PDF p." in c.locator and "printed p." in c.locator, c.locator


# ── the vocabulary came from the code ─────────────────────────────────────


class TestVocabularyIsNotInvented:
    def test_every_fault_family_appears_in_the_detector_source(self):
        """The names must be ones the detectors actually emit; a cause keyed to
        an invented fault is unreachable knowledge."""
        src = "\n".join(
            p.read_text() for p in (_REPO / "src/vib_agent/pdm_core").glob("*.py")
        )
        for family in FAULT_FAMILIES:
            assert f'"{family}"' in src, f"{family} does not appear in pdm_core — invented?"

    def test_bearing_families_match_the_bpfo_bpfi_bsf_ftf_map(self):
        rca = (_REPO / "src/vib_agent/pdm_core/bearing_rca.py").read_text()
        for key, family in (
            ("BPFO", "bearing_outer_race"),
            ("BPFI", "bearing_inner_race"),
            ("BSF", "bearing_ball_spin"),
            ("FTF", "bearing_cage"),
        ):
            assert re.search(rf'"{key}":\s*\("{family}"', rca), f"{key} -> {family} mapping moved"
        assert BEARING_FAULT_FAMILIES == {
            "bearing_outer_race", "bearing_inner_race", "bearing_ball_spin", "bearing_cage"
        }

    def test_stages_match_pdm_core_staging_plus_any(self):
        from vib_agent.pdm_core.staging import STAGE_LABELS

        assert STAGES == set(STAGE_LABELS) | {"any"}


# ── the rejections ────────────────────────────────────────────────────────


class TestBadEntriesAreRejected:
    def test_a_fabricated_citation_is_rejected(self):
        with pytest.raises(ValidationError, match=r"not a numbered entry"):
            CauseEntry.model_validate(
                _entry(citations=[dict(doc="#99", locator="§1", note="invented")])
            )

    def test_an_invented_fault_family_is_rejected(self):
        with pytest.raises(ValidationError, match=r"unknown fault_family"):
            CauseEntry.model_validate(_entry(fault_family=["bearing_race_wobble"]))

    def test_an_invented_family_is_caught_even_beside_a_valid_one(self):
        with pytest.raises(ValidationError, match=r"unknown fault_family"):
            CauseEntry.model_validate(
                _entry(
                    fault_family=["bearing_outer_race", "bearing_race_wobble"],
                    extends_families="because",
                )
            )

    def test_a_duplicated_family_is_rejected(self):
        with pytest.raises(ValidationError, match=r"duplicate fault_family"):
            CauseEntry.model_validate(
                _entry(
                    fault_family=["bearing_outer_race", "bearing_outer_race"],
                    extends_families="because",
                )
            )

    def test_a_multi_family_entry_must_justify_the_extension(self):
        """A family list claims the SOURCE is component-agnostic. Without the
        justification the list quietly becomes a way to make one observation
        cover everything."""
        with pytest.raises(ValidationError, match=r"extends_families is empty"):
            CauseEntry.model_validate(
                _entry(fault_family=["bearing_outer_race", "bearing_inner_race"])
            )

    def test_a_justified_extension_is_accepted(self):
        CauseEntry.model_validate(
            _entry(
                fault_family=["bearing_outer_race", "bearing_inner_race"],
                extends_families="EXTENSION. #01 p.73 shows it on both rings.",
            )
        )

    def test_citing_56_is_rejected(self):
        """#56's licence is unverified (GAPS.md §0b) — nothing may be quoted
        from it into a cause entry."""
        with pytest.raises(ValidationError, match=r"licence/provenance is unverified"):
            CauseEntry.model_validate(
                _entry(citations=[dict(doc="#56", locator="p.3", note="envelope paper")])
            )

    def test_a_quarantined_source_named_in_the_locator_is_rejected(self):
        """A quarantined title in the LOCATOR is a claim to have sourced from it.
        Those items carry no #NN, so the numbered-entry rule alone misses them."""
        with pytest.raises(ValidationError, match=r"quarantined source"):
            CauseEntry.model_validate(
                _entry(
                    citations=[
                        dict(doc="#01", locator="ISO 15243 clause A.2", note="x")
                    ]
                )
            )

    def test_a_quarantined_title_in_a_note_is_now_allowed(self):
        """Narrowed in R2-B0.1 by operator ruling. `note` is OUR description of
        what a legitimate document supports, and it must stay free to say what
        #04 actually is. Laundering is still blocked, because content can only
        enter through a doc/locator that resolves."""
        CauseEntry.model_validate(
            _entry(
                citations=[
                    dict(
                        doc="#04",
                        locator="Basic Wear Modes, p.3",
                        note="Noria's free restatement of the ISO 15243 six-category taxonomy.",
                    )
                ]
            )
        )

    def test_an_unknown_stage_is_rejected(self):
        with pytest.raises(ValidationError, match=r"unknown stage"):
            CauseEntry.model_validate(_entry(stage="stage_1"))

    def test_a_bare_fag_locator_is_rejected(self):
        """The copy on disk is the earlier /2 ED; a bare clause reference would
        silently imply the /3 EA that was never obtained."""
        with pytest.raises(ValidationError, match=r"must state the edition"):
            CauseEntry.model_validate(
                _entry(citations=[dict(doc="#03", locator="§1.2, p.8", note="x")])
            )

    def test_a_fag_locator_naming_its_edition_is_accepted(self):
        CauseEntry.model_validate(
            _entry(citations=[dict(doc="#03", locator="§1.2, p.8 (/2 ED)", note="x")])
        )

    def test_a_transcribed_mechanism_is_rejected(self):
        with pytest.raises(ValidationError, match=r"own words"):
            CauseEntry.model_validate(_entry(mechanism='He said "this is a quote".'))

    def test_an_over_long_mechanism_is_rejected(self):
        with pytest.raises(ValidationError, match=r"at most 2 sentences"):
            CauseEntry.model_validate(_entry(mechanism="One. Two. Three."))

    def test_triangulated_needs_more_than_one_document(self):
        with pytest.raises(ValidationError, match=r"claims corroboration"):
            CauseEntry.model_validate(_entry(basis="triangulated", review_question=None))

    def test_single_source_may_not_cite_two_documents(self):
        with pytest.raises(ValidationError, match=r"single_source"):
            CauseEntry.model_validate(
                _entry(
                    basis="single_source",
                    citations=[
                        dict(doc="#01", locator="§5, p.63", note="a"),
                        dict(doc="#02", locator="p.7", note="b"),
                    ],
                )
            )

    def test_an_unreviewed_judgment_call_is_rejected(self):
        with pytest.raises(ValidationError, match=r"requires a review_question"):
            CauseEntry.model_validate(_entry(basis="inferred", review_question="  "))

    def test_an_unknown_field_is_rejected(self):
        """extra='forbid' — a candidate cannot smuggle in an unreviewed field."""
        with pytest.raises(ValidationError):
            CauseEntry.model_validate(_entry(confidence="high"))

    def test_evidence_without_a_collection_method_is_rejected(self):
        """Every unresolved ambiguity ships with the measurement that resolves
        it — evidence nobody can collect is not knowledge."""
        with pytest.raises(ValidationError):
            CauseEntry.model_validate(
                _entry(
                    discriminating_evidence=[
                        dict(observation="x", stream="oil", how_to_collect="  ")
                    ]
                )
            )

    def test_an_invented_stream_is_rejected(self):
        with pytest.raises(ValidationError):
            CauseEntry.model_validate(
                _entry(
                    discriminating_evidence=[
                        dict(observation="x", stream="telepathy", how_to_collect="y")
                    ]
                )
            )


class TestIndexParsing:
    def test_the_index_is_the_source_of_truth(self):
        docs = index_doc_numbers()
        assert {"#1", "#2", "#3", "#4", "#43"} <= docs
        assert len(docs) > 40

    def test_zero_padding_does_not_matter(self):
        for form in ("#01", "#1"):
            Citation(doc=form, locator="§5, p.63", note="x")
