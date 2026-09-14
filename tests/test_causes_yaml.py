"""Session R2-BUILD — the merged product cause layer, knowledge/causes.yaml.

The distinction this file exists to hold: knowledge/candidates/*.yaml are review
artifacts that nothing reads at runtime, while causes.yaml is PRODUCT KNOWLEDGE
that reaches an analyst's report. Promotion between the two is an operator
decision, and nothing was relaxed to allow it — the merged file is put through
the same strict authoring validator the candidates went through, here, every run.

There are two readers of this one file (knowledge/schema.py for authoring,
vib_agent.knowledge.loader for runtime — see loader.py for why they cannot be
one), so the last class of tests asserts they agree field for field. Two models
over one file is a drift risk; this is how it is closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from knowledge.schema import CauseBook as AuthoringBook
from knowledge.schema import load_batch, load_causes
from vib_agent.knowledge import load_causes as load_runtime_causes
from vib_agent.knowledge.loader import CAUSES_PATH

_REPO = Path(__file__).resolve().parents[1]
CAUSES = _REPO / "knowledge" / "causes.yaml"
BATCH = _REPO / "knowledge" / "candidates" / "batch1_bearing_causes.yaml"


class TestTheMergedFile:
    def test_it_passes_the_full_authoring_validator(self):
        """Every rule the candidates faced — citations resolving against
        references/INDEX.md, the #03 edition rule, the INDEX §8 quarantine, the
        two-sentence mechanism cap, observation-level sourcing — still applies."""
        book = load_causes()
        assert len(book.entries) == 14
        assert book.schema_version == "1.2"

    def test_the_operator_approval_is_on_record_in_the_file(self):
        """Product cause knowledge ships on a named human decision. A book that
        cannot say who approved it, and when, is refused by the model."""
        book = load_causes()
        assert "operator" in book.approval.lower()
        assert "2026-08-13" in book.approval
        assert book.session == "R2-BUILD"
        assert book.source_batch == "knowledge/candidates/batch1_bearing_causes.yaml"

    def test_an_approval_that_names_nobody_is_refused(self):
        raw = yaml.safe_load(CAUSES.read_text())
        raw["approval"] = "approved, 2026-08-13"
        with pytest.raises(ValidationError, match=r"must record the OPERATOR"):
            AuthoringBook.model_validate(raw)

    def test_an_approval_with_no_date_is_refused(self):
        raw = yaml.safe_load(CAUSES.read_text())
        raw["approval"] = "approved by the operator"
        with pytest.raises(ValidationError, match=r"must record the date"):
            AuthoringBook.model_validate(raw)


class TestTheApprovedAmendment:
    """The one edit the operator authorised at merge, and its blast radius."""

    def test_the_entry_was_renamed(self):
        ids = {e.cause_id for e in load_causes().entries}
        assert "vibration_false_brinelling" in ids
        assert "standstill_vibration_false_brinelling" not in ids

    def test_the_related_back_reference_was_updated_too(self):
        """The half of a rename that is easy to forget and impossible to see:
        the sibling entry pointing at the old id."""
        entries = {e.cause_id: e for e in load_causes().entries}
        assert entries["inner_race_mounting_impact_brinelling"].related == [
            "vibration_false_brinelling"
        ]
        assert entries["vibration_false_brinelling"].related == [
            "inner_race_mounting_impact_brinelling"
        ]

    def test_a_dangling_related_pointer_is_refused_by_the_model_not_just_a_test(self):
        """In a candidate batch a broken cross-reference annoys a reviewer. In
        the product file it is a broken pointer in shipped knowledge, so the
        CauseBook model refuses it outright — which is exactly what would have
        caught a rename applied to `cause_id` and not to `related:`."""
        raw = yaml.safe_load(CAUSES.read_text())
        for entry in raw["entries"]:
            if entry["cause_id"] == "inner_race_mounting_impact_brinelling":
                entry["related"] = ["standstill_vibration_false_brinelling"]
        with pytest.raises(ValidationError, match=r"not an entry in this book"):
            AuthoringBook.model_validate(raw)

    def test_a_one_way_related_pointer_is_refused(self):
        raw = yaml.safe_load(CAUSES.read_text())
        for entry in raw["entries"]:
            if entry["cause_id"] == "vibration_false_brinelling":
                entry["related"] = None
        with pytest.raises(ValidationError, match=r"not reciprocal"):
            AuthoringBook.model_validate(raw)

    def test_the_candidate_batch_was_not_renamed(self):
        """`apply at merge` — the review record keeps the id it was reviewed
        under, or the citecheck report stops describing it."""
        batch_ids = {e.cause_id for e in load_batch(BATCH).entries}
        assert "standstill_vibration_false_brinelling" in batch_ids


class TestNothingElseChangedAtMerge:
    """The merge is a promotion, not an edit. Anything that differs between the
    candidate batch and the product file, beyond the approved rename, is an
    unreviewed change to knowledge an analyst will read."""

    def _rename(self, value):
        if isinstance(value, str):
            return value.replace(
                "standstill_vibration_false_brinelling", "vibration_false_brinelling"
            )
        if isinstance(value, list):
            return [self._rename(v) for v in value]
        if isinstance(value, dict):
            return {k: self._rename(v) for k, v in value.items()}
        return value

    def test_every_entry_is_carried_across_verbatim(self):
        candidate = {
            e["cause_id"]: e for e in self._rename(yaml.safe_load(BATCH.read_text()))["entries"]
        }
        product = {e["cause_id"]: e for e in yaml.safe_load(CAUSES.read_text())["entries"]}
        assert set(product) == set(candidate)
        for cause_id, entry in product.items():
            assert entry == candidate[cause_id], (
                f"{cause_id} differs between the reviewed candidate batch and the merged "
                "product file. Only the approved rename may differ; everything else means "
                "knowledge changed without review."
            )

    def test_the_entry_order_is_preserved(self):
        candidate = [e["cause_id"] for e in yaml.safe_load(BATCH.read_text())["entries"]]
        product = [e["cause_id"] for e in yaml.safe_load(CAUSES.read_text())["entries"]]
        assert product == self._rename(candidate)


class TestTheRuntimeDependencyIsDeclared:
    def test_pyyaml_is_a_runtime_dependency_not_a_dev_side_effect(self):
        """causes.yaml is read on the REPORT path, so a report cannot render
        without a YAML parser. PyYAML was reaching this venv only through
        `bandit` — a dev tool — which means a runtime-only `pip install -e .`
        would have shipped a product that crashes on any committed bearing
        fault. Same class as the defusedxml finding already recorded in
        pyproject's [web] extra, and pinned here for the same reason: the
        failure is invisible on a developer machine."""
        pyproject = (_REPO / "pyproject.toml").read_text()
        runtime = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
        assert "pyyaml" in runtime.lower(), (
            "PyYAML is imported on the report path but is not a declared runtime "
            "dependency — see vib_agent/knowledge/loader.py"
        )


class TestTheRuntimeViewMatchesTheAuthoringModel:
    """Two models read this file. They must never see different knowledge."""

    def test_the_runtime_loader_reads_the_committed_file(self):
        assert CAUSES_PATH == CAUSES
        assert CAUSES_PATH.exists()

    def test_both_models_see_the_same_entries_in_the_same_order(self):
        authored = load_causes()
        runtime = load_runtime_causes()
        assert [e.cause_id for e in runtime.entries] == [e.cause_id for e in authored.entries]
        assert runtime.schema_version == authored.schema_version
        assert runtime.approval == authored.approval

    def test_every_rendered_field_agrees_field_for_field(self):
        authored = {e.cause_id: e for e in load_causes().entries}
        for entry in load_runtime_causes().entries:
            source = authored[entry.cause_id]
            assert entry.cause == source.cause
            assert entry.mechanism == source.mechanism
            assert entry.fault_family == source.fault_family
            assert entry.stage == source.stage
            assert entry.basis == source.basis
            assert entry.consider == source.consider
            assert [c.doc for c in entry.citations] == [c.doc for c in source.citations]
            assert [c.locator for c in entry.citations] == [c.locator for c in source.citations]
            assert len(entry.discriminating_evidence) == len(source.discriminating_evidence)
            for observation, origin in zip(
                entry.discriminating_evidence, source.discriminating_evidence, strict=True
            ):
                assert observation.observation == origin.observation
                assert observation.stream == origin.stream
                assert observation.how_to_collect == origin.how_to_collect
                assert observation.inferred == origin.inferred
                assert [c.doc for c in observation.citations] == [
                    c.doc for c in (origin.citations or [])
                ]

    def test_the_runtime_view_deliberately_drops_the_reviewer_only_fields(self):
        """`note`, `review_question` and `disagreement` are written for the
        operator's review, not for an analyst, and must not be renderable."""
        runtime = load_runtime_causes().entries[0]
        assert not hasattr(runtime, "review_question")
        assert not hasattr(runtime, "disagreement")
        assert not hasattr(runtime.citations[0], "note")

    def test_the_two_bearing_family_lists_agree(self):
        from knowledge.schema import BEARING_FAULT_FAMILIES as AUTHORING_FAMILIES
        from vib_agent.knowledge import BEARING_FAULT_FAMILIES as RUNTIME_FAMILIES

        assert RUNTIME_FAMILIES == AUTHORING_FAMILIES
