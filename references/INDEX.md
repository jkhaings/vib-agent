# INDEX — vib-agent reference library

Reference corpus for the vib-agent cause-knowledge layer (R1 staging / R2 causes) and the oil-analysis core.

**Compiled** 11 Aug 2026 · **organised** 12 Aug 2026 · **brought into the repo and reconciled against the files on disk** 12 Aug 2026 (Session REF-INTAKE) · **#46 verified and filed** 13 Aug 2026 (Session REF-46)

**Sourcing rule:** official publisher sources only — manufacturer sites, standards bodies, commercial labs' own domains, `.gov` / `.mil`. Nothing from Scribd, SlideShare, CourseHero, libgen, Anna's Archive, ResearchGate, or any third-party re-host. Paid documents are not downloaded; they live in `GAPS.md` with purchase links.

**This file and `GAPS.md` are the only two files in `references/` that are committed.** The PDFs are gitignored and never enter the repository — see the References section of `CLAUDE.md`.

---

## Status

| | |
|---|---|
| **56** | PDFs on disk, every one magic-byte checked and page-counted from the file itself |
| **4** | symlinks, all resolving (the HYDAC dangler is fixed) |
| **4** | still to fetch by hand → `MANUAL-DOWNLOADS.md` (all free and official; they block bots, not people). `./verify.sh` prints **5** for the same set — it counts the #03 /3 EA edition upgrade, which this table tracks on its own row below. |
| **1** | present but the **wrong edition** — #03, see its row |
| **1** | present but **identity unconfirmed** — #54, see §9 |
| **0** | bad or corrupt files |

Quarantined unlicensed copies are **outside the repo** at `~/Desktop/vib-agent-refs/_unlicensed/` — see §8.

**Legend** — ✅ on disk, verified · ⚠ on disk with a caveat, read the row · ⬜ missing, see `MANUAL-DOWNLOADS.md`

**Feeds** — **R1** fault staging · **R2** cause knowledge · **OIL** numeric limits/codes · **GEN** background

**Every title links to its source URL.** An unlinked title means the document was obtained by hand and its retrieval URL was never recorded; the row says so.

---

## Layout

```
references/                 (gitignored except INDEX.md and GAPS.md)
├── INDEX.md              this file                                    [committed]
├── GAPS.md               paid · gated · searched-and-not-found        [committed]
├── MANUAL-DOWNLOADS.md   what is still outstanding, with save-as names
├── fetch.sh              bulk downloader (already run)
├── verify.sh             re-verify tree, rewrite PAGECOUNTS.tsv — downloads nothing
├── PAGECOUNTS.tsv        measured page counts
├── VERIFY.log            fetch.sh run log
├── bearing-damage/       damage taxonomies — the R2 backbone
├── wear-particles/       morphology, ferrography, debris — the R1 oil-side spine
├── oil-limits/           numeric thresholds, wear metals, cleanliness codes
├── lubrication/          symlinks into the canonical copies (no duplicate files)
└── general-pdm/          public-domain government handbooks + one journal paper (#56)

~/Desktop/vib-agent-refs/   (outside the repo, deliberately)
├── _unlicensed/          8 quarantined copies — see §8
├── _to_delete/           2 saved HTML error pages, safe to remove
└── _failed/              empty
```

---

## 1. `bearing-damage/` — damage taxonomies

| | # | Title | Publisher | Edition / year | Pages | Feeds | Coverage |
|---|---|---|---|---|---|---|---|
| ✅ | 01 | [**Bearing Damage and Failure Analysis**](https://cdn.skfmediahub.skf.com/api/public/093168a92d25cc46/pdf_preview_medium/14219_3_EN_-_Bearing_failures_LOW_pdf_preview_medium.pdf) | SKF | PUB BU/I3 14219/3 EN, 3rd ed. | **106** | R1 R2 | **The backbone.** Bearing life & failures · inspection and troubleshooting · **path patterns** · **ISO 15243 failure-mode classification** · damage and actions · case studies. The path-pattern chapter is the best free treatment anywhere of reading load-zone marks to infer mounting and loading cause. Troubleshooting matrix carries 44+ numbered solution codes. |
| ✅ | 02 | [**Bearing Damage Analysis with Lubrication Reference Guide**](https://www.timken.com/wp-content/uploads/2017/05/5892_Bearing-Damage-Analysis-Brochure.pdf) | Timken | order 5892, ©2015 repr. 2017 | **40** | R1 R2 | Second independent taxonomy — 15+ damage categories, photographed. **Lubrication reference section starts p.23.** Glossary, nomenclature, temperature guidelines, conversion tables. Served under a `…-Brochure.pdf` filename; it is not a brochure — confirmed by reading it. |
| ⚠ | 03 | **Rolling Bearing Damage — Recognition of damage and bearing inspection** | **FAG Bearings Corporation** | **WL 82 102/2 ED** — the *earlier* edition | **75** | R1 R2 | Third independent taxonomy, and what makes the three-way cross-check possible today. Unusual operating behaviour · securing damaged bearings · **evaluation of running features on dismounted bearings** · FAG inspection methods. Heavy photographic atlas. ⚠ **Two caveats.** (1) This is **/2 ED**, not the **WL 82 102/3 EA (2001)** that `MANUAL-DOWNLOADS.md` targets — treat any clause-level citation as edition-specific. (2) The retrieval host was never recorded and the filename did not match the Schaeffler URL, so **the publisher-domain sourcing rule is unproven for this file.** The /3 EA remains an open item in `GAPS.md`. |
| ✅ | 04 | [**Basic Wear Modes in Lubricated Systems**](https://www.machinerylubrication.com/Read/1375/wear-modes-lubricated) | Noria | n.d. (attrib. R. Llewellyn, NRC Canada) | **17** | R2 | Free restatement of the **ISO 15243:2004 classification** — 6 categories, 15 subcategories. **This is how you get the taxonomy structure without owning the standard.** |

---

## 2. `wear-particles/` — morphology, ferrography, debris

| | # | Title | Publisher / author | Year | Pages | Feeds | Coverage |
|---|---|---|---|---|---|---|---|
| ✅ | 05 | [**Deciphering Important Visual Features of Wear Particles**](https://www.machinerylubrication.com/Read/32037/deciphering-visual-features-wear-particles) | Noria — Jim Fitch | n.d. | **16** | R1 R2 | Wear Particle Contact Dynamics — shape, texture, edge character and colour tinting → contact condition and lubrication regime, tied to the Stribeck curve. **In-page table:** contact-dynamics zone → wear mode → particle characteristics. |
| ✅ | 06 | [**Anatomy of Wear Debris**](https://www.machinerylubrication.com/Read/29537/wear-debris-anatomy) | Noria — Bennett Fitch | n.d. | **20** | R1 | **Morphology with dimensions.** Six classes with size data — platelets (thickness 1/10–1/30 of lateral), spheres (<10 µm), wire/curl cutting chips, chunks, non-ferrous corrosion (submicron), ferrous oxides. Overall range <1–200 µm. |
| ✅ | 07 | [**Analytical Ferrography — Make It Work For You**](https://www.machinerylubrication.com/Read/5/analytical-ferrography) | Noria — Barrett & McMahon (Insight Services) | n.d. | **20** | GEN R2 | Ferrogram substrate, magnetic separation, particle ID by size/composition/shape/colour/magnetism, bichromatic filter for metallic vs organic. States the ≥30 µm severe threshold. Two costed case studies. |
| ✅ | 08 | [**Determining Fatigue Wear Using Wear Particle Analysis Tools**](https://www.machinerylubrication.com/Read/526/fatigue-wear-particle-analysis) | Noria — Dr. Jian Ding (Lubrosoft) | n.d. | **25** | **R1** | **The R1 spine on the oil side.** Rolling-element fatigue micropitting → deep spalling, four fatigue particle types (microspall, laminar, chunky, spherical) and a 5-level severity atlas. |
| ✅ | 09 | [**How to Prevent Equipment Failures with Wear Debris Analysis**](https://www.machinerylubrication.com/Read/31129/wear-debris-analysis) | Noria — M.D. Holloway (ALS) | n.d. | **12** | R2 | **In-page table:** three wear categories (fluid/particle, sliding/rolling/impact, chemical) against mechanisms. |
| ✅ | 10 | [**Maximizing Fault Detection in Rotating Equipment**](https://www.machinerylubrication.com/Read/69/rotating-equipment-wear-debris) | Noria — Jim Fitch | n.d. | **15** | GEN | Programme level — how sampling location and data quality dominate results; combining spectroscopy + ferrography + microscopy to localise a fault. |
| ✅ | 11 | [**Wear Analysis**](https://www.machinerylubrication.com/Read/382/wear-analysis) | Noria | n.d. | **16** | OIL GEN | **Read before designing the oil core.** Elemental spectroscopy is blind above ~3–8 µm and reliable only below ~1 µm; ferrous density keys on 5 µm; particle counting at >4/>6/>14 µm. Explains why no single test sees all wear. |
| ✅ | 12 | [**Condition Monitoring and Predictive Analysis of Tribosystems by Wear Debris**](https://www.machinerylubrication.com/Read/717/condition-monitoring-predictive-analysis-of-tribosystems-by-wear-debris) | Noria — Markova, Myshkin, Grigoriev | n.d. | **18** | R2 | Independent six-type taxonomy (rubbing, laminar, fatigue chunk, cutting, spherical, dark/red oxides). Cross-check against #06. |
| ✅ | 13 | [**TB16 — Debris Analysis / The Particle Quantifier**](https://wearcheck.com/virtual_directories/Literature/Techdoc/WZA016.htm) | WearCheck | Sept 1999 | **6** | OIL | PQ index — magnetic, size-independent, complements ICP's ~8–10 µm ceiling. **Numeric:** PQ failure limits by component (hydraulic 25, conveyor gearbox 230) + ISO 4406 particles/mL → scale-number table. *Rendered from the HTML mirror; the published PDF is an image-only scan.* |
| ✅ | 14a | [**TB24 — Detecting Particles in Oil, Pt 1**](https://wearcheck.com/resources/techdoc/WZA024.pdf) | WearCheck | May 2002 | **4** | OIL | Fullest free description of the PQ ferrous debris monitor; MPE observation grid. |
| ✅ | 14b | [**TB25 — Detecting Particles in Oil, Pt 2**](https://wearcheck.com/resources/techdoc/WZA025.pdf) | WearCheck | Sept 2002 | **6** | OIL GEN | Ferrography, laser optical counting, shape classification, ICP. **Table 1 maps the ISO standards** (4402, 4406 1987 vs 1999, ACFTD vs ISO 11171 MTD); Table 2 is a high-lead/high-silicon decision tree. |
| ⬜ | 15 | [**Wear Particle Atlas (Revised)**](https://apps.dtic.mil/sti/html/tr/ADA125512/index.html) | US NAEC (Anderson) · DTIC AD-A125512 | 1982 | ? | R1 R2 | Canonical visual taxonomy of wear-particle morphology — the oil-side analogue of ISO 15243. **Not on disk:** DTIC serves an HTML wrapper to automated clients, and the saved result is a 1.4 KB "Under Maintenance" page (correctly rejected, parked in `~/Desktop/vib-agent-refs/_to_delete/`). ⚠ **Confirm the distribution statement on page 1 before relying on it** (`GAPS.md` §3). |

---

## 3. `oil-limits/` — thresholds, wear metals, cleanliness codes

### 3a. The JOAP volumes — the densest numeric source in the library

Both primary volumes **are now on disk** — they were marked missing until Session REF-INTAKE found them saved under their raw download filenames (`33-1-37-4.pdf`, `33-1-37-3.pdf`).

| | # | Title | Publisher | Edition / year | Pages | Feeds | Coverage |
|---|---|---|---|---|---|---|---|
| ✅ | 16 | [**JOAP Manual Vol. 4 — Non-Aeronautical**](https://www.tinker.af.mil/Portals/106/Documents/Technical%20Orders/33-1-37-4.pdf) | Joint Navy/Army/USAF/USCG · NAVAIR 17-15-50.4 / TM 38-301-4 / T.O. 33-1-37-4 | 15 Jul 2020, ch.1 1 Jul 2022 | **594** | **OIL** R2 | ★ **The primary oil-limit source, and it is free and public.** WP 004 00–209 00: **per-equipment Wear Metal Evaluation Criteria Tables, numeric ppm, Normal/Marginal/High/Abnormal** — diesels (Cat, Cummins, Detroit, EMD), transmissions (Allison, Clark, Twin Disc), hydraulics, gas turbines, marine gearing, ship service generators. Distribution Statement A. Title page verified verbatim. |
| ✅ | 17 | [**JOAP Manual Vol. 3 — Aeronautical**](https://www.tinker.af.mil/Portals/106/Documents/Technical%20Orders/33-1-37-3.pdf) | same · NAVAIR 17-15-50.3 / T.O. 33-1-37-3 | 30 Apr 2018, ch.3 15 Jun 2022 | **454** | **OIL** R2 | Same structure, aero equipment. WP 002 00 **Decision Making Guidance Table** maps value + trend → recommendation code: a directly encodable rule set. Wear metal → component mapping (Fe/Ag/Cu → gearbox bearings). Dist A. Title page verified verbatim. |
| ⬜ | 18 | [**JOAP Manual Vol. 1**](https://www.tinker.af.mil/Portals/106/33-1-37-1.pdf) | same · NAVAIR 17-15-50.1 / T.O. 33-1-37-1 | rev. 15 Nov 2024 | 62 | GEN | Theory, sampling, ferrography intro. No limit tables — points to Vols 3 and 4. Doctrinal framing. Lowest-priority of the outstanding five. |

### 3b. WearCheck technical bulletins

| | # | Title | No. | Date | Pages | Feeds | Coverage |
|---|---|---|---|---|---|---|---|
| ✅ | 19 | [**Greek for Beginners, Pt 1**](https://wearcheck.com/resources/techdoc/WZA019.pdf) | TB19 | Sept 2000 | **8** | **OIL** | **Best per-component limit table on any lab's free site.** Silicon by component: engine 25 ppm · drivetrain 100 · hydraulic/compressor/turbine 35–45 · auto transmission 35–45. Water by component: engine 0.0% · drivetrain 1.0% · transmission 0.5% · hydraulics 0.5%. |
| ✅ | 20 | [**Limits — The Robots of Oil Analysis**](https://wearcheck.com/resources/techdoc/WZA048.pdf) | TB48 | May 2010 | **8** | **OIL** | **How limits are derived** — absolute vs relative, normalising for time, mean-and-σ. Table 5 kick-off limits (Base / Critical Low / Caution Low / Caution High / Critical High): viscosity engine −10/−5/+10/+20%, non-engine −10/−5/+5/+10%; moisture engine 0.1/0.2%, drivetrain 0.4/0.8%; silicon engine 25/100 ppm, industrial gear 25/50; soot 150–250/250. |
| ✅ | 21 | [**Solids, Liquids and Gases**](https://wearcheck.com/resources/techdoc/WZA050.pdf) | TB50 | Jan 2011 | **6** | **OIL** | **Best free water-limit table** — ppm dissolved/emulsified/free: engine 2000 / 2000–5000 / >5000 · hydraulic 200 / 200–1000 / >1000 · gear 500 / 500–2000 / >2000 · turbine 150 / 150–500 / >500. Plus mechanical clearances in µm (roller bearings 0.1–3.0, gearing 0.1–1.5, vane pumps 5–15) — the bridge to cleanliness targets. |
| ✅ | 22 | [**Where Does All That Metal Come From?**](https://wearcheck.com/resources/techdoc/WZA047.pdf) | TB47 | Jan 2010 | **6** | **R2** | Element → source: Fe, Cu, Pb, Sn, Cr, Al, Si, Na, B, Mo and more, each against wear / contaminant / additive origin. |
| ✅ | 23 | [**Wear Limits Versus Trends**](https://wearcheck.com/resources/techdoc/WZA015.pdf) | TB15 | May 1999 | **8** | R2 | Four ppm **diagnostic-pattern** tables (not condemning limits): dirt entry vs coolant leak vs silicone sealant vs anti-foam vs piston torching; Detroit two-stroke; site-specific; bearing wear vs coolant vs cooler leaching vs additive. *Image-only scan, one full-page JPEG per page — no text layer; use 23b for the text.* **Identity confirmed visually** (Session REF-INTAKE): WearCheck Africa Technical Bulletin ISSUE 15, "Wear limits versus trends", John S. Evans B.Sc. |
| ✅ | 23b | [**TB15 — HTML mirror**](https://wearcheck.com/virtual_directories/Literature/Techdoc/WZA015.htm) | TB15 | — | **6** | R2 | Readable text of the same four tables. |
| ✅ | 24 | [**Holistic Diagnosis**](https://wearcheck.com/resources/techdoc/WZA057.pdf) | TB57 | Jan 2014 | **4** | R2 | Eight worked Fe/Al/Cr/Sn/Cu/Na/Si scenarios and the diagnosis each implies; Si:Al reasoning; elements that are simultaneously additive, contaminant and wear metal. **Read before hard-coding thresholds.** |
| ✅ | 25 | [**SOS: Sources of Silicon**](https://wearcheck.com/resources/techdoc/WZA053.pdf) | TB53 | Feb 2012 | **6** | R2 | Six silicon sources and how to tell them apart — dirt (Si:Al 4:1 to 2:1), coolant, anti-foam, silicone fluids, sealant leaching, piston Al-Si torching. New oil 5–15 ppm; normal piston wear <10; sealant hundreds. |
| ✅ | 26 | [**Acids and Bases**](https://wearcheck.com/resources/techdoc/WZA056.pdf) | TB56 | May 2013 | **4** | OIL | TAN/TBN chemistry; why D664 / D974 / D2896 / D4739 are **not** interchangeable. TBN <4 mgKOH/g condemned · 4–6 conditional · >6 acceptable. |
| ✅ | 27 | [**Greek for Beginners, Pt 2**](https://wearcheck.com/resources/techdoc/WZA020.pdf) | TB20 | Jan 2001 | **8** | OIL | Oxidation, TAN, TBN, fuel dilution, water, PQ. TBN condemn at half start value (diesel start 10–14); TAN 0.05–2.00 by application; fuel dilution flagged >4%. |
| ✅ | 29 | [**Monitoring Oil Degradation with Infrared Spectroscopy**](https://wearcheck.com/resources/techdoc/WZA018.pdf) | TB18 | May 2000 | **8** | OIL | FTIR bands — oxidation 1800–1670 cm⁻¹, nitration 1650–1600, sulphation 1180–1120. |
| ✅ | 30a | [**Engine Troubleshooting Checklist**](https://wearcheck.com/resources/techdoc/WZATS001.pdf) | WZATS001 | n.d. | **1** | R2 | Air intake, cooling, fuel dilution, bottom-/top-end wear, incomplete combustion, internal coolant leaks. Already shaped like an R2 cause tree. |
| ✅ | 30b | [**Drivetrain Troubleshooting Checklist**](https://wearcheck.com/resources/techdoc/WZATS002.pdf) | WZATS002 | n.d. | **1** | R2 | Abnormal wear/debris, dirt, water, overheating; clean-oil-system checklist. |
| ⚠ | 54 | **The Best Oil Analysis Programs Start with a Good Sample** | WearCheck (WZA049, presumed) | PDF created 27 Jan 2011 | **8** | OIL | Sampling guidance. ⚠ **Identity unconfirmed — see §9.** Not fetched by `fetch.sh`; obtained by hand with no recorded URL. The page carries **no text-showing operators at all** (fonts outlined to vector paths, ~22k `l` / ~12k `c` per page), so neither text extraction nor the INDEX's former "scanned" note is right, and the title cannot be read without rasterising. Cross-check sampling guidance against #32 (ALS), which is machine-readable, until this is settled. |

### 3c. Bureau Veritas and ALS

The six Bureau Veritas sheets (#48–#53) are **not in `fetch.sh`** — they sit behind BV's registration gate and were downloaded by hand. Their links below are the gated landing pages, not direct PDF URLs; `GAPS.md` §4 has the full gate inventory.

| | # | Title | Publisher | Year | Pages | Feeds | Coverage |
|---|---|---|---|---|---|---|---|
| ✅ | 31 | [**The Basics of Oil Analysis**](https://oil-testing.com/wp-content/uploads/2020/08/Basics-of-Oil-Analysis-Booklet-2020V_compressed-1.pdf) | Bureau Veritas OCM | 2020 | **32** | OIL R2 | Wear/contaminant and additive element tables **by equipment class** — Engines / Transmissions / Gear Systems / Hydraulics / Compressors (pp.22–23). Sampling method and interval tables pp.14–16 (sample within 30 min of shutdown; explicit warning against sump/drain sampling). ISO cleanliness table p.29. Served openly from `/wp-content/uploads/`, bypassing BV's own gate — a fragile link. |
| ✅ | 48 | [**Guide to Wear, Contaminant and Additive Metals**](https://oil-testing.com/document/guide-to-wear-contaminant-and-additive-metals/) | Bureau Veritas | 2019 | **2** | **R2** | ★ BV's element→source reference, and the densest two pages of element-attribution in the library. Third independent cross-check against #22 (WearCheck) and #33 (ALS). *Gated landing page; operator manual download.* |
| ✅ | 49 | [**Understanding the ISO Cleanliness Code**](https://oil-testing.com/document/understanding-the-iso-cleanliness-code/) | Bureau Veritas | 2019 | **1** | OIL | Compact ISO 4406 reference sheet — **per 1 mL**, so it agrees with #35 and not with the per-100-mL sources. *Gated; operator manual download.* |
| ✅ | 50 | [**The Basics of Particle Counting**](https://oil-testing.com/document/the-basics-of-particle-counting/) | Bureau Veritas | n.d. | **3** | OIL | Counting method and interpretation. *Gated; operator manual download.* |
| ✅ | 51 | [**Understanding an Oil Analysis Report**](https://oil-testing.com/document/understanding-an-oil-analysis-report/) | Bureau Veritas | 2019 | **3** | GEN | Annotated specimen report, Normal/Monitor/Abnormal/Critical categories. Note: **no published numeric thresholds.** *Gated; operator manual download.* |
| ✅ | 52 | [**Comparative Viscosity Classifications**](https://oil-testing.com/document/comparative-viscosity-classifications/) | Bureau Veritas | n.d. | **1** | OIL | ISO VG / SAE / AGMA cross-reference. *Gated; operator manual download.* |
| ✅ | 53 | [**7 Steps for Establishing an Oil Analysis Program**](https://oil-testing.com/document/7-steps-for-establishing-an-oil-analysis-program/) | Bureau Veritas | n.d. | **3** | GEN | Programme setup. *Gated; operator manual download.* |
| ✅ | 32 | [**How to take an oil sample for testing**](https://www.alsglobal.com/-/media/ALSGlobal/Resources-Grid/Equipment-reliability/Technical-Bulletins/Oil-and-Lubricants-How-to-take-a-sample.pdf) | ALS Oil & Lubricants | ©2024 | **3** | OIL | **Best machine-readable sampling guidance.** Engines: in-line port before the filter, else dipstick tube with siphon pump at mid-sump, drain plug mid-drain only as last resort, oil warm and circulating. Circulating systems: return line, never downstream of a filter. Reservoirs: 1/3–2/3 from surface. Flush ≥3× tubing volume (3× valve + tubing for particle counts). Fill 80–90%. |
| ✅ | 33 | [**Interpreting your oil analysis results**](https://www.alsglobal.com/en/-/media/ALSGlobal/Resources-Grid/Equipment-reliability/Technical-Bulletins/Oil-and-Lubricants-Understanding-tests.pdf) | ALS Oil & Lubricants | ©2024 | **3** | R2 | Three-way split — wear metals (Fe, Cr, Pb, Cu, Sn, Al, Ni, Ag, Ti, V) → components; additives (Sb, Ba, B, Ca, Mg, Mo, P, Na, Si, Zn) → function; contaminants (Al, B, Mg, K, Si, Na) → ingress. |
| ✅ | 34 | [**TB #2 — What is Wear Debris Analysis?**](https://www.alsglobal.com/-/media/ALSGlobal/Resources-Grid/Technical-Bulletin-Wear-Debris-Analysis-AT2.pdf) | ALS Tribology | 2015 | **4** | GEN | Six methods: spectrochemical, PQ index, DR ferrography, patch test, LaserNet Fines, analytical ferrography. |

### 3d. ISO 4406 cleanliness coding

| | # | Title | Publisher | Edition / year | Pages | Feeds | Coverage |
|---|---|---|---|---|---|---|---|
| ✅ | 35 | [**ISO Fluid Cleanliness** (VEL1948)](https://www.parker.com/content/dam/Parker-com/Literature/Aerospace-Filtration/technical-information/VEL1948-BUL-ISO4406-1999-Fluid-Cleanliness-Information.pdf) | Parker Hannifin — Velcon | ©2011 | **2** | **OIL** | ★ **The code table of record.** Range codes **1–28 in particles per mL** (code 18 = 1300–2500/mL). Explains the three-number >4/>6/>14 µm(c) format **and is the only source found that states the pre-1999 two-number form** ("14/11" = >5 and >15 µm). Target codes by operating pressure for servo/proportional valves and pumps. *Was listed as 1 page and missing; it is 2 pages and on disk.* |
| ✅ | 36 | [**Fluid controlling: Contamination handbook**](https://www.hydac.com/download/fluid-controlling-contamination-handbook-1000451225-en.pdf) | HYDAC | EN 7.603.12/12.24, Dec 2024 | **17** | **OIL** | ★ Depth reference and the most current document in the library. Classes to >28, **per 100 mL**. Component targets — pumps, motors, cylinders, valves incl. servo, bearings, drive systems — across three pressure bands. SAE AS4059 and NAS 1638 tables, water saturation, varnish/ageing, bearing-life-vs-water chart. *Was listed as 31 pages and missing; it is **17** pages and on disk — check the older 31-page estimate wherever it was relied on.* |
| ⬜ | 37 | [**Guide to Contamination Standards** (FDCB805UK)](https://www.parker.com/content/dam/Parker-com/Literature/Hydraulic-Filter-Division-Europe/Websphere-Literature/FDCB805UK-.pdf) | Parker Hannifin HFDE | 02/2012 | ~16 | OIL | Codes 1–24 per 100 mL. p.8 acceptable levels by component. **p.14 ISO / NAS 1638 / SAE cross-reference.** |
| ✅ | 38 | [**Understanding ISO Cleanliness Codes**](https://www.donaldson.com/en/resources/technical-articles/understanding-iso-cleanliness-codes/) | Donaldson | n.d. | **8** | OIL | Codes 1–24 per 100 mL. Explicitly flags the unit trap: some publishers use per 1 mL, making their bands 1/100 of the chart. |
| ✅ | 39 | [**Particle Counts: What They Mean and How to Use Them**](https://www.machinerylubrication.com/Read/31500/use-particle-counts) | Noria — Devin Jarrett | n.d. | **13** | OIL | NAS 1638 → ISO 4406:87 → :99 history, why 4/6/14 µm matter against oil film thickness, setting targets by machine sensitivity. **In-page ISO 4406 correlation table.** |
| ✅ | 40 | [**Particle Counting — Oil Analysis 101**](https://www.machinerylubrication.com/Read/353/particle-counting-oil-analysis) | Noria | n.d. | **13** | GEN | Counter physics — white-light vs laser optical, pore-blockage flow-decay vs Δp; equivalent-spherical-diameter compromise; false positives. |
| ✅ | 41 | [**Matching Oil Cleanliness Standards to Machine Working Clearances**](https://www.machinerylubrication.com/Read/738/oil-cleanliness-standards) | Noria — J.M. Weiksner (Savannah River Site) | n.d. | **14** | OIL | ISO 4406:1999 table, machined-component clearances, **bearing internal clearances**, site-level target codes. The clearance → target-code bridge. |
| ✅ | 42 | [**ISOCLEAN Life Extension Tables**](https://www.texacolubricants.com/content/dam/external/isoclean/en_us/sales-material/sales-sheet/ISOCLEAN%20Life_Extension_Tables_FEB%202016.pdf) | Chevron / Texaco · IDC 0715-101092 | ©2020 (Feb 2016 sheet) | **5** | OIL | Current code → target code → life multiplier for **rolling element bearings, journal bearings/turbomachinery, hydraulics, gearboxes**, plus moisture. 20/18/15 → 17/15/12 gives bearings 1.7×, hydraulics 2.0×, gearboxes 1.5×. ⚠ Copyrighted marketing collateral — **cite, don't republish the table wholesale.** |

---

## 4. `general-pdm/` — public-domain handbooks (+ one journal paper)

| | # | Title | Publisher | Edition / year | Pages | Feeds | Coverage |
|---|---|---|---|---|---|---|---|
| ✅ | 43 | [**Reliability-Centered Maintenance Guide for Facilities and Collateral Equipment**](https://www.nasa.gov/wp-content/uploads/2023/06/nasa-rcmguide.pdf) | NASA | Sept 2008 | **472** | **R2** OIL | ★ **Best free narrative source for bearing cause reasoning.** **Table 4-5 "Cause of Motor Bearing Failure"** (fatigue, contamination, lubrication, electrical damage) is exactly R2-shaped. Also Table 4-4 motor component analysis · 5-5 rolling bearing misalignment limits · 10-1…10-7 motor balance, motor and pump vibration criteria, belt-driven fan limits, **ISO 10816-1:1995 zone boundaries**, acceptance classes · 10-8 lubricant tests · 10-9 Sperry Vickers hydraulic contamination · 10-10 transformer oil. Chapters 6–7 are the PT&I chapters. |
| ✅ | 44 | [**Reliability Centered Building and Equipment Acceptance Guide**](https://www.nasa.gov/wp-content/uploads/2023/06/rcbandeguidejul04.pdf) | NASA | July 2004 | **314** | OIL GEN | Acceptance thresholds. Table 2-2 ISO 3945 vibration severity · 2-3 acceptance classes · 2-4 machine classifications · **§2.2.2.2 bearing-defect criterion ("no discrete bearing frequencies should be detectable")** · 2-5 lubricant tests · 2-6 Sperry Vickers · 2-7 transformer oil. |
| ✅ | 45 | [**O&M Best Practices — A Guide to Achieving Operational Efficiency, Release 3.0**](https://www.energy.gov/sites/default/files/2020/04/f74/omguide_complete_w-eo-disclaimer.pdf) | US DOE FEMP / PNNL · **PNNL-19634** | Aug 2010 (current — no R4.0 exists) | **321** | GEN | Ch.6 predictive maintenance: thermography · **lubricant and wear particle analysis** · ultrasonics · vibration · motor analysis · performance trending. Table 6.1.1 technology × equipment matrix; **Spectrometer Metals Guide** §6.3.2 mapping metals to engines, transmissions, gears, hydraulics; Fig 6.5.1 vibration severity. **Caveat: narrative grade — no defect-frequency table, no numeric oil limits.** Cite as PNNL-19634; the widely-quoted "DOE/GO-102010-3119" is not corroborated on any `.gov` source. |
| ✅ | 46 | [**Wind Turbine Gearbox Condition Monitoring Round Robin Study — Vibration Analysis**](https://www.osti.gov/biblio/1048981) | NREL / DOE EERE — S. Sheng, Editor · **NREL/TP-5000-54530** · Contract DE-AC36-08GO28308, Task WE11.0305 | July 2012 | **157** | **R1** **R2** | ★ **The only ground truth in the library.** A 750 kW GRC gearbox that took **two oil-loss events in the field**, was dynamometer-retested, then torn down — so the damage is physically inspected, not seeded. **Ch. 2 is the test campaign and the ground truth**: §2.1 the test article and the two oil-loss events (p.3) · §2.3 the 12-channel accelerometer set with sensor map and Table 2.3 labels AN1–AN12 (pp.7-8) · §2.4 Table 2.4, three test conditions CM_2a/2b/2c at 1200/1800 rpm and 25/50 % rated (p.9) · **§2.5 Table 2.5, the actual damage list — 12 numbered instances across HSS/ISS gear sets and bearings, planet carrier, sun pinion, LSS** (pp.10-11). **Ch. 1 carries the blind-study outcome** (p.2, Fig. 1.1): 16 partners, no prior knowledge of the damage, 2 months; all agreed **7 of the 12 instances were vibration-detectable**, and the best partner found **5 of 7** — most had more missed detections than false alarms. **Ch. 3–10 are the eight partner diagnoses**, anonymous except where the chapter title names the firm — Ch. 3 General Electric · Ch. 4 algorithms originally built for DoD applications · Ch. 5 NRG Systems · Ch. 6 a methods-and-algorithms review · Ch. 7 sideband energy + enveloping spectral analysis · Ch. 8 a data-driven "jerk" approach · Ch. 9 separation/enhancement then envelope analysis · Ch. 10 a two-stage detection framework. Ch. 11 recommended practices, App. A the 16 partners (7 universities, 9 private sector). Envelope analysis, TSA, cepstrum, spectral kurtosis, BPFO/BPFI/FTF and SER all appear on real data with a known answer. **Deliberately recorded for later ground-truth work:** Table 2.5 ↔ Fig. 1.1 ↔ the per-chapter diagnoses is a ready-made scoring set for our own detectors, but **it is not wired to anything yet and must not be treated as an eval fixture until someone maps it.** ⚠ The **teardown itself is not in this document** — §2.5 summarises it and cites Errichello & Muller, *GRC Gearbox 1 Failure Analysis Report*, **NREL/SR-5000-53062** (Feb 2012), which is **not in the library** (`GAPS.md` §7). ⚠ **Page offset: printed body page N = PDF page N+15** (front matter runs i–xi); cite both, as with #43. |
| ⬜ | 47 | [**A Review of Rolling Element Bearing Vibration: Detection, Diagnosis and Prognosis**](https://apps.dtic.mil/sti/html/tr/ADA291123/index.html) | DSTO, archived by DTIC · **AD-A291123** / DSTO-RR-0013 | 1995 | ? | R1 R2 | Bearing kinematics, **defect frequencies**, bearing life, measurement, signal processing, prognosis. **Not on disk** — same DTIC HTML-wrapper problem as #15; the saved result is a 1.4 KB error page in `~/Desktop/vib-agent-refs/_to_delete/`. ⚠ Australian Defence work — publicly releasable but **not** US public domain. |
| ✅ | 55 | [**DoDM 4151.22 — Reliability Centered Maintenance**](https://www.esd.whs.mil/Portals/54/Documents/DD/issuances/dodm/415122m.pdf) | US DoD | 30 Jun 2011, Ch.1 31 Aug 2018 | **25** | GEN | RCM policy and process. No fault tables — doctrinal only. The closest `.mil`-hosted stand-in for NAVAIR 00-25-403, which has no official public copy (`GAPS.md` §5). |
| ⚠ | 56 | **Application for Selecting Filtering Parameters to Obtain Envelope Spectra** | A. Seetharamaiah & A. Jabłoński, AGH University of Krakow · *Journal of the Polish Mineral Engineering Society* | Jul–Dec 2025 · **doi:10.29227/IM-2025-02-80** | **14** | **R1** | The only item in the library on the **vibration** side of the house rather than the oil side, and the only non-government document in `general-pdm/`. Envelope analysis for rolling-element bearing diagnostics — band selection for the envelope spectrum, gear-mesh and bearing-characteristic amplitude modulation. Directly adjacent to what `bearing_rca.py` already does. ⚠ **Provenance and licence unverified:** it was on disk but indexed nowhere, is absent from `fetch.sh`, and its retrieval host was never recorded — the DOI above is the only source of record. **Confirm the journal's open-access terms before citing or redistributing anything from it** (`GAPS.md` §0b). |

---

## 5. `lubrication/`

Symlinks into the canonical copies — one file each, no drift. **All four resolve**; the HYDAC dangler was fixed in Session REF-INTAKE when #36 was renamed to its canonical filename.

| | File | → target |
|---|---|---|
| ✅ | `02_Timken_lubrication_reference_section.pdf` | `bearing-damage/02_…5892.pdf` (lubrication section p.23 ff.) |
| ✅ | `31_BV_Basics_of_Oil_Analysis.pdf` | `oil-limits/31_…2020.pdf` |
| ✅ | `36_HYDAC_water_saturation_varnish_ageing.pdf` | `oil-limits/36_HYDAC_Contamination_Handbook_EN_7.603.12_12.24.pdf` — **was dangling, now resolves** |
| ✅ | `45_DOE_FEMP_ch6_lubricant_wear_particle_analysis.pdf` | `general-pdm/45_…PNNL-19634.pdf` (§6.3) |
| ✅ | `28_WearCheck_TB52_How_do_Oils_Degrade.pdf` | real file, **6** pp — 14 degradation mechanisms, 10 °C-doubles-oxidation rule · [source](https://wearcheck.com/resources/techdoc/WZA052.pdf) |
| ✅ | `28b_WearCheck_TB38_Ups_and_Downs_of_Viscosity.pdf` | real file, **6** pp — causal explanation; no limit table (use #20 Table 5) · [source](https://wearcheck.com/resources/techdoc/WZA038.pdf) |

---

## 6. What feeds what

**R1 (staging).** #08 is the oil-side spine — fatigue particle progression micropitting → laminar → chunky → spherical with a 5-level severity atlas. #01 path patterns and #03 running features give the mechanical progression. #06 supplies the size bands to threshold against. #56 is the only item that touches the *vibration* signal-processing side directly. **#46 has landed** and is the only ground truth in the library — a torn-down gearbox with 12 inspected damage instances, 7 of them agreed vibration-detectable, and eight independent blind diagnoses of the same data. It validates staging rather than building it, so treat it as the *test set* and keep it out of the authoring loop. #15 is the visual reference, pending its distribution check.

**R2 (causes).** #01 + #02 + #03 are three independent damage→cause taxonomies to triangulate — that was the point of gathering all three, and **all three are now on disk**, with the caveat that #03 is the earlier /2 ED. #04 supplies ISO 15243 category structure without the standard. #43 Table 4-5 is a compact citable motor-bearing cause table. Oil side: #22 + #33 + #48 give **three independent element→source mappings** (WearCheck, ALS, Bureau Veritas) — a real cross-check. #24 and #25 on why single-element limits mislead. #30a/#30b are already cause trees.

**Oil-limit tables.** **#16 and #17 have landed** and they dwarf everything else — start there. #19, #20, #21 give the industrial cross-check and, uniquely, the *methodology* for deriving limits. #35 is the code table of record (per mL); #36 the depth reference (per 100 mL). #42 converts a cleanliness delta into a life multiplier.

**General.** #45 programme framing · #44 acceptance thresholds · #18 and #55 doctrine · #07/#10/#11/#34/#40 method background.

---

## 7. Two cautions for the build

1. **Per-mL vs per-100-mL.** Parker VEL1948 (#35) and Bureau Veritas (#49) publish ISO 4406 bands in particles/mL; HYDAC (#36), Parker Europe (#37) and Donaldson (#38) publish per 100 mL. Code 18 is 1300–2500/mL *and* 130,000–250,000/100 mL. Store the unit as an explicit field or the oil core will be wrong by two orders of magnitude in a way that looks entirely plausible.

2. **The labs deliberately do not publish universal wear-metal condemning limits.** No free per-element, per-component-type condemning table exists on WearCheck's, ALS's, POLARIS's or BV's own sites. Three WearCheck bulletins (#23, #24, #25) argue *against* publishing one — limits are machine-, oil-age- and environment-specific. #23's own pull-quote reads "Wear limit tables should be used as a guideline only." That is an editorial position, not an oversight; encode it as a caveat rather than papering over it. The JOAP volumes are the exception and get away with it by being **equipment-model-specific rather than universal**. Copy that pattern.

---

## 8. Quarantined material — outside the repo

Eight files sourced from Anna's Archive were quarantined on 12 Aug 2026 and are **not part of this library and not in this repository.** They stayed behind at `~/Desktop/vib-agent-refs/_unlicensed/` when the library moved in. They are paid standards and commercial books — the one category the brief said never to download from anywhere.

Beyond licensing, three are the **wrong or superseded item**, so citing from them would introduce errors:

| File | Problem |
|---|---|
| BS ISO 15243:2017 | The standard. Purchase: CHF 204 — `GAPS.md` §1 |
| BS ISO 4406:2021 | The standard. Purchase: CHF 67 — `GAPS.md` §1 |
| Randall, *Vibration-based Condition Monitoring* | **1st ed. 2011** (ISBN 9780470747858) — the current edition is the **2nd, 2021** (9781119477556). Different content. |
| Toms, *Machinery Oil Analysis* | **2nd ed. 1998** — the edition worth having is the **3rd, 2008**, 506 pp. |
| *CAPA for the FDA-Regulated Industry* | **Wrong book entirely** — matched on a bad ISBN, nothing to do with machinery. |
| Bloch & Geitner, *Machinery Failure Analysis and Troubleshooting* | 4th ed. 2012 — correct edition, but a commercial book. |
| Scheffer & Girdhar, *Practical Machinery Vibration Analysis* | 2004 — correct edition, commercial book. |
| Qin et al., "Dynamic weighted federated RUL prediction", MSSP 2023 | Paywalled Elsevier paper. Not previously indexed or requested. |

Purchase links, prices and buy-priority for all of these are in `GAPS.md` §1–3. The two ISO standards remain **unowned** — and per §1 there, the content you actually need from both is already covered free by #04, #01, #35 and #36; you only need to buy them if vib-agent will make normative conformance claims.

`~/Desktop/vib-agent-refs/_to_delete/` holds two 1.4 KB saved HTML error pages that `fetch.sh` correctly rejected (the failed #15 and #47 fetches). `_failed/` is empty. Both are safe to delete.

---

## 9. How this index was verified

Page counts here are **measured from the files on disk**, not estimated. Re-run `./verify.sh` after each batch of manual downloads; it re-checks magic bytes, re-counts pages, finds dangling symlinks and rewrites `PAGECOUNTS.tsv`. It downloads nothing.

**Session REF-INTAKE (12 Aug 2026)** re-measured all 55 files with `pypdf` and read every title page it could. Findings:

- **All page counts previously asserted in this index were correct.** The `?` in every row of the old `PAGECOUNTS.tsv` was a `verify.sh` defect, not a bad file: it tried `pdfinfo` (absent on stock macOS) and then `pypdf` under the *system* `python3` (which has no `pypdf`), so both counting paths failed silently and returned `?`. `verify.sh` now resolves an interpreter that actually has `pypdf` before running, and says so loudly if it cannot find one.
- **Five documents were on disk but indexed as missing** — #16, #17, #35, #36 and #03 — saved under their raw download filenames (`33-1-37-4.pdf`, `33-1-37-3.pdf`, `VEL1948-BUL-…pdf`, `fluid-controlling-contamination-handbook-…pdf`, `WL_82-102-ROLLING-BEARING-DAMAGES.pdf`). All five were renamed to the library's `NN_Publisher_Title.pdf` convention; that rename is what resolved the dangling HYDAC symlink.
- **Three page counts in the missing rows were wrong** and are corrected above: #35 is 2 pp (not 1), #36 is **17 pp (not 31)**, #03 is 75 pp (not ~67).
- **#03 is the wrong edition** — WL 82 102**/2 ED**, not the /3 EA that was targeted — and its retrieval host was never recorded.
- **#56 was on disk and indexed nowhere.** Now indexed, with its licence flagged unverified.
- **One identity could not be confirmed: #54.** Its pages contain zero text-showing operators (`BT`/`Tj`/`TJ` all absent) and ~22,000 `l` plus ~12,000 `c` path operations per page — the fonts were converted to vector outlines by a Bullzip PDF Printer run on 27 Jan 2011. So it is *not* a scan, and the previous "scanned — no text layer" note misdescribed it. Neither text extraction nor image extraction recovers the title; the only remaining routes are to open it, or `brew install poppler` and rasterise page 1. Until then treat #54's title, number and date as unconfirmed. `GAPS.md` §5 separately records WZA049 as *not indexed* for exactly this reason — that contradiction is now recorded rather than silently resolved.
- **#23's identity was confirmed visually** by extracting its full-page JPEG: WearCheck Africa Technical Bulletin ISSUE 15, "Wear limits versus trends", John S. Evans B.Sc.

**Not verified by REF-INTAKE:** the *contents* of any document beyond its title page — coverage notes in the tables above are inherited from the compilation pass, not re-read.

**Session REF-46 (13 Aug 2026)** filed **#46** into `general-pdm/`. Verified before filing:

- **Title page read as a rendered image, not only as extracted text.** Cover reads *Wind Turbine Gearbox Condition Monitoring Round Robin Study – Vibration Analysis* · S. Sheng, Editor · Technical Report **NREL/TP-5000-54530** · **July 2012** · Contract DE-AC36-08GO28308. PDF p.2 adds Task No. WE11.0305. PDF p.3 carries the US-government-sponsored-work NOTICE in full ("prepared as an account of work sponsored by an agency of the United States government"), which is what puts it in the public domain and in this library.
- **157 pages, measured** with `pypdf`. The count this index had already asserted for the missing row was correct.
- **The coverage note above IS content-verified**, unlike the rest of the table: it was written from the document's own Table of Contents (PDF pp.10-11), Executive Summary, §1, §2.1, §2.3-2.5 and the References list, each read directly. The **printed-to-PDF offset of +15** was derived by locating printed p.1 at PDF p.16 and confirmed against §2.5 at printed p.10 = PDF p.25.
- **Provenance recorded at intake**, per the #03/#56 lesson. Source of record is the **OSTI landing page** `https://www.osti.gov/biblio/1048981`, **DOI 10.2172/1048981**. The file arrived named `1048981.pdf`, which is the OSTI accession number. The **exact full-text URL was not recorded by the operator**, and this session did not fabricate one — the landing page and DOI are what the row claims. The NREL-side path `docs.nrel.gov/docs/fy12osti/54530.pdf` is robots-blocked and was **not** the successful route (`GAPS.md` §7).
- **A dependency was discovered and is now a gap.** §2.5 does not perform the teardown; it summarises one and cites **NREL/SR-5000-53062**, Errichello & Muller, *GRC Gearbox 1 Failure Analysis Report* (Feb 2012). That report is the actual failure analysis behind Table 2.5 and is not in the library.
