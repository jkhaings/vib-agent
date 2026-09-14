# GAPS — paid, gated, and not-found

Companion to `INDEX.md`. Nothing in this file was downloaded. Prices observed **11 August 2026**; where a price is not publicly visible it is recorded as unknown rather than guessed.

Reconciled against the files on disk **12 August 2026** (Session REF-INTAKE) · **#46 filed and §0/§7 reconciled 13 August 2026** (Session REF-46).

---

## 0. Target-list reconciliation

The items the build asked for, against what is actually on disk. Status is one of
**present** · **gated** · **paid-pending** · **missing**.

| Target | Status | Where it stands |
|---|---|---|
| **SKF bearing damage guide** | ✅ **present** | INDEX #01 — *Bearing Damage and Failure Analysis*, PUB BU/I3 14219/3 EN, **106 pp**, from SKF's own media hub. Only the downsampled `_LOW_` render is published; text and tables intact, photographic plates reduced (§5). |
| **Timken damage guide** | ✅ **present** | INDEX #02 — order 5892, ©2015 repr. 2017, **40 pp**, from timken.com. No newer 5892 exists (§5). |
| **Schaeffler / FAG rolling bearing damage** | ⚠ **present, wrong edition** | INDEX #03 — on disk at **75 pp**, but it is **WL 82 102/2 ED** (FAG Bearings Corporation), *not* the **WL 82 102/3 EA (2001)** that was targeted. Two open problems: the edition, and the fact that **its retrieval host was never recorded**, so the official-publisher-domain rule is unproven for this one file. The /3 EA stays on the manual-download list — see §7. |
| **Wear-particle / ferrography material** | ✅ **present**, one gap | INDEX #05–#14b — 11 documents (Noria morphology and ferrography set, WearCheck PQ bulletins). The canonical **Wear Particle Atlas (#15) is still missing** — DTIC serves an HTML wrapper to automated clients (§3). |
| **Lab limit bulletins (WearCheck etc.)** | ✅ **present**, plus one gated | INDEX #19–#30b, #54 (WearCheck), #32–#34 (ALS), #31, #48–#53 (Bureau Veritas). **POLARIS remains gated** (§4) and holds the single most on-target title found anywhere. |
| **ISO 4406 explainer** | ✅ **present**, one gap | INDEX #35 Parker VEL1948 (**2 pp**, per-mL code table of record) · #36 HYDAC (**17 pp**, per-100-mL depth reference) · #38 Donaldson · #49 Bureau Veritas. **#37 Parker FDCB805UK still missing** — it carries the ISO/NAS/SAE cross-reference. |
| **ISO 15243** | 💰 **paid-pending** | Not owned, deliberately. CHF 204, link only — §1. A third edition is in flight, so a 2017 purchase has a finite shelf life. |
| **Randall & Antoni (2011)** | 💰 **paid-pending** | Not owned. DOI only, closed access — §2. One free institutional route (HAL) is worth a manual check. |
| **Ground truth — a physically inspected gearbox** | ✅ **present** (13 Aug 2026, Session REF-46) | INDEX #46 — NREL/TP-5000-54530, S. Sheng ed., **157 pp measured**, US-government-sponsored work. Two field oil-loss events, dynamometer retest, teardown; **Table 2.5 lists 12 actual damage instances**, of which **7 were agreed vibration-detectable**, against **eight independent blind diagnoses** of the same 30 data files. Retrieved via **OSTI**, not NREL — see §7. |

**Net: 7 of 9 targets satisfied, 1 satisfied in the wrong edition, 2 deliberately unpurchased.**
Four hand-downloads outstanding beyond the FAG edition, plus one gap #46 itself opened — see §7.

The ground-truth row is listed as a target because it is one: it was the sole ★★ item in §7 whose
absence blocked *validating* R1 rather than building it. It is now the only document in the library
that can tell us whether a detector is right, and the only one whose contents were content-verified
at intake rather than inherited from the compilation pass (INDEX §9).

### 0b. Newly indexed, licence unverified

**INDEX #56** — Seetharamaiah & Jabłoński, *Application for Selecting Filtering Parameters to Obtain
Envelope Spectra*, AGH University of Krakow, *Journal of the Polish Mineral Engineering Society*,
Jul–Dec 2025, **doi:10.29227/IM-2025-02-80**, 14 pp.

This file was on disk but appeared in no index, is absent from `fetch.sh`, and its retrieval host was
never recorded — the DOI is the only source of record. It is the library's only document on the
**vibration** side rather than the oil side, and directly adjacent to `bearing_rca.py`, so it is worth
keeping. ⚠ **Confirm the journal's open-access / reuse terms in a browser before citing or
redistributing anything from it.** Until then it is indexed with a caveat and nothing should be quoted
from it into `causes.yaml`.

---

## 1. PAID — standards (never download; purchase or do without)

### ISO 15243 — Rolling bearings — Damage and failures — Terms, characteristics and causes

| Field | Value |
|---|---|
| Current edition | **ISO 15243:2017**, Edition 2, published 2017-03 |
| Status | Published — but stage flagged **"to be revised"** as of 2025-01-27 |
| Pages | 53 |
| Price | **CHF 204** (iso.org quotes CHF only; no USD shown) |
| Official purchase | https://www.iso.org/standard/59619.html |
| USD route | ANSI Webstore **USD $335.00** (member $268.00) — https://webstore.ansi.org/standards/iso/iso152432017 — noticeably more than buying direct from ISO |
| ICS | 21.100.20 |

**Buy-timing note.** A third edition is actively moving: **ISO/WD 15243.2**, stage 20.60 (close of comment period), stage date 2026-04-28 — https://www.iso.org/standard/91356.html. A 2017 purchase has a finite shelf life. If the taxonomy structure is what you need rather than the normative text, **INDEX #04** (Noria, "Basic Wear Modes in Lubricated Systems") restates the ISO 15243 six categories and fifteen subcategories for free, and **INDEX #01** (SKF) organises its entire damage chapter around the ISO 15243 classification. Between those two you can build the taxonomy without the standard; you just cannot *cite* it normatively.

**Do not buy:** ISO 15243:2004 (Ed. 1) — withdrawn 2017-04-10, https://www.iso.org/standard/27042.html. Nor ANSI/ABMA/ISO 15243-2010, which is a US adoption of the superseded 2004 text.

### ISO 4406 — Hydraulic fluid power — Fluids — Method for coding the level of contamination by solid particles

| Field | Value |
|---|---|
| Current edition | **ISO 4406:2021**, Edition 4, published 2021-01 |
| Status | Published, stage 90.93 — confirmed current on 2026 systematic review |
| Pages | 6 |
| Price | **CHF 67** |
| Official purchase | https://www.iso.org/standard/79716.html |
| USD route | ANSI Webstore **USD $110.00** (member $88.00) — https://webstore.ansi.org/standards/iso/iso44062021 |
| ICS | 23.100.60, 75.120 |

**Assessment:** at 6 pages for CHF 67 this is the highest cost-per-page item on the list, and it is the normative source for the X/Y/Z code — it cannot be substituted for citation purposes. But the *content* you need is fully covered free: **INDEX #35** (Parker VEL1948) reproduces the complete range-code table 1–28 in particles/mL, and **INDEX #36** (HYDAC, Dec 2024) runs to >28 with component target codes. Both are now on disk. Buy only if vib-agent's output will make normative claims of ISO 4406 conformance.

**Do not buy:** ISO 4406:2017 (withdrawn, https://www.iso.org/standard/72618.html), :1999 (https://www.iso.org/standard/21463.html), :1987 (https://www.iso.org/standard/10311.html).

---

## 2. PAID — the reference paper

### Randall, R.B. & Antoni, J. (2011) — "Rolling element bearing diagnostics — A tutorial"

> Randall, R.B. and Antoni, J. (2011). "Rolling element bearing diagnostics—A tutorial." *Mechanical Systems and Signal Processing*, **25**(2), 485–520.

| Field | Value |
|---|---|
| DOI | **10.1016/j.ymssp.2010.07.017** |
| Publisher link | https://www.sciencedirect.com/science/article/abs/pii/S0888327010002530 |
| Access | **Closed/paywalled.** OpenAlex: `is_oa: false`, `oa_status: "closed"`, `best_oa_location: null`. The `/article/abs/` path confirms abstract-only public access. |
| Individual purchase price | **Not shown publicly.** ScienceDirect is robots-disallowed to automated fetch and Elsevier's own e-commerce help page documents the flow without stating a price — it is revealed only inside the purchase funnel. Do not assume the commonly-quoted $31.50/$35.95 figures. |
| Issue date | February 2011 (online 2010; OpenAlex records the publication year as 2010) |

**One legitimate free route worth one manual check:** a HAL deposit exists at **hal-01018742** (https://hal.science/hal-01018742), which would be the proper institutional-repository route via Antoni's French affiliation. HAL returned an access-denied wall on both automated attempts, so whether a full text is attached or the record is metadata-only is **unverified**. Worth thirty seconds in a browser before paying. No author-hosted UNSW copy surfaced.

Copies exist on ResearchGate, Academia.edu and Scribd. **Excluded — a copy existing there is not a license.**

**Free and topically adjacent, if you want the concepts without the paywall:** INDEX #47, DTIC AD-A291123 (DSTO, "A Review of Rolling Element Bearing Vibration") — still missing, see §7. Covers bearing kinematics, defect frequencies, signal processing and prognosis. Not a substitute for Randall & Antoni's rigour, but free — with the copyright caveat noted in INDEX (Australian Defence work, publicly releasable, *not* US public domain). **INDEX #56** (now on disk) covers envelope-band selection specifically, which is the part of the Randall & Antoni method chain vib-agent is closest to needing.

---

## 3. PAID — books worth buying

Ranked by usefulness to this specific build.

### Tier 1 — buy these

**Randall, R.B. — *Vibration-based Condition Monitoring: Industrial, Automotive and Aerospace Applications*, 2nd ed.**
Wiley, 2021 · print ISBN **9781119477556** · eText ISBN 9781119477655 · book DOI 10.1002/9781119477631
Publisher: https://www.wiley.com/en-us/Vibration+based+Condition+Monitoring:+Industrial,+Automotive+and+Aerospace+Applications,+2nd+Edition-p-9781119477556
Price: **not retrievable** — every wiley.com and onlinelibrary.wiley.com URL returned 403 to automated fetch across four attempts. Confirmed authorised-channel price: **USD $134.00** e-textbook, lifetime access, via VitalSource (Wiley's authorised eText distributor) — https://www.vitalsource.com/products/vibration-based-condition-monitoring-robert-bond-randall-v9781119477655
*Why:* same author as the tutorial above, and this is the extended worked-through version of that method chain — envelope analysis, spectral kurtosis, cepstrum-based discrete-frequency removal, order tracking. Exactly the pipeline a bearing-fault engine must implement, with the diagnostic reasoning attached rather than just the formulae. **If you buy one thing on this page, buy this** — it substantially covers the Randall & Antoni gap too.

**Toms, L.A. — *Machinery Oil Analysis: Methods, Automation & Benefits*, 3rd ed.**
STLE, 2008 · hardback, 506 pp · ISBN not listed by vendor
**USD $159.00** — https://store.noria.com/products/machinery-oil-analysis-methods-automation-benefits-3rd-edition
*Why:* the deepest single treatment of oil-analysis test methods and limit-setting — the half of the knowledge base that no vibration text covers at all, and precisely the half the free lab literature deliberately withholds (see §5).

### Tier 2 — buy if budget allows

**Bloch, H.P. & Geitner, F.K. — *Machinery Failure Analysis and Troubleshooting*, 4th ed.** (Practical Machinery Management for Process Plants, Vol. 2)
Butterworth-Heinemann / Elsevier, 2012 · 760 pp · ISBN 9780123860453
https://shop.elsevier.com/books/machinery-failure-analysis-and-troubleshooting/bloch/978-0-12-386045-3
Price: **not shown to automated fetch** (JS-rendered) — verify in a browser.
*Why:* the standard root-cause reference tying failure *appearance* to failure *mechanism* across bearings, seals, gears and couplings. It is the causal-reasoning layer that sits above ISO 15243's terminology — R2 material in book form.

**Scheffer, C. & Girdhar, P. — *Practical Machinery Vibration Analysis and Predictive Maintenance*, 1st ed.**
Newnes / Elsevier, 2004 · 272 pp · ISBN 9780750662758
https://shop.elsevier.com/books/practical-machinery-vibration-analysis-and-predictive-maintenance/scheffer/978-0-7506-6275-8
Price: **not shown to automated fetch** — verify in a browser. Only the 1st edition exists; there is no newer one.
*Why:* the practitioner counterpart to Randall — defect-frequency calculation, alarm limits and route-based collection expressed as rules, which converts cleanly into knowledge-base logic.

### Tier 3 — cheap supplements

- ***Oil Analysis Basics*, 2nd ed.** — Jim Fitch & Drew Troyer, Noria, 192 pp — **USD $57.00** — https://store.noria.com/products/oil-analysis-basics-second-edition. Best-value entry point for encoding oil-analysis alarm logic.
- ***The Practical Handbook of Machinery Lubrication*, 4th ed.** — Scott, Fitch & Leugner, Noria, 2012, 220 pp — **USD $70.00** — https://store.noria.com/products/the-practical-handbook-of-machinery-lubrication-4th-edition. Covers the lubrication-failure causes that surface as ISO 15243 damage modes.
- Full Noria oil-analysis list (also carries *Wear Debris Analysis* at USD $89.95): https://store.noria.com/collections/oil-analysis/books

### Special case — *Wear Particle Atlas* (INDEX #15, still missing)

**A free `.mil` copy appears to exist**, which would make this a significant find rather than a purchase.
- Free route: **DTIC AD-A125512**, "Wear Particle Atlas. Revised", 28 June 1982 — https://apps.dtic.mil/sti/pdfs/ADA125512.pdf (landing page https://apps.dtic.mil/sti/html/tr/ADA125512/index.html). US Navy work, so not under US copyright, and indexed in DTIC's public STI area.
- ⚠ **Unverified, and the automated fetch failed.** DTIC returns an HTML wrapper to bots; `fetch.sh` correctly rejected the 1.4 KB "Under Maintenance" page it got, which is parked in `~/Desktop/vib-agent-refs/_to_delete/`. Use the landing page in a browser and click "Open PDF". **Then open page 1 and confirm the distribution statement before relying on it.**
- Paid fallback if the 1982 scan quality is inadequate: Noria sells a print reprint at **USD $89.00** — https://store.noria.com/products/wear-particle-atlas (no ISBN or edition year given on the product page).

---

## 4. GATED — free but behind a registration/email form

No form was submitted and no personal information was entered anywhere during the compilation pass. Each of these is a manual download if you want it.

**Note:** the six Bureau Veritas sheets below (**INDEX #48–#53**) were subsequently obtained by hand and **are now on disk.** They are retained in this section because the gate is still the only route to them and their landing pages remain the source of record — `fetch.sh` cannot get them.

### POLARIS Laboratories
Every POLARIS technical bulletin sits behind a marketing form on `www2.eoilreports.com`. The public articles on polarislabs.com are free but were checked and contain **no numeric tables**.

| Document | Gated URL | Note |
|---|---|---|
| **Setting Wear Metal Flagging Limits** | http://www2.eoilreports.com/Setting-Wear-Metal-Flagging-Limits | **The most on-target title found anywhere for per-component wear-metal limits — and it is gated.** Free companion article (no table): https://polarislabs.com/setting-wear-metal-flagging-limits/ (11 Sep 2014) |
| Setting Fluid Property Flagging Limits | https://www2.eoilreports.com/Setting-Fluid-Property-Flagging-Limits | Free companion (no table): https://polarislabs.com/setting-limits-on-fluid-properties/ (26 Sep 2014) |
| How to Take an Oil Sample | https://www2.eoilreports.com/How-to-Take-an-Oil-Sample | INDEX #32 covers this ground free |
| How to Take a Coolant Sample | https://www2.eoilreports.com/How-to-Take-a-Coolant-Sample | Out of scope |
| Complete Test List | https://www2.eoilreports.com/CompleteTestList | Reference only |

### Bureau Veritas
The entire Technical Data Sheets library is form-gated at the landing-page level (name, email, phone, company, country). Library index: https://oil-testing.com/document-library/technical-data-sheets/

| Document | Gated landing page | On disk? |
|---|---|---|
| **Guide to Wear, Contaminant and Additive Metals** (2019) | https://oil-testing.com/document/guide-to-wear-contaminant-and-additive-metals/ — highest-value BV title for element→source mapping; no direct PDF found | ✅ INDEX #48 |
| Understanding an Oil Analysis Report (2019) | https://oil-testing.com/document/understanding-an-oil-analysis-report/ — the **2017 edition is openly served** at https://oil-testing.com/wp-content/uploads/2017/11/BV_Understanding-An-Oil-Analysis-Report_FINAL_11_8_2017.pdf (annotated specimen report, Normal/Monitor/Abnormal/Critical, **no published numeric thresholds**) | ✅ INDEX #51 |
| Understanding the ISO Cleanliness Code (2019) | https://oil-testing.com/document/understanding-the-iso-cleanliness-code/ | ✅ INDEX #49 |
| Comparative Viscosity Classifications | https://oil-testing.com/document/comparative-viscosity-classifications/ | ✅ INDEX #52 |
| The Basics of Particle Counting | https://oil-testing.com/document/the-basics-of-particle-counting/ | ✅ INDEX #50 |
| 7 Steps for Establishing an Oil Analysis Program | https://oil-testing.com/document/7-steps-for-establishing-an-oil-analysis-program/ | ✅ INDEX #53 |

⚠ **Fragility note:** several BV PDFs are nonetheless served openly from `/wp-content/uploads/` and bypass their own gate — including INDEX #31, the Preventive & Predictive Maintenance data sheet, the Grease Analysis white paper and the Basics of Coolant Analysis booklet. These are free today but the links are fragile; `fetch.sh` grabbed #31 while it was available.

### Des-Case
- **"Cracking the ISO Code to Lubricant Cleanliness"** whitepaper — https://www.descase.com/resources/iso-cleanliness/ — gated landing page, no table on the public page. Excluded; INDEX #35/#36 cover the same ground free and ungated.

---

## 5. SEARCHED AND NOT FOUND

**A free per-element, per-component-type wear-metal *condemning-limit* table.** The strongest form of your priority-5 target does not exist as a free publication on WearCheck's, ALS's, POLARIS's or Bureau Veritas's own sites. This is **a deliberate editorial position, not an oversight** — three separate WearCheck bulletins (TB15, TB53, TB57 = INDEX #23, #25, #24) argue explicitly and at length against publishing one, on the grounds that limits are machine-, oil-age- and environment-specific. TB15's own pull-quote reads *"Wear limit tables should be used as a guideline only."* POLARIS's equivalent document exists but is gated (§4).

*Closest free substitutes, all indexed:* WearCheck TB19 (#19, silicon and water by component type), TB48 Table 5 (#20, viscosity/moisture/silicon/soot by application, five severity bands), TB16 (#13, PQ limits for two component types), and above all **JOAP Vols 3 & 4 (#16, #17)** — which solve the problem by being equipment-model-specific rather than universal, are free and public, and **are now on disk**. That is the pattern to copy.

**NAVAIR 00-25-403** (Guidelines for the Naval Aviation RCM Process) — **no copy on any `.mil` or `.gov` host.** Searched navair.navy.mil, navy.mil, e-publishing.af.mil and DTIC. Available copies are everyspec.com (2005 ed.) and an AWS-hosted solicitation attachment via govtribe (1 June 2016 ed.). The mirrored copies show Distribution Statement A on the cover, but that could not be verified on an official host, so **the third-party mirrors were not used.** The official copy should be on www.navair.navy.mil (Naval Aviation Maintenance Program section) or requestable via DTIC/NATEC. Closest `.mil`-hosted substitute: **DoDM 4151.22 "Reliability Centered Maintenance"** — https://www.esd.whs.mil/Portals/54/Documents/DD/issuances/dodm/415122m.pdf (policy and process only, INDEX #55).

**A publicly released NAVSEA vibration-monitoring / machinery-condition-analysis technical manual** — none located. `S0570-AC-CCM-010/8010` surfaced as a candidate and is Distribution A on navsea.navy.mil, but on fetch it is the *Industrial Ship Safety Manual for Fire Prevention and Response* (129 pp) — **irrelevant, do not chase it.** The Joint Fleet Maintenance Manual volumes are Distribution A but carry no condition-monitoring diagnostic tables. NAVSEA's public CBM+ material is briefing slides only.

**Three WearCheck bulletins with no text layer** — content could not be verified during compilation, and the HTML mirrors are robots-blocked:
- **WZA049** "The Best Oil Analysis Programs Start with a Good Sample" — https://wearcheck.com/resources/techdoc/WZA049.pdf. ⚠ **Contradiction on record:** a document under this title *is* on disk as INDEX #54, obtained by hand and not via `fetch.sh`, but its identity could not be confirmed offline — its fonts are outlined to vector paths, so it has no text to extract and is not a scan either (INDEX §9). Settle it by opening the file, or `brew install poppler` and rasterising page 1. Until then INDEX #54 carries a ⚠ and ALS #32 / BV #31 pp.14–16 remain the trustworthy sampling guidance.
- **WZA059** "Talking Turbine Testing" — prime candidate for turbine condemning limits. Not on disk.
- **WZA001** "Particle Counting and Contamination Analysis in Fluid Power Systems" — prime candidate for ISO target cleanliness per hydraulic component. Not on disk.

**A free RPVOT / oxidation / varnish condemning-limit table** — none found. WearCheck TB44 "Predicting Remaining Useful Life" (Jan 2009, https://wearcheck.com/resources/techdoc/WZA044.pdf) discusses RULER, TAN, RPVOT and FTIR but publishes no rejection limits beyond "70–80% antioxidant depletion".

**A consolidated ALS technical-bulletin index** — does not exist. https://www.alsglobal.com/en/resources-and-downloads lists only geochemistry fee schedules; the oil bulletins live as unindexed files under `/-/media/ALSGlobal/Resources-Grid/Equipment-reliability/Technical-Bulletins/` and were reachable only via search. Only four ALS oil bulletins were located in total (three indexed); "Technical Bulletin #1" (AT1) returns 404. **None contains a numeric limit table.**

**A higher-resolution public SKF *Bearing Damage and Failure Analysis*** — the only version SKF publishes openly is the downsampled `_LOW_` / `pdf_preview_medium` render (INDEX #01). Text and tables are intact; photographic plates are reduced. No full-resolution public URL exists. If plate quality matters for R1 visual staging, request it from an SKF application engineer.

**A newer Timken 5892** — the resources landing page furniture reads 2023, but the PDF timken.com actually serves is the ©2015 / 2017 reprint (INDEX #02). No newer 5892 PDF exists on timken.com. The file is named `...-Brochure.pdf`; it is not a brochure — that was confirmed by reading it.

**Eaton/Vickers first-party ISO 4406 literature** — no explainer PDF on eaton.com. The historical Vickers contamination-control literature appears only on third-party re-hosts. Excluded. *(Note: the "Sperry Vickers contamination levels" tables referenced in NASA #43 Table 10-9 and #44 Table 2-6 reproduce this material inside a public-domain government document — that is the clean route to it.)*

**Pall as an ISO 4406 primary** — the only ungated first-party document is the *Industrial Pocket Book* (https://www.pall.com/content/dam/pall/industrial-manufacturing/literature-library/non-gated/POCKET_BOOK_EN_Standard.pdf, ©2006, 35 pp), whose ISO 4406 table is **incomplete (roughly codes 6–21)** and which gives no target codes per component. Not indexed; Parker and HYDAC are strictly better.

**A Noria article reproducing the full ISO 4406 range table** — surprisingly, none. "What Is the ISO Cleanliness Code?" (https://www.machinerylubrication.com/Read/28979/iso-cleanliness-code) references "Table 1" without rendering it. Hence Parker VEL1948 (#35) as the code table of record.

**Noria's Life Extension Table itself** — "Machine Life Extension Calculator" (https://www.machinerylubrication.com/Read/95/machine-life-extension) references it but **does not display it**; only qualitative data (OSU: 10× cleaner fluid → up to 50× pump life). The Chevron ISOCLEAN sheet (#42) publishes derived multipliers and is the practical stand-in.

**ASTM D7684 particle classification content** — https://www.machinerylubrication.com/Read/28859/lubricant-particles-characterization is free and ungated but **structurally truncated**: the headings "Particle Classifications, Causes and Actions" and "Severity Ranking" have no text beneath them (verified on a second targeted fetch). The payload is almost certainly in non-text images. **Not indexed** — INDEX #05, #09 and #39 carry that content instead. The ASTM D7684 standard itself is a paid document if you need it normatively.

---

## 6. Rules applied

- Official publisher sources only: manufacturer sites, standards bodies, commercial labs' own domains, `.gov` / `.mil`. Every URL in `fetch.sh` is on the publisher's own domain. **One exception is now on record:** INDEX #03 (FAG WL 82 102/2 ED) arrived without a recorded retrieval host, so its compliance with this rule is unproven — see §0 and §7.
- **Nothing was taken from Scribd, SlideShare, CourseHero, libgen, ResearchGate, Academia.edu, everyspec.com, or any forum or third-party PDF re-host** — several of the target documents were readily available on such hosts and were passed over. Third-party copies specifically rejected: Timken 5892 (mgis.com.au, bell.si, wellertruck.com), SKF (scribd, studylib, pdfcoffee, torontostle.com, MIT course page), NAVAIR 00-25-403 (everyspec, govtribe), Randall & Antoni (ResearchGate, Academia.edu, Scribd), Vickers/HYDAC contamination literature (airlinehyd, crossco, hyprofiltration, ccjensen, statewidehydraulics, torontech and others).
- Paid documents were never downloaded from anywhere — recorded here with official purchase page and price instead.
- No account created, no payment made, no form filled with personal information during the compilation pass. Gated items were recorded and skipped; the six BV sheets in §4 were later obtained by the operator directly.
- Instruction-like text encountered on fetched pages was treated as data and ignored.
- Unlicensed copies are quarantined **outside the repository** at `~/Desktop/vib-agent-refs/_unlicensed/` and are not part of the library — INDEX §8.

---

## 7. Still to obtain

Four hand-downloads outstanding from the original list (#15, #37, #47, #18), plus one edition
upgrade (#03 /3 EA), plus **one newly opened gap** (NREL/SR-5000-53062, below). Full URLs and
save-as names for the four are in `MANUAL-DOWNLOADS.md`; `./verify.sh` reports what is still
missing on every run.

**Retired 13 Aug 2026 — #46 NREL/TP-5000-54530 has landed.** It was the ★★ head of this table.
Recorded because the routing lesson generalises to the rest of the list: **`docs.nrel.gov` stayed
robots-blocked and was never the successful route — the document came from OSTI**, whose landing
page `https://www.osti.gov/biblio/1048981` (**DOI 10.2172/1048981**) is now its source of record in
INDEX. Every DOE-lab report in this table has an OSTI mirror; try OSTI *first* rather than the
lab's own `docs.` host. The exact full-text URL was not recorded at retrieval, so the row cites the
landing page and DOI rather than a fabricated direct link — the #03/#56 lesson, applied on time for
once.

**New gap opened by that same intake.** #46 §2.5 does not perform the teardown; it summarises one
and cites **NREL/SR-5000-53062** — Errichello, R.; Muller, J., *Gearbox Reliability Collaborative
Gearbox 1 Failure Analysis Report*, Geartech / NREL, February 2012. That is the actual failure
analysis behind Table 2.5, and it is the document that would let us map a damage instance to a
mechanism rather than to a one-line mode label. It is **not in the library** and is listed below.
(Note: #46's own reference list prints the number as "NREL/SR-5000-530262", a typo — the report
number is **53062**, which is what its own URL uses.)

| Priority | Item | Why it matters | Blocker |
|---|---|---|---|
| ★★ | **NREL/SR-5000-53062** — GRC Gearbox 1 Failure Analysis Report (Errichello & Muller, Feb 2012) | The teardown behind #46 Table 2.5. #46 gives 12 damage instances as mode labels; this gives the failure analysis they were derived from. Without it the ground truth is a list, not a chain of causation. | Not yet attempted. Try **OSTI first** (see above), then `docs.nrel.gov/docs/fy12osti/53062.pdf`. Needs an INDEX number on arrival |
| ★★ | **#15 Wear Particle Atlas** (DTIC AD-A125512) | Canonical visual taxonomy of wear-particle morphology — the oil-side analogue of ISO 15243 | DTIC serves an HTML wrapper to bots. Also: **check the distribution statement on page 1** |
| ★★ | **#03 FAG WL 82 102/3 EA** | A /2 ED copy is on disk; the /3 EA is the targeted edition, and would also give a *publisher-sourced* file to replace one whose host is unrecorded | schaeffler.com balked at `curl` |
| ★ | **#37 Parker FDCB805UK** | p.14 ISO / NAS 1638 / SAE cross-reference — best free cross-standard conversion on a first-party site | blocked automated fetch |
| ★ | **#47 DTIC AD-A291123** | Bearing kinematics, defect frequencies, signal processing, prognosis — the free stand-in for Randall & Antoni | same DTIC wrapper. ⚠ **not** US public domain |
| — | **#18 JOAP Vol. 1** | Theory and sampling only; no limit tables. Skip if short on time — Vols 3 and 4 are the ones that matter and both are on disk | blocked automated fetch |
