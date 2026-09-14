"""Session R2-B0 — the cause-knowledge candidate schema and its validator.

This package holds REVIEWABLE KNOWLEDGE CANDIDATES, not product knowledge.
Nothing here is wired into pdm_core, agent/, report/ or webapp/, and nothing
merges into causes.yaml (which does not exist yet). Approval happens with the
operator, entry by entry.

Why the validator is strict
===========================
The whole point of the reference library is that a cause entry is only as good
as the source it resolves to. The failure this schema exists to prevent is a
plausible-sounding mechanism with a citation that does not check out -- a guess
wearing a citation's clothes. So every rule below is mechanical:

  * `fault_family` must be a name the DETECTORS ACTUALLY EMIT. Enumerated from
    the code, never invented -- a cause keyed to a fault the product cannot
    commit is unreachable knowledge.
  * `stage` must be a value pdm_core.staging can actually produce, plus "any".
  * every citation's `doc` must be a numbered entry that EXISTS in
    references/INDEX.md, parsed from the file at validation time.
  * #56 is refused: its licence is unverified (INDEX #56, GAPS.md §0b).
  * the INDEX §8 quarantine -- the unlicensed copies, including both ISO
    standards -- is refused by title in `doc` and `locator`, because those items
    carry no #NN and would otherwise slip through the "must be numbered" rule.
    NARROWED in R2-B0.1 (operator ruling): `note` is exempt, so an entry may
    describe what a legitimate source is (e.g. #04 as a free restatement of the
    ISO 15243 categories) without the validator reading that as a citation.
  * #03 locators must carry "/2 ED": the copy on disk is the EARLIER Schaeffler
    edition, so a bare clause reference would silently imply the /3 EA that was
    targeted and never obtained (INDEX #03, GAPS.md §7).
  * `mechanism` is capped at two sentences, in our own words. Distil, never
    transcribe: this file must not become a reproduction of a source table.

SCHEMA v1.2 (session R2-B0.2) — sourcing moves down to the observation
=====================================================================
Every `discriminating_evidence` observation must now carry EITHER its own
`citations` (validated exactly like an entry citation) OR `inferred: true`.
There is no third state, and no both-at-once state.

R2-CITECHECK graded the batch page by page and found the rule this replaces was
not enough: 15 of the 16 vibration, oil and thermal observations in batch1 had
no support on any cited page. They had been written from general engineering
knowledge and placed in the same block as citations to visual damage atlases,
where they read as sourced. Entry-level citations cannot prevent that, because
they say nothing about WHICH observation each source backs -- authority spreads
by adjacency. So the claim moves down: an observation either names the page that
states it, or says out loud that it is ours.

The corollary rule is the split. An observation with one clause the source
states and one clause it does not is TWO observations, not one partially-cited
one -- otherwise the cited half launders the uncited half, which is the same
failure one level finer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REPO = Path(__file__).resolve().parents[1]
_INDEX = _REPO / "references" / "INDEX.md"

# ── vocabularies ──────────────────────────────────────────────────────────
# Enumerated FROM THE CODE (Session R2-B0 step 0), never invented:
#   bearing_* .......... pdm_core/bearing_rca.py, the BPFO/BPFI/BSF/FTF -> name map
#   everything else .... `fault=` literals in pdm_core/{synthesize,bearing_rca}.py
# tests/test_knowledge_schema.py proves every name below still appears in the
# detector source, so this list cannot drift into fiction.
BEARING_FAULT_FAMILIES: frozenset[str] = frozenset(
    {"bearing_outer_race", "bearing_inner_race", "bearing_ball_spin", "bearing_cage"}
)
OTHER_FAULT_FAMILIES: frozenset[str] = frozenset(
    {
        "imbalance",
        "angular_misalignment",
        "parallel_misalignment",
        "severe_misalignment",
        "misalignment_general",
        "bent_shaft",
        "mechanical_looseness",
        "belt_fault",
        "elevated_blade_pass",
        "possible_resonance",
        "elevated_vibration_undetermined",
    }
)
FAULT_FAMILIES: frozenset[str] = BEARING_FAULT_FAMILIES | OTHER_FAULT_FAMILIES

#: pdm_core.staging stages, plus "any" for a cause that is stage-independent.
STAGES: frozenset[str] = frozenset(
    {"stage_3_early", "stage_3_advanced", "stage_4_suspected", "not_determinable", "any"}
)

Stream = Literal["vibration", "oil", "thermal", "visual", "history"]
Basis = Literal["triangulated", "single_source", "inferred"]

#: Refused outright. #56's licence is unverified (GAPS.md §0b).
FORBIDDEN_DOCS: frozenset[str] = frozenset({"#56"})

#: INDEX §8 quarantine — unlicensed copies, held OUTSIDE the repo and never
#: citable. Matched on the title as well as any number, because these items
#: carry no INDEX number and would otherwise evade the "must be numbered" rule.
QUARANTINED_TITLE_PATTERNS: tuple[str, ...] = (
    r"iso\s*15243",
    r"iso\s*4406",
    r"randall",
    r"antoni",
    r"\btoms\b",
    r"bloch",
    r"geitner",
    r"scheffer",
    r"girdhar",
    r"anna'?s\s*archive",
)

_DOC_RE = re.compile(r"^#(\d{1,3})([ab])?$")
_SENTENCE_RE = re.compile(r"[.!?](?:\s|$)")


def canonical_doc(doc: str) -> str:
    """Normalise a doc reference so `#01`, `#1` and INDEX's own `01` all compare
    equal. INDEX writes zero-padded numbers in its tables; citations are written
    the same way by convention, but the validator must not depend on padding."""
    m = _DOC_RE.match(doc.strip())
    if m is None:
        return doc.strip()
    return f"#{int(m.group(1))}{m.group(2) or ''}"


def index_doc_numbers(index_path: Path | None = None) -> set[str]:
    """Every numbered entry in references/INDEX.md, canonicalised.

    Parsed from the file rather than hardcoded, so an entry retired from the
    library immediately stops validating anywhere it is cited.
    """
    path = index_path or _INDEX
    if not path.exists():
        raise FileNotFoundError(
            f"references/INDEX.md not found at {path}. The citation validator cannot "
            "run without it — see the References convention in CLAUDE.md."
        )
    docs: set[str] = set()
    for line in path.read_text().splitlines():
        m = re.match(r"\|\s*[✅⚠⬜]\s*\|\s*(\d{1,3}[ab]?)\s*\|", line)
        if m:
            docs.add(canonical_doc(f"#{m.group(1)}"))
    return docs


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc: str = Field(description='INDEX entry, e.g. "#01"')
    locator: str = Field(description='Where in it, e.g. "§5, p.63" — never a bare doc reference')
    note: str = Field(description="What this source actually supports, in our words")

    @field_validator("doc")
    @classmethod
    def _doc_is_a_live_index_entry(cls, v: str) -> str:
        if not _DOC_RE.match(v):
            raise ValueError(f"doc must look like '#NN', got {v!r}")
        if canonical_doc(v) in {canonical_doc(d) for d in FORBIDDEN_DOCS}:
            raise ValueError(
                f"{v} is not citable: its licence/provenance is unverified (GAPS.md §0b). "
                "Nothing may be quoted from it into a cause entry."
            )
        if canonical_doc(v) not in index_doc_numbers():
            raise ValueError(
                f"{v} is not a numbered entry in references/INDEX.md. Citations resolve "
                "against INDEX by number (CLAUDE.md, References) — add the document to the "
                "library first, with its page count measured from the file."
            )
        return v

    @field_validator("locator")
    @classmethod
    def _locator_is_specific(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("locator must not be empty — a bare document reference is not a citation")
        return v

    @model_validator(mode="after")
    def _fag_locator_states_its_edition(self) -> Citation:
        """#03 on disk is WL 82 102/2 ED, not the /3 EA that was targeted. A bare
        clause reference would silently imply the edition we do not have."""
        if self.doc == "#03" and "/2 ED" not in self.locator:
            raise ValueError(
                "#03 locators must state the edition, e.g. '§1.2 fig.5, p.7 (/2 ED)'. The copy "
                "on disk is the EARLIER WL 82 102/2 ED; the /3 EA was never obtained "
                "(INDEX #03, GAPS.md §7), so a bare clause reference misattributes the edition."
            )
        return self

    @model_validator(mode="after")
    def _no_quarantined_source_is_cited(self) -> Citation:
        """Narrowed in R2-B0.1 (operator ruling) to `doc` and `locator` only.

        Those two fields are the citation ITSELF -- naming a quarantined source
        there is a claim to have sourced from it. `note` is our description of
        what the cited document supports, and it must stay free to say things
        like "a free restatement of the ISO 15243 categories", which the earlier
        blanket match refused. Laundering is still blocked, because content can
        only enter through a doc/locator that resolves.
        """
        blob = f"{self.doc} {self.locator}".lower()
        for pattern in QUARANTINED_TITLE_PATTERNS:
            if re.search(pattern, blob):
                raise ValueError(
                    f"citation text matches a quarantined source ({pattern!r}). INDEX §8 items — "
                    "the unlicensed copies, including both ISO standards — are held outside the "
                    "repo and are never citable, directly or through a derived document."
                )
        return self


class DiscriminatingEvidence(BaseModel):
    """One observation that would separate THIS cause from its neighbours.

    The product pillar is that every unresolved ambiguity ships with the
    measurement that resolves it, so an entry whose evidence cannot be collected
    is not knowledge — hence `how_to_collect` is mandatory.
    """

    model_config = ConfigDict(extra="forbid")

    observation: str
    stream: Stream
    how_to_collect: str
    citations: list[Citation] | None = Field(
        default=None,
        description="The source(s) that state THIS observation, not merely the entry's "
        "subject. Same rules as an entry citation. Mutually exclusive with `inferred`.",
    )
    inferred: bool = Field(
        default=False,
        description="True when this observation is OUR reasoning rather than something a "
        "cited source states. Keeps a derived discriminator visible at review instead of "
        "letting it inherit the authority of the citation next to it.",
    )

    @field_validator("observation", "how_to_collect")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v

    @model_validator(mode="after")
    def _sourced_or_declared_inferred(self) -> DiscriminatingEvidence:
        """Schema v1.2. Exactly one of: cited, or declared inferred."""
        cited = bool(self.citations)
        if cited and self.inferred:
            raise ValueError(
                "an observation may not be both cited and inferred:true. If part of it is "
                "sourced and part is ours, SPLIT it into two observations — a half-cited "
                "observation launders the uncited half behind the cited one."
            )
        if not cited and not self.inferred:
            raise ValueError(
                "every observation must carry its own citations[] or inferred: true "
                "(schema v1.2). An uncited observation sitting beside an entry citation "
                "inherits authority it was never given — that is the R2-CITECHECK finding, "
                "and this rule is the whole reason the schema was revised."
            )
        return self


class CauseEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause_id: str
    fault_family: list[str] = Field(
        min_length=1,
        description="One or more families this cause can present as. A list only where the "
        "CITED SOURCE's own description is component-agnostic — see extends_families.",
    )
    stage: str
    cause: str
    mechanism: str
    discriminating_evidence: list[DiscriminatingEvidence] = Field(min_length=1)
    typical_actions: list[str] = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)
    basis: Basis
    review_question: str | None = Field(
        default=None,
        description="One line for the operator where judgment is needed. Not optional in "
        "practice for anything not triangulated — see the model validator.",
    )
    disagreement: str | None = Field(
        default=None,
        description="Where sources conflict, state the conflict here rather than "
        "harmonizing it away silently.",
    )
    related: list[str] | None = Field(
        default=None,
        description="cause_ids a reviewer should read alongside this one — neighbours that "
        "share a mechanism, or that this entry could be confused with. Reciprocal by "
        "convention, and checked as such.",
    )
    consider: str | None = Field(
        default=None,
        description="Ordering or precedence guidance for the reviewer, e.g. that a cause "
        "should be weighed last because its base rate is low.",
    )
    extends_families: str | None = Field(
        default=None,
        description="Required when fault_family lists more than one family: the justification, "
        "naming the cited locator whose own description is component-agnostic.",
    )

    @field_validator("cause_id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", v):
            raise ValueError(f"cause_id must be lower_snake_case, got {v!r}")
        return v

    @field_validator("fault_family")
    @classmethod
    def _known_fault_families(cls, v: list[str]) -> list[str]:
        for family in v:
            if family not in FAULT_FAMILIES:
                raise ValueError(
                    f"unknown fault_family {family!r}. It must be a name the detectors actually "
                    f"emit (enumerated from pdm_core); known: {sorted(FAULT_FAMILIES)}"
                )
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate fault_family entries: {v}")
        return v

    @field_validator("stage")
    @classmethod
    def _known_stage(cls, v: str) -> str:
        if v not in STAGES:
            raise ValueError(f"unknown stage {v!r}; known: {sorted(STAGES)}")
        return v

    @field_validator("mechanism")
    @classmethod
    def _mechanism_is_short_and_ours(cls, v: str) -> str:
        text = v.strip()
        if not text:
            raise ValueError("mechanism must not be empty")
        sentences = [s for s in _SENTENCE_RE.split(text) if s.strip()]
        if len(sentences) > 2:
            raise ValueError(
                f"mechanism must be at most 2 sentences (distil, never transcribe); got "
                f"{len(sentences)}"
            )
        if '"' in text or "“" in text:
            raise ValueError(
                "mechanism must be in our own words — a quoted passage is a reproduction, "
                "not a distillation"
            )
        return text

    @model_validator(mode="after")
    def _judgment_is_surfaced(self) -> CauseEntry:
        if self.basis != "triangulated" and not (self.review_question or "").strip():
            raise ValueError(
                f"basis={self.basis!r} requires a review_question: anything not corroborated by "
                "more than one source needs an explicit operator decision before it can ship."
            )
        return self

    @model_validator(mode="after")
    def _multi_family_is_justified(self) -> CauseEntry:
        """A family list is a claim that the SOURCE is component-agnostic, not a
        convenience. Requiring the justification stops the list quietly becoming
        a way to make one observation cover everything."""
        if len(self.fault_family) > 1 and not (self.extends_families or "").strip():
            raise ValueError(
                f"fault_family lists {self.fault_family} but extends_families is empty. State "
                "which cited locator describes the damage in component-agnostic terms."
            )
        return self

    def cited_docs(self) -> set[str]:
        """Every document this entry draws on, canonicalised.

        v1.2 counts OBSERVATION citations too. An observation citation is a real
        sourcing claim, so leaving it out of the basis count would let an entry
        say `single_source` while quietly resting on three documents — the same
        adjacency problem the observation-level rule exists to close.
        """
        docs = {canonical_doc(c.doc) for c in self.citations}
        for evidence in self.discriminating_evidence:
            docs.update(canonical_doc(c.doc) for c in (evidence.citations or []))
        return docs

    @model_validator(mode="after")
    def _basis_matches_the_citations(self) -> CauseEntry:
        distinct = self.cited_docs()
        if self.basis == "triangulated" and len(distinct) < 2:
            raise ValueError(
                f"basis='triangulated' claims corroboration but cites only {sorted(distinct)}. "
                "Use 'single_source', or add the corroborating document."
            )
        if self.basis == "single_source" and len(distinct) > 1:
            raise ValueError(
                f"basis='single_source' but {len(distinct)} distinct documents are cited "
                f"({sorted(distinct)}). Say 'triangulated' if they genuinely corroborate."
            )
        return self


class CandidateBatch(BaseModel):
    """A reviewable batch. `batch` and `sources` are provenance for the review,
    not product data."""

    model_config = ConfigDict(extra="forbid")

    batch: str
    description: str
    status: Literal["awaiting_operator_review"]
    entries: list[CauseEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _ids_are_unique(self) -> CandidateBatch:
        seen = [e.cause_id for e in self.entries]
        dupes = {i for i in seen if seen.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate cause_id(s): {sorted(dupes)}")
        return self


class CauseBook(BaseModel):
    """PRODUCT knowledge — knowledge/causes.yaml, the merged approved layer.

    Session R2-BUILD. The distinction from CandidateBatch is the whole point of
    having two models rather than reusing one:

      * A CandidateBatch is a REVIEW artifact. It stays
        `awaiting_operator_review` permanently, nothing reads it at runtime, and
        its `status` field is the record that it was never product knowledge.
      * A CauseBook IS read at runtime, so it carries an `approval` field
        instead of a `status` one. That field is not decoration: a cause entry
        may only reach an analyst's report because a named operator approved it
        on a named date, and this model refuses a book that cannot say so.

    Every entry is still a full CauseEntry, so every authoring rule -- citations
    resolving against references/INDEX.md, the #03 edition rule, the INDEX §8
    quarantine, the two-sentence mechanism cap, observation-level sourcing --
    applies to the product file exactly as it applied to the candidate file.
    Merging approved entries does not relax anything.

    NOTE ON THE IMPORT BOUNDARY. This module is NOT importable from the
    installed package: `knowledge/` sits at the repo root and pyproject's
    setuptools config only packages `src/`, so `import knowledge.schema` works
    under pytest (rootdir on sys.path) and fails in the deployed service. That
    is deliberate and it is why the runtime read path lives in
    `src/vib_agent/knowledge/loader.py` instead. This model is the AUTHORING
    gate -- strict, INDEX-resolving, test-enforced; the runtime view is a
    narrow, permissive projection of the same file that never touches
    references/. tests/test_causes_yaml.py runs both over the one file and
    asserts they agree field for field, so the two cannot drift.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.2"]
    session: str
    date: str
    approval: str
    source_batch: str
    description: str
    entries: list[CauseEntry] = Field(min_length=1)

    @field_validator("approval")
    @classmethod
    def _approval_names_an_operator_and_a_date(cls, v: str) -> str:
        """A book that cannot say who approved it, and when, is not approved."""
        text = v.strip()
        if "operator" not in text.lower():
            raise ValueError(
                "approval must record the OPERATOR who approved these entries -- product "
                "cause knowledge ships on a named human decision, not on a merge."
            )
        if not re.search(r"\b\d{4}-\d{2}-\d{2}\b", text):
            raise ValueError(
                "approval must record the date of the decision in ISO form (YYYY-MM-DD)."
            )
        return v

    @model_validator(mode="after")
    def _ids_are_unique(self) -> CauseBook:
        seen = [e.cause_id for e in self.entries]
        dupes = {i for i in seen if seen.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate cause_id(s): {sorted(dupes)}")
        return self

    @model_validator(mode="after")
    def _related_pointers_resolve_and_are_reciprocal(self) -> CauseBook:
        """In a candidate batch a dangling `related:` is a reviewer annoyance.
        In the product file it is a broken pointer in shipped knowledge, so it
        is refused here rather than only asserted in a test. This is also what
        catches a rename applied to a `cause_id` but not to the entries that
        point at it -- exactly the R2-BUILD amendment's failure mode."""
        ids = {e.cause_id for e in self.entries}
        for entry in self.entries:
            for other in entry.related or []:
                if other not in ids:
                    raise ValueError(
                        f"{entry.cause_id} relates to {other!r}, which is not an entry in this "
                        "book. A rename must be applied to every `related:` pointer too."
                    )
                back = next(e for e in self.entries if e.cause_id == other).related or []
                if entry.cause_id not in back:
                    raise ValueError(
                        f"{entry.cause_id} -> {other} is not reciprocal: {other} does not list "
                        f"{entry.cause_id} in its own `related:`."
                    )
        return self


def load_batch(path: str | Path) -> CandidateBatch:
    """Parse and validate a candidate YAML file. Raises pydantic ValidationError
    on anything the rules above refuse."""
    import yaml

    data: Any = yaml.safe_load(Path(path).read_text())
    return CandidateBatch.model_validate(data)


def load_causes(path: str | Path | None = None) -> CauseBook:
    """Parse and validate knowledge/causes.yaml through the FULL authoring
    schema. Authoring-time gate only -- the product reads the same file through
    vib_agent.knowledge.loader, which does no INDEX I/O."""
    import yaml

    target = Path(path) if path is not None else _REPO / "knowledge" / "causes.yaml"
    data: Any = yaml.safe_load(target.read_text())
    return CauseBook.model_validate(data)
