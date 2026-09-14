# INDEX.md — the shipped cause-illustration register

This file is the **register the product reads at render time**
(`report/cause_art.py` parses it). It is not documentation about the drawings;
it *is* the mapping, and there is deliberately no second copy of it in code —
two hand-maintained copies of the same table is how a picture ends up beside
the wrong cause.

## Provenance

Every drawing here is an **original schematic, generated from parametric
geometry** by `design/report_v2_proto/make_cause_art.py` — circles, arcs and
lines computed from a handful of numbers. Nothing is copied, traced or adapted
from `references/`, from a bearing manufacturer's catalogue, or from any other
source. Delete the SVGs, re-run that script, and they come back identical; that
is the provenance claim, and it is checkable rather than asserted.

## The two rules that are not style choices

1. **Every illustration is captioned, in the report, with exactly:**

   > Schematic illustration — not derived from your data.

   The caption lives in ONE place in the code (`report/cause_art.py::DISCLAIMER`)
   and is asserted against the generator's own copy, so the three modules that
   have to agree on it cannot drift.

2. **No fallback drawing, ever.** A cause with no row here renders **no image
   element at all**. It never falls back to a generic schematic: a picture of
   the wrong mechanism beside the right words is worse than no picture, because
   the reader takes it as evidence — which is exactly what this section's own
   heading ("hypotheses for analyst confirmation") exists to prevent.

## Causes → asset

Fourteen of the fourteen `cause_id`s in `knowledge/causes.yaml`, in file order.

| `cause_id` | asset |
|---|---|
| `outer_race_contamination_abrasive_wear` | `abrasive-wear.svg` |
| `outer_race_inadequate_lubricant_film` | `lubricant-film-loss.svg` |
| `outer_race_housing_fit_creep` | `housing-creep.svg` |
| `outer_race_oval_clamping_overload` | `housing-ovality.svg` |
| `outer_race_moisture_corrosion` | `moisture-corrosion.svg` |
| `outer_race_electrical_current_erosion` | `electrical-erosion.svg` |
| `inner_race_misalignment_edge_loading` | `misalignment-angular.svg` |
| `inner_race_mounting_impact_brinelling` | `mounting-brinelling.svg` |
| `inner_race_creep_on_shaft_seat` | `shaft-seat-creep.svg` |
| `inner_race_excessive_axial_load` | `axial-overload.svg` |
| `rolling_element_debris_denting` | `debris-denting.svg` |
| `rolling_element_cage_wear_grooving` | `cage-wear.svg` |
| `rolling_element_subsurface_fatigue_normal_life` | `subsurface-fatigue.svg` |
| `vibration_false_brinelling` | `false-brinelling.svg` |

## Drawn but NOT shipped

Five further schematics exist under `design/report_v2_proto/assets/causes/` and
are **deliberately not in this package**, per the operator's ship list for
Session V2-WIRE:

| asset | why it is not here |
|---|---|
| `bearing-anatomy.svg` | belongs to a report SECTION rather than to a `cause_id`; no slot was built for section art |
| `staging-progression.svg` | same |
| `misalignment-parallel.svg` | **no `cause_id` exists for this mechanism.** `knowledge/loader.py` restricts the cause section to `BEARING_FAULT_FAMILIES`, and every entry in `causes.yaml` is a bearing-damage mechanism |
| `imbalance.svg` | same |
| `looseness.svg` | same |

The last three become mappable the day a non-bearing cause batch is authored and
approved. **Inventing a cause entry to hang a drawing on is exactly what the
references doctrine forbids** — a cause with no operator-approved indexed source
does not ship, and a drawing is not a source.
