"""The runtime view of knowledge/causes.yaml, plus lookup_causes().

Session R2-BUILD — wiring the approved cause knowledge into the product.

WHY THIS MODULE EXISTS SEPARATELY FROM knowledge/schema.py
==========================================================
`knowledge/` sits at the repo root, and pyproject's setuptools config packages
only `src/`. So `import knowledge.schema` resolves under pytest (which puts the
rootdir on sys.path) and raises ModuleNotFoundError in the installed service.
The product therefore cannot import the authoring schema, and must not try.

That constraint turns out to be the right architecture anyway, because the two
readers want opposite things:

  * knowledge/schema.py is the AUTHORING gate. It is strict on purpose: it
    resolves every citation against references/INDEX.md, refuses #56, refuses
    the INDEX §8 quarantine, caps mechanisms at two sentences. It runs when a
    human or an agent WRITES an entry.
  * this module is the RUNTIME view. It reads the same file, is permissive
    about anything it does not render, and — importantly — never touches
    references/ at all. CLAUDE.md's References doctrine is explicit that the
    citation register is not RAG and that nothing in pdm_core/, agent/ or
    webapp/ reads it; making report rendering depend on INDEX.md would break
    that, and would make a report fail to render over a bibliography file.

The risk of two models over one file is drift, so it is closed mechanically:
tests/test_causes_yaml.py loads causes.yaml through BOTH and asserts they agree
field for field. Neither can quietly grow a field the other does not see.

WHAT THIS LAYER IS NOT
======================
It is not a detector, not evidence, and not a diagnosis. pdm_core commits a
FAULT (BPFO/BPFI/BSF/FTF) from measured spectra; this file lists the CAUSES
known to produce that fault. No cause here is confirmed by a vibration reading —
batch1 is built from dismounted-bearing damage atlases and carries no cited
vibration observation with frequency content at all (knowledge/review/
b03_queue.md §A-0). The report says so in the section's own framing, and the
consistency contract enforces that the drafting model cannot upgrade it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: Same idiom as vib_agent/config.py's CONFIG_DIR: the data lives at the repo
#: root, the loader ships inside the package. parents[3] is the repo root from
#: src/vib_agent/knowledge/loader.py.
CAUSES_PATH = Path(__file__).resolve().parents[3] / "knowledge" / "causes.yaml"

#: The one heading this knowledge may appear under, in any report on either
#: path. It lives here rather than in report/ because three modules need to
#: agree on it and they must not agree by copying a string: report/generate.py
#: renders it, report/templates/_evidence.md.j2 prints it, and
#: agent/consistency.py finds the drafted section by it. The wording is load
#: bearing -- "hypotheses for analyst confirmation" is in the HEADING, not a
#: footnote, so a reader skimming section titles cannot mistake this for the
#: diagnosis.
CAUSE_HEADING = "Possible underlying causes — hypotheses for analyst confirmation"

#: Fault families that get a cause section. Deliberately the bearing families
#: only: batch1 is a bearing-cause batch, so there is nothing to say for an
#: imbalance or misalignment call and the section must not appear for one.
#: Mirrors knowledge.schema.BEARING_FAULT_FAMILIES (asserted equal in tests).
BEARING_FAULT_FAMILIES: frozenset[str] = frozenset(
    {"bearing_outer_race", "bearing_inner_race", "bearing_ball_spin", "bearing_cage"}
)

#: `consider:` is free prose for a reviewer, but it sometimes opens with an
#: explicit precedence directive. Only these two words are read as one; every
#: other leading token ("RING IDENTITY", "COLOUR IS NOT A DISCRIMINATOR HERE",
#: "TWO COLLISIONS") is neutral, ranked 0, and left in authored order. An
#: unrecognised directive is never guessed at.
_CONSIDER_RANK: dict[str, int] = {"FIRST": -1, "LAST": 1}

#: Ordering within a `consider` rank: a cause corroborated by more than one
#: document is offered before one resting on a single source, which is before
#: one that is our own inference.
_BASIS_RANK: dict[str, int] = {"triangulated": 0, "single_source": 1, "inferred": 2}


class CauseCitation(BaseModel):
    """The rendered half of a citation: which INDEX document, and where in it.

    `note` is deliberately NOT carried into the runtime view. It is a reviewer's
    description of what the source supports, written for the review record, and
    it is often long, hedged and full of source wording. Rendering it into an
    analyst report would put un-reviewed prose in front of a customer.
    """

    model_config = ConfigDict(extra="ignore")

    doc: str
    locator: str

    def rendered(self) -> str:
        """`[#01 §5 Damage and actions, Abrasive wear, p.66]` — the register
        reference an analyst can resolve against references/INDEX.md."""
        return f"[{self.doc} {self.locator}]"


class CauseObservation(BaseModel):
    """One discriminating observation, with the stream it is collected from."""

    model_config = ConfigDict(extra="ignore")

    observation: str
    stream: str
    how_to_collect: str
    citations: list[CauseCitation] = Field(default_factory=list)
    inferred: bool = False


class Cause(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cause_id: str
    fault_family: list[str]
    stage: str
    cause: str
    mechanism: str
    discriminating_evidence: list[CauseObservation]
    typical_actions: list[str] = Field(default_factory=list)
    citations: list[CauseCitation] = Field(default_factory=list)
    basis: str
    consider: str | None = None
    related: list[str] = Field(default_factory=list)

    def consider_rank(self) -> int:
        """-1 / 0 / +1 from a leading FIRST/LAST directive in `consider`."""
        if not self.consider:
            return 0
        first_token = self.consider.strip().split(None, 1)[0] if self.consider.strip() else ""
        return _CONSIDER_RANK.get(first_token.strip(".,:;").upper(), 0)

    def basis_rank(self) -> int:
        return _BASIS_RANK.get(self.basis, len(_BASIS_RANK))


class CauseBook(BaseModel):
    """The runtime projection of causes.yaml. `approval` is carried because the
    provenance of shipped knowledge is worth having in memory, not only on
    disk."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str
    session: str
    date: str
    approval: str
    entries: list[Cause]


def _read_yaml(path: Path) -> Any:
    import yaml

    return yaml.safe_load(path.read_text())


@lru_cache(maxsize=None)
def load_causes(path: str | Path | None = None) -> CauseBook:
    """Load and cache the cause book. Cached like load_config(): the file is
    static product knowledge, and re-parsing 1,500 lines of YAML per rendered
    report would be pure waste."""
    return CauseBook.model_validate(_read_yaml(Path(path) if path else CAUSES_PATH))


def lookup_causes(
    fault_family: str,
    stage: str = "any",
    *,
    book: CauseBook | None = None,
) -> list[Cause]:
    """Every approved cause that can produce `fault_family` at `stage`.

    Pure and deterministic: same arguments in, same list out, in the same order,
    with no I/O beyond the cached book. This is the function the agent tool and
    the report section both call, so that the drafted and deterministic paths
    cannot see different knowledge.

    FILTER
      * `fault_family` must be listed in the entry's own `fault_family`. An
        entry keyed to several families is one whose CITED SOURCE describes the
        damage in component-agnostic terms — the authoring schema makes that
        claim justify itself (`extends_families`), so honouring it here is not a
        widening.
      * `stage` matches when the entry's stage is `any` (stage-independent
        knowledge, which is all of batch1), when the entry names exactly this
        stage, or when the CALLER asks for `any` and so wants every stage.

    ORDER — three keys, all deterministic:
      1. the `consider:` directive (FIRST / LAST). This exists for
         rolling_element_subsurface_fatigue_normal_life, whose own `consider`
         reads "LAST. Weigh every other cause in this batch before this one" —
         it is the only entry that concludes nothing was done wrong, so offering
         it first would end the investigation the other thirteen exist to run.
      2. triangulated before single_source before inferred.
      3. authored order in causes.yaml, which groups by ring and is meaningful.

    Returns [] for a non-bearing family, an unknown family, or an empty book —
    never raises. The report renders no section on an empty list.
    """
    resolved = book if book is not None else load_causes()
    matched = [
        (index, entry)
        for index, entry in enumerate(resolved.entries)
        if fault_family in entry.fault_family
        and (entry.stage == "any" or stage == "any" or entry.stage == stage)
    ]
    matched.sort(key=lambda pair: (pair[1].consider_rank(), pair[1].basis_rank(), pair[0]))
    return [entry for _, entry in matched]


def lookup_causes_for(
    fault_families: list[str] | tuple[str, ...],
    stage: str = "any",
    *,
    book: CauseBook | None = None,
) -> list[Cause]:
    """lookup_causes over SEVERAL committed families at once, de-duplicated.

    An analysis can commit more than one bearing family, and the cause lists
    overlap heavily (eleven of the fourteen entries key to both races). Emitting
    one section per fault would print the same cause twice; emitting the plain
    union would put the `consider: LAST` entry in the middle, because it would
    sort last only within the first family's list. So the union is re-sorted on
    the same three keys, and the result reads as one ordered list however many
    families were committed.
    """
    resolved = book if book is not None else load_causes()
    order = {entry.cause_id: index for index, entry in enumerate(resolved.entries)}
    merged: dict[str, Cause] = {}
    for family in fault_families:
        for entry in lookup_causes(family, stage, book=resolved):
            merged.setdefault(entry.cause_id, entry)
    return sorted(
        merged.values(),
        key=lambda c: (c.consider_rank(), c.basis_rank(), order.get(c.cause_id, 0)),
    )
