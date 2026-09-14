# b03 queue — batch1 items NOT applied in R2-B0.2

Session R2-B0.2 applied the citation-check report (`batch1_citecheck.md`) to
`knowledge/candidates/batch1_bearing_causes.yaml` under two rules:

* **Truth items** — every corrected locator in §7.3–§7.5, every NOT_FOUND or
  unsourced clause, every note-precision fix — applied without discretion.
* **Enrichment items whose page evidence is already transcribed in the report**
  and named in the session brief — applied.

Everything else is here. Nothing in this file has been applied to the candidate
entries, and nothing here should be applied without the operator's decision.

Two sections, because they cost different things:

* **§A — needs fresh page reads.** Cannot be settled from the report alone. Each
  one names the document and the pages to open.
* **§B — transcribed but deliberately not applied.** The evidence is already in
  the report; the reason for holding is judgment, not information. Each row says
  which.

---

## §A — needs fresh page reads

### A-0 — the headline. Re-source the 22 inferred observations, or accept them as ours.

This is E-0 from the citecheck report, in its *expensive* form. R2-B0.2 took the
cheap-and-honest half: every unsupported observation is now marked
`inferred: true` and is visible as our reasoning. That closes the honesty defect.
It does not close the **evidence** defect.

Post-fix the batch stands at **22 of 68 observations inferred**, and they cluster
exactly where the product actually measures:

| Stream | n | cited | inferred |
|---|---|---|---|
| visual | 36 | 27 | 9 |
| history | 15 | 12 | 3 |
| oil | 8 | 3 | 5 |
| thermal | 2 | 2 | 0 |
| **vibration** | **7** | **2** | **5** |

The two cited vibration observations are both the same SKF Table 2 sentence
("increased noise and vibration levels"), a level symptom, on the two creep
entries. **No entry in batch1 carries a cited vibration observation with any
frequency content at all.**

The library already holds the documents. Batch1 cites none of them:

* **#03 (FAG /2 ED)** — vibration procedures, shock value, envelope-adjacent
  material. Cited by two entries, and only for temperature and the operating-data
  checklist.
* **The oil shelf** — **#05–#14b** (wear-particle morphology and ferrography),
  **#22 / #33 / #48** (element → source). **Not cited anywhere in batch1.**
* **Lab limit bulletins** — the WearCheck TB set, ALS, Bureau Veritas. Needed for
  A-2 below.

Batch1 is a bearing-damage batch built only from bearing-damage atlases. That is
the real finding, and it is not fixed by a flag.

**Work:** read #03 §1.2 and the oil shelf, then re-author the five vibration and
five oil inferred observations as cited ones. Anything that cannot be sourced
stays `inferred: true` — which is then a *result*, not an omission.

### A-1 — sibling entries the sources support and batch1 has no key for

Each needs the page read plus a new entry authored to schema v1.2.

| Proposed entry | Source already located | Why it is not just an edit |
|---|---|---|
| cage wear from inadequate lubrication | #01 p.67 (angular contact ball bearing cage, pocket wear "due to inadequate lubrication and vibration", cage bars worn away) | This is the citation **removed** from `rolling_element_cage_wear_grooving` in R2-B0.2. It is a different cause of cage damage, currently keyed nowhere. |
| moisture corrosion on inner ring / cage | #02 p.9 figs. 13 (cylindrical INNER ring), 15 (ball bearing INNER ring and CAGE) | `outer_race_moisture_corrosion` extends to the inner race but not the cage, and the outer-race localisation rests on one #04 p.7 line about submerged lower portions. |
| false brinelling from in-service reversing oscillation | #02 p.20 ¶3 — "positions that encounter very small reversing angular oscillation (less than one complete rotation of the rolling element)" | A **third duty class** that produces the same damage with no idle or transport history. `standstill_vibration_false_brinelling`'s history observation would miss it entirely, and the entry is named for standstill. |

### A-2 — a lab limit bulletin for the water-content observation

`outer_race_moisture_corrosion`'s oil observation ("a moisture level above the
component-type limit, reported as dissolved / emulsified / free") is
`inferred: true` because neither #02 p.9 nor #04 p.7 gives a concentration limit
or uses the dissolved/emulsified/free split. #04 p.7's only number is that
corrosion rate roughly doubles per 10 °C.

INDEX's lab-bulletin shelf is where this lives. **Read before citing:** INDEX §7
records that the labs deliberately refuse to publish universal condemning limits,
so the citable claim may be the *component-type dependence itself* rather than a
number.

### A-3 — the two `disagreement` fields rest on #02 p.6, which no checker read

Both `outer_race_inadequate_lubricant_film` and
`rolling_element_subsurface_fatigue_normal_life` carry a `disagreement` citing
#02 p.6 statistics — "about 90 percent of prematurely failed bearings", "roughly
half never reached calculated life". Those numbers drove a real design decision
(`consider: LAST` on the subsurface-fatigue entry).

p.6 was seen only in an **off-by-one check** during verification, never graded.
And `disagreement` carries no citation under any schema version, so nothing
forced it. **Read #02 p.6 and either cite the figures or restate them.**

### A-4 — `inner_race_excessive_axial_load`, extension to multi-row bearings

#01 p.66 bottom-left gives an axial-load visual signature for a two-row spherical
roller bearing: one raceway track discoloured and wider, the other narrower and
matt. That would meet the v1.1 extension gate — except the caption sits under
**Abrasive wear**, and attributes the discolouration to inadequate lubrication and
heat generation. The section placement has to be resolved on the page before this
can be cited for an axial-load entry.

---

## §B — transcribed, deliberately not applied

Information is not the blocker. Judgment is.

| # | Entry | Item | Why held |
|---|---|---|---|
| B-1 | both creep entries | E-3: #43 Table 4-5 lists `Poor Fit-up` as a Cause under Fatigue → Excessive Load, which would lift `inner_race_creep_on_shaft_seat` off `single_source` and retire its review question | **A two-word table cell is thin ground for claiming corroboration.** Upgrading `basis` on that is authority inflation — precisely what the citecheck exists to catch. Recorded in the entry's own `review_question`. Operator's call. |
| B-2 | `rolling_element_cage_wear_grooving` | Add #01 p.66: cage pocket wear cuts a groove into the **INNER RING** on either side — a stronger and more on-point discriminator than the removed p.67 photo | Adding it re-introduces a second document and would flip `basis` back to `triangulated` in the same session that deliberately downgraded it. Decide the basis question first. |
| B-3 | `outer_race_housing_fit_creep` | #01 p.90–91 case study: fretting corrosion on the outer ring **side faces** used as independent confirmation of creep; #01 p.51 places fretting corrosion at ISO 5.3.3.2 | Would make the entry span three ISO damage classes (abrasive p.67, adhesive p.68, fretting p.90). That is a scope decision about whether the entry is keyed to a *mechanism* or an *appearance*. |
| B-4 | `outer_race_contamination_abrasive_wear` | Widen #04 to pp.4–6 (the Abrasive Wear box: two-body/three-body diagrams, "scratch marking, scoring, furrows, grooves and polishing", influencing factors) and #02 to pp.7–8 | Locator widening only; no claim changes. Low value, non-zero churn. |
| B-5 | `outer_race_contamination_abrasive_wear` | Competing cause: **two-body abrasion** produces the same distributed cutting with no contaminant particle involved (#04 p.6) | New differential content. Belongs in a `consider:` — but the entry is already the batch's longest and this is a *neighbouring cause*, which argues for a sibling entry, not a note. |
| B-6 | `outer_race_contamination_abrasive_wear` | **Product-relevant confound:** abrasive wear increases endplay/internal clearance and "can create misalignment in the bearing" (#02 p.7 sidebar) — a contamination-worn bearing can masquerade as a misalignment call | Strongest unapplied item in the file for a *vibration* product: it is a cross-family false-positive path our differential does not model. Held only because it argues for a change in `pdm_core`'s interaction rules, not in a YAML note. **Recommend taking this one first.** |
| B-7 | `outer_race_inadequate_lubricant_film` | Discriminator caution: contamination-initiated abrasive wear and inadequate film converge on the same visual (broad surface distress → advanced spalling), which is why #01 p.64's captions split as they do | Overlaps B-6 and B-5; all three are the same underlying point about visual non-specificity. Decide once. |
| B-8 | `outer_race_moisture_corrosion` | Component scope: #02 p.9's figures span inner ring, cage and outer race, so the entry name implies a localisation the figures do not establish | Superseded by A-1's sibling-entry proposal — resolve there, not by re-scoping this entry. |
| B-9 | `inner_race_misalignment_edge_loading` | p.63 and p.65 sit under **different ISO sub-modes** (Subsurface vs Surface initiated fatigue), which changes which Actions #01 itself prescribes — selection/mounting vs lubrication/contamination | Real and cited. Held because it implies the entry is two causes wearing one key, which is a split decision, not a note. |
| B-10 | `inner_race_excessive_axial_load` | Same-plate competing cause: #01 p.65 bottom-left distinguishes excessive-misalignment overload from axial overload by load-zone count | Already covered in substance by the COLLISION note on `inner_race_misalignment_edge_loading`, and the two entries are `related`. Duplicating it is churn. |
| B-11 | `rolling_element_subsurface_fatigue_normal_life` | #01 p.64's **Surface** initiated fatigue Actions are entirely operational (lubrication, surface separation, contamination) against p.63's entirely non-operational Actions — the sharpest subsurface-vs-surface discriminator in the document | Adds a fourth citation to an entry that already carries three, for a discriminator between two *modes* rather than between two causes. Better placed in whatever entry covers surface-initiated fatigue. |
| B-12 | `inner_race_mounting_impact_brinelling` | `typical_actions`: "Treat any bearing that has been hammered as scrap" is conservative but uncited | Schema-invisible: `typical_actions` and `disagreement` carry **no sourcing mechanism at any version**. See B-13. |
| B-13 | schema | **v1.3 candidate:** `typical_actions` and `disagreement` are free prose with no citation slot, so the R2-CITECHECK finding is only half closed — authority-by-adjacency can still happen in those two fields | v1.2 moved sourcing down to the observation. The same argument applies to actions and to disagreements, and A-3 is a live example of it biting. Not attempted this session: it re-shapes every entry again. |

---

## Accounting

The citecheck edits queue holds **93 items** — E-0 (§8.1), E-1…E-5 (§8.2) and
87 agent rows (§8.3). This file holds **18** of them: A-0…A-4 and B-1…B-13.

That does not make the arithmetic "93 − 18 = 75 applied", and it should not be
read that way. Several §8.3 rows bundle two or three asks (a locator correction
*and* a scope question *and* a suggested sibling entry), so a row can be applied
in part and queued in part — A-1's cage sibling and B-2's #01 p.66 both come out
of the one `rolling_element_cage_wear_grooving` row whose basis downgrade *was*
applied. Every truth item was applied in full; every named enrichment item was
applied in full; what is deferred is enumerated above rather than counted.

The candidate
file is still `status: awaiting_operator_review`; `causes.yaml` still does not
exist; nothing has merged.
