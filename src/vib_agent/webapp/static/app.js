// index.html behaviour — extracted from the inline <script> so a strict CSP
// (default-src 'self') can forbid inline scripts. Served by GET /app.js.
// The contact email is injected server-side into index.html's <meta> tag
// (that placeholder is only substituted in index.html, never in this static file).
const CONTACT_EMAIL = (document.querySelector('meta[name="contact-email"]') || {}).content || '';
// Session AUTH-1. The server writes this meta tag only when STORE_BACKEND=db;
// on the backend production runs the tag is not in the markup at all, so this
// is false and the ledger below says exactly what it said before accounts
// existed. It is read from the page rather than fetched because a request per
// page load, to learn something the server already knew while rendering, is a
// request for nothing.
const ACCOUNTS_ON = ((document.querySelector('meta[name="accounts"]') || {}).content || '') === 'on';

const form = document.getElementById('upload-form');
const formCard = document.getElementById('form-card');
const stateCard = document.getElementById('state');
const submitBtn = document.getElementById('submit');
const fileInput = document.getElementById('file');
const fileName = document.getElementById('file-name');

const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

const EXTRA_SLOTS = [['file_2', 'direction_2', 'file-name-2'], ['file_3', 'direction_3', 'file-name-3']];
const modeSel = document.getElementById('mode');
const extraChannels = document.querySelector('.extra-channels');
const DROP_PROMPT = 'CSV · XLSX · UFF/UNV · WAV · .MAT · TXT/DAT/ASC — max 25 MB';

const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const scrollToEl = (el) => el.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });

fileInput.addEventListener('change', () => {
  Promise.resolve().then(() => previewSlot(1));
  demoFile = null;  // a hand-picked file always wins over a loaded example
  fileName.textContent = fileInput.files.length ? fileInput.files[0].name : DROP_PROMPT;
});
EXTRA_SLOTS.forEach(([fid, , nameId], i) => {
  const inp = document.getElementById(fid), lbl = document.getElementById(nameId);
  const clear = document.getElementById('clear-' + (i + 2));
  const paint = () => {
    Promise.resolve().then(() => previewSlot(i + 2));
    lbl.textContent = inp.files.length ? inp.files[0].name : '';
    if (clear) clear.hidden = !inp.files.length;
  };
  inp.addEventListener('change', paint);
  // Clearing a slot empties the INPUT. It does not need to delete the form
  // parts here -- the submit path already drops any slot with no file, and that
  // is the one place the rule can be enforced for every route into this state
  // (Remove, switching schema, never touching the slot at all). A browser posts
  // an EMPTY FILE PART for a cleared input, which FastAPI would see as a
  // zero-byte file; deleting BOTH `file_n` and `direction_n` is what stops that,
  // and it is the Session E lesson written down.
  if (clear) clear.addEventListener('click', () => { inp.value = ''; paint(); });

  // Per-slot drop target. Live, only slot 1 accepted a drop and the extra slots
  // were plain file inputs -- so a three-channel upload meant three trips
  // through a file picker. The hover state promises "release to choose", NEVER
  // "this will work": whether we can read the file is decided by a magic-byte
  // sniff on the server, over bytes that only exist once it has been dropped.
  const zone = inp.parentElement;
  if (zone && typeof DataTransfer !== 'undefined') {
    const over = (on) => (e) => {
      e.preventDefault(); e.stopPropagation();
      zone.classList.toggle('over', on);
    };
    zone.addEventListener('dragover', over(true));
    zone.addEventListener('dragenter', over(true));
    zone.addEventListener('dragleave', over(false));
    zone.addEventListener('drop', (e) => {
      e.preventDefault(); e.stopPropagation();
      zone.classList.remove('over');
      const dropped = e.dataTransfer && e.dataTransfer.files;
      if (!dropped || !dropped.length) return;
      try {
        const dt = new DataTransfer();
        dt.items.add(dropped[0]);       // one file per slot; a slot is one channel
        inp.files = dt.files;
        paint();
      } catch (err) { /* older browser — the picker still works */ }
    });
  }
  paint();
});

// U2: the extra slots are revealed on demand, not rendered up front -- a
// browser posts an empty file part for a revealed-but-unfilled input.
let channelsRevealed = false;

// HIST-2. The same two slots serve two different questions, so the copy has to
// say which one is being asked. In `spectrum` the extra slots are CHANNELS
// (directions of one reading); in `compare` slot 2 is a TIME (the later reading
// of the same point) and direction stops being a concept at all.
const SLOT_COPY = {
  spectrum: {
    eyebrow: 'Additional channels · same machine, same session',
    file2: 'Second file <span class="opt">(optional)</span>',
    help: 'Upload what you measured — one channel is fine; three enables full directional analysis.',
  },
  compare: {
    eyebrow: 'Before / after · two readings of the same measurement point',
    file2: 'After — the later reading',
    help: 'Two files, in time order: the slot above is the BEFORE reading and this one is the AFTER. '
      + 'The report describes the after reading and states what changed between them.',
  },
};

function setSlotCopy(mode) {
  const copy = SLOT_COPY[mode] || SLOT_COPY.spectrum;
  const eyebrow = document.getElementById('slots-eyebrow');
  const label = document.getElementById('file_2_label');
  const help = document.getElementById('slots-help');
  if (eyebrow) eyebrow.textContent = copy.eyebrow;
  if (label) label.innerHTML = copy.file2;
  if (help) help.textContent = copy.help;
}

function clearSlot(fid, did, nameId) {
  Promise.resolve().then(() => refreshPreviews());
  const inp = document.getElementById(fid);
  if (!inp) return;
  inp.value = '';
  const name = document.getElementById(nameId);
  if (name) name.textContent = '';
  const clear = document.getElementById('clear-' + fid.slice(-1));
  if (clear) clear.hidden = true;
}

// ── Session INTAKE-2 — the other measurement locations ───────────────────────
// A machine is not one point. Location 1 is the block that has always been
// there and keeps every field name it had (`file`, `direction`,
// `measurement_location`, `rpm`, `bearing_model`, …); locations 2..MAX get a
// `loc<i>_` prefix. `webapp/locations.py`'s docstring carries the reason that
// asymmetry is deliberate: the single-file identity path is pinned
// byte-identical, and renaming its fields would have made a one-location upload
// a new code path on the day the form changed.
//
// The template's controls carry `data-field`, not `name`, so an un-cloned
// template posts nothing at all. Cloning sets the real `name`, prefixed.
const MAX_LOCATIONS = 8;            // locations.py::MAX_LOCATIONS
const LOCATION_DEFAULT_LABELS = {   // common.py::DEFAULT_LOCATION_LABELS
  motor: ['Motor DE', 'Motor NDE'],
  pump: ['Pump DE', 'Pump NDE'],
  compressor: ['Compressor DE', 'Compressor NDE'],
  fan: ['Fan DE', 'Fan NDE'],
  blower: ['Blower DE', 'Blower NDE'],
  gearbox: ['Gearbox input', 'Gearbox output'],
  other: ['Point 1', 'Point 2'],
};

/** The label the form offers for the Nth point of this kind of machine.
 *  An offer, never a substitution: a typed label always wins, and a cleared one
 *  stays cleared. Past the end of the list it stops guessing. */
function defaultLocationLabel(machineType, ordinal) {
  const list = LOCATION_DEFAULT_LABELS[String(machineType || '').toLowerCase()]
    || LOCATION_DEFAULT_LABELS.other;
  return list[ordinal - 1] || '';
}

function locationBlocks() {
  const host = document.getElementById('locations');
  // The method check, not just the null check. This runs on every submit, and
  // `submit` is reachable from views that do not render the intake at all -- and
  // from the node harness, whose `getEl` invents an object for an id it does not
  // know (TIDY-1 F-12), so `host` can be truthy and still have no DOM methods.
  // An intake feature must not be able to throw on a page that has no intake.
  if (!host || typeof host.querySelectorAll !== 'function') return [];
  return Array.from(host.querySelectorAll('[data-loc]'));
}

/** Renumber every block so the posted names are contiguous from 2.
 *  Called after a removal as well as an addition: a gap in the numbering would
 *  be read as a location that was sent and is missing, and `collect_extra_locations`
 *  stops at the first index it finds nothing for. */
function renumberLocations() {
  locationBlocks().forEach((block, i) => {
    const index = i + 2;
    block.setAttribute('data-loc', String(index));
    const n = block.querySelector('.loc-n');
    if (n) n.textContent = String(index);
    block.querySelectorAll('[data-field]').forEach((el) => {
      el.name = `loc${index}_${el.getAttribute('data-field')}`;
    });
  });
  const add = document.getElementById('add-location');
  if (add) add.disabled = locationBlocks().length + 1 >= MAX_LOCATIONS;
}

function addLocation() {
  const host = document.getElementById('locations');
  const tpl = document.getElementById('location-template');
  if (!host || !tpl) return null;
  if (locationBlocks().length + 1 >= MAX_LOCATIONS) return null;
  const block = tpl.content.firstElementChild.cloneNode(true);
  host.appendChild(block);
  renumberLocations();
  const label = block.querySelector('[data-field="label"]');
  if (label && !label.value) {
    const kind = (form.elements.machine_type && form.elements.machine_type.value) || '';
    // Ordinal within the machine, not within the added blocks: location 1 is
    // the first point, so the first ADDED block is the machine's second.
    label.value = defaultLocationLabel(kind, locationBlocks().length + 1);
  }
  const remove = block.querySelector('[data-remove-loc]');
  if (remove) {
    remove.addEventListener('click', () => {
      block.remove();
      renumberLocations();
    });
  }
  return block;
}

/** Delete the empty file parts of every added location.
 *  The same rule the three slots above follow: an untouched `<input type=file>`
 *  posts a zero-byte part, and a server that received one would be reading "a
 *  file we cannot parse" where the truth is "no file". */
function dropEmptyLocationParts(fd) {
  locationBlocks().forEach((block) => {
    const index = block.getAttribute('data-loc');
    ['file', 'file_2', 'file_3'].forEach((slot) => {
      const input = block.querySelector(`[data-field="${slot}"]`);
      if (input && input.files && input.files.length) return;
      fd.delete(`loc${index}_${slot}`);
      fd.delete(`loc${index}_${slot === 'file' ? 'direction' : slot.replace('file', 'direction')}`);
    });
  });
}

/**
 * Hide or show an element so that a BROWSER agrees, not just the DOM.
 *
 * `hidden` is only honoured through the UA stylesheet's `[hidden]{display:none}`,
 * which ANY author rule declaring `display` outranks. style.css:366-369 already
 * records the hazard for `.hero`; the same thing silently defeated `hidden` on
 * `#slot_3_row` (`.grid{display:grid}`, style.css:89) and on `#add-channels`
 * (`.btn-link{display:inline-block}`, style.css:114) — both stayed on screen in
 * compare mode. The node tests read the property back and passed, because a stub
 * DOM has no stylesheet to be outranked by.
 *
 * Setting the inline `display` settles it at the highest-priority origin, and
 * does so for ANY element whatever classes it carries — a CSS `[hidden]` guard
 * has to be written again for every class that ever grows a `display`. `''`
 * restores whatever the stylesheet says rather than pinning a value here.
 */
function setHidden(el, hide) {
  if (!el) return;
  el.hidden = hide;
  el.style.display = hide ? 'none' : '';
}

const hideById = (id, hide) => setHidden(document.getElementById(id), hide);

// Multi-channel is spectrum-only; compare is exactly two files and no directions.
function syncMode() {
  const mode = modeSel.value;
  const compare = mode === 'compare';
  const spectrum = mode === 'spectrum';
  // Compare ALWAYS shows its second slot: the whole mode is meaningless without
  // it, so it is not something to go and reveal.
  const visible = compare || (spectrum && channelsRevealed);
  setHidden(extraChannels, !visible);
  hideById('add-channels', compare || !spectrum || channelsRevealed);

  // Direction is where a sensor pointed. Two readings of one point at two times
  // share it, so asking twice would invite an answer that means nothing.
  ['direction_row', 'direction_2_row', 'direction_3_row'].forEach((id) => {
    hideById(id, compare);
  });
  hideById('slot_3_row', compare);
  // The three-directions pitch is not merely irrelevant in compare mode, it is
  // wrong: compare is two readings of ONE channel, and the slot helper inside
  // the panel is already saying which file is which.
  hideById('channels-help', compare);
  setSlotCopy(compare ? 'compare' : 'spectrum');

  // Clear whatever is no longer askable. A revealed-but-unfilled input posts an
  // empty file part, and a slot-3 file left over from spectrum mode would make
  // a compare post three files and earn a 400 the analyst did not cause.
  if (!visible) EXTRA_SLOTS.forEach(([fid, did, nameId]) => clearSlot(fid, did, nameId));
  else if (compare) clearSlot('file_3', 'direction_3', 'file-name-3');
}
modeSel.addEventListener('change', syncMode);
syncMode();

// ── demo path (Session F2) ────────────────────────────────────────
// "Run the example analysis" loads the bundled example file, prefills every
// field, and submits the ORDINARY form to the ORDINARY endpoint: same pipeline,
// same drafting pass, same verification, same per-code rate limit. Nothing is
// canned — whatever the analysis computes is what the report says.
const EXAMPLE_URL = '/example-spectrum.csv';
const EXAMPLE_FILENAME = 'example-bearing-fault.csv';
const EXAMPLE_FIELDS = {
  machine_alias: 'Demo Pump', rpm: '1800', iso_group: '2', iso_support: 'rigid',
  bearing_model: '6206', velocity_unit: 'mm_s', detection_type: 'rms', mode: 'spectrum',
  direction: '',
  // Session INTAKE-2. The demo posts through the ordinary form, so it has to
  // answer the ordinary form's required fields -- and it answers TRUTHFULLY:
  // the machine is called "Demo Pump", so it is a pump. Pinned against
  // tests/test_session_f2.py's copy of this map.
  machine_type: 'pump',
};
let demoFile = null;        // kept so submit works even without DataTransfer support
const exampleBtn = document.getElementById('run-example');
const exampleNote = document.getElementById('example-note');
const codeInput = document.getElementById('code');

function setField(name, value) {
  const el = form.elements[name];
  if (el) el.value = value;
}

// ── code in link (Session F2) ─────────────────────────────────────
// /?code=XXXX prefills the invite field, so a per-person link is one tap from a
// run. Codes stay per-person and nothing else changes: the code is never
// stored, never logged (only its label is), and still travels exactly one
// place — the /api/jobs form post.
(function prefillCodeFromLink() {
  if (!codeInput) return;
  const code = new URLSearchParams(location.search).get('code');
  if (code) codeInput.value = code.trim().slice(0, 64);
})();

async function loadExample() {
  const res = await fetch(EXAMPLE_URL);
  if (!res.ok) throw new Error('example unavailable');
  const file = new File([await res.blob()], EXAMPLE_FILENAME, { type: 'text/csv' });
  demoFile = file;
  let nativeFileSet = false;
  try {
    const dt = new DataTransfer();
    dt.items.add(file);
    fileInput.files = dt.files;
    nativeFileSet = fileInput.files.length === 1;
  } catch (err) { /* older browser — the submit handler falls back to demoFile */ }
  Object.keys(EXAMPLE_FIELDS).forEach((k) => setField(k, EXAMPLE_FIELDS[k]));
  syncMode();
  syncBearingGeometry(false);
  fileName.textContent = EXAMPLE_FILENAME;
  return nativeFileSet;
}

function submitForm(nativeFileSet) {
  // requestSubmit() runs native validation, which needs a real file in the
  // input; without one, dispatch the submit event directly (the handler below
  // supplies the example blob).
  if (nativeFileSet && form.requestSubmit) form.requestSubmit();
  else form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
}

if (exampleBtn) {
  exampleBtn.addEventListener('click', async () => {
    // Session UX-1. The button lives on the landing, which is the HOME view;
    // the form it fills belongs to the upload view. Route there first or the
    // scroll below targets an element that is not on screen.
    navigate('#/new');
    exampleBtn.disabled = true;
    let nativeFileSet;
    try {
      nativeFileSet = await loadExample();
    } catch (err) {
      exampleBtn.disabled = false;
      show(errorCard('The example file could not be loaded — please try again.'));
      return;
    }
    exampleBtn.disabled = false;
    if (!codeInput.value.trim()) {
      if (exampleNote) {
        exampleNote.textContent = 'Example loaded and the form is filled in — enter your invite code below, '
          + 'then press Analyze file.';
        exampleNote.classList.add('note-live');
      }
      scrollToEl(formCard);
      codeInput.focus();
      return;
    }
    scrollToEl(formCard);
    submitForm(nativeFileSet);
  });
}

// ── machine memory, browser-side ONLY (Session F2) ────────────────
// The operator-approved exception to the no-browser-storage default: our own
// web app, vanilla JS, localStorage on the analyst's device. No server state,
// no accounts, no cookie, nothing sent anywhere. Machine CARD fields only —
// never the invite code, never a file, never the trace-consent choice.
// Session GEOM-A takes the card to v2. INTAKE-HONEST could add its six fields
// under v1 because a missing field reads as '' and '' means "not provided" —
// true of every field it added. GEOM-A's card carries a field where that
// reasoning stops being free: `coupling`. '' means "not stated", the server
// leaves MachineMeta.coupled at its default, and the report says the coupling
// state was not provided — so an old card still behaves correctly. The version
// bump is therefore not a bug fix; it is the honest record that the SHAPE
// changed, so a future field whose blank value is NOT harmless has a version to
// migrate from rather than a silent guess.
//
// Read-compat is the requirement, not a nicety: an analyst who saved a machine
// last week must find it in the dropdown today, with everything they typed
// still in it. `migrateEntry` fills the new keys with '' and nothing else — no
// value is invented, and a v1 card round-trips to exactly the same submission
// it made before this session.
const STORE_KEY = 'vib.machines.v2';
const LEGACY_STORE_KEYS = ['vib.machines.v1'];
const MEMORY_FIELDS = ['machine_alias', 'rpm', 'iso_group', 'iso_support', 'bearing_model',
  'velocity_unit', 'detection_type', 'mode', 'direction',
  'sensor_sensitivity_mv_per_g', 'fmax_hz', 'spectral_lines', 'window_type',
  'averages', 'integration',
  // Session GEOM-A — machine geometry.
  'measurement_location', 'coupling', 'blades', 'drive_type', 'poles', 'line_freq_hz',
  'rotor_bars', 'gear_teeth_driving', 'gear_teeth_driven',
  'drive_pulley_mm', 'driven_pulley_mm', 'pulley_center_distance_mm',
  // Session INTAKE-2 — what kind of machine it is, and the nameplate the ISO
  // group is derived from. Appended, and appended HERE in this order, because
  // db/models.py::CARD_FIELDS mirrors this list and
  // tests/test_db1_schema.py diffs the two TUPLE FOR TUPLE -- order included.
  // Adding a remembered field is also a change to what this browser keeps, so
  // it moves with the retention ledger and /privacy in the same commit
  // (ROADMAP common law #8 as amended by RULED D-22).
  'machine_type', 'rated_kw', 'driven_rpm',
  // Session LIMITS-1c — the machine's own severity limits. Appended HERE, at
  // the tail, for the same reason INTAKE-2's three were: db/models.py::
  // CARD_FIELDS mirrors this list and tests/test_db1_schema.py diffs the two
  // TUPLE FOR TUPLE, order included. Stored as typed text like every other
  // card field, so '' keeps meaning "not provided" and a blank limit stays
  // blank rather than becoming 0.
  'limit_ab', 'limit_bc', 'limit_cd'];

// What the form substitutes for a blank alias, and therefore what a machine
// WITHOUT a name is called everywhere -- mirrored in `routes_machines.py` and
// `db/importer.py`, which both hold the same literal for the same reason.
//
// Declared HERE, above `migrateEntry`, rather than beside TREND_KEY where it
// used to sit: `renderSavedMachines()` runs during module init and reads the
// card store, so anything that store's reader touches has to exist by then. A
// const declared further down would be in its temporal dead zone and would
// throw on page load -- which is why `cardKey` below spells the pair out
// rather than calling `namedMachine`, and that constraint is now lifted.
const UNNAMED_MACHINE = 'Unnamed machine';
const savedRow = document.getElementById('saved-row');
const savedSel = document.getElementById('saved');
const forgetBtn = document.getElementById('delete-saved');
const rememberBox = document.getElementById('remember');

/** A stored card, with every field this version knows about. Missing keys
 *  become '' — "not provided" — and no value is ever invented.
 *
 *  Session UX-5 (STRANGER C3). Returns NULL for a card with no usable alias,
 *  and `readMachines` drops it. Such a card is not a machine anywhere else in
 *  this file: `browserMachines` already refuses it (so it is in no list and has
 *  no page), `createMachineLocal` refuses to make one, and HIST-1 withholds the
 *  trend offer from it -- but the SAVED-MACHINES DROPDOWN read this store
 *  directly, so a stale record left by an older build appeared there as a
 *  phantom "Unnamed machine" that led to a machine the analyst could not open.
 *  The stranger met exactly that, with rpm 1185 pre-filled from it.
 *
 *  Dropped on READ rather than migrated away, for `readMachines`' own reason:
 *  the read happens on every page load, and a filter is honest about a record
 *  we will not use without deleting something an analyst never asked us to
 *  touch. `writeMachines` then persists the filtered list the first time
 *  anything is saved. */
function migrateEntry(entry) {
  const out = {};
  MEMORY_FIELDS.forEach((f) => { out[f] = String((entry && entry[f]) || ''); });
  return namedMachine(out.machine_alias) ? out : null;
}

function readStore(key) {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch (err) { return []; }  // private mode / disabled storage / corrupt value
}

function readMachines() {
  const current = readStore(STORE_KEY);
  if (current.length) return current.map(migrateEntry).filter(Boolean);
  // Nothing under the current key: adopt the newest legacy card set, write it
  // forward, and drop the old key so one machine is never stored twice. Done
  // on READ so it happens the first time the page loads, not only if the
  // analyst happens to save something.
  for (let i = 0; i < LEGACY_STORE_KEYS.length; i += 1) {
    const legacy = readStore(LEGACY_STORE_KEYS[i]);
    if (legacy.length) {
      const migrated = legacy.map(migrateEntry).filter(Boolean);
      writeMachines(migrated);
      try { localStorage.removeItem(LEGACY_STORE_KEYS[i]); } catch (err) { /* nothing to do */ }
      return migrated;
    }
  }
  return [];
}

function writeMachines(list) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(list)); } catch (err) { /* nothing to do */ }
}

const machineName = (m) => (m && m.machine_alias) || 'Unnamed machine';

/** The identity of a card, which is the identity of its trend.
 *
 *  Session UX-2. `machineName` alone was the dedupe key from Session F2 until
 *  now, and it was wrong the moment GEOM-A added a measurement location: a
 *  machine IS an alias AND a location everywhere else in this file
 *  (`trendKey`, `browserMachines`, and the server's own
 *  UNIQUE(user_id, machine_alias, measurement_location)). Saving "Pump A ·
 *  Motor DE" and then "Pump A · Pump NDE" therefore DESTROYED the first card
 *  while `browserMachines` kept both machines -- so the first one silently
 *  became a card-less trend whose every fact rendered "Not recorded".
 *
 *  Written through `trendKey` rather than beside it so the card store and the
 *  trend store cannot come to disagree about what one machine is.
 *
 *  Session UX-5: this used to say it could use no module const, because
 *  `UNNAMED_MACHINE` was declared below and `renderSavedMachines()` runs during
 *  module init. That const now sits above `migrateEntry` for exactly that
 *  reason, so the hazard is gone -- but this function still needs no name check
 *  at all (it answers "which machine is this", not "is it named"), and it is
 *  left as it is. */
function cardKey(card) {
  return trendKey((card && card.machine_alias) || '',
                  (card && card.measurement_location) || '');
}

/** How a card reads in a list. The pair, for the same reason: two rows both
 *  saying "Pump A" are two machines the analyst cannot tell apart. */
function cardLabel(card) {
  const loc = String((card && card.measurement_location) || '').trim();
  return machineName(card) + (loc ? ' · ' + loc : '');
}

function renderSavedMachines(selectKey) {
  if (!savedRow || !savedSel) return;
  const list = readMachines();
  savedSel.innerHTML = '<option value="">Choose a saved machine…</option>'
    + list.map((m, i) => `<option value="${i}">${esc(cardLabel(m))}</option>`).join('');
  if (selectKey) {
    const idx = list.findIndex((m) => cardKey(m) === selectKey);
    if (idx > -1) savedSel.value = String(idx);
  }
  // Session UX-1: through setHidden, like every other hide in this file. It
  // worked as a bare property only because `.saved-row` happens to declare no
  // `display` -- luck held in place by a comment, and the row now sits inside
  // a view another function hides and shows.
  setHidden(savedRow, list.length === 0);
  if (forgetBtn) forgetBtn.disabled = !savedSel.value;
}

function rememberCurrentMachine() {
  if (!rememberBox || !rememberBox.checked) return;
  const entry = {};
  MEMORY_FIELDS.forEach((f) => { entry[f] = String((form.elements[f] && form.elements[f].value) || ''); });
  if (!entry.machine_alias.trim()) entry.machine_alias = 'Unnamed machine';
  // Session UX-2: by the full key, not the alias. See `cardKey`.
  const list = readMachines().filter((m) => cardKey(m) !== cardKey(entry));
  list.push(entry);
  writeMachines(list);
  renderSavedMachines(cardKey(entry));
}

/** Put a saved card back into the form. Session UX-1 factored this out of the
 *  dropdown's own handler so the machine page's "New reading for this machine"
 *  fills the form the SAME way -- one definition, so the two routes into a
 *  prefilled form cannot come to disagree about which fields carry over. */
/** A stored card as a FORM can carry it. Session UX-5.
 *
 *  There are two forms that put a saved card into controls -- the upload form
 *  (`applyCard`) and the machine form (`renderMachineEdit` + `fillEditForm`) --
 *  and a value neither of them can hold is silently reset to empty by the
 *  browser. Both go through here, so a card cannot mean one thing on one screen
 *  and something else on the other. Returns a COPY; the stored card is never
 *  rewritten by being looked at.
 *
 *  Two resolutions, both of which are only ever a widening in the analyst's
 *  favour -- nothing here invents an answer they did not give:
 *
 *  B5. A benchmark-rig bearing key the form no longer offers, but whose
 *  geometry is the same bearing under a route name, becomes that name. Dropping
 *  it to empty would turn the bearing screen OFF for a machine it had been
 *  running for, with nothing on screen saying so. The rigs with no route
 *  equivalent are left alone and `unlistedBearingNote` names them.
 *
 *  B7. A blank velocity unit becomes `mm_s`. A card can hold '' three ways -- a
 *  v1 card from before the field existed, a card the machine form wrote (it did
 *  not ask until this session), or a machine known only from its trend, which
 *  has no card at all. Assigned to a select of mm_s/in_s, '' leaves
 *  selectedIndex -1: the control renders EMPTY under a caption reading "Stated,
 *  never assumed", and the run then reports mm/s regardless because that is the
 *  server's own default (`app.py`: `velocity_unit: str = Form("mm_s")`). The
 *  blank is the defect. `mm_s` is what a first-time analyst is shown and what
 *  the server would have used either way -- so it is stated on screen, where it
 *  can be seen and changed, rather than applied invisibly. */
function cardForForm(card) {
  const out = Object.assign({}, card || {});
  const same = corpusEquivalent(out.bearing_model);
  if (same) out.bearing_model = same;
  if (!out.velocity_unit) out.velocity_unit = 'mm_s';
  return out;
}

function applyCard(card) {
  if (!card) return;
  // Recorded BEFORE the fields are written: `setField` is what loses it, and
  // after that call the form can no longer be asked what the card held.
  noteUnlistedBearing(card);
  const applied = cardForForm(card);
  MEMORY_FIELDS.forEach((f) => setField(f, applied[f] || ''));
  if (rememberBox) rememberBox.checked = true;
  syncMode();
  // Session GEOM-1. A card can carry a bearing MODEL, so the geometry block's
  // visibility has to follow it. Silent: applying a card discards nothing the
  // analyst entered -- `MEMORY_FIELDS` does not carry the geometry (see the
  // note beside the controls in index.html), so there is nothing to clear.
  syncBearingGeometry(false);
  renderCarried(applied);
}

/** The facts THIS RUN will be analysed with, in the order the report prints
 *  them. Not "the facts a card carried": on a fresh form these are the form's
 *  own defaults (ISO group 2, rigid, mm/s), which are what the run uses and are
 *  carried from nothing. Review wants that list as it stands; the step-1 line
 *  wants it because a card has just been applied, and says so in its own words.
 *
 *  Session UX-5 (STRANGER C4): "The machine wizard step asks for rpm, ISO
 *  group, alias, velocity unit — but not support, coupling or bearing, which
 *  I'd just entered on Add a machine. Those turn out to be carried into step 3
 *  under More options (collapsed). Measurement point isn't shown at all until
 *  Review."
 *
 *  Read from the FORM rather than from the card, so what it lists is what will
 *  actually be posted -- a card field the form has no control for would
 *  otherwise be advertised and never sent. */
function analysisFacts() {
  const rows = [
    ['Measurement point', fieldValue('measurement_location')],
    ['ISO group', ISO_GROUP_LABEL[fieldValue('iso_group')] || ''],
    ['Support', SUPPORT_LABEL[fieldValue('iso_support')] || ''],
    ['Coupling', COUPLING_LABEL[fieldValue('coupling')] || ''],
    ['Bearing', bearingFact(fieldValue)],
    ['Velocity unit', unitLabel(fieldValue('velocity_unit'))],
  ];
  const geometry = geometryFields().filter((g) => fieldValue(g.name))
    .map((g) => `${g.label} ${fieldValue(g.name)}`);
  if (geometry.length) rows.push(['Geometry', geometry.join(' · ')]);
  return rows.filter(([, value]) => !!value);
}

/** The one-line version, on the step that loaded the machine. The full list is
 *  on Review, where it is the last thing read before the run. */
function renderCarried(card) {
  const box = document.getElementById('carried');
  if (!box) return;
  const rows = analysisFacts();
  if (!card || !rows.length) { box.innerHTML = ''; setHidden(box, true); return; }
  box.innerHTML = `<b>Carried over from this machine:</b> ${
    rows.map(([label, value]) => `${esc(label)} ${esc(value)}`).join(' · ')}.
    All of it is on the review step before you run, and anything you change here is what we use.`;
  setHidden(box, false);
}

if (savedSel) {
  savedSel.addEventListener('change', () => {
    if (forgetBtn) forgetBtn.disabled = !savedSel.value;
    if (savedSel.value === '') return;
    applyCard(readMachines()[Number(savedSel.value)]);
  });
}

if (forgetBtn) {
  // Session UX-5 (C11). This button used to splice the card out of the store
  // here, on the spot, with no confirmation -- and it deleted only the CARD,
  // leaving the machine's saved readings behind under the same key. The result
  // was a machine that vanished from this dropdown and stayed on the machines
  // list with every fact rendering "Not stated": UX-2's card-less-trend defect,
  // reachable from a one-click control.
  //
  // There is one delete path now and this is a route to it: the machine's own
  // page, where `deletePanel` names what will go (the card AND its readings),
  // takes the alias typed back, and reports through the confirmation component.
  // The stranger called that panel good; the fix is to send this control there
  // rather than to give it a second, weaker mechanism of its own.
  forgetBtn.addEventListener('click', () => {
    if (savedSel.value === '') return;
    const chosen = readMachines()[Number(savedSel.value)];
    if (!chosen) return;
    // The HASH, not `machineHref`. That helper builds an `href` for an anchor
    // and carries a leading '/'; `navigate` assigns to `location.hash`, so
    // handing it a path would produce '#/#/m/...' and land nowhere. The two
    // shapes of "where a machine lives" are `submitMachineEdit`'s form and
    // this one, and they are the same string.
    navigate('#/m/' + encodeURIComponent(cardKey(chosen)));
  });
}

renderSavedMachines();

// ── one confirmation, for every lifecycle action (Session UX-4) ────────
//
// STRANGER U12: "Inline confirmation copy is small grey text ('Saved to this
// machine's trend, in this browser.'); rename gave no confirmation at all;
// deleting a reading is a red 'Forget / Cancel' toggle that shifts the
// column; only Forget machine gets a proper type-to-confirm box."
//
// Four different shapes for one job, and one action that did the job silently.
// This is the fifth shape and it replaces the reporting half of the other
// four — the type-to-confirm box for Forget machine STAYS, because it is a
// guard rather than a report and the stranger called it good.
//
// ONE AT A TIME, deliberately. A stack looks richer and is worse here: the
// region is re-rendered by assigning innerHTML (which is how the node harness
// can see it at all), so every render recreates every element and replays
// every enter animation — a second toast would make the first one flash. The
// actions this reports are discrete and sequential; the newest one is the one
// the analyst is waiting to hear about.

const TOAST_HOLD_MS = 5200;
// Session UX-5. A confirmation carrying an UNDO has to outlive the moment the
// analyst notices it: 5.2s is long enough to read "Saved" and too short to
// read it, decide it was wrong, and reach the control.
const TOAST_ACTION_HOLD_MS = 12000;
const TOAST_EXIT_MS = 200;

let toastNow = null;
let toastLeaving = false;
let toastTimer = 0;
let toastSeq = 0;

function renderToasts() {
  const region = document.getElementById('toasts');
  if (!region) return;
  if (!toastNow) { region.innerHTML = ''; return; }
  const kind = toastNow.kind ? ' t-' + toastNow.kind : '';
  const leaving = toastLeaving ? ' leaving' : '';
  // Session UX-5. An optional ACTION, for the one case where reporting what
  // happened is not enough because it happened without being asked for: the
  // auto-save (C6). It is a button inside the same one component rather than a
  // second surface, and it is absent unless a caller supplied one -- every
  // other confirmation in the file renders byte-identically to before.
  const action = toastNow.action
    ? `<button type="button" class="t-act" id="toast-action">${esc(toastNow.action.label)}</button>`
    : '';
  region.innerHTML = `<div class="toast${kind}${leaving}">`
    + `<span class="t-msg">${esc(toastNow.message)}</span>`
    + action
    + '<button type="button" class="t-x" id="toast-dismiss" '
    + 'aria-label="Dismiss this message">&#215;</button></div>';
}

/** Say that something happened. `kind` selects a fill from the one colour
 *  system and is NEVER the only carrier — the sentence says it too.
 *
 *  `action` is `{label, run}` and is optional. It exists for an outcome the
 *  analyst did not ask for and may want back -- and it holds the toast longer,
 *  because a control that leaves in 5.2 seconds is a control that was not
 *  really offered. */
function notify(message, kind, action) {
  if (!message) return;
  if (toastTimer) { clearTimeout(toastTimer); toastTimer = 0; }
  toastSeq += 1;
  toastNow = { message: String(message), kind: kind || '', id: toastSeq,
    action: (action && action.label && action.run) ? action : null };
  toastLeaving = false;
  renderToasts();
  toastTimer = setTimeout(dismissToast, toastNow.action ? TOAST_ACTION_HOLD_MS : TOAST_HOLD_MS);
}

/** Take it away. Two phases so it can animate out; one phase under reduced
 *  motion, where the exit animation does not run and waiting for it would
 *  leave the message on screen for 200ms of nothing. */
function dismissToast() {
  if (!toastNow) return;
  if (toastTimer) { clearTimeout(toastTimer); toastTimer = 0; }
  if (reduceMotion) { toastNow = null; toastLeaving = false; renderToasts(); return; }
  toastLeaving = true;
  renderToasts();
  toastTimer = setTimeout(() => {
    toastNow = null;
    toastLeaving = false;
    renderToasts();
  }, TOAST_EXIT_MS);
}

const toastRegion = document.getElementById('toasts');
if (toastRegion) {
  toastRegion.addEventListener('click', (e) => {
    if (!e.target) return;
    if (e.target.id === 'toast-dismiss') { dismissToast(); return; }
    if (e.target.id === 'toast-action' && toastNow && toastNow.action) {
      const run = toastNow.action.run;
      // Taken away FIRST: the action re-renders the card it was reporting on,
      // and a stale confirmation sitting under a corrected result is the
      // failure this component exists to stop.
      dismissToast();
      run();
    }
  });
}

// ── the machine's trend, browser-side ONLY (Session HIST-1) ────────
// One point per finished analysis, kept on the analyst's own device and posted
// back with the NEXT upload for the same machine and measurement point, where
// the server's existing Layer-4 trend runs on it. Nothing is stored on our
// side: the readings arrive inside a request and leave with it.
//
// A SIBLING key, deliberately, rather than a field on `vib.machines.v2`:
// `migrateEntry` above rebuilds a saved card from the MEMORY_FIELDS whitelist
// and String()-coerces every value, so an array hung on a card is destroyed on
// the next read. Beyond that mechanic the two stores are different promises --
// a machine card is what you TYPED and never leaves, a trend is what we
// MEASURED and is sent back -- so they get their own retention line and their
// own paragraph on /privacy. Old cards are untouched; read-compat costs nothing.
//
// Identity is the machine alias plus the measurement location, read from the
// form exactly the way `rememberCurrentMachine` reads it, so a machine and its
// trend are keyed on the same two answers. An UNNAMED machine gets no trend:
// 'Unnamed machine' is what the form substitutes for a blank alias, and two
// different unnamed machines would otherwise accumulate into one series that
// belongs to neither.
const TREND_KEY = 'vib.trend.v1';
// Mirrors HISTORY_MAX_POINTS in adapters/uploads/common.py, and pinned equal to
// it. The server refuses a longer card with a 422; trimming here means an
// analyst who has been saving for years never meets that refusal.
const TREND_MAX_POINTS = 400;
// `UNNAMED_MACHINE` was declared here until Session UX-5. It moved up beside
// MEMORY_FIELDS because `migrateEntry` now needs it at module-init time; see
// the note there. `namedMachine` below is unchanged and still the only reader.

function trendKey(alias, location) {
  // Session INTAKE-2. A blank location normalises to `Default` HERE, at the one
  // choke point every reader and writer of the trend store goes through, so
  // nothing can write the old blank-location shape again and
  // `migrateTrendLocations` only ever has pre-existing data to move. Doing it in
  // the migration alone would have left new readings being written as `alias|`
  // and migrated back on the next read -- a churn that makes `RETAINED.trendCount`
  // disagree with what is stored.
  //
  // The word matches `app.py::_FIRST_LOCATION_LABEL`, so the label the server
  // gives an unnamed first location and the key the browser files it under are
  // the same string.
  const point = String(location || '').trim() || LEGACY_BLANK_LOCATION_LABEL;
  return `${String(alias || '').trim()}|${point}`;
}

function namedMachine(alias) {
  const name = String(alias || '').trim();
  return !!name && name !== UNNAMED_MACHINE;
}

/** The series this submission belongs to, or '' when it belongs to none.
 *  '' for an unnamed machine, and for any mode the server would refuse a
 *  history in: `trend` builds its history from the analyst's own file, and a
 *  comparison is two readings of one point in one request. */
function currentTrendKey() {
  const alias = (form.elements.machine_alias && form.elements.machine_alias.value) || '';
  const loc = (form.elements.measurement_location && form.elements.measurement_location.value) || '';
  if (!namedMachine(alias)) return '';
  if (String((modeSel && modeSel.value) || 'spectrum') !== 'spectrum') return '';
  return trendKey(alias, loc);
}

// ── Session INTAKE-2 — the location half of the trend key ────────────────────
// The key has always been `alias|location`, and the D-24 replacement has always
// been within it on date + axis, so the tuple is already machine + location +
// date + axis. What changes is what `location` MEANS: it used to be the single
// machine-level `measurement_location` field, and now it is the label of one of
// up to eight points.
//
// The one thing that needs migrating is the series saved with a BLANK location.
// They are readings of a real point -- the analyst just never named it -- and
// under the new model an unnamed point is `Default`, which is also what the
// server calls the first location when the field is blank
// (`app.py::_FIRST_LOCATION_LABEL`). One word, in both places, so a machine's
// history does not split between "" and "Default".
//
// A NON-BLANK old location keeps its own label (operator ruling R-2). The brief
// said "existing browser-held history migrates to a location labelled Default";
// taken literally that would rename "Motor DE" -- a label the analyst typed --
// to "Default", and merge two points of one machine into one series. The ruling
// is recorded in outputs/SESSION_INTAKE2.md.
const LEGACY_BLANK_LOCATION_LABEL = 'Default';

/** Migrate blank-location series onto `Default`, in place, once.
 *  Idempotent: a store that has already been migrated has no blank-location
 *  keys left, so a second pass finds nothing. Merges rather than overwrites if
 *  both "" and "Default" somehow exist -- losing readings to a key collision is
 *  the one outcome worse than an ugly key. */
function migrateTrendLocations(store) {
  let moved = 0;
  Object.keys(store).forEach((key) => {
    const cut = key.lastIndexOf('|');
    if (cut < 0 || key.slice(cut + 1) !== '') return;   // named, or not a key
    const target = key.slice(0, cut + 1) + LEGACY_BLANK_LOCATION_LABEL;
    const incoming = Array.isArray(store[key]) ? store[key] : [];
    const existing = Array.isArray(store[target]) ? store[target] : [];
    const seen = new Set(existing.map((p) => p && p.ts));
    store[target] = existing
      .concat(incoming.filter((p) => p && !seen.has(p.ts)))
      .sort((a, b) => (a.ts < b.ts ? -1 : (a.ts > b.ts ? 1 : 0)));
    delete store[key];
    moved += 1;
  });
  return moved;
}

function readTrendStore() {
  try {
    const parsed = JSON.parse(localStorage.getItem(TREND_KEY) || '{}');
    const store = (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) ? parsed : {};
    // Migrated on READ, not behind a version flag: the store is a plain map and
    // every reader goes through here, so there is no path that can see the old
    // shape. Written back only when something actually moved, so a migrated
    // store costs no write on every read.
    if (migrateTrendLocations(store)) writeTrendStore(store);
    return store;
  } catch (err) { return {}; }  // private mode / disabled storage / corrupt value
}

function writeTrendStore(store) {
  try { localStorage.setItem(TREND_KEY, JSON.stringify(store)); } catch (err) { /* nothing to do */ }
}

/** Every stored reading for one machine and point, oldest first.
 *  Anything that is not a usable reading is dropped on READ rather than
 *  trusted. This store sits on the analyst's own device, where an older build,
 *  a hand edit or another tool could have left something else behind — and a
 *  bad reading posted back comes home as a 422 they cannot act on. */
function readTrend(key) {
  const raw = readTrendStore()[key];
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((p) => p && typeof p.ts === 'string' && p.ts
      && typeof p.v === 'number' && Number.isFinite(p.v) && p.v >= 0)
    .sort((a, b) => (a.ts < b.ts ? -1 : (a.ts > b.ts ? 1 : 0)));
}

/** Add one finished analysis to a series.
 *
 *  Idempotent on the analysis stamp, so a second Save on the same job keeps one
 *  reading rather than two -- that has been true since HIST-1 and is unchanged.
 *
 *  Session UX-2 added `mode`, RULED D-24. Session UX-5 carries D-24 AS AMENDED:
 *  a reading for the same machine, same measurement date, same axis is KEPT
 *  ALONGSIDE unless the analyst explicitly asked to replace. The test is
 *  written against the one mode that now replaces -- `mode === 'replace'` --
 *  for the same reason UX-2 wrote its inverse: a call site that passes nothing
 *  must get the ruling's default rather than its opposite, and the default is
 *  now the other one. Before/after a repair on one day is the flagship flow,
 *  and it was the case the old default destroyed.
 *
 *  The date is `ts.slice(0, 10)` on both sides, i.e. UTC -- the same slice
 *  `readingsTable` prints. A local date would make the intake's duplicate
 *  notice and this filter disagree for anyone west of UTC after about 17:00. */
function saveTrendPoint(key, point, mode) {
  const replace = mode === 'replace';
  const day = String(point.captured_at || '').slice(0, 10);
  const axis = point.dominant_axis || '';
  const kept = readTrend(key).filter((p) => {
    if (p.ts === point.captured_at) return false;
    if (!replace) return true;
    return !(p.ts.slice(0, 10) === day && (p.axis || '') === axis);
  });
  kept.push({
    ts: point.captured_at,
    v: point.severity_rms_mms,
    zone: point.iso_zone,
    axis: point.dominant_axis || '',
  });
  kept.sort((a, b) => (a.ts < b.ts ? -1 : (a.ts > b.ts ? 1 : 0)));
  const store = readTrendStore();
  store[key] = kept.slice(-TREND_MAX_POINTS);
  writeTrendStore(store);
  return store[key];
}


// ── the report, kept with the reading (Session UX-5, RULED D-26) ───
//
// STRANGER B4: "Reports are not kept with a reading — each one is deleted 60
// minutes after it is produced. To read this analysis again, upload the file
// again. ... For $30 I expect the PDF to live with the reading in my browser
// (it's already localStorage-first). Right now the thing I paid for is the one
// thing the app won't keep."
//
// RULED D-26: the PDF and the narrative are kept CLIENT-SIDE with the reading,
// in the browser's own storage. Server retention is unchanged and nothing new
// crosses the wire -- this fetches the same `/api/jobs/<id>/report.pdf` the
// analyst can already click, during the 60 minutes it is already served for,
// and keeps the bytes here. The server deletes its copy on exactly the schedule
// it always did; what changes is that the analyst's own copy outlives it.
//
// THE NARRATIVE COMES WITH IT AND IS NOT A SECOND THING. There is no endpoint
// that serves the drafted prose on its own -- `report.md` is deleted at
// completion (`worker.py::_keep_only_report`) and was never served -- so the
// narrative exists in exactly one downloadable artifact, the PDF. Keeping the
// PDF keeps the narrative; a separate narrative store would need a route in
// `app.py`, which this session may not open, and is recorded rather than faked.
//
// TWO STORES, AND THE SPLIT IS THE POINT.
//
//   * The FACTS -- file name, committed call, confidence, job reference -- are
//     small strings, and they go in `localStorage` beside the trend they
//     describe. They are what the readings table renders, so they have to be
//     readable synchronously while a row is being drawn.
//   * The BYTES go in IndexedDB. A report is ~250-350 KB and localStorage is a
//     ~5 MB origin-wide quota SHARED with `vib.machines.v2` and `vib.trend.v1`:
//     a dozen reports would fill it, and a full quota makes `setItem` THROW --
//     so the failure mode of putting PDFs there is that saving a READING stops
//     working, silently, in a catch that has nowhere to report to. That is a
//     worse product than not keeping the PDF at all.
//
// NOTHING HERE IS ON THE CRITICAL PATH. Every function resolves rather than
// rejects, and a browser with IndexedDB disabled or full gets the run it always
// got, the reading saved, and a row that says no report is held. A kept report
// is a convenience the analyst is told about; it is never a precondition for
// the analysis, the save, or the trend.
const REPORTS_KEY = 'vib.reports.v1';
const REPORT_DB = 'vib.reports.v1';
const REPORT_STORE = 'pdf';

/** The id one report is filed under: the series, and the stamp of the reading
 *  it belongs to. The same pair the facts store keys on, so the two halves of
 *  "this reading's report" cannot come apart. */
function reportId(key, ts) {
  return `${String(key || '')}|${String(ts || '')}`;
}

function readReportFacts() {
  try {
    const parsed = JSON.parse(localStorage.getItem(REPORTS_KEY) || '{}');
    return (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) ? parsed : {};
  } catch (err) { return {}; }  // private mode / disabled storage / corrupt value
}

function writeReportFacts(store) {
  try { localStorage.setItem(REPORTS_KEY, JSON.stringify(store)); } catch (err) { /* nothing to do */ }
}

/** What we know about one machine's reports, by reading stamp. Read-filtered
 *  the way `readTrend` is: this store sits on the analyst's own device, where
 *  an older build or a hand edit could have left anything. */
function reportFactsFor(key) {
  const raw = readReportFacts()[key];
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
  const out = {};
  Object.keys(raw).forEach((ts) => {
    const f = raw[ts];
    if (!f || typeof f !== 'object') return;
    out[ts] = {
      file: String(f.file || ''),
      fault: String(f.fault || ''),
      confidence: String(f.confidence || ''),
      job: String(f.job || ''),
      pdf: !!f.pdf,
    };
  });
  return out;
}

/** File one reading's facts. Returns the series' facts as they now stand. */
function putReportFacts(key, ts, facts) {
  if (!key || !ts) return {};
  const store = readReportFacts();
  const series = Object.assign({}, store[key]);
  series[ts] = Object.assign({ file: '', fault: '', confidence: '', job: '', pdf: false },
                             series[ts] || {}, facts || {});
  store[key] = series;
  writeReportFacts(store);
  return series;
}

/** Forget one reading's facts, or a whole machine's. Called from the SAME
 *  places that delete a reading and a machine, so a report cannot outlive the
 *  thing it is a report about -- which would be a promise on /privacy that the
 *  code did not keep. */
function dropReportFacts(key, ts) {
  const store = readReportFacts();
  if (!store[key]) return;
  if (ts === undefined) delete store[key];
  else if (store[key][ts]) delete store[key][ts];
  if (store[key] && !Object.keys(store[key]).length) delete store[key];
  writeReportFacts(store);
}

/** Move a machine's reports when its identity changes, the way the trend
 *  moves. A rename that left the reports behind would strand them under a key
 *  nothing renders, i.e. keep them for ever with no way to delete them. */
function moveReportFacts(oldKey, newKey) {
  const store = readReportFacts();
  if (!store[oldKey] || oldKey === newKey) return;
  store[newKey] = store[oldKey];
  delete store[oldKey];
  writeReportFacts(store);
}

/** The PDF bytes, in IndexedDB.
 *
 *  EVERY FUNCTION HERE RESOLVES. None of them rejects and none of them throws:
 *  a browser in private mode, with storage disabled, over quota, or simply
 *  older than this feature must produce exactly the run it produced before
 *  D-26 -- the analysis, the report link that works for its 60 minutes, and the
 *  saved reading. `false` and `null` are the answers, and the UI says "no
 *  report kept" rather than showing an error about a convenience.
 *
 *  `indexedDB` is reached through `typeof` because the node harness's sandbox
 *  does not define it: a bare reference is a ReferenceError that would take the
 *  whole file down at parse-independent call time, which is exactly the class
 *  of failure this store is not allowed to introduce.
 */
function reportDbAvailable() {
  return typeof indexedDB !== 'undefined' && !!indexedDB;
}

function openReportDb() {
  return new Promise((resolve) => {
    if (!reportDbAvailable()) { resolve(null); return; }
    let req;
    try { req = indexedDB.open(REPORT_DB, 1); } catch (err) { resolve(null); return; }
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(REPORT_STORE)) {
        const store = db.createObjectStore(REPORT_STORE, { keyPath: 'id' });
        // By series, so a machine page reads its own reports in one request
        // rather than one per row.
        store.createIndex('key', 'key', { unique: false });
      }
    };
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => resolve(null);
    req.onblocked = () => resolve(null);
  });
}

/** One transaction, wrapped so a failure at ANY of the four places it can fail
 *  -- open, transaction, request, abort -- is one resolved `null`. */
function reportTx(mode, run) {
  return openReportDb().then((db) => new Promise((resolve) => {
    if (!db) { resolve(null); return; }
    let tx;
    try { tx = db.transaction(REPORT_STORE, mode); } catch (err) { resolve(null); return; }
    let out = null;
    tx.onabort = () => resolve(null);
    tx.onerror = () => resolve(null);
    tx.oncomplete = () => resolve(out);
    try {
      run(tx.objectStore(REPORT_STORE), (value) => { out = value; });
    } catch (err) { resolve(null); }
  })).catch(() => null);
}

/** Keep one report. `true` only if it is actually in the store. */
function putReport(key, ts, blob) {
  if (!key || !ts || !blob) return Promise.resolve(false);
  return reportTx('readwrite', (store) => {
    store.put({ id: reportId(key, ts), key, ts, blob });
  }).then(() => getReport(key, ts).then((held) => !!held));
}

function getReport(key, ts) {
  return reportTx('readonly', (store, done) => {
    const req = store.get(reportId(key, ts));
    req.onsuccess = () => done(req.result ? req.result.blob : null);
  });
}

/** Every report held for one machine, as {ts: blob}. */
function reportsFor(key) {
  return reportTx('readonly', (store, done) => {
    const out = {};
    let req;
    try {
      req = store.index('key').openCursor(IDBKeyRange.only(key));
    } catch (err) { done(out); return; }
    req.onsuccess = () => {
      const cursor = req.result;
      if (!cursor) { done(out); return; }
      if (cursor.value && cursor.value.blob) out[cursor.value.ts] = cursor.value.blob;
      cursor.continue();
    };
  }).then((out) => out || {});
}

function dropReport(key, ts) {
  return reportTx('readwrite', (store) => { store.delete(reportId(key, ts)); });
}

/** Every report for one machine, gone. Reached from the machine delete, so a
 *  "this cannot be undone, there is no copy anywhere else" promise stays true
 *  about the bytes as well as the row. */
function dropReports(key) {
  return reportTx('readwrite', (store) => {
    let req;
    try { req = store.index('key').openCursor(IDBKeyRange.only(key)); } catch (err) { return; }
    req.onsuccess = () => {
      const cursor = req.result;
      if (!cursor) return;
      cursor.delete();
      cursor.continue();
    };
  });
}

/** Carry a machine's reports to its new identity. Read-then-write rather than
 *  a cursor update, because `moveReportFacts` does the same and the two halves
 *  must survive the same rename. */
function moveReports(oldKey, newKey) {
  if (!oldKey || oldKey === newKey) return Promise.resolve(null);
  return reportsFor(oldKey).then((held) => {
    const stamps = Object.keys(held);
    if (!stamps.length) return null;
    return reportTx('readwrite', (store) => {
      stamps.forEach((ts) => {
        store.delete(reportId(oldKey, ts));
        store.put({ id: reportId(newKey, ts), key: newKey, ts, blob: held[ts] });
      });
    });
  });
}

/** Fetch the report this job produced and keep it with the reading.
 *
 *  Fire-and-forget by design, and it ends in a `.catch` for a reason node 26
 *  makes sharp: an unhandled rejection aborts the process, and this is called
 *  from a render path nobody awaits. Resolves to `true` only when the bytes are
 *  actually held, so the card can say so truthfully rather than optimistically.
 */
function keepReport(key, ts, jobId) {
  if (!key || !ts || !jobId) return Promise.resolve(false);
  if (!reportDbAvailable()) return Promise.resolve(false);
  return fetch(`/api/jobs/${encodeURIComponent(jobId)}/report.pdf`, { credentials: 'same-origin' })
    .then((res) => {
      // The harness's scripted responses are JSON-shaped; a real one is a PDF.
      // Asking whether this response can even produce bytes is cheaper than
      // discovering it cannot halfway through a transaction.
      if (!res || !res.ok || typeof res.blob !== 'function') return false;
      return res.blob().then((blob) => putReport(key, ts, blob));
    })
    .then((held) => {
      if (held) putReportFacts(key, ts, { pdf: true });
      return !!held;
    })
    .catch(() => false);
}

// ── the lifecycle, browser-side (Session UX-2) ─────────────────────
// UX-1 built the view and deliberately wrote nothing. These are the writes.
// They are PURE functions over the two stores -- no DOM, no fetch -- so the
// node suite can prove what they do to localStorage without a browser, and so
// the adapter below can put the same four verbs over a server.
//
// IDENTITY IS DERIVED AND STAYS DERIVED. A machine's id is `alias|location`,
// computed, never stored. Two independent things forbid a synthetic id here:
// `MEMORY_FIELDS` is pinned equal to the server's `CARD_FIELDS` by tuple
// equality (tests/test_db1_schema.py), so a new card field would demand a
// database column this session may not add; and webapp/db/importer.py reads
// `vib.trend.v1`'s KEYS as `alias|location`, so an export an analyst can
// produce today would stop parsing. The cost is stated rather than hidden: a
// rename changes the id, and therefore the URL, in this backend and does not
// in `db`, where the row has a uuid. `renameMachine` returns the id to go to.

/** The direction the analyst DECLARED, as the axis the analysis will report it
 *  on. Mirrors webapp/assembly.py's DIRECTION_TO_AXIS, plus the fact that this
 *  form's empty default option is labelled "Radial – horizontal" -- so '' is an
 *  answer here, not a blank. Used only to FORECAST a duplicate at intake; the
 *  axis that decides the save is the one the analysis actually reported. */
const DIRECTION_AXIS = { '': 'y', radial_h: 'y', radial_v: 'z', axial: 'x' };

/** The declared direction as the axis the analysis will report it on.
 *
 *  A function rather than a bare lookup because a top-level `const` is not a
 *  property of the global object: in the node harness -- and in anything else
 *  that loads this file as a script -- the table itself is unreachable, so the
 *  mapping could not be proved without one. An unknown direction claims no
 *  axis rather than guessing one. */
function declaredAxis(direction) {
  return DIRECTION_AXIS[String(direction || '')] || '';
}

/** Today, as a stored reading's date reads. UTC on both sides -- see
 *  `saveTrendPoint`. */
function todayUTC() {
  return new Date().toISOString().slice(0, 10);
}

/** The stored readings a save would REPLACE under D-24: same machine, same
 *  date, same axis. An empty `axis` means "any", which is what an intake that
 *  cannot know the axis yet is entitled to ask. */
function duplicateMatches(key, day, axis) {
  if (!key || !day) return [];
  return readTrend(key).filter((p) => p.ts.slice(0, 10) === day
    && (!axis || (p.axis || '') === axis));
}

/** Create a card. Refuses an unnamed machine and refuses a collision.
 *
 *  Unnamed is refused for HIST-1's reason: 'Unnamed machine' is what the form
 *  substitutes for a blank alias, so two of them would accumulate into one
 *  series belonging to neither. */
function createMachineLocal(fields) {
  const entry = {};
  MEMORY_FIELDS.forEach((f) => { entry[f] = String((fields && fields[f]) || ''); });
  entry.machine_alias = entry.machine_alias.trim();
  entry.measurement_location = entry.measurement_location.trim();
  if (!namedMachine(entry.machine_alias)) return { ok: false, reason: 'unnamed' };
  const id = cardKey(entry);
  const list = readMachines();
  if (list.some((m) => cardKey(m) === id)) return { ok: false, reason: 'exists', id };
  if (readTrendStore()[id]) return { ok: false, reason: 'exists', id };
  list.push(entry);
  writeMachines(list);
  return { ok: true, id };
}

/** Edit a card, and move its trend with it when the identity changes.
 *
 *  A COLLISION REFUSES; it never merges. Merging two series is a claim about
 *  measurement identity that only the analyst can make, and a wrong one
 *  fabricates a trend the report then assesses -- so the refusal names the
 *  other machine and lets them decide. The `db` half answers the same way for
 *  free: the UNIQUE constraint raises and the route returns 409.
 *
 *  The trend moves FIRST. There is no transaction across two localStorage
 *  keys and none is needed: `browserMachines()` is the UNION of the two
 *  stores, so a half-finished move leaves the series visible under the name
 *  the analyst just typed rather than losing it. Card-first would leave it
 *  visible under the OLD name, which reads like the rename failed. */
function updateMachineLocal(oldId, fields) {
  const list = readMachines();
  const idx = list.findIndex((m) => cardKey(m) === oldId);
  const entry = {};
  const base = idx > -1 ? list[idx] : {};
  MEMORY_FIELDS.forEach((f) => {
    entry[f] = String((fields && fields[f] !== undefined ? fields[f] : base[f]) || '');
  });
  entry.machine_alias = entry.machine_alias.trim();
  entry.measurement_location = entry.measurement_location.trim();
  if (!namedMachine(entry.machine_alias)) return { ok: false, reason: 'unnamed' };
  const id = cardKey(entry);
  if (id !== oldId) {
    const clash = list.some((m, i) => i !== idx && cardKey(m) === id) || !!readTrendStore()[id];
    if (clash) return { ok: false, reason: 'exists', id };
    const store = readTrendStore();
    const moving = store[oldId];
    if (moving) {
      store[id] = moving;
      delete store[oldId];
      writeTrendStore(store);
    }
    // The reports move with the readings, for the same reason the readings move
    // with the name: left behind, they would sit under a key no view renders --
    // kept for ever with nothing able to delete them.
    moveReportFacts(oldId, id);
    moveReports(oldId, id).catch(() => null);
  }
  if (idx > -1) list[idx] = entry; else list.push(entry);
  writeMachines(list);
  return { ok: true, id };
}

/** Forget a machine: the card AND every reading filed under it.
 *
 *  Readings first, for the same reason `auth/store.py::CASCADE_ORDER` puts
 *  them first -- the child before the thing it points at, so no state exists
 *  in which a series belongs to nothing. Here that is a discipline rather
 *  than a constraint, and it is written this way so the two halves of this
 *  feature read the same. */
function forgetMachineLocal(id) {
  const readings = readTrend(id).length;
  const store = readTrendStore();
  if (store[id]) { delete store[id]; writeTrendStore(store); }
  const list = readMachines().filter((m) => cardKey(m) !== id);
  writeMachines(list);
  // Session UX-5 / D-26, and it is what makes the delete panel's own sentence
  // true: "It was only ever in this browser, so there is no copy anywhere else
  // to restore it from." Every report filed under this machine goes with it.
  dropReportFacts(id);
  dropReports(id).catch(() => null);
  return { ok: true, readings };
}

/** Delete ONE saved reading, keyed by its stamp -- the only key the browser's
 *  record has, and the key the server route takes for exactly that reason. */
function deleteReadingLocal(id, ts) {
  const before = readTrend(id);
  const kept = before.filter((p) => p.ts !== ts);
  if (kept.length === before.length) return { ok: false, reason: 'missing', left: before.length };
  const store = readTrendStore();
  store[id] = kept;
  writeTrendStore(store);
  // Session UX-5 / D-26. The report goes WITH the reading. The ledger and
  // /privacy both say a kept report lasts "until you delete the reading", and a
  // PDF left behind under a stamp nothing renders would be a promise the code
  // did not keep -- and, worse, an undeletable one: no surface would ever offer
  // it again. The bytes are asynchronous and the facts are not, so the facts go
  // now and the blob goes when it can; neither can fail the delete.
  dropReportFacts(id, ts);
  dropReport(id, ts).catch(() => null);
  return { ok: true, left: kept.length };
}

/** What the SERVER is allowed to see: {ts, value}, and nothing else.
 *  The zone and the axis stay here. `ClientHistoryPoint` is a closed model and
 *  refuses them with a 422 — which is the contract working rather than a
 *  nuisance to route around, because the zone that matters is the one the
 *  server computes from the reading, not one we hand it. */
function trendPayload(key) {
  return readTrend(key).map((p) => ({ ts: p.ts, value: p.v }));
}

// The band the card badges with. NOT the ISO trend verdict: that one needs
// `trend.min_days` readings (14) and arrives in the report as soon as it can.
// This is rise over the OLDEST stored reading, answerable from the second point
// on, which is the number the analyst actually has for most of a route's life.
function trendRise(points) {
  if (points.length < 2) return null;
  const first = points[0].v;
  const last = points[points.length - 1].v;
  if (!(first > 0)) return null;              // no baseline to rise from
  return ((last - first) / first) * 100;
}

function trendBand(rise) {
  if (rise === null) return { cls: '', text: 'no baseline to compare against yet' };
  if (rise < 0) return { cls: 'zA', text: `${Math.abs(rise).toFixed(0)}% below the first reading` };
  if (rise < 25) return { cls: 'zA', text: `${rise.toFixed(0)}% above the first reading` };
  if (rise <= 50) return { cls: 'zC', text: `${rise.toFixed(0)}% above the first reading` };
  return { cls: 'zD', text: `${rise.toFixed(0)}% above the first reading` };
}

/** The trend card. Every number in it was computed by the server and returned
 *  on a job's status; this renders them and derives no severity of its own. */
function trendCard(key, points, facts) {
  if (!points.length) return '';
  const latest = points[points.length - 1];
  const band = trendBand(trendRise(points));
  // STRANGER C10: "Trend panel says '49% below the first reading' and nothing
  // about the bearing fault still being committed."
  //
  // The overall value falling is not the same claim as the fault being gone,
  // and a card that reports only the first invites the second to be read into
  // it -- which is the severity-truthfulness family this product exists to
  // avoid. So the committed call on the LATEST reading is named beside it,
  // from what was recorded at save time. Nothing is inferred: a reading saved
  // before this session holds no call and the line simply does not appear.
  const latestFact = (facts || {})[latest.ts] || {};
  const called = latestFact.fault
    ? `<div class="foot call">Latest reading: <b>${esc(latestFact.fault)}</b>${
        latestFact.confidence ? `, ${esc(latestFact.confidence)} confidence` : ''}. A falling
        overall value is not the same thing as a fault clearing — the report says which.</div>`
    : '';
  const zone = /^[A-D]$/.test(String(latest.zone || '')) ? latest.zone : '';
  const name = key.split('|')[0];
  const at = key.split('|')[1];
  return `<div class="ledger" id="trend-card">
    <h5>Trend for ${esc(name)}${at ? ` · ${esc(at)}` : ''}</h5>
    <div class="rcards">
      <div class="rcard${zone ? ` sev z${zone}` : ' sev'}"><div class="lbl">Latest reading</div>
        <h4>${zone ? `Zone ${esc(zone)}` : 'unrated'}</h4>
        <div class="sub">${esc(latest.v.toFixed(2))} mm/s RMS</div></div>
      <div class="rcard ${esc(band.cls)}"><div class="lbl">Since the first reading</div>
        <h4>${esc(band.text)}</h4>
        <div class="sub">${points.length} reading${points.length > 1 ? 's' : ''} in this browser</div></div>
    </div>
    ${called}
    <div class="foot">These readings are sent with your next upload for this machine so the
      report can assess the trend. They stay in this browser; we keep no copy.</div>
  </div>`;
}

/** The saved reading and what is kept with it, or the offer, or the one line
 *  that says why there is neither.
 *
 *  RULED (Sep 7): a reading run against a SELECTED machine is saved to it, with
 *  undo. STRANGER C6: "Reading is not saved to the machine automatically — you
 *  must click 'Save this measurement'. I only noticed because I was looking.
 *  The machine page said 'No readings saved' after my first run."
 *
 *  Silent non-save is how trend data is lost, and the trend is the thing the
 *  analyst comes back for (POSITIONING §9, step 7 Return). So a run against a
 *  machine the analyst has actually saved is filed against it, said out loud
 *  through the same confirmation component every other lifecycle action uses,
 *  and undoable from there.
 *
 *  A machine that was only TYPED is not saved to automatically -- it has no
 *  card, the analyst has not said it is one of theirs, and creating a series
 *  for it silently is the same error in the other direction. That case keeps
 *  the button it always had.
 *
 *  RULED D-24 AMENDED: the second ask is GONE. The intake asked once and its
 *  answer is applied here, against the axis the analysis actually reported --
 *  so a declared/reported mismatch can only ever keep both, never replace a
 *  reading the forecast never named. */
function trendBlock(data) {
  const point = data.trend_point;
  if (!point) return '';                       // nothing trendable was measured
  const key = RETAINED.trendKey;
  if (!key) {
    // The analysis is fine; it just has no named machine to belong to.
    return `<p class="help" style="margin-top:10px">Give this machine an alias to keep a trend
      for it — readings are grouped by the alias and the measurement location, and they stay in
      this browser.</p>`;
  }
  const points = readTrend(key);
  const saved = points.some((p) => p.ts === point.captured_at);
  const mode = (RETAINED.duplicate && RETAINED.duplicate.mode) || 'keep_both';
  let offer;
  if (saved) {
    offer = `<p class="help" style="margin-top:10px">Saved to this machine\u2019s trend, in this
      browser. <a href="${esc(machineHref(key))}">Open ${esc(key.split('|')[0])}</a> to see the
      trend, or to remove this reading.</p>`;
  } else {
    offer = `<div class="ready-actions"><button type="button" class="btn-ghost" id="save-trend"
         data-key="${esc(key)}" data-mode="${esc(mode)}">Save this measurement to this
         machine\u2019s trend</button></div>`;
  }
  return `${offer}${reportKeptLine(key, point.captured_at)}${
    trendCard(key, points, reportFactsFor(key))}`;
}

/** What this browser is holding of the report itself (D-26). Rendered from the
 *  FACTS store rather than from the fetch, because the fetch is fire-and-forget
 *  and this line must describe what is actually held rather than what was
 *  attempted -- the card is re-rendered when it lands. */
function reportKeptLine(key, ts) {
  if (!key || !ts) return '';
  const facts = reportFactsFor(key)[ts];
  if (facts && facts.pdf) {
    return `<p class="help" id="pdf-kept" style="margin-top:10px"><b>This report is kept with the
      reading, in this browser.</b> It stays on the machine\u2019s page after our copy is deleted,
      and it goes when you delete the reading.</p>`;
  }
  if (!reportDbAvailable()) {
    return `<p class="help" id="pdf-kept" style="margin-top:10px">This browser will not let us keep
      a copy of the report, so ours is the only one — <b>download it before it is deleted</b>.</p>`;
  }
  return '<p class="help" id="pdf-kept" style="margin-top:10px">Keeping a copy of this report in '
    + 'this browser\u2026</p>';
}

/** The committed call, in the report's own words, or '' when there was none.
 *  `result_summary.faults[0].label` is already the label `report/generate.py`
 *  prints (`FAULT_LABELS`, applied in `worker.py`), so nothing is translated
 *  here and the browser holds no second fault vocabulary. */
function committedLabel(data) {
  const rs = (data && data.result_summary) || {};
  if (rs.no_findings) return 'No significant findings';
  const first = (rs.faults || [])[0];
  return (first && first.label) ? String(first.label) : '';
}

/** Its computed confidence ordinal, as the rubric produced it. */
function committedConfidence(data) {
  const rs = (data && data.result_summary) || {};
  if (rs.no_findings) return '';
  const first = (rs.faults || [])[0];
  return (first && first.confidence) ? String(first.confidence) : '';
}

/** File this run against the machine it was run for, if that machine is one the
 *  analyst has actually saved. Returns what happened, so the caller can say it.
 *
 *  Called BEFORE the card renders, so the card draws the saved state rather
 *  than an offer it is about to contradict. */
function autoSaveReading(jobId, data) {
  const point = data && data.trend_point;
  const key = RETAINED.trendKey;
  if (!point || !key || !RETAINED.autosave) return null;
  if (readTrend(key).some((p) => p.ts === point.captured_at)) return null;
  const mode = (RETAINED.duplicate && RETAINED.duplicate.mode) || 'keep_both';
  const before = readTrend(key).length;
  RETAINED.trendCount = saveTrendPoint(key, point, mode).length;
  // C10's four columns, filed with the reading rather than derived later: the
  // job is gone in an hour and the file name never leaves this browser at all.
  putReportFacts(key, point.captured_at, {
    file: RETAINED.files, job: String(jobId || ''),
    fault: committedLabel(data), confidence: committedConfidence(data),
  });
  // Session INTAKEFIX-1 (INTAKE-2's F-3). `others` is filled BY
  // `saveOtherLocationReadings` rather than returned from it: that function's
  // return value is a count, and `tests/js/location_trend_tests.js` asserts the
  // count in four places. An out-parameter keeps the new information additive,
  // so another session's pins keep asserting exactly what they were written to
  // assert -- and it keeps "which entries were actually filed" in the ONE place
  // that decides it, instead of re-deriving the duplicate skip here.
  const others = [];
  saveOtherLocationReadings(data, mode, others);
  return { key, ts: point.captured_at, replaced: RETAINED.trendCount <= before, others };
}

/** File the OTHER measurement locations' readings, each under its own point.
 *
 *  Session INTAKE-2. `key` above is `alias|location` for the point the form's
 *  own fields describe; every other location on the machine has its own label
 *  and therefore its own series. Filing them all under one key is precisely the
 *  confusion this session exists to end -- four points of one machine
 *  interleaved into a series that belongs to none of them.
 *
 *  No report is filed with these: `report/` is REPORT-3's this round, so the
 *  document covers the first location only and there is no per-location PDF to
 *  keep. The READING is kept, because it is the thing a trend is made of.
 *
 *  `filed`, WHEN GIVEN, COLLECTS THE `{key, ts}` PAIRS THIS WROTE -- Session
 *  INTAKEFIX-1 closing INTAKE-2's F-3. That session declined the undo on the
 *  argument that "a run that saved four would need four", which is right, and
 *  is what the caller now does: one Undo, four removals, through the one
 *  `undoAutoSave` that already drops a reading with everything filed under it.
 *  An out-parameter and not a second return value, because the return value is
 *  a COUNT that `tests/js/location_trend_tests.js` asserts in four places --
 *  additive here, so those pins keep asserting what they were written to.
 *  What this deliberately does NOT do is invent a per-location control: there is
 *  still ONE confirmation, naming the machine, because one upload is one action
 *  and an analyst who did not mean to save it did not mean to save any of it. */
function saveOtherLocationReadings(data, mode, filed) {
  const others = (data && data.locations) || [];
  // No `RETAINED.autosave` check here: `autoSaveReading` -- the only caller --
  // has already returned if autosave is off, so a second copy of that decision
  // would be a rule in two places that can disagree, and it makes this function
  // untestable from the node harness (`RETAINED` is a top-level `const`, which
  // is a lexical binding rather than a property of the sandbox global).
  if (!others.length) return 0;
  const alias = (form.elements.machine_alias && form.elements.machine_alias.value) || '';
  if (!namedMachine(alias)) return 0;      // an unnamed machine gets no trend
  let saved = 0;
  others.forEach((entry) => {
    // `status` GATES THE NUMBERS -- the contract's section 5 rule 1, and this
    // is the browser half of it. Until Session INTAKEFIX-1 the server put a
    // gate-failed point's `trend_point` on the wire and this function filed it,
    // so a point the data-quality gate REFUSED to diagnose joined the machine's
    // trend as a reading. The producer no longer sends it; this refuses it
    // anyway, because a trend is the thing an analyst comes back for and one
    // undiagnosed point in it is a line on a chart nobody can account for.
    if (entry && entry.status && entry.status !== 'ok') return;
    const point = entry && entry.trend_point;
    const label = String((entry && entry.label) || '').trim();
    if (!point || !label) return;
    const key = trendKey(alias, label);
    if (readTrend(key).some((p) => p.ts === point.captured_at)) return;
    saveTrendPoint(key, point, mode);
    saved += 1;
    if (filed) filed.push({ key, ts: point.captured_at });
  });
  return saved;
}

/** Put it back. The reading, and everything filed with it -- a kept report that
 *  outlived the reading it is a report about would be a promise this session
 *  makes on the ledger and does not keep.
 *
 *  Called once per measurement location since Session INTAKEFIX-1, so the
 *  ledger count is updated only for the point it actually describes.
 *  `RETAINED.trendCount` is what the retention ledger renders -- "<b>This
 *  machine's trend readings</b> - N kept in this browser" -- and N is the series
 *  for `RETAINED.trendKey`, the point the form's own fields name. Assigning
 *  another location's length here would put a different point's count behind
 *  that sentence, which is a wrong number on the one panel that exists to say
 *  what is held. */
function undoAutoSave(key, ts) {
  const left = deleteReadingLocal(key, ts);
  dropReportFacts(key, ts);
  dropReport(key, ts).catch(() => null);
  if (key === RETAINED.trendKey) RETAINED.trendCount = readTrend(key).length;
  return left;
}

/** The form-side half of the promise: the analyst is told the readings will be
 *  sent BEFORE they submit, not after. A payload that leaves the browser
 *  silently is the one thing this feature must not do. */
function refreshTrendNote() {
  const note = document.getElementById('trend-note');
  if (!note) return;
  const key = currentTrendKey();
  const points = key ? readTrend(key) : [];
  if (points.length) {
    const at = key.split('|')[1];
    note.innerHTML = `<b>${points.length} saved reading${points.length > 1 ? 's' : ''}</b> for this
      machine${at ? ` at ${esc(at)}` : ''} will be sent with this upload so the report can assess
      the trend. They are stored in this browser only.`;
  }
  setHidden(note, points.length === 0);
}

['machine_alias', 'measurement_location'].forEach((name) => {
  const field = form.elements[name];
  if (field && field.addEventListener) {
    field.addEventListener('input', refreshTrendNote);
    field.addEventListener('change', refreshTrendNote);
  }
});
// `syncMode()` runs at module load, ABOVE this block, so it cannot call
// refreshTrendNote without hitting the temporal dead zone on TREND_KEY. The
// mode's own listener is attached here instead, after the consts exist.
if (modeSel) modeSel.addEventListener('change', refreshTrendNote);
if (savedSel) savedSel.addEventListener('change', refreshTrendNote);
refreshTrendNote();

// ── Session INTAKE-2 — the ISO group follows the nameplate, visibly ──
// The server derives this again and its answer is the authority; this exists so
// the analyst SEES the group change when they type a rating, rather than
// submitting a select that says Group 2 and reading Group 1 on the report.
//
// It writes the select rather than disabling it: an analyst who knows the group
// and not the rating still uses the select, and one who typed a rating can
// still override afterwards. What it must not do is move a group the analyst
// set while the rating field is empty -- '' from `isoGroupFromKw` means "we
// were told nothing", and is left alone.
function syncIsoGroupFromRating() {
  const grp = form.elements.iso_group;
  const kwField = form.elements.rated_kw;
  const help = document.getElementById('grp-help');
  if (!grp || !kwField) return;
  const derived = isoGroupFromKw(kwField.value);
  if (derived) grp.value = derived;
  if (help) {
    help.textContent = derived
      ? `Group ${derived}, from the rated power you entered.`
      : 'Assumed Group 2 unless you state a rated power.';
  }
}
if (form.elements.rated_kw) {
  form.elements.rated_kw.addEventListener('input', syncIsoGroupFromRating);
  form.elements.rated_kw.addEventListener('change', syncIsoGroupFromRating);
}

// ── machines → readings: the front door (Session UX-1) ────────────
// ONE ADAPTER, TWO BACKENDS. `getMachines()`/`getMachine()` read this
// browser's own localStorage on the `browser` backend and GET /api/machines on
// `db`; every view below renders from what they return and knows nothing about
// which one answered. The record shape is HIST-1's -- {ts, v, zone, axis} --
// on BOTH sides, which is what lets `trendCard`, `trendRise` and `trendBand`
// be reused here verbatim instead of written a second time against a second
// shape. Two severities that disagree is the failure that would produce.
//
// A MACHINE IS AN ALIAS AND A MEASUREMENT LOCATION, not an alias. That is
// already the identity of a trend (`trendKey`) and of a server-side row
// (`UNIQUE(user_id, machine_alias, measurement_location)`); on a route the
// measurement point is the thing that gets trended, so "Pump A" measured at
// two bearings is two machines here and says so.
//
// NOTHING BELOW DERIVES A SEVERITY. Every number it prints was computed by the
// server and stored by `saveTrendPoint`; a zone the browser never recorded
// renders as `unrated` and never as `A`.
const LANDING_IDS = ['hero', 'how', 'proof', 'showcase', 'start'];
const ISO_GROUP_LABEL = { 1: 'Group 1 — large (>300 kW)', 2: 'Group 2 — medium (15–300 kW)' };
const SUPPORT_LABEL = { rigid: 'Rigid', flexible: 'Flexible' };
const COUPLING_LABEL = {
  coupled: 'Coupled to a driven load',
  uncoupled: 'Not coupled (direct-driven fan, standalone rotor)',
};
// Session UX-5, RULED D-26. This line used to be the product's answer to
// "where is my report?" and the answer was "gone -- upload the file again".
// STRANGER B4 called it the worst of the blocking findings: "For $30 I expect
// the PDF to live with the reading in my browser. Right now the thing I paid
// for is the one thing the app won't keep."
//
// Our copy still goes in 60 minutes -- server retention is unchanged and the
// privacy page's promise about it is untouched. What changed is that the
// analyst's browser keeps its own, with the reading, so the machine page can
// open it afterwards.
const REPORT_KEPT_LINE = 'Reports are kept in this browser with the reading they belong to, so '
  + 'they stay after our copy is deleted. Ours goes 60 minutes after the analysis; yours goes '
  + 'when you delete the reading.';

/** Which store answers. The same meta tag the retention ledger reads, because
 *  the backend that keeps machines on a server is the backend that has an
 *  account to keep them for -- there is no second thing to ask. */
function storeMode() {
  return ACCOUNTS_ON ? 'db' : 'browser';
}

function machineSort(a, b) {
  if (a.alias !== b.alias) return a.alias < b.alias ? -1 : 1;
  return a.location < b.location ? -1 : (a.location > b.location ? 1 : 0);
}

/** Every machine this browser holds: the saved cards, UNION the trend series.
 *
 *  The union matters in both directions. A card saved with Remember but never
 *  analysed is a machine with no readings, and must still be listed or the
 *  analyst cannot find what they typed. A trend saved without Remember has no
 *  card at all, and dropping it would hide readings we are about to promise we
 *  are keeping. An UNNAMED machine is in neither list: HIST-1 withheld the
 *  trend offer from one precisely so two of them could not accumulate into a
 *  series belonging to neither, and listing them here would undo that. */
function browserMachines() {
  const byId = {};
  readMachines().forEach((card) => {
    if (!namedMachine(card.machine_alias)) return;
    const id = trendKey(card.machine_alias, card.measurement_location);
    byId[id] = {
      id,
      alias: String(card.machine_alias).trim(),
      location: String(card.measurement_location || '').trim(),
      card,
      readings: readTrend(id),
    };
  });
  const store = readTrendStore();
  Object.keys(store).forEach((id) => {
    if (byId[id]) return;
    const alias = id.split('|')[0];
    if (!namedMachine(alias)) return;
    byId[id] = {
      id, alias, location: id.split('|')[1] || '', card: null, readings: readTrend(id),
    };
  });
  return Object.keys(byId).map((k) => byId[k]).sort(machineSort);
}

/** A fetch failure the views can act on: 401 is "sign in", anything else is
 *  "we could not read it", and neither one is an empty list. An empty list is
 *  a claim -- "you have no machines" -- and we must not make it on a request
 *  that failed. */
function machinesFailed(status) {
  const err = new Error('machines unavailable');
  err.status = status;
  return err;
}

function fetchMachines(path) {
  return fetch(path, { credentials: 'same-origin' }).then((res) => {
    if (!res.ok) throw machinesFailed(res.status);
    return res.json();
  });
}

function getMachines() {
  if (storeMode() === 'browser') return Promise.resolve(browserMachines());
  return fetchMachines('/api/machines').then((body) => (body.machines || []).slice());
}

function getMachine(id) {
  if (storeMode() === 'browser') {
    const found = browserMachines().filter((m) => m.id === id);
    return Promise.resolve(found.length ? found[0] : null);
  }
  return fetchMachines('/api/machines/' + encodeURIComponent(id)).catch((err) => {
    if (err && err.status === 404) return null;
    throw err;
  });
}


// ── the same four verbs, over whichever store answers (Session UX-2) ──
// UX-1's rule, kept: the VIEW never learns which backend it is talking to.
// Each writer returns {ok, id?, reason?} on both sides, so a refusal reads the
// same whether it came from a collision in localStorage or a 409 from Postgres.

/** The CSRF token the server rendered INTO the page (app.py's `csrf_field`),
 *  not one read from a cookie -- the cookie is HttpOnly and stays that way.
 *  Empty on the `browser` backend, where the field is not rendered at all and
 *  no request needs it. */
function csrfToken() {
  const field = form && form.elements && form.elements.csrf;
  return (field && field.value) || '';
}

/** A JSON write against the machines API.
 *
 *  The token travels as a HEADER rather than a body field: a custom header
 *  cannot be set cross-origin without a preflight this app grants nobody, so
 *  it is a stronger double-submit than a form field would be here. Errors
 *  come back as {ok:false, reason} -- never a throw -- because every caller is
 *  a click handler that has to say something to the analyst either way. */
function writeMachines_(path, method, body) {
  return fetch(path, {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
    body: JSON.stringify(body || {}),
  }).then((res) => (res.ok
    ? res.json().then((data) => Object.assign({ ok: true }, data))
    : res.json().catch(() => ({})).then((data) => ({
      ok: false, status: res.status, reason: (data && data.reason) || 'failed',
      detail: (data && data.detail) || '',
    }))))
    .catch(() => ({ ok: false, status: 0, reason: 'offline' }));
}

function createMachine(fields) {
  if (storeMode() === 'browser') return Promise.resolve(createMachineLocal(fields));
  return writeMachines_('/api/machines', 'POST', { card: fields });
}

function updateMachine(id, fields) {
  if (storeMode() === 'browser') return Promise.resolve(updateMachineLocal(id, fields));
  return writeMachines_('/api/machines/' + encodeURIComponent(id), 'PUT', { card: fields });
}

function removeMachine(id, confirm) {
  if (storeMode() === 'browser') return Promise.resolve(forgetMachineLocal(id));
  return writeMachines_('/api/machines/' + encodeURIComponent(id), 'DELETE', { confirm });
}

function removeReading(id, ts) {
  if (storeMode() === 'browser') return Promise.resolve(deleteReadingLocal(id, ts));
  return writeMachines_('/api/machines/' + encodeURIComponent(id)
    + '/readings/' + encodeURIComponent(ts), 'DELETE', {});
}

// ── rendering ───────────────────────────────────────────────────

function machineHref(id) {
  return '/#/m/' + encodeURIComponent(id);
}

/** A zone, in words, with the tint as decoration on top of the sentence. The
 *  regex is `trendCard`'s: `not_assessable` and a missing zone are both
 *  `unrated`, because neither one is a grade and `A` would be a clean bill
 *  nobody issued. */
function zoneTag(zone) {
  const z = /^[A-D]$/.test(String(zone || '')) ? String(zone) : '';
  return z ? `<span class="ztag z${z}">Zone ${z}</span>` : '<span class="ztag">unrated</span>';
}

/** The direction glyph, and nothing but a glyph: `trendBand().text` next to it
 *  already carries the meaning in words, so this is aria-hidden and a reader
 *  that cannot see it loses nothing. */
function trendArrow(rise) {
  if (rise === null || rise === 0) return '';
  return `<span class="marrow" aria-hidden="true">${rise > 0 ? '↑' : '↓'}</span> `;
}

function machineTrendLine(readings) {
  if (!readings.length) return '<div class="mline muted">No readings saved yet</div>';
  const rise = trendRise(readings);
  return `<div class="mline ${esc(trendBand(rise).cls)}">${trendArrow(rise)}${
    esc(trendBand(rise).text)}</div>`;
}

function machineCard(m) {
  const latest = m.readings.length ? m.readings[m.readings.length - 1] : null;
  const type = (m.card && m.card.iso_group)
    ? 'ISO group ' + esc(m.card.iso_group)
    : '<span class="muted">ISO group not stated</span>';
  const last = latest
    ? `${esc(latest.ts.slice(0, 10))} · ${zoneTag(latest.zone)}`
    : '<span class="muted">Not yet analysed</span>';
  return `<a class="mcard" href="${esc(machineHref(m.id))}">
    <div class="mname">${esc(m.alias)}</div>
    <div class="mat">${m.location ? esc(m.location) : '<span class="muted">Measurement point not stated</span>'}</div>
    <div class="mtype">${type}</div>
    <div class="mlast">${last}</div>
    ${machineTrendLine(m.readings)}
  </a>`;
}

/** What to do, when there is nothing yet. Never a bare "no results": the
 *  empty state is the only instruction a first-time analyst gets. */
function machinesEmpty() {
  if (storeMode() === 'db') {
    return `<p class="help">No machines on this account yet. Analyse a file and save the
      reading, and the machine appears here.</p>`;
  }
  // STRANGER: the empty state was four lines of instruction. It is the first
  // thing a new analyst reads and it was the longest. One line, and the two
  // actions are in the header above it rather than described in prose.
  return `<p class="help">No machines yet \u2014 add one, or analyse a file and save the
    reading. Everything stays in this browser.</p>`;
}

function machinesSignIn() {
  // POSITIONING §7: "Errors and empty states talk like a colleague: what
  // happened, what to do, one line." "This deployment" is engineer-speak.
  return `<p class="help">Your machines are kept with your account.
    <a href="/login">Sign in</a> to see them.</p>`;
}

function machinesUnavailable() {
  return `<p class="help">Your machine list could not be read just now. This is our side, not
    yours — reload the page, and nothing you have saved is affected.</p>`;
}

/** STRANGER C2: on a first visit the very first thing on the home page was a
 *  grey "MACHINES — No machines yet…" panel, and the pitch was BELOW it. A
 *  stranger landed on an empty dashboard for a product they had not read
 *  about.
 *
 *  The markup order does not move -- `tests/test_ux1_views.py` pins `#state`
 *  as the last section in <main>, and the two view containers sit above the
 *  hero on purpose (index.html:46-52) so the node harness can prove them
 *  hidden. So the ORDER is decided in CSS, and this sets the one attribute it
 *  reads.
 *
 *  An ATTRIBUTE and not a class, deliberately: `tests/js/harness.js` records
 *  `setAttribute` in `_attrs` and stubs `classList` to no-ops, so a class here
 *  would be untestable in node -- true of the page and invisible to every
 *  suite. The same trap UX-2 F-1 and UX-3 F-12 are about.
 */
function orderHome(landingFirst) {
  const panel = document.getElementById('view-machines');
  if (panel && panel.setAttribute) {
    panel.setAttribute('data-first', landingFirst ? 'landing' : 'machines');
  }
}

/** Paint the home view, and decide the landing with it.
 *
 *  The landing is shown whenever the list is EMPTY OR UNAVAILABLE — an analyst
 *  with no machines, and a visitor who is not signed in, are both people the
 *  pitch is written for. An analyst who has machines gets their machines. */
function renderMachines() {
  const body = document.getElementById('machines-body');
  return getMachines().then((machines) => {
    const empty = machines.length === 0;
    if (body) {
      body.innerHTML = empty
        ? machinesEmpty()
        : `<div class="mcards">${machines.map(machineCard).join('')}</div>`;
    }
    LANDING_IDS.forEach((id) => hideById(id, !empty));
    orderHome(empty);
    focusHeading('machines-head');
  }).catch((err) => {
    if (body) body.innerHTML = (err && err.status === 401) ? machinesSignIn() : machinesUnavailable();
    LANDING_IDS.forEach((id) => hideById(id, false));
    orderHome(true);
    focusHeading('machines-head');
  });
}

function machineFacts(card) {
  const c = card || {};
  const rows = [
    // Session UX-2: this row has printed the ISO 20816 GROUP under the
    // heading "Machine type" since UX-1. A group is a size and mounting class,
    // not a machine type, and the product has no machine-type field at all --
    // so the label now says what the value is.
    ['ISO group', ISO_GROUP_LABEL[c.iso_group] || ''],
    ['Support', SUPPORT_LABEL[c.iso_support] || ''],
    ['Running speed', c.rpm ? c.rpm + ' rpm' : ''],
    ['Bearing', c.bearing_model || ''],
    ['Coupling', COUPLING_LABEL[c.coupling] || ''],
    // Session UX-5 (C11): the form says "Not stated" and this said "Not
    // recorded" about the same blank field, two screens apart. One word.
  ].map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${
    value ? esc(value) : '<span class="muted">Not stated</span>'}</dd></div>`).join('');
  return `<dl class="mfacts">${rows}</dl>`;
}

/** Newest first, because that is the row an analyst looks for. The ARRAY stays
 *  oldest-first everywhere else -- `trendRise` reads points[0] as the baseline
 *  -- so this reverses a copy and the caption says which way it runs. */
/** Session UX-5 (STRANGER C10): "Columns: Date · Overall mm/s RMS · ISO zone ·
 *  Forget. Two rows both dated 2026-09-07 with no time and no diagnosis — I
 *  can't tell which is before and which is after, and the BPFO call (the
 *  interesting part) isn't recorded at all."
 *
 *  Four columns answer those two complaints, and every value in them was
 *  computed by the server and stored at save time -- nothing here is derived,
 *  and a fact this browser never recorded renders as an em dash rather than as
 *  a guess. The report column is D-26's: a blob URL for the copy this browser
 *  holds, or nothing.
 *
 *  `facts` and `urls` are passed IN rather than read here, because the URLs are
 *  asynchronous (IndexedDB) and a table that fetched its own would render
 *  twice, with the second render silently leaking the first one's URLs. */
function readingsTable(readings, facts, urls) {
  if (!readings.length) {
    return '<p class="help">No readings saved for this machine yet.</p>';
  }
  const known = facts || {};
  const held = urls || {};
  // Session UX-2: one control per row, with the confirmation INLINE rather
  // than typed. A typed phrase is right for a machine (it names WHICH one, and
  // takes its readings with it); for a single row sitting under the pointer it
  // would be ceremony, and ceremony teaches people to type through it.
  const rows = readings.slice().reverse().map((p) => {
    const cell = pendingReading === p.ts
      ? `<button type="button" class="rlink stop" data-ryes="${esc(p.ts)}">${
           deleteWord(true)}</button>
         <button type="button" class="rlink" data-rno="1">Cancel</button>`
      : `<button type="button" class="rlink" data-rdel="${esc(p.ts)}"
           aria-label="${deleteWord(true)} the reading of ${esc(p.ts.slice(0, 10))}">${
             deleteWord(true)}</button>`;
    const f = known[p.ts] || {};
    const none = '<span class="muted">—</span>';
    // UTC on both sides, like every other stamp this file prints: the day the
    // duplicate rule is decided on and the time shown here must not disagree
    // for an analyst west of UTC after about 17:00.
    const time = p.ts.length > 11 ? p.ts.slice(11, 16) : '';
    const link = held[p.ts]
      ? `<a href="${esc(held[p.ts])}" target="_blank" rel="noopener">Open</a>
         <a href="${esc(held[p.ts])}" download="${esc(reportFileName(p.ts))}">Save</a>`
      : none;
    return `<tr>
      <td>${esc(p.ts.slice(0, 10))}</td>
      <td class="num">${time ? esc(time) : none}</td>
      <td class="fname">${f.file ? esc(f.file) : none}</td>
      <td class="num">${esc(p.v.toFixed(2))}</td>
      <td>${zoneTag(p.zone)}</td>
      <td>${f.fault ? esc(f.fault) : none}</td>
      <td>${f.confidence ? esc(f.confidence) : none}</td>
      <td class="rrep">${link}</td>
      <td class="racts">${cell}</td></tr>`;
  }).join('');
  return `<div class="tscroll"><table class="mtable">
    <caption>Readings saved for this machine, newest first</caption>
    <thead><tr><th scope="col">Date</th><th scope="col">Time (UTC)</th>
      <th scope="col">File</th><th scope="col">Overall mm/s RMS</th>
      <th scope="col">ISO zone</th><th scope="col">Committed diagnosis</th>
      <th scope="col">Confidence</th><th scope="col">Report</th>
      <th scope="col"><span class="vh">Actions</span></th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function machineNotFound() {
  return `<p class="help">That machine is not in this list any more. It may have been
    forgotten on the upload form, or saved in a different browser.</p>`;
}

/** The machine the page is currently showing, and its alias. Read by the
 *  delegated click handler, which is fired by a target that carries a row
 *  stamp but not a machine. */
let currentMachineId = '';
let currentMachineAlias = '';

/** The typed confirmation. The phrase is the machine's own alias rather than
 *  ACCOUNT-2's fixed word: there is only one account, so a fixed phrase names
 *  it unambiguously, and there are many machines. */
/** The verb. ONE verb, on both backends, and it is `delete`.
 *
 *  Session UX-2 made this backend-dependent for a good reason it stated
 *  honestly: `static/privacy.html` promises that *the "Forget" button deletes
 *  it*, that file belonged to another session, and naming the control anything
 *  else would have left the sentence describing a control that no longer
 *  existed. So the browser said Forget and only `db` said Delete.
 *
 *  Session UX-5 (STRANGER C11) closes it the other way: "Delete is called
 *  Forget everywhere." Forget is softer than what the button does -- it removes
 *  a machine and every reading filed under it, with no copy anywhere -- and two
 *  words for one action across two backends is the drift UX-2 was avoiding, one
 *  level up. `privacy.html` is LEGAL-1's this round, so the sentence it owes is
 *  written verbatim in `outputs/SESSION_UX5.md` and pinned as a strict xfail
 *  that turns green the moment LEGAL-1 lands it -- the D-22 debt is recorded
 *  and mechanical rather than remembered.
 *
 *  Kept as a function, rather than folded into the call sites, so there is
 *  still exactly one place the product's word for this is decided. */
function deleteWord(capital) {
  const word = 'delete';
  return capital ? word.charAt(0).toUpperCase() + word.slice(1) : word;
}

function deletePanel(m) {
  const n = m.readings.length;
  const readings = n
    ? ` and its <b>${n}</b> saved ${n === 1 ? 'reading' : 'readings'}`
    : ', which has no saved readings';
  const held = Object.keys(reportFactsFor(m.id || currentMachineId))
    .filter((ts) => reportFactsFor(m.id || currentMachineId)[ts].pdf).length;
  const reports = held
    ? ` The ${held} saved ${held === 1 ? 'report' : 'reports'} kept with ${
        held === 1 ? 'it' : 'them'} go too.`
    : '';
  const kept = storeMode() === 'db'
    ? 'This cannot be undone, and we keep no copy.'
    : 'This cannot be undone. It was only ever in this browser, so there is no copy anywhere '
      + 'else to restore it from.';
  return `<div class="mdanger">
    <p class="kv">${deleteWord(true)}ting <b>${esc(m.alias)}${
      m.location ? ' · ' + esc(m.location) : ''}</b>${readings}.${reports} ${kept}</p>
    <label for="md-confirm">Type the machine’s alias to confirm</label>
    <input type="text" id="md-confirm" autocomplete="off" placeholder="${esc(m.alias)}">
    <p class="help stop" id="md-error" hidden></p>
    <div class="ready-actions">
      <button type="button" class="cta danger" id="md-go">${
        deleteWord(true)} this machine</button>
      <button type="button" class="btn-ghost" id="md-cancel">Keep it</button>
    </div>
  </div>`;
}

/** What a saved report is called when the analyst saves it out of the browser.
 *  The reading it belongs to, so a folder of them sorts by machine and date. */
function reportFileName(ts) {
  return `report-${String(ts || '').slice(0, 10)}.pdf`;
}

/** Blob URLs for the reports this browser holds for one machine.
 *
 *  Revoked before the next set is made. A URL created on every render and never
 *  released is a leak the page cannot recover from -- the machine page
 *  re-renders on every delete, every cancel and every row click. */
let machineReportUrls = {};
function releaseReportUrls() {
  if (typeof URL === 'undefined' || !URL.revokeObjectURL) { machineReportUrls = {}; return; }
  Object.keys(machineReportUrls).forEach((ts) => {
    try { URL.revokeObjectURL(machineReportUrls[ts]); } catch (err) { /* already gone */ }
  });
  machineReportUrls = {};
}

function loadReportUrls(key) {
  releaseReportUrls();
  if (typeof URL === 'undefined' || !URL.createObjectURL) return Promise.resolve({});
  return reportsFor(key).then((held) => {
    Object.keys(held).forEach((ts) => {
      try { machineReportUrls[ts] = URL.createObjectURL(held[ts]); } catch (err) { /* skip */ }
    });
    return machineReportUrls;
  }).catch(() => ({}));
}

function renderMachine(id) {
  const head = document.getElementById('machine-head');
  const body = document.getElementById('machine-body');
  if (head) head.textContent = '';
  currentMachineId = id;
  return getMachine(id).then((m) => {
    if (!m) {
      if (body) body.innerHTML = machineNotFound();
      return null;
    }
    // The reports are asynchronous and the rest of the page is not. Drawn in
    // ONE pass with them, rather than painted and then patched: a table that
    // gains a column after the fact moves everything under the pointer.
    return loadReportUrls(id).then((urls) => ({ m, urls }));
  }).then((loaded) => {
    if (!loaded) return;
    const { m, urls } = loaded;
    const facts = reportFactsFor(id);
    if (head) head.textContent = m.alias + (m.location ? ' · ' + m.location : '');
    currentMachineAlias = m.alias;
    if (!body) return;
    body.innerHTML = `${machineFacts(m.card)}
      ${trendCard(trendKey(m.alias, m.location), m.readings, facts)}
      ${readingsTable(m.readings, facts, urls)}
      <p class="help">${esc(REPORT_KEPT_LINE)}</p>
      <div class="ready-actions">
        <a class="cta" href="/#/new?m=${encodeURIComponent(m.id)}">New reading for this machine</a>
        <a class="btn-ghost" href="/#/m/${encodeURIComponent(m.id)}/edit">Edit machine</a>
        ${confirmingDelete ? '' : `<button type="button" class="btn-ghost" id="m-delete">${deleteWord(true)} machine</button>`}
      </div>
      ${confirmingDelete ? deletePanel(m) : ''}`;
    focusHeading('machine-head');
  }).catch((err) => {
    if (body) body.innerHTML = (err && err.status === 401) ? machinesSignIn() : machinesUnavailable();
  });
}

/** Fill the form for a machine arrived at from its own page.
 *
 *  The alias and location are forced on top of the card rather than trusted
 *  from it: a machine known only from its trend has no card at all, and one
 *  whose card disagrees with the series it is filed under would silently start
 *  a second series on the next save. */
function prefillFor(id) {
  if (!id) return Promise.resolve();
  return getMachine(id).then((m) => {
    if (!m) return;
    const card = Object.assign({}, m.card || {}, {
      machine_alias: m.alias, measurement_location: m.location,
    });
    applyCard(card);
    // Session UX-5 (STRANGER C3): "Arrived from 'New reading for this machine',
    // but the dropdown still says 'Choose a saved machine…'". The form was
    // filled and the control that says WHICH machine it was filled from was
    // not, so the analyst could not tell whether their machine was selected or
    // whether they were about to create a second one. A machine known only from
    // its trend has no card to match; `renderSavedMachines` simply finds
    // nothing and leaves the placeholder, which is honest -- there is no saved
    // card to point at.
    renderSavedMachines(cardKey(card));
    refreshTrendNote();
  }).catch(() => { /* the form is usable without the prefill */ });
}


// ── the machine form, and the two verbs it serves (Session UX-2) ────
// Rendered from JS into a container index.html declares, never written into
// index.html itself: `bearing_model`, `iso_support` and `detection_type` are
// pinned to live under the More options disclosure and not before it, and a
// static second copy of those names would break that pin without changing
// anything on screen. See the comment beside #view-machine-edit.
//
// The vocabularies are the ones the server validates, borrowed from the labels
// UX-1 already reads cards back with, so a value this form can produce is a
// value the upload form could have produced.
/** The bearings we hold geometry for -- `config/bearings.json`, and pinned
 *  equal to it (tests/test_ux2_bearings.py) along with the copy in
 *  index.html, because three lists that must agree and do not is how a UI
 *  comes to offer a screen the analysis cannot run.
 *
 *  A CLOSED list, not a suggestion: `bearing_spec_from_form` raises for
 *  anything else, and it raises INSIDE the parse sandbox, so a typed model we
 *  do not hold used to reach the analyst as a PARSE_ERROR about their file. */
const BEARING_MODELS = [
  '6205', '6206', '6309',
  '22220 EK', 'NU216', 'SKF_6205',
  'MAFAULDA_ABVT', 'MFPT_NICE',
];

/** The catalogue entries that exist for a BENCHMARK RIG, not for a route.
 *
 *  Session UX-5 (STRANGER B5): "Eight entries, three of which are
 *  benchmark-dataset names with underscores, tells an analyst the bearing
 *  screen was built for the validation corpora and never generalised."
 *
 *  They are filtered HERE and `config/bearings.json` is NOT edited, which is
 *  the operator's instruction and is also the only correct place: the geometry
 *  is real, `bearing_spec_from_form` still honours all eight, and the .mat
 *  adapters name their own rig bearing without going through this form
 *  (`adapters/uploads/mfpt.py`). Hiding them from the SELECT costs the
 *  benchmark paths nothing and stops the route analyst reading a lab catalogue.
 *
 *  A function rather than a bare const, so the node suite can reach it: a
 *  top-level `const` is not a property of the sandbox global (UX-2 F-1 #1,
 *  UX-3 F-6). */
function corpusBearings() {
  return ['SKF_6205', 'MAFAULDA_ABVT', 'MFPT_NICE'];
}

/** A corpus key that is the SAME BEARING as one we offer, so a card holding it
 *  can be carried over rather than dropped. `SKF_6205` is the CWRU rig's
 *  6205 and `config/bearings.json` gives the two identical geometry -- the
 *  separate key exists so CWRU cases cite an explicitly-sourced entry. The
 *  other two rigs have no route equivalent and get the unlisted note. */
function corpusEquivalent(model) {
  return String(model || '') === 'SKF_6205' ? '6205' : '';
}

/** What the form offers: the catalogue minus the rigs. */
function offeredBearings() {
  const corpus = corpusBearings();
  return BEARING_MODELS.filter((k) => corpus.indexOf(k) === -1);
}
const BEARING_OPTIONS = [['', 'Not listed']].concat(
  BEARING_MODELS.filter((k) => corpusBearings().indexOf(k) === -1).map((k) => [k, k]));

// ── the bearing we do not hold (Session GEOM-1, STRANGER B5) ───────────
//
// "Eight entries [...] Nearly every real machine on my route will be 'Not
// listed', which silently turns the headline feature off." UX-5 hid the three
// rig keys, which fixed the lab-catalogue smell and left the blocking half
// standing: a route bearing still had no way in.
//
// `pdm_core` never wanted a catalogue. `BearingSpec` takes four numbers and
// `bearing_rca` computes BPFO/BPFI/BSF/FTF from them, so the analyst can BE
// the catalogue for their own bearing. The select above keeps its one meaning
// -- bearings whose geometry WE hold, diffed against config/bearings.json --
// and the four controls below are a different claim, which is why they are a
// revealed block rather than a sixth option in it.

/** The four controls, in the order the form asks for them and the order the
 *  label prints them. A function rather than a top-level const so the node
 *  suite can reach it (UX-2 F-1 #1, UX-3 F-6). */
function bearingGeometryFields() {
  return ['bearing_n_balls', 'bearing_ball_dia_mm', 'bearing_pitch_dia_mm',
    'bearing_contact_angle_deg'];
}

/** One number, as BOTH the page and the report write it: `46.0` -> `46`,
 *  `7.940` -> `7.94`.
 *
 *  The twin of `_geometry_number` in `adapters/uploads/common.py`, pinned
 *  byte-equal to it by `tests/test_geom1_geometry.py`. Fixed at four decimals
 *  and then trimmed rather than left to `String(n)`, because Python has no
 *  `String(n)`: this is the formatting both languages can agree on, and they
 *  have to agree -- the report prints the label the SERVER builds, and a page
 *  that spelled the same bearing differently would leave an analyst checking
 *  two strings against one datasheet. */
function geomNum(value) {
  const n = Number(value);
  if (!isFinite(n)) return '';
  const text = n.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
  return text || '0';
}

// ── Session INTAKE-2 — the ISO 20816-3 group, derived here and on the server ──
// The twin of `adapters/uploads/common.py::group_from_rated_kw`, and diffed
// against it in node by tests/test_intake2_machine_js.py for the same reason
// `geomNum` above is: the page shows one answer and the analysis uses the
// other, and two answers to "which ISO row is this machine on" is two different
// severity thresholds on one reading.
//
// The boundaries are duplicated rather than fetched because they are a
// STANDARD's constants, not a tunable -- and they are marked VERIFY on the
// Python side, where the reason lives (references/INDEX.md has no ISO 20816-3
// entry yet).
const ISO_GROUP_2_MIN_KW = 15.0;
const ISO_GROUP_1_MIN_KW = 300.0;

/** The ISO 20816-3 group for a rated power, or '' when nothing usable was
 *  typed. '' means "we were told nothing" and leaves the select alone -- it
 *  must never be read as a group. */
function isoGroupFromKw(value) {
  const text = String(value === null || value === undefined ? '' : value).trim();
  if (!text) return '';
  const kw = Number(text);
  if (!isFinite(kw) || kw <= 0) return '';
  return kw > ISO_GROUP_1_MIN_KW ? '1' : '2';
}

/** The label `BearingSpec.model` carries for a hand-entered bearing -- the
 *  twin of `bearing_geometry_label`.
 *
 *  Not a designation, deliberately. `report/` prints `bearing.model` wherever
 *  it names a bearing, so this one string is the report's only chance to say
 *  the numbers came from the analyst rather than from our catalogue -- and to
 *  print them, so somebody who typed 3.904 for 39.04 has a wrong BPFO AND the
 *  input beside it. */
function bearingGeometryLabel(n, ball, pitch, angle) {
  const count = Number(n) === 1 ? '1 element' : geomNum(n) + ' elements';
  return 'geometry as entered \u2014 ' + count
    + ', element ' + geomNum(ball) + ' mm'
    + ', pitch ' + geomNum(pitch) + ' mm'
    + ', contact ' + geomNum(angle) + '\u00b0';
}

/** The geometry a form or a card is carrying, as four numbers, or `null`.
 *
 *  Null for ANY incomplete answer. A partial one is a sentence
 *  `app.py::_bearing_geometry_422` owns and this file must not pre-empt by
 *  completing it with a guess -- the contact angle is the one exception, and
 *  it is not a guess: blank means 0 deg by the same deep-groove convention
 *  every entry in `config/bearings.json` is recorded under, and the form says
 *  so beside the control. */
function bearingGeometryValues(get) {
  const raw = bearingGeometryFields().map((name) => String(get(name) || '').trim());
  if (!raw[0] || !raw[1] || !raw[2]) return null;
  const nums = [Number(raw[0]), Number(raw[1]), Number(raw[2]), raw[3] ? Number(raw[3]) : 0];
  return nums.some((v) => !isFinite(v)) ? null : nums;
}

/** How the bearing for THIS RUN reads to an analyst: the catalogue name, or
 *  the entered geometry in the report's own words, or nothing. One function,
 *  so the page and the report cannot come to call one bearing two things. */
function bearingFact(get) {
  const model = String(get('bearing_model') || '').trim();
  if (model) return model;
  const values = bearingGeometryValues(get);
  return values ? bearingGeometryLabel(values[0], values[1], values[2], values[3]) : '';
}

/** A model and hand-entered geometry are two answers to one question, so the
 *  page never lets both be posted: choosing a catalogue bearing hides the
 *  block AND CLEARS IT. Hiding alone is not enough -- a hidden input still
 *  posts, which is the lesson `clearSlot` above is written from.
 *
 *  The clearing is the part that has to be SAID, and it is why this reaches
 *  for UX-4's confirmation component rather than doing it quietly: discarding
 *  what somebody entered is a thing to say, not a thing to do (UX-5's rule for
 *  the bearing a `<select>` silently reset). It carries UX-5's undo action,
 *  which puts both halves back -- the numbers AND the empty select -- because
 *  restoring one without the other would re-create the state this refuses.
 *
 *  `announce` is false at module load and whenever a saved card is applied:
 *  neither is the analyst discarding anything. */
function syncBearingGeometry(announce) {
  const box = document.getElementById('brg-geometry');
  const chosen = String(((form.elements || {}).bearing_model || {}).value || '').trim();
  setHidden(box, !!chosen);
  if (!chosen) return;
  const held = bearingGeometryFields()
    .map((name) => [name, String(((form.elements || {})[name] || {}).value || '')]);
  if (!held.some((pair) => !!pair[1])) return;
  held.forEach((pair) => setField(pair[0], ''));
  bearingGeometryAnnounced = false;
  if (!announce) return;
  notify('Bearing set to ' + chosen + '. The geometry you had entered was cleared \u2014 '
    + 'a run is analysed against one bearing.', 'ok', {
    label: 'Undo',
    run: () => {
      setField('bearing_model', '');
      held.forEach((pair) => setField(pair[0], pair[1]));
      syncBearingGeometry(false);
      renderUnlocks();
    },
  });
}

/** Said ONCE, when the four controls first add up to a bearing. The analyst
 *  typed four numbers into a section they had to open to find; that the screen
 *  is now on is the thing they are waiting to hear, and it is exactly what
 *  UX-4's component is for. Re-armed by a change that breaks the geometry
 *  again, so correcting a typo is confirmed rather than silently accepted. */
let bearingGeometryAnnounced = false;

// ── Session LIMITS-1c: the machine's own severity limits ─────────────────
//
// All three or none. `app.py::_thresholds_422` is the AUTHORITY -- it builds a
// real `MachineThresholds` and returns that model's own sentence -- and this is
// the immediate half, so an analyst is told at the field rather than after a
// round trip. The two are deliberately not the same code: the rules live once,
// in `models.py::MachineThresholds`, and the server is what enforces them.
// Session RENAME+PRICE (A3). `machines.machine_alias`'s column width, and
// `app.py::_ALIAS_MAX_CHARS`, which is the authority on the wire. ONE copy in
// this file: the upload input's `maxlength` attribute lives in index.html and
// the machines-card control below reads this constant, so the number is in two
// places in the browser rather than three.
const ALIAS_MAX = 120;
const LIMIT_FIELDS = ['limit_ab', 'limit_bc', 'limit_cd'];
const LIMIT_LABELS = { limit_ab: 'Zone A/B', limit_bc: 'Zone B/C', limit_cd: 'Zone C/D' };

/** The three as typed, trimmed. */
function limitTexts(get) {
  return LIMIT_FIELDS.map((name) => String(get(name) || '').trim());
}

/**
 * The first problem with the trio, as a sentence, or '' when there is none.
 *
 * Mirrors `_thresholds_422`'s ORDER as well as its rules: blank-check, then
 * positivity, then monotonicity -- so the analyst does not read one complaint
 * here and a different one from the server for the same input.
 */
function limitProblem(get) {
  const raw = limitTexts(get);
  const given = raw.filter((v) => v !== '');
  if (!given.length) return '';
  const missing = LIMIT_FIELDS.filter((name, i) => raw[i] === '').map((n) => LIMIT_LABELS[n]);
  if (missing.length) {
    return 'All three boundaries are needed \u2014 ' + missing.join(', ')
      + (missing.length === 1 ? ' is' : ' are')
      + ' still blank. Leave all three blank to judge against ISO 20816-3.';
  }
  const nums = raw.map(Number);
  if (nums.some((v) => !isFinite(v))) return 'Each boundary must be a number.';
  const bad = LIMIT_FIELDS.filter((name, i) => nums[i] <= 0).map((n) => LIMIT_LABELS[n]);
  if (bad.length) return bad.join(' and ') + ' must be greater than 0.';
  if (!(nums[0] < nums[1] && nums[1] < nums[2])) {
    return 'The Zone A/B, B/C and C/D limits must increase.';
  }
  return '';
}

/** The three as numbers, or null when `limitProblem` has anything to say. */
function limitValues(get) {
  if (limitProblem(get)) return null;
  const raw = limitTexts(get);
  return raw[0] === '' ? null : raw.map(Number);
}

/* Session RENAME+PRICE (A3) -- the alias cap's browser half.
 *
 * `app.py::_ALIAS_MAX_CHARS` is the AUTHORITY, exactly as `_thresholds_422` is
 * for the trio above; this is the immediate half, and the number is the same
 * one already on the input's `maxlength` and on the machines-card control at
 * :2621. Three copies of 120 in this file and one in `db/models.py`; the
 * server pin is what stops them drifting.
 *
 * WHY THIS IS NOT DEAD CODE BEHIND `maxlength`. `maxlength` constrains what a
 * person can TYPE or paste. It does not constrain a value assigned by script,
 * and this form is populated by script whenever a saved machine card is
 * restored into it -- a card written before the cap existed, or imported, can
 * put a longer alias in the box, and the first the analyst would otherwise hear
 * of it is a 422 after choosing a file.
 */
function aliasProblem(get) {
  const value = String(get('machine_alias') || '');
  if (value.length <= ALIAS_MAX) return '';
  return 'The machine alias is ' + value.length + ' characters and the longest we can store is '
    + ALIAS_MAX + ' \u2014 shorten it. It is a label, not a description.';
}

function showAliasError(message) {
  const box = document.getElementById('alias-error');
  if (!box) return;
  box.textContent = message || '';
  setHidden(box, !message);
}

function onAliasChange() {
  showAliasError(aliasProblem((name) => ((form.elements || {})[name] || {}).value));
}

function showLimitError(message) {
  const box = document.getElementById('lim-error');
  if (!box) return;
  box.textContent = message || '';
  setHidden(box, !message);
}

function onLimitChange() {
  showLimitError(limitProblem((name) => ((form.elements || {})[name] || {}).value));
}

function onBearingGeometryChange() {
  const complete = !!bearingGeometryValues((name) =>
    ((form.elements || {})[name] || {}).value);
  renderUnlocks();
  if (!complete) { bearingGeometryAnnounced = false; return; }
  if (bearingGeometryAnnounced) return;
  bearingGeometryAnnounced = true;
  notify('Bearing geometry entered \u2014 BPFO, BPFI, BSF and FTF will be computed from '
    + 'those numbers and matched against your spectrum.', 'ok');
}

if (form && form.elements) {
  // Session LIMITS-1c. `input` as well as `change`: a partial trio should stop
  // reading as an error the moment the third box is filled, not when it blurs.
  LIMIT_FIELDS.forEach((name) => {
    const el = (form.elements || {})[name];
    if (el && el.addEventListener) {
      el.addEventListener('change', onLimitChange);
      el.addEventListener('input', onLimitChange);
    }
  });
  // Session RENAME+PRICE. `input` as well as `change`, for `onLimitChange`'s
  // reason: an over-long alias should stop reading as an error the moment it is
  // shortened, not when the field blurs.
  const aliasEl = (form.elements || {}).machine_alias;
  if (aliasEl && aliasEl.addEventListener) {
    aliasEl.addEventListener('change', onAliasChange);
    aliasEl.addEventListener('input', onAliasChange);
  }
  const brgSelect = form.elements.bearing_model;
  if (brgSelect && brgSelect.addEventListener) {
    brgSelect.addEventListener('change', () => syncBearingGeometry(true));
  }
  bearingGeometryFields().forEach((name) => {
    const el = form.elements[name];
    if (el && el.addEventListener) el.addEventListener('change', onBearingGeometryChange);
  });
}
syncBearingGeometry(false);

/** A bearing a saved card holds that this build has no geometry for.
 *
 *  A `<select>` handed a value none of its options carry silently resets to
 *  empty, so a card saved when this was a free-text field loses what the
 *  analyst typed the moment it is applied. It never worked -- the screen only
 *  runs for the catalogue, and an unknown model used to fail as a PARSE_ERROR
 *  about the FILE -- but discarding what somebody entered is a thing to say,
 *  not a thing to do quietly. Recorded here and said in both places a card is
 *  applied. */
let unlistedBearing = '';

function noteUnlistedBearing(card) {
  const value = String((card && card.bearing_model) || '').trim();
  // Session UX-5: measured against what the select OFFERS, not against the
  // catalogue. A `<select>` resets to empty for any value its options do not
  // carry, so the three rig keys became droppable the moment they stopped
  // being offered -- and dropping what somebody entered is a thing to say.
  const offered = offeredBearings();
  unlistedBearing = (value && offered.indexOf(value) === -1) ? value : '';
  return unlistedBearing;
}

function unlistedBearingNote(value) {
  if (!value) return '';
  // Session UX-5. Two different situations reach here and they need different
  // sentences, because only one of them is the analyst's to fix:
  //   * a rig bearing this form no longer offers, whose geometry we DO hold
  //     and which has a route equivalent -- carried over, not lost;
  //   * anything else -- a model we hold no geometry for, which is UX-2's
  //     original case and is still the analyst's to answer.
  const same = corpusEquivalent(value);
  if (same) {
    return `<p class="help">The bearing saved for this machine, <b>${esc(value)}</b>, is a
      test-rig entry from our benchmark work. It is the same bearing as <b>${esc(same)}</b>, which
      is what this form now carries — we have selected that for you and the screen runs exactly as
      it did.</p>`;
  }
  return `<p class="help stop">The bearing saved for this machine,
    <b>${esc(value)}</b>, is not one we hold geometry for — so the bearing screen was never
    running for it, and the report will have said the family was not assessed. Choose one from
    the list, or leave it at <b>Not listed</b>.</p>`;
}

const EDIT_FIELDS = [
  // Session UX-3, SESSION_JOBDB.md F-3. `ALIAS_MAX` is
  // `machines.machine_alias`'s column width (`db/models.py::CARD_FIELD_MAX`),
  // the way the 60 below is `measurement_location`'s. SQLite does not enforce
  // VARCHAR length and Postgres does, so an uncapped alias is a row that fits
  // on a laptop and raises StringDataRightTruncation in production.
  //
  // **The upload wire's server-side check now EXISTS** -- `app.py::_alias_422`,
  // Session RENAME+PRICE (A3). UX-3 booked it as somebody else's to add and
  // JOBDB met it again; until it landed, an over-long alias was accepted by the
  // form, refused silently by `db/recorder.py`, and cost the analyst a run with
  // no machine row and nothing said. This control is still the immediate half.
  { name: 'machine_alias', label: 'Machine alias', type: 'text', required: true, maxlength: ALIAS_MAX,
    help: 'What you call this machine. Readings are grouped by this and the measurement point '
      + 'below, so two points on one machine are two entries here.' },
  { name: 'measurement_location', label: 'Measurement point', type: 'text', maxlength: 60,
    help: 'Where this reading is taken — “Motor DE”, “Pump NDE”. Blank is allowed and is '
      + 'kept as “not provided”.' },
  { name: 'rpm', label: 'Running speed', type: 'number', unit: 'rpm', min: '1', step: 'any',
    help: 'Every frequency in the analysis is derived from this.' },
  { name: 'iso_group', label: 'ISO 20816 group', type: 'select',
    options: [['', 'Not stated'], ['1', ISO_GROUP_LABEL[1]], ['2', ISO_GROUP_LABEL[2]]] },
  { name: 'iso_support', label: 'Support', type: 'select',
    options: [['', 'Not stated'], ['rigid', 'Rigid'], ['flexible', 'Flexible']] },
  { name: 'coupling', label: 'Coupling', type: 'select',
    options: [['', 'Not stated'], ['coupled', COUPLING_LABEL.coupled],
      ['uncoupled', COUPLING_LABEL.uncoupled]] },
  // Session UX-5 (B7). This form wrote every card the machines page creates and
  // never asked for a velocity unit, so every one of them carried '' -- which a
  // <select> of mm_s/in_s cannot hold, so the intake rendered the control BLANK
  // under a caption reading "Stated, never assumed". `applyCard` now resolves a
  // blank to mm_s; this is the other half, so new cards are not born blank.
  { name: 'velocity_unit', label: 'Velocity unit', type: 'select',
    options: [['mm_s', 'mm/s'], ['in_s', 'in/s']],
    help: 'What the amplitudes in your export are in. Stated, never assumed — it is the '
      + 'single most common cause of a severity that is wrong by a factor of 25.' },
  { name: 'bearing_model', label: 'Bearing model', type: 'select',
    options: BEARING_OPTIONS,
    help: 'Optional. Naming one of these turns on the rolling-element bearing screen — '
      + 'BPFO, BPFI, BSF and FTF computed from its geometry and matched against your '
      + 'spectrum. The match reads the radial channels, so an axial, thrust-loaded fault '
      + 'is still not assessed.' },
];

/** The control, with NO value in its markup.
 *
 *  Every field is filled afterwards by `fillEditForm`, assigning `.value` the
 *  way `setField` fills the upload form. Two reasons, and the second is the
 *  one that matters: a `<select>` filled by a `selected` attribute and one
 *  filled by `.value` are two code paths for one job, and only the second is
 *  what `applyCard` already does; and a value that lives only in an attribute
 *  is invisible to anything that READS the element, which is how a form comes
 *  to look right on screen and submit blanks. */
function editControl(f) {
  const id = 'me-' + f.name;
  if (f.type === 'select') {
    const opts = f.options.map(([v, label]) =>
      `<option value="${esc(v)}">${esc(label)}</option>`).join('');
    return `<select id="${id}" name="${esc(f.name)}">${opts}</select>`;
  }
  const attrs = [`type="${esc(f.type)}"`, `id="${id}"`, `name="${esc(f.name)}"`];
  if (f.maxlength) attrs.push(`maxlength="${esc(f.maxlength)}"`);
  if (f.min) attrs.push(`min="${esc(f.min)}"`);
  if (f.step) attrs.push(`step="${esc(f.step)}"`);
  return `<input ${attrs.join(' ')}>`;
}

/** Put a card into the rendered form -- the mirror of `readEditForm`, and the
 *  same shape as `applyCard`: one assignment per field, no value invented. */
function fillEditForm(card) {
  const c = card || {};
  EDIT_FIELDS.forEach((f) => {
    const el = document.getElementById('me-' + f.name);
    if (el) el.value = String(c[f.name] || '');
  });
}

/** The form, plus whatever this particular arrival needs said above it. */
function machineForm(notice) {
  const rows = EDIT_FIELDS.map((f) => `<div class="mfield">
      <label for="me-${esc(f.name)}">${esc(f.label)}${
        f.unit ? ` <span class="opt">(${esc(f.unit)})</span>` : ''}</label>
      ${editControl(f)}
      ${f.help ? `<div class="help">${esc(f.help)}</div>` : ''}
    </div>`).join('');
  return `${notice || ''}
    <div class="mform">${rows}</div>
    <div class="ready-actions">
      <button type="button" class="cta" id="me-save">Save machine</button>
      <a class="btn-ghost" id="me-cancel" href="/#/">Cancel</a>
    </div>
    <p class="help" id="me-error" hidden></p>`;
}

/** A refusal, in the words of the thing that refused. Both backends answer
 *  with the same `reason`, so this is written once. */
function editRefusal(res) {
  if (res.reason === 'unnamed') {
    return 'Give the machine an alias. Readings are grouped by the alias and the measurement '
      + 'point, so an unnamed machine cannot keep a trend.';
  }
  if (res.reason === 'exists') {
    return 'You already have a machine with that alias and measurement point. Two series are '
      + 'never merged automatically — that is a claim about which readings came from the same '
      + 'place, and only you can make it. Rename this one, or open the other machine and '
      + 'delete the readings you do not want.';
  }
  if (res.status === 401) return 'Your session has expired. Sign in again and retry.';
  if (res.reason === 'offline') return 'That did not reach us. Check your connection and retry; '
    + 'nothing has been changed.';
  return res.detail || 'That could not be saved. Nothing has been changed.';
}

function renderMachineEdit(id) {
  const head = document.getElementById('machine-edit-head');
  const body = document.getElementById('machine-edit-body');
  editingId = id;
  if (head) head.textContent = id ? 'Edit machine' : 'Add a machine';
  if (!id) {
    if (body) body.innerHTML = machineForm('');
    fillEditForm(null);
    focusHeading('machine-edit-head');
    return Promise.resolve();
  }
  return getMachine(id).then((m) => {
    if (!m) {
      if (body) body.innerHTML = machineNotFound();
      return;
    }
    // A machine known only from its trend has no card; the alias and location
    // are forced on top for `prefillFor`'s reason -- a card that disagrees
    // with the series it is filed under would start a second one on save.
    const card = Object.assign({}, m.card || {}, {
      machine_alias: m.alias, measurement_location: m.location,
    });
    // Session UX-5: recorded against the card as SAVED, then filled from the
    // card as a FORM can carry it -- the same order `applyCard` uses, and the
    // reason the note can still name a value the control no longer holds.
    const unlisted = unlistedBearingNote(noteUnlistedBearing(card));
    const note = m.readings.length
      ? `<p class="help">This machine has <b>${m.readings.length}</b> saved
         ${m.readings.length === 1 ? 'reading' : 'readings'}. Changing the alias or the
         measurement point moves them with it.</p>`
      : '';
    if (body) body.innerHTML = machineForm(unlisted + note);
    fillEditForm(cardForForm(card));
    focusHeading('machine-edit-head');
  }).catch((err) => {
    if (body) body.innerHTML = (err && err.status === 401) ? machinesSignIn() : machinesUnavailable();
  });
}

let editingId = '';
let pendingReading = '';
let confirmingDelete = false;

function readEditForm() {
  const out = {};
  EDIT_FIELDS.forEach((f) => {
    const el = document.getElementById('me-' + f.name);
    out[f.name] = el ? String(el.value || '') : '';
  });
  return out;
}

function showEditError(message) {
  const box = document.getElementById('me-error');
  if (!box) return;
  box.textContent = message || '';
  setHidden(box, !message);
}

function submitMachineEdit() {
  const fields = readEditForm();
  const write = editingId ? updateMachine(editingId, fields) : createMachine(fields);
  return write.then((res) => {
    if (!res.ok) { showEditError(editRefusal(res)); return res; }
    // The id may have MOVED: on this backend it is derived from the two
    // answers that were just edited. The adapter says where to go.
    // U12. A rename used to say nothing at all: the page navigated and the
    // analyst was left to infer it had worked. Named, because "Saved" does
    // not tell you WHICH machine when the alias is the thing you just typed.
    notify(editingId
      ? `Saved. \u201C${fields.machine_alias || 'This machine'}\u201D and its readings moved with it.`
      : `\u201C${fields.machine_alias || 'Machine'}\u201D added.`, 'ok');
    navigate('#/m/' + encodeURIComponent(res.id || editingId));
    return res;
  });
}

// ── routing ─────────────────────────────────────────────────────
// Hash routes, so every view is one document and the upload form the analyst
// arrives at is the SAME form, with the same handlers, that /' has always
// served. A hash that is not one of ours -- '#showcase' from the landing --
// is not a route and lands on home, where that anchor lives.

/** `?preview=` takes the whole page over for screenshots. It is not a route,
 *  and every view decision has to know that before it consults one. */
function previewMode() {
  return !!new URLSearchParams((location && location.search) || '').get('preview');
}

function parseRoute() {
  const raw = String((location && location.hash) || '').replace(/^#/, '');
  if (raw.charAt(0) !== '/') {
    // Session F2's per-person link, `/?code=XXXX`, is meant to be "one tap
    // from a run". Before the views existed the form was simply always on
    // screen; now that it is a view, a code in the link has to say which view
    // it belongs to, or the analyst arrives at a page whose form is hidden
    // with their code already typed into it where they cannot see it.
    const code = new URLSearchParams((location && location.search) || '').get('code');
    return { view: code ? 'new' : 'home', id: '', step: 1 };
  }
  const path = raw.split('?')[0];
  const query = raw.split('?')[1] || '';
  const parts = path.split('/');
  if (parts[1] === 'm' && parts[2]) {
    // Session UX-2. `#/m/new` is unambiguous because every real id contains a
    // '|' (alias|location) or is a uuid hex, and `machineHref` percent-encodes
    // both the pipe and any '/' in an alias -- so no machine's id can ever
    // arrive here as the bare word `new`.
    if (parts[2] === 'new') return { view: 'machine-edit', id: '' };
    if (parts[3] === 'edit') return { view: 'machine-edit', id: decodeURIComponent(parts[2]) };
    return { view: 'machine', id: decodeURIComponent(parts[2]) };
  }
  if (parts[1] === 'new') {
    // `#/new`, `#/new/2`, `#/new/3`, `#/new/4`. A step is a route so that Back
    // and Forward work through the form the way they work between views --
    // and so a half-filled intake is a URL the analyst can be sent back to.
    return {
      view: 'new',
      id: new URLSearchParams(query).get('m') || '',
      step: Number(parts[2]) || 1,
    };
  }
  return { view: 'home', id: '', step: 1 };
}

let routed = false;
let wantFocus = false;

/** Move focus to the view's heading -- but never on the FIRST paint, which is
 *  a page load and not a navigation. Stealing focus from someone who has just
 *  opened the site moves their caret and can scroll the page under them.
 *
 *  The decision is taken SYNCHRONOUSLY in `route()` and read here, because the
 *  renderers are async: a flag set to "we have routed once" at the end of
 *  route() is already true by the time the first render's callback runs, which
 *  is precisely the case it exists to exclude. */
function focusHeading(id) {
  if (!wantFocus) return;
  wantFocus = false;                 // one move per navigation, not per render
  const head = document.getElementById(id);
  if (head && head.focus) head.focus();
}

function route() {
  // `?preview=` owns the page for screenshots and takes it over below; a route
  // running underneath it would put the landing back.
  if (previewMode()) return;
  const r = parseRoute();
  // Taken here, before anything async starts. See `focusHeading`.
  wantFocus = routed;
  routed = true;
  hideById('view-machines', r.view !== 'home');
  hideById('view-machine', r.view !== 'machine');
  hideById('view-machine-edit', r.view !== 'machine-edit');
  setHidden(formCard, r.view !== 'new');
  // The state card belongs to the upload view, and only once it has something
  // to say: an empty aria-live region revealed on every visit announces
  // nothing and takes up the space where the form should be.
  setHidden(stateCard, r.view !== 'new' || stateCard.innerHTML === '');
  // Hidden on every route; `renderMachines` puts the landing back when the
  // list turns out to be empty or unreadable, which is the only time it
  // belongs -- a visitor with nothing to show is who the pitch is written for.
  LANDING_IDS.forEach((id) => hideById(id, true));
  // A fresh arrival at a machine page is not mid-confirmation and not
  // mid-row-delete; both are transient states of ONE visit.
  if (r.view !== 'machine') {
    confirmingDelete = false;
    pendingReading = '';
    // Leaving the page that made them. A blob URL held after its page is gone
    // pins the whole PDF in memory for the life of the document.
    releaseReportUrls();
  }
  if (r.view === 'home') return renderMachines();
  if (r.view === 'machine') return renderMachine(r.id);
  if (r.view === 'machine-edit') return renderMachineEdit(r.id);
  // The prefill runs first: which step is reachable depends on the running
  // speed, and a machine arrived at from its own page supplies one.
  return prefillFor(r.id).then(() => { applyStep(r.step); });
}

function navigate(hash) {
  location.hash = hash;
  // Called as well as set: `hashchange` does the same work a moment later and
  // route() is idempotent, but a caller that goes on to scroll to the form
  // needs the form on screen NOW, not after the event loop turns.
  return route();
}


// ── the two views' click handlers (Session UX-2) ────────────────────
// Delegated on the containers, the way `stateCard`'s handler has been since
// Session G: a card is re-rendered wholesale on every change, so a listener
// bound to a button inside it would be bound to a button that no longer
// exists. Node drives these by dispatching a click with a scripted target.

function machinePageClick(e) {
  const t = e.target || {};
  const data = t.dataset || {};
  if (t.id === 'm-delete') {
    confirmingDelete = true;
    return renderMachine(currentMachineId);
  }
  if (t.id === 'md-cancel') {
    confirmingDelete = false;
    return renderMachine(currentMachineId);
  }
  if (t.id === 'md-go') {
    const typed = document.getElementById('md-confirm');
    const want = currentMachineAlias;
    if (!typed || String(typed.value || '').trim() !== want) {
      const box = document.getElementById('md-error');
      if (box) { box.textContent = 'That is not this machine’s alias, so nothing was deleted.'; setHidden(box, false); }
      return Promise.resolve();
    }
    return removeMachine(currentMachineId, want).then((res) => {
      confirmingDelete = false;
      if (!res.ok) {
        const box = document.getElementById('md-error');
        if (box) { box.textContent = editRefusal(res); setHidden(box, false); }
        return res;
      }
      notify(`\u201C${want}\u201D and its readings are gone from this browser.`, 'gone');
      navigate('#/');
      return res;
    });
  }
  if (data.rdel) { pendingReading = data.rdel; return renderMachine(currentMachineId); }
  if (data.rno) { pendingReading = ''; return renderMachine(currentMachineId); }
  if (data.ryes) {
    const ts = data.ryes;
    return removeReading(currentMachineId, ts).then((res) => {
      pendingReading = '';
      if (res && res.ok !== false) {
        notify(`Reading of ${String(ts).slice(0, 10)} deleted.`, 'gone');
      }
      // Re-rendered from the store, never patched in place: the trend card and
      // the band are DERIVED from the series, and a patched table beside a
      // stale card is two answers to one question.
      return renderMachine(currentMachineId).then(() => res);
    });
  }
  return undefined;
}

function machineEditClick(e) {
  const t = e.target || {};
  if (t.id === 'me-save') {
    if (t.preventDefault) t.preventDefault();
    return submitMachineEdit();
  }
  return undefined;
}

const viewMachineEl = document.getElementById('view-machine');
if (viewMachineEl) viewMachineEl.addEventListener('click', machinePageClick);
const viewMachineEditEl = document.getElementById('view-machine-edit');
if (viewMachineEditEl) viewMachineEditEl.addEventListener('click', machineEditClick);

if (window.addEventListener) window.addEventListener('hashchange', route);
route();

// The three-direction upload lives under "More options" so a first run stays
// three fields long, but it must not read as "one axis only": this affordance
// sits in MEASUREMENTS, opens the disclosure, and jumps straight to the slots.
const addChannelsBtn = document.getElementById('add-channels');
if (addChannelsBtn) {
  addChannelsBtn.addEventListener('click', () => {
    channelsRevealed = true;
    syncMode();
    if (extraChannels) {
      scrollToEl(extraChannels);
      const second = document.getElementById('file_2');
      if (second) second.focus();
    }
  });
}

// Session INTAKE-2 — "+ Add a measurement location". Focus lands on the new
// block's label, which is the one field it cannot be submitted without, on the
// UX-4 pattern the Add-a-channel button above already follows.
const addLocationBtn = document.getElementById('add-location');
if (addLocationBtn) {
  addLocationBtn.addEventListener('click', () => {
    const block = addLocation();
    if (!block) return;
    scrollToEl(block);
    const label = block.querySelector('[data-field="label"]');
    if (label) label.focus();
  });
}

function channelsBlock(channels) {
  if (!channels || !channels.channels || channels.channels.length < 1) return '';
  const rows = channels.channels.map((ch) => {
    const tag = ch.status === 'ok' ? (ch.has_velocity ? 'ok' : 'warn')
      : (ch.status === 'gate_fail' ? 'warn' : 'stop');
    const state = ch.status === 'ok' ? (ch.has_velocity ? 'OK' : 'frequency ID only')
      : (ch.status === 'gate_fail' ? 'excluded — data quality' : 'unreadable');
    // Session UX-5 (STRANGER C8): "Radial – horizontal — OK (direction assumed)
    // even though I explicitly chose Radial – horizontal in step 2."
    //
    // The label was right about the wire and wrong about the analyst. Radial-h
    // WAS the empty option, so choosing it posted nothing, FastAPI read the
    // absence as the default and `app.py` set assumed=true -- a single-file
    // analyst had no way to state that direction at all. Step 2 now offers a
    // real `radial_h` alongside an empty option that says what it is, so
    // `assumed` finally means what it says: nobody told us. The wording follows
    // -- "assumed" claims we guessed, when in fact we read it the one documented
    // way and are saying so.
    const asm = ch.assumed
      ? ' <span style="color:var(--mut)">(direction not given \u2014 read as radial \u2013 horizontal)</span>'
      : '';
    return `<li><code>${esc(ch.label)}</code> — <b class="${tag}">${esc(state)}</b>${asm}` +
           (ch.detail && ch.status !== 'ok' ? ` <span style="color:var(--mut)">${esc(ch.detail)}</span>` : '') + '</li>';
  }).join('');
  const warn = channels.speed_warning
    ? `<p class="kv" style="margin-top:8px"><b class="warn">${esc(channels.speed_warning)}</b></p>` : '';
  return `<p class="kv" style="margin-top:10px">Channels</p><ul class="plain">${rows}</ul>${warn}`;
}

function show(html) {
  // Register the aria-live region in the a11y tree BEFORE the first content
  // mutation, so the initial announcement is not swallowed as an unhide.
  const first = stateCard.hidden;
  // Session UX-1, two things. `setHidden`, not the bare property: `route()`
  // hides this card with an inline `display:none` when the analyst is on
  // another view, and a bare `hidden = false` does not clear an inline style
  // -- the card would be live in the accessibility tree and invisible on the
  // screen. And the card belongs to the UPLOAD view: a job that finishes while
  // the analyst is reading their machines writes its content and stays hidden
  // until they come back, rather than painting a status card onto that list.
  const onUpload = previewMode() || parseRoute().view === 'new';
  setHidden(stateCard, !onUpload);
  if (first && onUpload) { requestAnimationFrame(() => { stateCard.innerHTML = html; }); }
  else { stateCard.innerHTML = html; }
}


// ── the preview: the file, drawn here, before anything is uploaded ──
// Session UX-2. The moment a file lands we read it in this browser and draw
// it. This is A PICTURE OF THE FILE and nothing else: no FFT, no severity, no
// zone, no ISO anything. Every number the product commits to is computed by
// tested Python on the server, and the one thing a preview must never do is
// look like an answer. The figure says PREVIEW, and a test asserts the words
// "Zone", "mm/s RMS", "ISO" and "severity" never appear in it.
//
// TEXT SPECTRA ONLY -- .csv/.txt/.dat/.asc. XLSX would need an unzip, UFF a
// dataset-58 reader, WAV an FFT, .mat a binary parser; each is a second
// implementation of something the server already does correctly, and a second
// implementation that disagrees is worse than no picture. Those formats get a
// panel that says so and states the file is still analysed.
//
// The parser is deliberately a REFUSER. It has an adversarial corpus to answer
// to (tests/fixtures/intake_adversarial/) and the rule throughout is that a
// wrong picture is worse than no picture: thousands separators are refused
// rather than guessed, a non-monotonic axis is refused because the server's
// own gate refuses that file, and anything that does not read as text is
// refused rather than drawn as noise.
const PREVIEW_EXTS = ['csv', 'txt', 'dat', 'asc'];
const PREVIEW_HEAD_BYTES = 4 * 1024 * 1024;   // bounded work on a 25 MB upload
const PREVIEW_MIN_BINS = 32;
const PREVIEW_MAX_BINS = 200000;
const PREVIEW_SAMPLES = 1200;                 // ~820px of viewBox: sub-pixel
const PREVIEW_DELIMS = [',', ';', '\t', '|'];

const PREVIEW_REFUSALS = {
  binary: 'this does not read as text in the browser, so there is nothing to draw. That is '
    + 'not a verdict on the file — some perfectly good exports (UTF-16 out of Excel, for '
    + 'one) are read on the server and not here.',
  thousands: 'the numbers in this file use a thousands separator, and reading one wrongly '
    + 'would draw a spectrum that is not the one you uploaded. We would rather show nothing '
    + 'than the wrong picture.',
  nonmono: 'the frequency axis in this file goes backwards partway through — usually two '
    + 'spectra in one file, or a statistics block after the data. The analysis refuses files '
    + 'like this, so a tidy picture of it would be misleading.',
  toofew: 'there are too few numeric rows here to draw a spectrum.',
  shape: 'we could not find two columns of numbers in this file.',
};

//: A grouped number, as a whole token. `1.800,00` and `1 800,00` (the group
//: separator is a dot or a space, the decimal is a comma), and `1,800.00` (the
//: other way round). The US form allows a comma as its trailing boundary,
//: because a file that groups with commas is overwhelmingly comma-delimited
//: too; the EU/SI forms do not, because that is exactly where an ordinary
//: 3-decimal CSV would be mistaken for one.
const GROUPED_EU = /(?:^|[^\d.,])\d{1,3}(?:[.\u00a0 ]\d{3})+,\d+(?:$|[^\d.,])/;
const GROUPED_US = /(?:^|[^\d.,])\d{1,3}(?:,\d{3})+\.\d+(?:$|[^\d.])/;

const ext = (name) => String(name || '').split('.').pop().toLowerCase();

/** Do we draw this format here? A function rather than a const arrow because a
 *  top-level binding is not a property of the global object, and this is one
 *  of the two decisions in the preview worth proving directly. */
function previewable(name) {
  return PREVIEW_EXTS.indexOf(ext(name)) !== -1;
}

/** The refusal for a named cause, so a test can assert WHICH refusal fired
 *  rather than that some sentence came back. */
function previewRefusal(kind) {
  return PREVIEW_REFUSALS[kind] || '';
}

/** Does this read as text at all? A NUL byte, or a head full of control
 *  characters, means something we should not pretend to understand -- a UTF-16
 *  file decoded as UTF-8 included, which is a real and perfectly readable
 *  export that simply is not readable HERE. */
function looksBinary(text) {
  const head = text.slice(0, 4096);
  if (head.indexOf('\u0000') !== -1) return true;
  let odd = 0;
  for (let i = 0; i < head.length; i += 1) {
    const c = head.charCodeAt(i);
    if (c < 9 || (c > 13 && c < 32) || c === 127) odd += 1;
  }
  return head.length > 0 && odd / head.length > 0.02;
}

/** Split a line on a candidate delimiter. `null` means "runs of whitespace",
 *  which is what a column-aligned export uses. */
function splitOn(line, delim) {
  return delim === null ? line.trim().split(/\s+/) : line.split(delim);
}

function previewNumber(field, commaDecimal) {
  let s = String(field == null ? '' : field).trim();
  if (!s) return null;
  if (commaDecimal) s = s.replace(',', '.');
  if (!/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(s)) return null;
  const v = parseFloat(s);
  return Number.isFinite(v) ? v : null;
}

/** How many numbers are at the FRONT of this row, under this delimiter and
 *  this decimal convention. Leading-only on purpose: `0.5<d>0.012<d>SOMETEXT`
 *  is still a two-column datum, and a trailing label should not change the
 *  shape a row is counted as. */
function numericWidth(line, delim, commaDecimal) {
  const parts = splitOn(line, delim);
  let n = 0;
  for (let i = 0; i < parts.length; i += 1) {
    if (previewNumber(parts[i], commaDecimal) === null) break;
    n += 1;
  }
  return n;
}

/** The most common value in a list, and how often it occurs. */
function modeOf(values) {
  const counts = {};
  let best = 0;
  let bestN = 0;
  values.forEach((v) => {
    counts[v] = (counts[v] || 0) + 1;
    if (counts[v] > bestN) { bestN = counts[v]; best = v; }
  });
  return { value: best, n: bestN };
}

/** Read a text spectrum, or say why not.
 *
 *  Returns {bins, xUnit, rowsRead, refusal}. `xUnit` is 'hz' when a header said
 *  so, 'other' when a header named CPM/RPM/orders, and 'unstated' when there
 *  was no header to ask -- the caller resolves that last one against the
 *  running speed rather than assuming it. */
function parseSpectrumText(text, truncated) {
  if (looksBinary(String(text || ''))) return { refusal: PREVIEW_REFUSALS.binary };
  let body = String(text || '');
  if (body.charAt(0) === '\uFEFF') body = body.slice(1);
  // Grouped thousands, in the three conventions there are: 1.800,00 (EU),
  // 1 800,00 (SI) and 1,800.00 (US). Refused rather than guessed, because
  // reading one of them wrongly moves every point on the axis.
  //
  // Matched as a WHOLE TOKEN and not as a substring, which is the difference
  // between a guard and a nuisance. The first version of this probed for
  // `\d[.\u00a0 ]\d{3},\d` anywhere in the file, and that shape sits inside
  // ordinary comma-delimited data with three decimal places -- `1.500,0.012`,
  // `250.000,120.5` -- so it refused files it could read perfectly well. A
  // grouped number is one-to-three digits, then groups of exactly three, then
  // the decimal, with a non-numeric boundary at each end; `1.500,0.012` fails
  // the trailing one, `1.800,00;` passes it.
  if (GROUPED_EU.test(body) || GROUPED_US.test(body)) {
    return { refusal: PREVIEW_REFUSALS.thousands };
  }
  let lines = body.split(/\r\n|\r|\n/);
  if (truncated) lines.pop();                 // our own slice cut it, not them
  lines = lines.filter((l) => {
    const t = l.trim();
    return t !== '' && t.charAt(0) !== '#' && t.slice(0, 2) !== '//';
  });
  if (!lines.length) return { refusal: PREVIEW_REFUSALS.toofew };

  // Pick the delimiter and the decimal convention TOGETHER, by which pair
  // yields the most rows of one consistent shape. A decimal comma is only
  // conceivable when the comma is not itself the delimiter.
  const sample = lines.slice(0, 400);
  let best = null;
  PREVIEW_DELIMS.concat([null]).forEach((delim) => {
    [false, true].forEach((commaDecimal) => {
      if (commaDecimal && delim === ',') return;
      const widths = sample.map((l) => numericWidth(l, delim, commaDecimal))
        .filter((w) => w >= 2);
      if (!widths.length) return;
      const m = modeOf(widths);
      if (!best || m.n > best.n) best = { delim, commaDecimal, width: m.value, n: m.n };
    });
  });
  if (!best) return { refusal: PREVIEW_REFUSALS.shape };

  const bins = [];
  let header = '';
  for (let i = 0; i < lines.length && bins.length < PREVIEW_MAX_BINS; i += 1) {
    if (numericWidth(lines[i], best.delim, best.commaDecimal) !== best.width) {
      // Everything before the first datum is header, and is where a unit gets
      // named. After the data starts, an off-shape row is junk and is dropped.
      if (!bins.length) header += ' ' + lines[i];
      continue;
    }
    const parts = splitOn(lines[i], best.delim);
    const x = previewNumber(parts[0], best.commaDecimal);
    const y = previewNumber(parts[1], best.commaDecimal);
    if (x === null || y === null) continue;
    bins.push([x, y]);
  }
  if (bins.length < PREVIEW_MIN_BINS) return { refusal: PREVIEW_REFUSALS.toofew };
  for (let i = 1; i < bins.length; i += 1) {
    if (bins[i][0] < bins[i - 1][0]) return { refusal: PREVIEW_REFUSALS.nonmono };
  }

  let xUnit = 'unstated';
  if (/\bhz\b/i.test(header)) xUnit = 'hz';
  else if (/\bcpm\b|\brpm\b|order/i.test(header)) xUnit = 'other';
  return { bins, xUnit, rowsRead: bins.length, refusal: '' };
}

/** Reduce to at most `n` x-samples, keeping the LOUDEST bin in each bucket.
 *
 *  Bucket-max, never stride-sampling. A stride drops a one-bin peak, and a
 *  one-bin peak is the entire content of a spectrum -- a decimation that can
 *  hide the line the analyst is looking for is worse than no chart at all. */
function decimate(bins, n) {
  if (bins.length <= n) return bins;
  const out = [];
  const size = bins.length / n;
  for (let i = 0; i < n; i += 1) {
    const lo = Math.floor(i * size);
    const hi = Math.min(bins.length, Math.floor((i + 1) * size));
    let pick = bins[lo];
    for (let j = lo + 1; j < hi; j += 1) if (bins[j][1] > pick[1]) pick = bins[j];
    if (pick) out.push(pick);
  }
  return out;
}

// The report's own geometry and palette, so a preview and the figures in the
// PDF are visibly the same product (report/charts.py C_TRACE, C_SHAFT).
const PREVIEW_GEOM = { w: 880, h: 220, l: 46, r: 14, t: 12, b: 40 };

/** The figure. `opts`: {bins, xUnit, name, rpm, truncated}.
 *
 *  Linear, full-span, never auto-zoomed and never log. A cropped preview lies
 *  about what is in the file, which is the one thing this must not do. The
 *  amplitude axis carries NO unit, because the browser does not know it. */
function spectrumFigure(opts) {
  const g = PREVIEW_GEOM;
  const bins = decimate(opts.bins, PREVIEW_SAMPLES);
  const xmin = bins[0][0];
  const xmax = bins[bins.length - 1][0];
  let ymax = 0;
  let ymin = 0;
  bins.forEach((b) => { if (b[1] > ymax) ymax = b[1]; if (b[1] < ymin) ymin = b[1]; });
  const span = (xmax - xmin) || 1;
  const yspan = (ymax - ymin) || 1;
  const px = (x) => g.l + ((x - xmin) / span) * (g.w - g.l - g.r);
  const py = (y) => (g.h - g.b) - ((y - ymin) / yspan) * (g.h - g.b - g.t);
  const points = bins.map((b) => `${px(b[0]).toFixed(1)},${py(b[1]).toFixed(1)}`).join(' ');

  // Shaft orders, only when the axis is KNOWN to be Hz. 'unstated' is resolved
  // by asking whether 3x even fits on the axis: if it does not, the axis is
  // almost certainly not Hz, and drawing markers would invent a reading.
  const shaft = Number(opts.rpm) > 0 ? Number(opts.rpm) / 60 : 0;
  const hz = opts.xUnit === 'hz'
    || (opts.xUnit === 'unstated' && shaft > 0 && shaft * 3 <= xmax);
  const orders = [];
  let offScale = 0;
  if (hz && shaft > 0) {
    [1, 2, 3].forEach((k) => {
      const f = shaft * k;
      if (f < xmin || f > xmax) { offScale += 1; return; }
      orders.push(`<line class="spx-o" x1="${px(f).toFixed(1)}" y1="${g.t}" `
        + `x2="${px(f).toFixed(1)}" y2="${g.h - g.b}"></line>`
        + `<text class="spx-ol" x="${(px(f) + 3).toFixed(1)}" y="${g.t + 9}">${k}×</text>`);
    });
  }

  const marked = orders.length
    ? `Shaft orders marked at ${shaft.toFixed(2)} Hz and its multiples.`
    : 'No shaft-order markers are drawn.';
  const alt = `Preview of ${opts.name}: ${opts.bins.length} rows drawn across `
    + `${xmin.toFixed(1)} to ${xmax.toFixed(1)} on the frequency axis. ${marked}`
    + ' Amplitude as supplied; this drawing carries no analysis.';

  const notes = [];
  if (opts.truncated) {
    notes.push('Only the first 4 MB of this file is drawn — the analysis reads all of it.');
  }
  if (offScale) {
    notes.push(`${offScale} of the three shaft orders `
      + `${offScale === 1 ? 'sits' : 'sit'} past this file's highest frequency, so `
      + `${offScale === 1 ? 'it is' : 'they are'} not drawn.`);
  }
  if (!hz && shaft > 0) {
    notes.push('This file does not state its frequency axis in Hz, so the shaft-order markers'
      + ' are withheld rather than put where they might not belong.');
  }
  if (hz && !shaft) {
    notes.push('Give the running speed and the shaft-order markers appear here.');
  }
  if (ymin < 0) {
    notes.push('There are negative amplitudes in this file, which usually means a phase or '
      + 'real-part column. The analysis refuses a spectrum like that.');
  }
  if (ymax === 0) {
    notes.push('Every amplitude in this file is zero. The analysis refuses that as a stopped '
      + 'machine or a dead channel.');
  }

  return `<figure class="spx">
    <svg class="spx-svg" viewBox="0 0 ${g.w} ${g.h}" preserveAspectRatio="xMidYMid meet"
         role="img" aria-label="${esc(alt)}">
      ${orders.join('')}
      <polyline class="spx-tr" fill="none" points="${points}"></polyline>
      <line class="spx-ax" x1="${g.l}" y1="${g.h - g.b}" x2="${g.w - g.r}" y2="${g.h - g.b}"></line>
      <text class="spx-xl" x="${g.l}" y="${g.h - g.b + 18}">${esc(xmin.toFixed(0))}</text>
      <text class="spx-xr" x="${g.w - g.r}" y="${g.h - g.b + 18}">${esc(xmax.toFixed(0))}${
        hz ? ' Hz' : ''}</text>
    </svg>
    <figcaption><b class="spx-tag">PREVIEW</b> — drawn in this browser from the file you
      chose, so you can see you picked the right one. Amplitude as supplied; nothing here is
      analysed, graded, or sent anywhere.${notes.length ? ' ' + notes.join(' ') : ''}</figcaption>
  </figure>`;
}

/** The panel for a format we do not read here, or a file we would rather not
 *  draw. Not an error: the file is acceptable and is about to be analysed. */
function previewUnavailable(name, reason) {
  return `<figure class="spx spx-none">
    <figcaption><b class="spx-tag">NO PREVIEW</b> — ${esc(reason)} <b>${esc(name)}</b> is
      uploaded and analysed exactly as normal.</figcaption>
  </figure>`;
}

/** The only thing here that touches a File. Reads a bounded head, parses it,
 *  and writes a figure into `mountId`. NEVER rejects: a preview that fails is
 *  a preview that says so, and an upload that proceeds regardless. */
function previewFile(file, mountId, rpm) {
  const mount = document.getElementById(mountId);
  if (!mount) return Promise.resolve('');
  if (!file || !file.name) {
    mount.innerHTML = '';
    setHidden(mount, true);
    return Promise.resolve('');
  }
  setHidden(mount, false);
  if (!previewable(file.name)) {
    mount.innerHTML = previewUnavailable(file.name,
      `we only draw text spectra (${PREVIEW_EXTS.join(', ')}) in the browser —`);
    return Promise.resolve('unavailable');
  }
  const truncated = file.size > PREVIEW_HEAD_BYTES;
  // `.then(() => ...)` and not `Promise.resolve(file.text())`: the second
  // evaluates the read OUTSIDE the chain, so a reader that throws
  // synchronously escapes the catch below and rejects a promise this function
  // promises never to reject.
  return Promise.resolve().then(() => {
    const slice = (truncated && file.slice) ? file.slice(0, PREVIEW_HEAD_BYTES) : file;
    return slice.text();
  }).then((text) => {
    const read = parseSpectrumText(text, truncated);
    if (read.refusal) {
      mount.innerHTML = previewUnavailable(file.name, read.refusal);
      return 'refused';
    }
    mount.innerHTML = spectrumFigure({
      bins: read.bins, xUnit: read.xUnit, name: file.name, rpm, truncated,
    });
    return 'drawn';
  }).catch(() => {
    mount.innerHTML = previewUnavailable(file.name, 'we could not read this file here —');
    return 'refused';
  });
}


// ── the four steps (Session UX-2) ──────────────────────────────────
// The panels are real markup inside `#form-card`, so `route()` already hides
// all four with the one `setHidden(formCard, ...)` it has always done and the
// step machinery costs the router nothing. Which step is showing is a HASH
// ROUTE -- `#/new/3` -- so Back and Forward work through the form, and a
// half-filled intake is a link.
//
// AND IT IS CLAMPED. A File cannot survive a reload, so `#/new/4` typed cold
// would otherwise paint a review of inputs that are gone. The reachable step
// is computed from the SERVER'S OWN required set -- running speed, then a file
// -- using submitJob's exact file predicate so the example-file path counts.
const STEP_COUNT = 4;
let currentStep = 1;

function stepFileChosen() {
  return !!((fileInput && fileInput.files.length) || demoFile);
}

function reachableStep() {
  const rpm = String((form.elements.rpm && form.elements.rpm.value) || '').trim();
  if (!rpm) return 1;
  if (!stepFileChosen()) return 2;
  return STEP_COUNT;
}

function renderRail(step, max) {
  for (let i = 1; i <= STEP_COUNT; i += 1) {
    const li = document.getElementById('rail-' + i);
    if (!li) continue;
    const state = i === step ? 'now' : (i < step ? 'done' : 'pending');
    li.className = 's ' + state + (i > max ? ' locked' : '');
    li.setAttribute('aria-current', i === step ? 'step' : 'false');
  }
  // STRANGER U5: at 375px the four steps rendered as four stacked rows, each
  // with its own full-width rule -- a quarter of the screen spent on chrome
  // before the question. Below 600px the stylesheet shows the current step
  // alone and prefixes it from this attribute, so the compact form reads
  // "Step 2 of 4 — Measurement" without a second markup path to keep in sync.
  //
  // DESKTOP IS UNCHANGED and that is the point of doing it in CSS: the
  // four-step rail is the design target (operator amendment: this is a web
  // application first), and `tests/test_ux4_shell.py` pins that the compact
  // form cannot reach 1280.
  const rail = document.getElementById('step-rail');
  if (rail && rail.setAttribute) {
    rail.setAttribute('data-step', String(step));
    rail.setAttribute('data-of', String(STEP_COUNT));
  }
}

/** Show one step, never more than the inputs have earned.
 *
 *  Rewriting the hash on a clamp converges in one extra pass: a browser fires
 *  `hashchange`, `route()` runs again, and the second time the requested step
 *  already equals the reachable one so nothing is rewritten. */
function applyStep(requested) {
  const max = reachableStep();
  const step = Math.min(Math.max(1, Number(requested) || 1), max);
  if (step !== (Number(requested) || 1) && location) {
    // With the '#', like `navigate` and like what a browser writes back:
    // the two ways this file sets a hash must not disagree about its shape.
    location.hash = step === 1 ? '#/new' : '#/new/' + step;
  }
  for (let i = 1; i <= STEP_COUNT; i += 1) hideById('step-' + i, i !== step);
  renderRail(step, max);
  currentStep = step;
  if (step === 3) renderUnlocks();
  if (step === 4) renderReview();
  focusHeading('step-' + step + '-head');
  return step;
}

function goToStep(n) {
  return navigate(Number(n) === 1 ? '#/new' : '#/new/' + n);
}

// The step buttons, delegated on the card, so a step re-rendered from JS keeps
// working. Only a `data-step` target is claimed -- the submit button is in
// here too and must reach the form's own handler untouched.
if (formCard) {
  formCard.addEventListener('click', (e) => {
    const t = e.target || {};
    const to = (t.dataset && t.dataset.step) || '';
    if (!to) return;
    if (t.preventDefault) t.preventDefault();
    goToStep(to);
  });
}

/** Which step a form control lives on, so a refusal can go there.
 *
 *  Native validation stays authoritative: `required` is still on the file, the
 *  running speed and the invite code, exactly where `test_session_f2.py` pins
 *  it. But a `required` control that is INVALID and hidden blocks submission
 *  in Chrome with NO visible symptom -- the form simply does not submit and
 *  the only trace is a console line. Step gating makes that unreachable in
 *  normal use; this makes it survivable when it happens anyway. */
function stepOf(el) {
  for (let i = 1; i <= STEP_COUNT; i += 1) {
    const panel = document.getElementById('step-' + i);
    if (panel && panel.contains && panel.contains(el)) return i;
  }
  return 0;
}

if (form && form.addEventListener) {
  form.addEventListener('invalid', (e) => {
    const el = e.target;
    const on = stepOf(el);
    if (!on || on === currentStep) return;
    goToStep(on);
    if (el && el.focus) el.focus();
  }, true);                                  // capture: `invalid` does not bubble
}


/** The running speed as the preview reads it: the shaft-order markers move
 *  with this field, so entering it late still lights them up. */
function currentRpm() {
  return String((form.elements.rpm && form.elements.rpm.value) || '');
}

function previewSlot(i) {
  const input = document.getElementById(i === 1 ? 'file' : 'file_' + i);
  const chosen = (input && input.files && input.files[0])
    || (i === 1 ? demoFile : null) || null;
  return previewFile(chosen, 'spx-' + i, currentRpm());
}

function refreshPreviews() {
  return Promise.all([previewSlot(1), previewSlot(2), previewSlot(3)]);
}

if (form.elements.rpm && form.elements.rpm.addEventListener) {
  // Redrawn on every change of speed, because the markers ARE the speed. An
  // analyst who drops the file first and types the speed second must not be
  // left looking at a chart that never gained them.
  ['input', 'change'].forEach((evt) => {
    form.elements.rpm.addEventListener(evt, () => { refreshPreviews(); });
  });
}

// ── card renderers ─────────────────────────────────────────────


// ── step 3: the coverage roster, read forwards (Session UX-2) ──────
// `report/generate.py`'s COVERAGE_ROSTER tells the analyst, AFTER the fact,
// which fault families were not assessed and why. Four of its rows name an
// input they were missing -- so the same table, read forwards, is a list of
// checks the analyst can switch ON before the run. The keys here are the
// roster's own (`_COVERAGE_INPUT_PRESENT`, `_COVERAGE_GEOMETRY_ON_FILE`) and
// a test asserts the two sets are equal, so a roster row renamed or added
// fails a test rather than leaving this page promising a screen that no
// longer exists (ROADMAP common law #9, mechanised).
//
// THE UI MUST NOT OVER-PROMISE, and the roster is careful in three places
// this has to reproduce:
//   * `coupling = coupled` is an ANSWER, not an unlock. The predicate is
//     `m.coupled is False`, and the roster REWORDS rather than disappearing.
//   * turning the bearing screen on brings a CAVEAT with it: matching reads
//     the radial channels, so an axial thrust-loaded fault is still not
//     assessed. Both halves are stated together or neither is.
//   * gear teeth, rotor bars, poles, line frequency and drive type are a
//     THIRD state -- recorded and printed, no detector reads them yet. They
//     are never sold as a screen.
const UNLOCKS = [
  {
    key: 'bearing_geometry',
    title: 'Rolling-element bearing faults',
    on: 'BPFO, BPFI, BSF and FTF are computed from that bearing\u2019s geometry and matched '
      + 'against your spectrum.',
    ask: 'Choose the bearing under \u201cBearing model\u201d \u2014 or, if it is not on the '
      + 'list, enter its geometry there.',
    caveat: 'The match reads the radial channels, so an axial, thrust-loaded fault is still '
      + 'reported as not assessed.',
    // Session GEOM-1: the screen runs on geometry, and a catalogue name is one
    // way of supplying it. A predicate that read only the name would report
    // "off" over four numbers the analysis is about to use.
    has: () => !!bearingFact(fieldValue),
  },
  {
    key: 'blade_count',
    title: 'Blade and vane pass',
    on: 'The blade-pass frequency is computed and checked.',
    ask: 'Give the blade or vane count.',
    has: () => !!fieldValue('blades'),
  },
  {
    key: 'belt_frequency',
    title: 'Belt and pulley faults',
    on: 'The belt frequency is derived from the pulley geometry and the running speed, and '
      + 'checked along with its harmonics.',
    ask: 'Give both pulley diameters AND the centre distance \u2014 all three, plus the '
      + 'running speed. Any one of them missing and the belt frequency cannot be derived.',
    has: () => !!(fieldValue('drive_pulley_mm') && fieldValue('driven_pulley_mm')
      && fieldValue('pulley_center_distance_mm') && fieldValue('rpm')),
  },
  {
    key: 'uncoupled_declared',
    title: 'Bent shaft, on an uncoupled machine',
    on: 'Axial 1\u00d7 is read as a bent shaft rather than as misalignment.',
    ask: 'Set Coupling to \u201cNot coupled\u201d, if that is what this machine is.',
    // The roster's own third state, reproduced: "coupled" is an answer.
    answered: () => fieldValue('coupling') === 'coupled',
    answeredNote: 'You have said this machine is coupled, which is an answer rather than a '
      + 'gap \u2014 the bent-shaft branch does not apply to it, and the report says so.',
    has: () => fieldValue('coupling') === 'uncoupled',
  },
];

const GEOMETRY_ON_FILE = [
  { key: 'gear_teeth', title: 'Gear mesh, sidebands and hunting tooth',
    fields: ['gear_teeth_driving', 'gear_teeth_driven'] },
  { key: 'rotor_bar_geometry', title: 'Rotor bar faults',
    fields: ['rotor_bars', 'poles', 'line_freq_hz'] },
  { key: 'line_frequency', title: 'Stator faults and VFD line-frequency faults',
    fields: ['line_freq_hz'] },
  { key: 'drive_type', title: 'VFD carrier-frequency artifacts', fields: ['drive_type'] },
];

function fieldValue(name) {
  const el = form.elements[name];
  return String((el && el.value) || '').trim();
}

/** The declared geometry, with the words the intake uses for it. Session UX-5
 *  (C4) shows these on the step that loaded the machine and on Review; the
 *  UNLOCKS/GEOMETRY_ON_FILE tables above say what each one turns ON, which is a
 *  different question and stays where it is. */
function geometryFields() {
  return [
    { name: 'blades', label: 'blades/vanes' },
    { name: 'drive_type', label: 'drive' },
    { name: 'poles', label: 'poles' },
    { name: 'line_freq_hz', label: 'line Hz' },
    { name: 'rotor_bars', label: 'rotor bars' },
    { name: 'gear_teeth_driving', label: 'teeth driving' },
    { name: 'gear_teeth_driven', label: 'teeth driven' },
    { name: 'drive_pulley_mm', label: 'drive pulley mm' },
    { name: 'driven_pulley_mm', label: 'driven pulley mm' },
    { name: 'pulley_center_distance_mm', label: 'centres mm' },
  ];
}

/** A velocity unit as the analyst reads it. Reads the same table the confirm
 *  card's own select is built from, so the two cannot come to disagree. */
function unitLabel(value) {
  return UNIT_LABELS[String(value || '')] || '';
}

function unlockRow(u) {
  if (u.has()) {
    return `<li class="ul on"><span class="m" aria-hidden="true">\u2713</span><span>
      <b>${esc(u.title)}</b> \u2014 on. ${esc(u.on)}${
        u.caveat ? ` <span class="ul-cav">${esc(u.caveat)}</span>` : ''}</span></li>`;
  }
  if (u.answered && u.answered()) {
    return `<li class="ul answered"><span class="m" aria-hidden="true">\u2022</span><span>
      <b>${esc(u.title)}</b> \u2014 ${esc(u.answeredNote)}</span></li>`;
  }
  return `<li class="ul off"><span class="m" aria-hidden="true">+</span><span>
    <b>${esc(u.title)}</b> \u2014 off. ${esc(u.ask)} ${esc(u.on)}</span></li>`;
}

function renderUnlocks() {
  const box = document.getElementById('unlocks');
  if (!box) return;
  const rows = UNLOCKS.map(unlockRow).join('');
  const onFile = GEOMETRY_ON_FILE
    .filter((g) => g.fields.some((f) => !!fieldValue(f)))
    .map((g) => `<li>${esc(g.title)}</li>`).join('');
  const recorded = onFile
    ? `<div class="ul-note"><b>Recorded, and printed in the report \u2014 but no detector
       reads them yet:</b><ul class="ul-plain">${onFile}</ul>These are kept with the analysis
       and named in Analysis parameters. They do not turn a check on, and the report does not
       pretend they did.</div>`
    : '';
  box.innerHTML = `<div class="unlocks">
    <h4>What this run will check</h4>
    ${unlistedBearingNote(unlistedBearing)}
    <ul class="ul-list">${rows}</ul>
    ${recorded}
    <p class="help">Whatever stays off is named in the report as <b>not assessed</b>, with the
      field that would have turned it on \u2014 never left out quietly.</p>
  </div>`;
}


// ── step 4: review, and RULED D-24 AS AMENDED ──────────────────────
// The duplicate notice is a FORECAST, and it is careful to be only that. At
// intake the browser knows the machine and it knows the direction the analyst
// declared; it does NOT know the axis the analysis will report as dominant,
// and the reading's stamp is the server's. So this asks the question against
// today's UTC date and the DECLARED direction, says which stored readings it
// matched, and carries the answer to the save.
//
// RULED D-24 AMENDED (Sep 7): the question is asked ONCE, here, and the default
// is KEEP BOTH. Two things changed and both were wrong for this product:
//
//   * The default was REPLACE. The flagship flow is before-and-after a repair
//     (POSITIONING §3.2) and those two readings are the same machine, the same
//     point, the same axis, on the same day -- so the default silently
//     destroyed the "before" of the exact comparison the product is sold on.
//     Keeping a reading the analyst did not want costs one click on the machine
//     page; replacing one they did want cannot be undone from anywhere.
//   * It asked AGAIN on the report, with the axis the analysis actually found.
//     That was defensible (the real axis is better evidence than the declared
//     one) and it was still two questions for one decision, one of them after
//     the analyst had moved on. The second ask is gone. The intake's answer is
//     applied, and the axis it is applied against is the reported one -- so a
//     declared/reported mismatch can only ever keep both, never replace
//     something the forecast never named.
let duplicateChoice = 'keep_both';             // D-24 AMENDED: keep both is the default

function duplicateForecast() {
  const key = currentTrendKey();
  if (!key) return { key: '', day: '', axis: '', matches: [] };
  const day = todayUTC();
  const axis = declaredAxis(fieldValue('direction'));
  return { key, day, axis, matches: duplicateMatches(key, day, axis) };
}

function renderDuplicate() {
  const box = document.getElementById('dup-notice');
  if (!box) return;
  const f = duplicateForecast();
  setHidden(box, f.matches.length === 0);
  if (!f.matches.length) { box.innerHTML = ''; return; }
  const rows = f.matches.map((p) => `<li>${esc(p.ts.slice(0, 10))} \u00b7
    ${esc(p.v.toFixed(2))} mm/s RMS on the ${esc(p.axis || 'unstated')} axis</li>`).join('');
  const checked = (v) => (duplicateChoice === v ? ' checked' : '');
  // The order is FIXED, and it is the same order at every width. The stranger
  // found the two buttons swapping places between desktop and mobile (C7); a
  // destructive choice that moves under the pointer is how the wrong one gets
  // pressed. Radios in one source order, laid out by one rule.
  box.innerHTML = `<div class="dup">
    <h4>You already have a reading for this machine today</h4>
    <ul class="ul-plain">${rows}</ul>
    <p class="help">Before-and-after the same repair, on the same day, is a pair \u2014 so both are
      kept unless you say otherwise. Replace it only if this run is a re-measurement of the reading
      above rather than a second measurement.</p>
    <label class="dup-opt"><input type="radio" name="dup_mode" value="keep_both"${checked('keep_both')}>
      <span>Keep both <b>(default)</b></span></label>
    <label class="dup-opt"><input type="radio" name="dup_mode" value="replace"${checked('replace')}>
      <span>Replace the reading above</span></label>
    <p class="help">Asked once, here. Whatever you choose is applied when the reading is saved.</p>
  </div>`;
}

if (formCard) {
  formCard.addEventListener('change', (e) => {
    const t = e.target || {};
    if (t.name === 'dup_mode') duplicateChoice = t.value === 'keep_both' ? 'keep_both' : 'replace';
  });
}

function reviewRow(label, value) {
  return `<div><dt>${esc(label)}</dt><dd>${
    value ? esc(value) : '<span class="muted">Not stated</span>'}</dd></div>`;
}

function chosenFileNames() {
  const names = [];
  [1, 2, 3].forEach((i) => {
    const input = document.getElementById(i === 1 ? 'file' : 'file_' + i);
    const chosen = (input && input.files && input.files[0]) || (i === 1 ? demoFile : null);
    if (chosen && chosen.name) names.push(chosen.name);
  });
  return names;
}

function renderReview() {
  const box = document.getElementById('review-body');
  renderDuplicate();
  if (!box) return;
  const alias = fieldValue('machine_alias');
  const files = chosenFileNames();
  const on = UNLOCKS.filter((u) => u.has()).length;
  // Session UX-5 (C4). Everything that will be posted, in one place, on the step
  // before the run -- the stranger had to open a collapsed disclosure two steps
  // back to find out whether the geometry they typed on another screen had come
  // with them. `analysisFacts` reads the FORM, so this lists what is actually
  // going to be sent rather than what a card happens to hold.
  const carried = analysisFacts()
    .filter(([label]) => label !== 'Measurement point')
    .map(([label, value]) => reviewRow(label, value)).join('\n    ');
  box.innerHTML = `<dl class="mfacts">
    ${reviewRow('Machine', alias || 'Unnamed machine')}
    ${reviewRow('Measurement point', fieldValue('measurement_location'))}
    ${reviewRow('Running speed', fieldValue('rpm') ? fieldValue('rpm') + ' rpm' : '')}
    ${reviewRow(files.length > 1 ? 'Files' : 'File', files.join(', '))}
    ${reviewRow('Direction', DIRECTION_LABELS[fieldValue('direction')] || 'Not given — read as radial – horizontal')}
    ${carried}
    ${reviewRow('Checks switched on', `${on} of ${UNLOCKS.length}`)}
  </dl>`;
}

// ── the honest rail ────────────────────────────────────────────
// FIVE positions, and every one of them is a moment the system can actually
// distinguish:
//
//   Uploaded  ·  Accepted  ·  Analysis   ·  Drafting   ·  Report
//   (client)    (queued)     (phase=       (phase=       (any terminal
//                             analyzing)    drafting)     state)
//
// There is no `Verifying` step. The live rail used to carry one that could
// NEVER be the current step: verification happens inside the drafting call and
// is never published as its own state (jobs.py:27-31 says so outright), so the
// code could only ever light it retroactively. A step that is only lit in the
// past tense is a step the analyst cannot use. The guarantee it was gesturing
// at is stated on the finished report instead, in the checks' own terms.
//
// And there is no percentage. The bar that used to sit under this rail was
// drawn from {queued:10, analyzing:35, drafting:70} — nothing measured those
// numbers. In a product whose pillar is *computed* confidence, a picture of a
// percentage nobody computed is the same class of defect as the
// `verifier: 31/31` claim PARITY §X10 cut from the export. What replaces it:
// an indeterminate sweep on the current step, the state in words, and a
// client-side ELAPSED clock — the one honest number the browser holds.
const RAIL_STEPS = ['Uploaded', 'Accepted', 'Analysis', 'Drafting', 'Report'];

// `nowIdx` is the current position, or -1 when the job is finished and no step
// is current. `stopped` lists positions that DID NOT RUN and never will: they
// render dashed and grey, never "done" (which would be the meter's lie in a
// different font) and never "pending" (which implies they are still coming).
function rail(nowIdx, stopped) {
  const skip = stopped || [];
  const cells = RAIL_STEPS.map((label, i) => {
    let cls;
    if (skip.indexOf(i) !== -1) cls = 'stopped';
    else if (i === nowIdx) cls = 'now';
    else if (nowIdx === -1 || i < nowIdx) cls = 'done';
    else cls = 'pending';
    const inner = (cls === 'now' || cls === 'pending') ? `<b>${label}</b>` : label;
    return `<span class="s ${cls}">${inner}</span>`;
  }).join('');
  return `<div class="rail">${cells}</div>`;
}

// ── the elapsed clock ──────────────────────────────────────────
// Client-side, and it derives from nothing new: it is measured from the moment
// THIS PAGE POSTed. It is not a countdown and must never be presented as one —
// a countdown here would be the meter's mistake wearing a clock. `show()`
// rewrites the card on every poll, so the ticker re-finds its element each
// second rather than holding a reference across renders.
let runStartMs = null;
function markRunStart() { runStartMs = Date.now(); }
function elapsedText() {
  if (runStartMs === null) return '';
  const secs = Math.max(0, Math.floor((Date.now() - runStartMs) / 1000));
  return `elapsed ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
}
setInterval(() => {
  const el = document.getElementById('el-clock');
  if (el && runStartMs !== null) el.textContent = elapsedText();
}, 1000);

// The status line replaces the state pill. Colour is never the only carrier —
// the state is spelled out in words on the same line.
//
// Session UX-3: it is also THE FOCUS TARGET for a finished run. It lives here
// rather than in each card for one concrete reason — `readyCard` is pinned
// byte-for-byte at the source (tests/test_ux1_views.py), and the report page
// must not change to give the run an outcome the keyboard can reach. Every
// card calls this exactly once, so the id is unique inside `#state`.
function statusLine(tone, words, withClock) {
  const clock = withClock ? `<span class="el" id="el-clock">${elapsedText()}</span>` : '';
  return `<div class="statusline ${tone}" id="run-status" tabindex="-1">${words}${clock}</div>`;
}

function contextLine() {
  return window._ctx ? `<p class="kv muted">${window._ctx.replace(/ · $/, '')}</p>` : '';
}

function stepCard(state, phase) {
  let idx, tone, status, now, next;
  if (state === 'running' && phase === 'drafting') {
    idx = 3; tone = 'work'; status = 'Drafting';
    // Session UX-3 names the render here rather than as a rail step. Nothing
    // publishes it: `JobPhase` is analyzing|drafting (jobs.py) and the PDF
    // child runs inside THIS phase (worker.py::_finalize_markdown_and_pdf), so
    // a sixth position could only ever be lit in the past tense — which is the
    // exact objection that removed `Verifying` above.
    now = 'Drafting the narrative, then rendering your PDF — numbers are already final.';
    next = `Every number is computed and fixed. A language model is writing the narrative around
      them; <b>it cannot change a number</b>, and every claim it writes is re-checked against the
      computed result before you see it. If that check fails, or the drafting pass is unavailable,
      you get the same report without the written narrative — clearly marked.`;
  } else if (state === 'running') {
    idx = 2; tone = 'work'; status = 'Analysis';
    now = 'Computing — deterministic pipeline.';
    next = `Tested code is computing every number: the ISO 20816 zone, bearing fault frequencies,
      trend and evidence-scored confidence. <b>If the data quality gate fails here, the run stops
      and you get an insufficient-data report</b> naming exactly what to re-collect — never a
      diagnosis on data that can’t support one. Either way the PDF you download is rendered on
      our server when the run ends; nothing on this page draws it.`;
  } else {
    // `queued` covers THREE situations and the copy is written wide on purpose:
    // not yet scheduled, waiting on the 2-permit worker semaphore, and actively
    // parsing your file in a sandbox — because "running" is not written until
    // after every parse and the merge (WEBAPP_STATES §3.1-3.2). On a
    // three-channel upload an analyst can watch this sit lit for a minute and a
    // half while the server is, in fact, parsing. This is the widest statement
    // true of all three, claiming none of them specifically. Do not narrow it to
    // "parsing" and do not add a queue position — neither is available. When
    // DEP-2 lands a real phase="parsing" hint, the change is a substitution into
    // this sentence, not a redesign.
    idx = 1; tone = 'work'; status = 'Accepted';
    now = 'Accepted — preparing your file.';
    next = `Your file is being read and checked. Next, the deterministic pipeline computes every
      number: ISO zone, trend, bearing fault frequencies, confidence. <b>No diagnosis is made until
      the data-quality gate passes.</b>`;
  }
  return `${statusLine(tone, status, true)}
    ${rail(idx)}
    <p class="now-line">${now}</p>
    <p class="next-line">${next}</p>
    ${contextLine()}
    <p class="help">You can close this page — the analysis continues on the server. The report
      link works for 60 minutes after it finishes.
      <span id="poll-status" class="poll-note"></span></p>`;
}

// The severity/diagnosis pair the report opens with, in the same colours.
// Session V2-WIRE. Every value here comes from `result_summary`, which the
// worker builds from the AnalysisResult using the report's own labels — this
// renders it, it never derives anything. A severity the analysis did not
// establish renders as "not established", never as a zone.
function resultCards(rs, point) {
  if (!rs) return '';
  const sev = rs.severity || '';
  const zone = (sev.match(/^ISO Zone ([A-D])$/) || [])[1];
  const sevCls = zone ? ` sev z${zone}` : ' sev';
  const sevHead = zone ? `Zone ${zone}` : (sev || 'Severity not established');
  // STRANGER U9: "Result card shows 'Zone B · ISO 20816-3' with no overall mm/s
  // value; the trend card two panels down shows '1.77 mm/s RMS'." The zone IS
  // that number classified, so the two belong together -- and POSITIONING §7
  // asks for numbers over adjectives. Taken from `trend_point`, which the wire
  // already carries and which is the same scalar the trend card renders, so
  // there is ONE number on the page rather than two that could disagree.
  const value = point && typeof point.severity_rms_mms === 'number'
    ? `${point.severity_rms_mms.toFixed(2)} mm/s RMS` : '';
  const sevSub = zone
    ? (value ? `${value} · ISO 20816-3` : 'ISO 20816-3')
    : (sev ? value : 'the analysis established no severity zone');
  let head, sub;
  if (rs.no_findings) {
    head = 'none — parameters within normal range';
    sub = '';
  } else {
    const first = (rs.faults || [])[0];
    head = first ? first.label : '—';
    const rest = (rs.faults || []).length - 1;
    sub = first ? `confidence ${String(first.confidence).toLowerCase()}` : '';
    if (rest > 0) sub += ` · ${rest} further finding${rest > 1 ? 's' : ''} in the report`;
  }
  return `<div class="rcards">
      <div class="rcard${sevCls}"><div class="lbl">Severity</div><h4>${esc(sevHead)}</h4>
        ${sevSub ? `<div class="sub">${esc(sevSub)}</div>` : ''}</div>
      <div class="rcard"><div class="lbl">Committed diagnosis</div><h4>${esc(head)}</h4>
        ${sub ? `<div class="sub">${esc(sub)}</div>` : ''}</div>
    </div>`;
}

// ── the retention ledger ───────────────────────────────────────
// Eight lines on every terminal card. "What was kept" is a question with eight
// answers and four of them flip on choices the analyst made on the form, so
// they are rendered FROM WHAT WAS SUBMITTED, never assumed.
//
// Session UX-5 adds the EIGHTH line -- the report itself, now kept in this
// browser with the reading it belongs to (RULED D-26). It is a live row on both
// backends, because it is a promise about THIS browser and is true the moment
// the bytes land, exactly like the two rows above it.
//
// And the whole thing is COLLAPSED by default (STRANGER U8: "~40 lines of WHAT
// WAS KEPT plus a second list NOT YET IN EFFECT appear under every result. One
// line + link to /privacy. A results page is not the place to tell me about
// features you haven't shipped."). The summary is one sentence, computed from
// the same `RETAINED` the rows are, so it is true of THIS run rather than
// generic. Nothing is deleted: every row is still here, still diffed against
// static/privacy.html, and one click away -- which is the difference between
// shortening a promise and hiding one.
//
// Every line traces to a code path, named here so the next person can check
// the promise rather than trust it:
//   uploaded file  -- unlinked as soon as it is parsed        app.py:316-322
//   working files  -- everything but the report, at completion worker.py:239-259
//   this report    -- TTL, re-anchored at completion           jobs.py:78, worker.py:337-339
//   the trace      -- double opt-in; see the widened wording below
//   one log line   -- the per-job outcome line                 app.py:77-92
//   form values    -- localStorage, this browser only          client
//   trend readings -- localStorage, this browser only, SENT    client
//   format note    -- opt-in, endorsed recipes only, NO expiry app.py:713-733
//
// Session BILL-1 added the seventh ACCOUNT row (credit purchases). The account
// list is the one that grows: it is drawn from what the schema can hold, and
// `static/privacy.html` says the same thing in the same commit -- D-22, and the
// note below would otherwise be a promise about a table nobody was told about.
//
// Session GEOM-A widened the last line's SCOPE: the card now also holds the
// declared machine geometry (coupling, blade/vane count, gear tooth counts,
// rotor bars, poles, line frequency, drive type, pulley dimensions and the
// measurement location) under a new key, `vib.machines.v2`. Same place, same
// consent, same "Forget" button, same never-our-servers -- but the ledger and
// static/privacy.html enumerate what is stored, so both move with the list
// above, in this commit (ROADMAP common law #8).
//
// Session HIST-1 adds the SEVENTH line, and it is the one line here that says
// something LEAVES. Every other browser-held thing on this list is a promise
// that it never moves; trend readings are held in this browser and then sent
// with the next upload for the same machine, used for that one analysis, and
// deleted with the rest of that job. Saying "kept in this browser" and stopping
// there would be true and misleading, so the line says both halves.
//
// Session DB-1 adds a SECOND list below the seven, and deliberately not an
// eighth line. The seven describe an analysis that has finished: each one is
// true right now, and three of them were decided by this analyst on this form.
// The DB-1 rows describe an account that does not exist -- the schema is in the
// repo, switched off behind STORE_BACKEND, and production runs `browser`. Mixed
// into the seven they would read as things we are keeping; kept apart, under
// their own heading and closed by a sentence saying none of it is in effect,
// they read as what they are. That is also why they carry class="future": the
// count of live promises is asserted (tests/js/trend_card_tests.js), and a
// promise about the future must not be able to pass itself off as one of them.
//
// THIS LEDGER IS A PROMISE. If privacy.html and these lines disagree, one of
// them is false; they are diffed against each other deliberately.
const RETAINED = { trace: false, remember: false, trendKey: '', trendCount: 0,
  duplicate: null, autosave: false, files: '', share: false };

function ledger() {
  // Line 4 is written wide ON PURPOSE, and the reason is a measured cost, not
  // caution. A trace is created during drafting REGARDLESS of consent --
  // consent gates RETENTION at completion, not creation. On the ordinary path
  // it goes with the working files at completion. But a STRANDED job (one that
  // stopped without finishing) never completes: the sweeper deliberately does
  // not touch its directory, because the abandoned worker thread may still be
  // writing into it, so anything in there -- a working trace included --
  // survives until a LATER sweep reclaims it, up to one TTL (jobs.py:252-267,
  // S7-ACCEPT F-3). So this line claims deletion at completion AND names the
  // cleanup sweep for the paths that never complete. It does not say "the
  // moment", because on those paths that would be false.
  const trace = RETAINED.trace
    ? `<b>The analysis trace</b> — kept only if trace retention is enabled on this server; you
       ticked the box that allows it. Otherwise it is deleted with the working files when the
       analysis completes, and if a job fails or is stopped, its working files — any trace
       included — are cleared by the cleanup sweep within the hour.`
    : `<b>The analysis trace</b> — not kept. You did not tick the trace box, so it is deleted with
       the working files when the analysis completes; and if a job fails or is stopped, its working
       files — any trace included — are cleared by the cleanup sweep within the hour.`;
  // Session INTAKE-2 widened what this row covers, so the row says so (RULED
  // D-22): the machine's KIND and its rated nameplate joined MEMORY_FIELDS.
  // Named rather than left under "what you typed about the machine", because
  // the whole point of this ledger is that an analyst can read what is held
  // without having to trust a summary.
  const remembered = RETAINED.remember
    ? `<b>This machine’s form values</b> — kept in <b>this browser only</b>, because you ticked
       Remember: what you typed about the machine, including its type, its rated power and speeds,
       its declared geometry, and any machine-specific severity limits you set for it. Never on our
       servers; never your invite code; never your file. Kept until you forget it — from the
       Saved machines row on this form, or from the machine's own page.`
    : `<b>This machine’s form values</b> — not kept. You did not tick Remember, so nothing about
       this machine — its type, its rating, its details or its geometry, and no severity limits
       you set — was stored, in this browser or anywhere else.`;
  const trended = RETAINED.trendCount
    ? `<b>This machine’s trend readings</b> — ${RETAINED.trendCount} kept in
       <b>this browser only</b>: the overall mm/s value, the ISO zone we computed for it, the
       axis it was strongest on and when it was analysed. Unlike the form values above they are
       <b>sent with your next upload</b> for this machine, used for that one analysis, and deleted
       with the rest of that job. We keep no copy between uploads. Kept until you delete them —
       one reading, or the whole machine, from the machine's page.`
    : `<b>This machine’s trend readings</b> — none kept. Saving this measurement stores it in
       <b>this browser only</b>; it is then sent with your next upload for this machine so the
       report can assess the trend, and deleted with the rest of that job.`;
  // Session UX-5 / D-26. The report, kept HERE, with the reading. Written from
  // what is actually held rather than from what was attempted: the fetch is
  // fire-and-forget and a browser can refuse it, so this row must be able to
  // say "not kept" and mean it.
  const reportsKept = RETAINED.trendKey ? Object.keys(reportFactsFor(RETAINED.trendKey))
    .filter((ts) => reportFactsFor(RETAINED.trendKey)[ts].pdf).length : 0;
  // Session TIDY-1 / SESSION_LEGAL1 §7.4: *"an eighth row is owed for
  // `format_pings.jsonl`"*. The one thing this product keeps that no line here
  // named, and the only one with NO expiry -- logrotate covers app.log and
  // nothing covers this file (deploy/setup_server.sh:110-122), so the row says
  // "no expiry" rather than borrowing a period from the line above it.
  //
  // Double-conditioned, like the trace row and for the same reason: ticking the
  // box is necessary and not sufficient. A ping is written only for a file that
  // needed a reading recipe we did not already have, and only for a recipe the
  // data endorsed (app.py:1109-1115). So the ticked branch says what is written
  // WHEN one is written, and does not claim one was.
  const shared = RETAINED.share
    ? `<b>The format note</b> — kept, with <b>no expiry</b>, because you ticked "Help us support
       this format": how a file laid out like yours is read — its structure and the recipe — and
       nothing from inside it. No readings, no numbers we read from it, no filename, no machine
       name, no invite code, and not the file. Written only if this file needed a recipe we did
       not already have.`
    : `<b>The format note</b> — not kept. You did not tick "Help us support this format", so
       nothing about how this file is laid out was recorded.`;
  const reported = reportsKept
    ? `<b>Your reports</b> — ${reportsKept} kept in <b>this browser only</b>, with the readings
       they belong to: the PDF itself, and with it the written narrative, which exists nowhere
       else once our copy is deleted. Never sent anywhere; they are not uploaded and there is no
       account they could belong to. Kept until you delete the reading, or the machine.`
    : `<b>Your reports</b> — none kept in this browser. Ours is deleted on the schedule above, so
       download anything you need to keep.`;
  const row = (cls, mark, html) => `<li class="${cls}"><span class="m">${mark}</span><span>${html}</span></li>`;
  // A DB-1 row, in the tense the running backend makes true. On `browser` it is
  // a promise about a thing that does not exist: its own class, so it can never
  // be counted among the seven live rows (tests/js/trend_card_tests.js). On `db`
  // the same row IS one of the things being kept, so it becomes a live row and
  // is counted with them. The wording flips with `static/privacy.html` in one
  // commit — if the two disagree, one of them is false.
  const soon = (html) => (ACCOUNTS_ON ? row('kept', '●', html) : row('future', '◦', html));
  // The one line, and it is a SUMMARY rather than a headline: it names the two
  // things an analyst actually wants to know after a run (their file is gone,
  // the report has a deadline) and points at the rest.
  const summary = `Your file is already deleted; our copy of this report goes in 60 minutes. `
    + `${reportsKept ? 'Your browser keeps its own copy. ' : ''}`
    + 'What was kept, and for how long';
  return `<details class="ledger">
    <summary><span class="sum">${summary}</span></summary>
    <h5>What was kept, and for how long</h5>
    <ul>
      ${row('gone', '✕', `<b>Your uploaded file</b> — deleted already. It is unlinked as soon as it
        has been parsed, before the analysis even finished.`)}
      ${row('gone', '✕', `<b>Working files</b> — deleted. Everything except the report itself was
        removed when the analysis completed.`)}
      ${row('timed', '⏱', `<b>This report</b> — kept for <b>60 minutes</b> from now, then deleted.
        Save it if you want to keep it; we keep no copy.`)}
      ${row('gone', '✕', trace)}
      ${row('kept', '●', `<b>One log line</b> — kept for <b>14 days</b>: the time, which invite
        label, the file kind and size, how long it took, token cost, and the outcome. <b>No IP
        address, no filename, no machine name.</b>`)}
      ${row('kept', '●', remembered)}
      ${row('kept', '●', trended)}
      ${row('kept', '●', reported)}
      ${row(RETAINED.share ? 'kept' : 'gone', RETAINED.share ? '●' : '✕', shared)}
    </ul>
    <h5 class="next">${ACCOUNTS_ON ? 'Kept with your account' : 'Not yet in effect — when accounts arrive'}</h5>
    <ul>
      ${soon(`<b>Your account</b> — the email address you sign in with, and the identifier your
        sign-in provider gives us.`)}
      ${soon(`<b>Sign-in links</b> — a one-time link, stored only as a hash, and the time it
        stops working.`)}
      ${soon(`<b>Credits</b> — every addition and deduction, as a list we only ever add to; your
        balance is the sum of it.`)}
      ${soon(`<b>Credit purchases</b> — for each pack bought: which pack, how many credits and
        when. <b>No card number and no amount</b> — those stay with Stripe, who take the payment
        on their own page.`)}
      ${soon(`<b>Your machine list</b> — one card for each machine you analyse or save, kept
        with the account rather than only in this browser, including any machine-specific
        severity limits you set for it.`)}
      ${soon(`<b>Saved readings</b> — for each: the overall mm/s value, the ISO zone recorded
        for it and whether we computed that zone or your browser did, the axis it was strongest
        on, and when it was analysed.`)}
      ${soon(`<b>Job metadata</b> — for each analysis: when it ran, which machine,
        the zone, the value, and what we called it.`)}
    </ul>
    <div class="note">${ACCOUNTS_ON
      ? `<b>Kept until you delete it</b>, which you can do yourself: the
         <a href="/privacy">Privacy</a> page carries a form that removes everything in this list
         at once, and each machine and each saved reading can be deleted on its own from the
         machine's page. Your measurement data is processed and deleted
         exactly as the first list says — no spectrum, no waveform and no copy of your report is
         held with your account. The last row is what we keep about an analysis itself — when
         it ran, on which machine, the zone and value it found and what we called it — and
         never the report it produced.`
      : `<b>None of this is in effect.</b> There is no account today and nothing in
      that second list is stored anywhere. Your measurement data is processed and deleted exactly
      as the first list says — no spectrum, no waveform and no copy of your report would be kept
      even once accounts exist.`}</div>
    <div class="foot">Full detail on the <a href="/privacy">Privacy</a> page.</div>
  </details>`;
}

// Session HIST-1. Every card is a full innerHTML replacement, so a Save click
// cannot reach back into the card it was rendered on — it re-renders from what
// the job actually returned. Held here, at the render, rather than threaded
// through the click handler, for the same reason `RETAINED` is captured at
// submit: what the card says must come from what happened, not from whatever
// the page happens to hold when a button is pressed.
let lastReady = null;

/** The job reference, with something to press.
 *
 *  STRANGER B3, and it was the worst moment in the whole test: "The drafting
 *  pass did not produce a narrative this time... so if you need the written
 *  version, send us the job reference. No job reference is shown anywhere on
 *  the page. The only place the id exists is inside the PDF link href. Telling
 *  me to quote something you haven't shown me is the moment I stop trusting the
 *  flow. Happened on 100% of my runs (3/3)."
 *
 *  The id is already in the page -- the two download links carry it -- so this
 *  crosses no new wire and asks the server for nothing. It shows what the copy
 *  is asking for, says WHY in the words the wire actually supports, and copies
 *  it, because a 32-character hex string retyped by hand is a hex string
 *  retyped wrongly.
 *
 *  The reason is `degraded_reason`, which has exactly two values (`jobs.py`:
 *  spend_budget | draft_failure). It is not narrowed further here, and that is
 *  deliberate: the finer cause is not on the wire by design, it is in our log
 *  against this reference, and saying so is honest where guessing between an
 *  API error, a consistency hard-fail and an absent key would not be. */
function jobRefBlock(jobId, reason) {
  if (!jobId) return '';
  const because = reason === 'spend_budget'
    ? 'the drafting budget for today was spent'
    : 'the drafting pass failed on our side, and the cause is in our log against this reference';
  return `<div class="jobref">
    <span class="lbl">Job reference</span>
    <code id="job-ref">${esc(jobId)}</code>
    <button type="button" class="btn-mini" id="copy-ref" data-ref="${esc(jobId)}">Copy</button>
    <span class="help">Quote it if you contact us — ${esc(because)}.</span>
  </div>`;
}

function readyCard(jobId, data, degraded) {
  lastReady = { jobId, data, degraded };
  const rs = data.result_summary;
  const excluded = ((data.channels || {}).channels || [])
    .filter(c => c && c.status && c.status !== 'ok').length;
  const words = degraded ? 'Report ready — deterministic'
    : (excluded ? 'Report ready — with an exclusion' : 'Report ready');
  const tag = degraded
    ? statusLine('warn', words, false) + rail(-1, [3])
    : statusLine(excluded ? 'warn' : 'ok', words, false) + rail(-1);
  const why = data.degraded_reason === 'spend_budget'
    ? `Today’s drafting budget for this service is used up, so the narrative was not written. The
       budget resets tomorrow — <b>uploading the same file again then produces the drafted
       version</b>. Nothing about your file caused this.`
    : `The drafting pass did not produce a narrative this time. <b>This may not clear by itself</b>,
       so if you need the written version, send us the job reference below rather than re-uploading
       repeatedly. Nothing about your file caused this.`;
  const note = degraded
    ? `<p class="help" style="margin-top:10px">This is the deterministic report — <b>every number
       is the same computed result</b>, without the AI-written prose. ${why}</p>
       ${jobRefBlock(jobId, data.degraded_reason)}`
    : '';
  // The committed call and the severity live in the CARD PAIR below, which is
  // the report's own treatment. This line used to repeat both in prose; with
  // the cards there it was the same sentence twice.
  return `${tag}
    <p class="kv"><b class="ok">Report ready.</b> Draft — pending analyst review.</p>
    ${resultCards(rs, data.trend_point)}
    ${channelsBlock(data.channels)}
    <div class="ready-actions">
      <a class="dl" id="dl" href="/api/jobs/${esc(jobId)}/report.pdf" target="_blank"
         rel="noopener">Open the report</a>
      <a class="dl ghost" id="dl-save" href="/api/jobs/${esc(jobId)}/report.pdf"
         download>Download PDF</a>
      <button type="button" class="btn-ghost" id="again">Analyze another file for this machine</button>
    </div>
    ${note}
    ${trendBlock(data)}
    ${ledger()}`;
}

// ── confirm card (Session G: schema-inference intake) ─────────────
// A text export has no adapter of its own, so the layout was INFERRED. Before
// anything is analysed, show how the file was read and let the analyst correct
// the three things that change the answer: units, detection, and speed.
// Every word here comes from the server's own vocabulary — no inferred text.
const UNIT_LABELS = { mm_s: 'mm/s', in_s: 'in/s' };
const DETECTION_LABELS = { rms: 'RMS', peak: 'Peak', peak_to_peak: 'Peak-to-peak' };
const DIRECTION_LABELS = { radial_h: 'Radial – horizontal', radial_v: 'Radial – vertical', axial: 'Axial' };

function unitOptions(file, id) {
  // A file read as g / µm / unknown must NOT default to a velocity unit: leaving
  // the control alone would relabel acceleration as mm/s and manufacture an ISO
  // severity out of it — the exact error this product exists to prevent. So the
  // selected option for those files is "keep as read", and a velocity unit is
  // only ever applied when the analyst deliberately picks one.
  const selected = (file.editable || {}).velocity_unit;
  const keep = file.severity_available ? ''
    : `<option value="" selected>${esc(file.amplitude_label || 'as read')} — keep as read</option>`;
  const rest = Object.keys(UNIT_LABELS).map(v =>
    `<option value="${v}"${v === selected ? ' selected' : ''}>${UNIT_LABELS[v]}</option>`).join('');
  return `<select id="${id}">${keep}${rest}</select>`;
}

function detectionOptions(file, id) {
  const selected = (file.editable || {}).detection_type;
  return `<select id="${id}">` + Object.keys(DETECTION_LABELS).map(v =>
    `<option value="${v}"${v === selected ? ' selected' : ''}>${DETECTION_LABELS[v]}</option>`)
    .join('') + '</select>';
}

function directionOptions(file, id) {
  return `<select id="${id}">` + Object.keys(DIRECTION_LABELS).map(v =>
    `<option value="${v}"${v === file.direction ? ' selected' : ''}>${DIRECTION_LABELS[v]}</option>`)
    .join('') + '</select>';
}

function confirmFileBlock(file) {
  const slot = file.slot;
  const tag = file.source === 'template'
    ? '<span class="tag-soft">our template</span>'
    : (file.from_cache ? '<span class="tag-soft">recognised format</span>' : '');
  if (file.status !== 'ok') {
    // The analytical consequence of losing THIS direction, in the product's own
    // documented terms — an axial channel is what separates angular misalignment
    // from imbalance (assembly.py / the multi-axis conjunction). Stating it is
    // not a prediction about this machine; it is what the report will be unable
    // to do. Every other direction degrades less specifically, so it gets the
    // general sentence rather than an invented one.
    const consequence = file.direction === 'axial'
      ? ` Without an axial channel the analysis cannot separate angular misalignment from
         imbalance — the report will say which directions it had.`
      : '';
    return `<div class="confirm-file bad">
      <p class="kv"><b class="warn">${esc(file.label)} — couldn’t be interpreted.</b>
        ${esc(file.message || '')}</p>
      <p class="help">It will be left out. The other files are still analysed.${consequence}</p>
    </div>`;
  }
  const ignored = file.ignored_columns
    ? `<p class="help">${file.ignored_columns} further data column(s) in this file were not read —
       upload one as its own channel if you need it.</p>` : '';
  const severity = file.severity_available ? ''
    : '<p class="help">Not velocity — no ISO severity from this channel, fault frequencies only.</p>';
  const editable = file.source === 'template' ? '' : `
      <div class="confirm-grid">
        <div><label for="c-unit-${slot}">Amplitude unit</label>${unitOptions(file, 'c-unit-' + slot)}</div>
        <div><label for="c-det-${slot}">Detection</label>${detectionOptions(file, 'c-det-' + slot)}</div>
        <div><label for="c-dir-${slot}">Direction</label>${directionOptions(file, 'c-dir-' + slot)}</div>
      </div>`;
  return `<div class="confirm-file">
    <p class="kv"><b>${esc(file.label)}</b> ${tag}</p>
    <div class="readas">
      <div class="lbl">Read as</div>
      <p><b>${esc(file.headline)}</b>, frequency axis in <code>${esc(file.x_axis)}</code>.</p>
    </div>
    ${severity}${ignored}${editable}
    <label class="consent"><input type="checkbox" class="c-skip" data-slot="${slot}">
      <span>Leave this file out</span></label>
  </div>`;
}

// ── confirm card (Session G: schema-inference intake; G2: multi-file) ──
// A text export — or a spreadsheet our template could not read — has no fixed
// layout, so the layout was INFERRED. Before anything is analysed, show how each
// file was read and let the analyst correct what changes the answer.
// Every word here comes from the server's own vocabulary — no inferred text.
function confirmCard(jobId, interpretation) {
  const i = interpretation || {};
  const files = i.files || [i];
  const multi = files.length > 1;
  const rpm = (files.find(f => f.status === 'ok') || i).rpm;
  const rpmFrom = (files.find(f => f.status === 'ok') || i).rpm_from || 'the form';
  const single = !multi ? `
    <p class="kv"><b>Read as: <code>${esc(files[0].headline)}</code></b>, frequency axis in
      <code>${esc(files[0].x_axis)}</code>, running speed <code>${esc(String(rpm))} rpm</code>
      from ${esc(rpmFrom)} — correct?
      ${files[0].from_cache ? '<span class="tag-soft">recognised format</span>' : ''}</p>
    ${files[0].severity_available ? '' :
      '<p class="help" style="margin-top:8px">These units are not velocity, so ISO 20816 severity '
      + 'will not be computed — the report identifies fault frequencies only.</p>'}
    ${files[0].ignored_columns ? `<p class="help">${files[0].ignored_columns} further data column(s)
      were not read — upload one as its own channel if you need it.</p>` : ''}
    <div class="confirm-grid">
      <div><label for="c-unit-1">Amplitude unit</label>${unitOptions(files[0], 'c-unit-1')}</div>
      <div><label for="c-det-1">Detection</label>${detectionOptions(files[0], 'c-det-1')}</div>
      <div><label for="c-rpm">Running speed (RPM)</label>
        <input type="number" id="c-rpm" step="any" value="${esc(String(rpm))}"></div>
    </div>` : `
    <p class="kv"><b>${files.length} files — check how each was read.</b>
      They are analysed together as one machine at
      <code>${esc(String(rpm))} rpm</code>.</p>
    ${files.map(confirmFileBlock).join('')}
    <div class="confirm-grid">
      <div><label for="c-rpm">Running speed (RPM)</label>
        <input type="number" id="c-rpm" step="any" value="${esc(String(rpm))}"></div>
    </div>`;
  const frozen = multi
    ? `<div class="frozen"><b>Fixed for this run.</b> The files are already on the server and are
        not re-read from your device. Confirming analyses the ones that could be read, as described
        above. To change anything else — different files, a different machine — start over.</div>`
    : `<div class="frozen"><b>Fixed for this run.</b> The file is already on the server and is not
        re-read from your device. Confirming analyses <em>this</em> file, with the settings above.
        To change anything else — a different file, a different machine — start over.</div>`;
  return `${statusLine('pause', 'Check the interpretation', false)}
    ${single}
    ${frozen}
    <label class="consent" style="margin-top:12px"><input type="checkbox" id="c-share">
      <span>Help us support this format — share the file’s <b>structure</b> and how it was read.
      No readings, no machine name, no file (<a href="/privacy">Privacy</a>).</span></label>
    <div class="ready-actions">
      <button type="button" class="cta" id="confirm-go" data-job="${esc(jobId)}"
        data-slots="${esc(files.map(f => f.slot || 1).join(','))}">Yes — analyze it</button>
      <button type="button" class="btn-ghost" id="confirm-cancel">Start over</button>
    </div>
    <p class="help" style="margin-top:10px">Nothing has been analysed yet. Your file is deleted the
      moment analysis finishes, or within 60 minutes if you leave this page.
      <span id="poll-status" class="poll-note"></span></p>`;
}

function stoppedCard(jobId, data) {
  const gs = data.gate_summary || {};
  // The gate's own reasons, in the report's attention treatment rather than as
  // a bullet list: on this card they are the whole message.
  const reasons = (gs.reasons || [])
    .map(r => `<div class="attn fail"><span class="st">FAIL</span><span class="txt">${esc(r)}</span></div>`)
    .join('');
  const collect = (gs.collect || []).map(c => `<li>${esc(c)}</li>`).join('');
  return `${statusLine('warn', 'Stopped — data quality', false)}
    ${rail(-1, [3])}
    <p class="kv"><b class="warn">Analysis stopped before diagnosis.</b> This data can’t support a reliable call, so no diagnosis was made.</p>
    ${reasons}
    ${channelsBlock(data.channels)}
    ${collect ? `<p class="kv" style="margin-top:8px">What to collect:</p><ul class="plain">${collect}</ul>` : ''}
    <a class="dl" id="dl" href="/api/jobs/${esc(jobId)}/report.pdf">Download insufficient-data report</a>
    ${ledger()}`;
}

// DEP-4a. `error` alone cannot tell the analyst what to do next, and the one
// piece of advice this card used to give — download the template, email us the
// export — is exactly wrong when the file was fine and the failure was ours.
// The server now says whose problem it was (`failure_kind`, from the S7 error
// taxonomy) and whether trying the same thing again is sensible (`retryable`),
// and the two halves of the card branch on that instead of on the state.
//
// Session UX-3 gives the retryable half a BUTTON. `retryable` is the wire's
// answer to exactly one question — "is submitting this again a sensible thing
// to do?" — so the control is gated on it being true, never on it not being
// false: `submitJob`'s 4xx branch calls this with no kind and no flag, because
// the server has told us what is wrong with the REQUEST, and a button that
// re-sends it unchanged is a button that fails again.
//
// It reuses `#post-retry`, which already re-POSTs this form. That makes "the
// same inputs" literal rather than a promise: the file inputs still hold their
// Files here, since `clearFileSlots` is reached only from `#again` and
// `#confirm-cancel`.
//
// This is the control SESSION_RENDERPROC.md §6 needs. A render child killed by
// SIGSEGV/SIGBUS/SIGKILL raises `RenderChildCrashed`, which reaches
// `app.py::_run_guarded` through an unmodified worker and becomes
// `internal_error` — server_error, retryable, "the file was fine". Until now
// this card said exactly that and gave the analyst nothing to press.
//
// There is deliberately NO RAIL on this card. The wire cannot say WHERE a job
// failed — an unreadable upload and a crashed renderer are both `error` — so a
// rail here would have to guess which steps ran, and a guessed step is the
// thing the whole rail was rebuilt to stop drawing.
function errorCard(message, kind, retryable) {
  const ours = kind === 'server_error';
  const fallback = ours
    ? 'The analysis failed on our side.'
    : 'This file couldn’t be processed.';
  const retry = retryable === true
    ? `<div class="ready-actions">
         <button type="button" class="cta" id="post-retry">Try again</button>
       </div>`
    : '';
  const advice = ours
    ? `<p class="help">Nothing was wrong with your file, so there is nothing to change.
       <b>This one has not been counted against your daily limit</b> — the failure was ours, so
       your allowance was put back automatically. If it happens twice,
       <a href="mailto:${CONTACT_EMAIL}">email us the job reference</a> and we will look.</p>`
    : `<p class="help"><b>Uploading this file again unchanged gives the same answer</b> — change
       what the line above names first. Our layouts are the <a href="/sample.csv">CSV template</a>
       / <a href="/sample.xlsx">XLSX template</a>, or
       <a href="mailto:${CONTACT_EMAIL}">email the original export</a> exactly as it came off the
       instrument — most instrument formats are added within days.</p>`;
  return `${statusLine('stop', 'Stopped — error', false)}
    <p class="kv"><b class="stop">${esc(message || fallback)}</b></p>
    ${retry}
    ${advice}`;
}

// The report is downloadable idempotently within its TTL — view, save, and
// refresh all work — so the link is NOT swapped after a click (unlike the old
// one-shot purge-on-GET behavior).

// ── "Analyze another file for this machine" (Session F2) ──────────
// Returns to the form with every machine value carried and only the file slots
// cleared. The finished card stays on the page, so the previous report is still
// downloadable for the rest of its TTL.
function clearFileSlots() {
  Promise.resolve().then(() => refreshPreviews());
  demoFile = null;
  fileInput.value = '';
  fileName.textContent = DROP_PROMPT;
  EXTRA_SLOTS.forEach(([fid, , nameId]) => {
    document.getElementById(fid).value = '';
    document.getElementById(nameId).textContent = '';
  });
}

stateCard.addEventListener('click', async (e) => {
  if (e.target && e.target.id === 'poll-resume') {
    const jobId = e.target.getAttribute('data-job') || lastJobId;
    if (!jobId) return;
    showRun(jobId, { state: 'running' });
    pollJob(jobId);
    return;
  }
  if (e.target && e.target.id === 'post-retry') {
    submitJob();
    // Session UX-3. `submitJob` replaces this card with the working one, so
    // the button that was just pressed no longer exists and focus would fall
    // back to <body>. Land it on the new card instead — the same place a
    // finished run lands it.
    focusRunStatus();
    return;
  }
  if (e.target && (e.target.id === 'copy-ref' || e.target.id === 'copy-ref-ready')) {
    const ref = e.target.getAttribute('data-ref') || '';
    if (!ref) return;
    // `navigator.clipboard` is https-or-localhost only and can be refused by
    // permission, so the promise is caught and the failure SAYS the reference
    // rather than pretending. Selecting it is what a browser without the API
    // leaves the analyst -- and it is what they would have had to do anyway.
    const done = () => notify('Job reference copied.', 'ok');
    const failed = () => notify(`Copy it by hand: ${ref}`, '');
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard
          && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(ref).then(done).catch(failed);
      } else { failed(); }
    } catch (err) { failed(); }
    return;
  }
  if (e.target && e.target.id === 'save-trend') {
    // Session HIST-1. The key comes off the BUTTON, which was rendered from the
    // identity that was submitted -- not from the form, which the analyst may
    // have edited while the job ran.
    const key = e.target.getAttribute('data-key');
    if (!key || !lastReady || !lastReady.data.trend_point) return;
    // The mode comes off the BUTTON for the same reason the key does.
    // D-24 AMENDED: the intake's answer, and keep-both when it said nothing.
    const dupMode = e.target.getAttribute('data-mode') || 'keep_both';
    const point = lastReady.data.trend_point;
    RETAINED.trendCount = saveTrendPoint(key, point, dupMode).length;
    putReportFacts(key, point.captured_at, {
      file: RETAINED.files, job: lastReady.jobId,
      fault: committedLabel(lastReady.data), confidence: committedConfidence(lastReady.data),
    });
    show(readyCard(lastReady.jobId, lastReady.data, lastReady.degraded));
    refreshTrendNote();
    notify(dupMode === 'replace'
      ? 'Saved — it replaced the reading of the same day, in this browser.'
      : 'Saved to this machine\u2019s trend, in this browser.', 'ok');
    // RULED D-26: the report goes with the reading, from either save path.
    keepReport(key, point.captured_at, lastReady.jobId).then((held) => {
      if (held && lastReady) show(readyCard(lastReady.jobId, lastReady.data, lastReady.degraded));
    }).catch(() => null);
    return;
  }
  if (e.target && e.target.id === 'confirm-cancel') {
    clearFileSlots();
    setHidden(stateCard, true);   // UX-1: paired with show()'s setHidden above
    submitBtn.disabled = false;
    scrollToEl(formCard);
    fileInput.focus();
    return;
  }
  if (e.target && e.target.id === 'confirm-go') {
    const jobId = e.target.getAttribute('data-job');
    e.target.disabled = true;
    const slots = (e.target.getAttribute('data-slots') || '1').split(',').filter(Boolean);
    const files = slots.map((slot) => {
      const unit = document.getElementById('c-unit-' + slot);
      const det = document.getElementById('c-det-' + slot);
      const dir = document.getElementById('c-dir-' + slot);
      const skip = document.querySelector('.c-skip[data-slot="' + slot + '"]');
      const entry = { slot: Number(slot) };
      if (unit && unit.value) entry.velocity_unit = unit.value;
      if (det && det.value) entry.detection_type = det.value;
      if (dir && dir.value) entry.direction = dir.value;
      if (skip && skip.checked) entry.skip = true;
      return entry;
    });
    const body = {
      rpm: parseFloat((document.getElementById('c-rpm') || {}).value) || null,
      files: files,
      share_format: !!(document.getElementById('c-share') || {}).checked,
      retain_trace: !!(form.elements.retain_trace && form.elements.retain_trace.checked),
    };
    // The share box lives on the CONFIRM card, not the upload form, so it is
    // read here rather than beside the other two RETAINED flags. The ledger is
    // written from what this run actually did.
    RETAINED.share = body.share_format;
    try {
      const res = await fetch('/api/jobs/' + jobId + '/confirm', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        show(errorCard(typeof detail.detail === 'string' ? detail.detail
          : 'Those settings could not be applied — please try again.'));
        return;
      }
    } catch (err) {
      // Reserved copy, second and last site: the confirmation never reached the
      // server. The job is still paused there, so this is recoverable — say so.
      show(confirmOfflineCard(jobId));
      return;
    }
    markRunStart();
    showRun(jobId, { state: 'queued' });
    pollJob(jobId);
    return;
  }
  if (!e.target || e.target.id !== 'again') return;
  // STRANGER C9: "'Analyze another file for this machine' just scrolls to the
  // top of the Review step, still showing before.csv and the previous result
  // beneath. I had to press Back twice to reach the file picker. Old result
  // stays on screen while the new review is being edited."
  //
  // Three things, and the third is the one that was actually dangerous: go to
  // the step that asks for a file (2, not 4); clear the slots, which this
  // already did; and take the finished card down. A result panel sitting under
  // a half-edited review is a page showing an answer to a question that is no
  // longer being asked.
  //
  // The REPORT is not lost by that: since D-26 it is kept with the reading, on
  // the machine's own page, rather than existing only as a card on screen.
  clearFileSlots();
  submitBtn.disabled = false;
  setSubmitNote(false);
  stateCard.innerHTML = '';
  setHidden(stateCard, true);
  announcedState = '';
  lastReady = null;
  goToStep(2);
  scrollToEl(formCard);
  fileInput.focus();
});

// ── network policy (hotfix-net) ───────────────────────────────────
// A slow network is not an error, and a server saying "wait" is not a failure.
// Both used to land on the red error card, which told the analyst their upload
// had failed when their analysis was in fact still running.
const POLL_INTERVAL_MS = 2500;        // never faster; the job takes ~a minute
const POLL_CONFIRM_MS = 20000;        // the confirm card is interactive — do not hammer
const POLL_BACKOFF_MS = [3000, 6000, 12000];
const POLL_MAX_FAILURES = 8;          // ~1 minute of retries before we admit we lost contact
const MAX_RETRY_AFTER_S = 300;        // never honour an absurd Retry-After
// config/webapp.json max_upload_bytes. Pinned to that value by a test, so the
// client and the server cannot drift into disagreeing about what is too big.
const MAX_UPLOAD_BYTES = 26214400;

let lastJobId = null;                 // retained so "Check again" can resume

// ── Session INTAKE-2 / PARTC F-8 — retiring a poll loop ──────────────────
// `pollJob` advances a chain of `setTimeout(poll, …)` calls and keeps NO handle
// on them, so nothing could stop a loop once it started. That is the defect
// PART-C photographed: a second submit that fails leaves the FIRST job's loop
// running, and it calls `show()` on every tick -- so the error card the failure
// path paints is overwritten within a second by the previous job's "Accepted"
// panel, elapsed clock still counting. `get_page_text` found no error content
// because by then there was none on the page.
//
// A generation counter rather than a stored timer id: the loop has several
// scheduling sites (`poll`, `retry`, the confirm pause) and a handle would have
// to be threaded through all of them, whereas one check at the top of the tick
// retires every future tick of a superseded loop no matter who scheduled it.
let pollGeneration = 0;

/** Retire every in-flight poll loop, and stop the elapsed clock.
 *  Called when a run is superseded or refused -- nothing from the previous run
 *  may paint over what the analyst is being told now. */
function retirePolling() {
  pollGeneration += 1;
  runStartMs = null;
}

function retryAfterMs(res, fallbackMs) {
  const stated = retryAfterSeconds(res);
  return stated === null ? fallbackMs : stated * 1000;
}

// Whether the server ACTUALLY stated a wait, as distinct from what we will do
// if it did not. `null` means "no honest number available" — the card must not
// count down, and must say why rather than inventing one.
function retryAfterSeconds(res) {
  const raw = res && res.headers && res.headers.get ? res.headers.get('Retry-After') : null;
  const seconds = parseInt(raw, 10);
  if (isNaN(seconds) || seconds <= 0) return null;
  return Math.min(seconds, MAX_RETRY_AFTER_S);
}

function softStatus(text) {
  // An inline line INSIDE the current card. The card itself stays put: whatever
  // the job was doing, it is still doing it.
  const el = document.getElementById('poll-status');
  if (el) el.textContent = text || '';
}

function startCountdown(ms, prefix) {
  let remaining = Math.ceil(ms / 1000);
  const tick = () => {
    if (remaining <= 0) { softStatus(''); return; }
    softStatus(prefix + ' — retrying in ' + remaining + 's');
    remaining -= 1;
    setTimeout(tick, 1000);
  };
  tick();
}

// ── the error taxonomy ────────────────────────────────────────────
// Each of these says what happened and what to do about it. The generic
// "something went wrong" card is gone.
function inviteCard() {
  return `${statusLine('warn', 'Invite code', false)}
    <p class="kv"><b class="warn">That invite code wasn’t recognised.</b> Check it for typos —
      codes are per person, and the link you were sent fills it in for you.</p>
    <p class="help">If you think it should work, reply to the email that sent it
      (<a href="mailto:${CONTACT_EMAIL}">${CONTACT_EMAIL}</a>) and we’ll sort it out.</p>`;
}

function sizeCard(name, bytes) {
  const mb = (bytes / (1024 * 1024)).toFixed(1);
  const limit = Math.floor(MAX_UPLOAD_BYTES / (1024 * 1024));
  return `${statusLine('warn', 'File too large', false)}
    <p class="kv"><b class="warn">${esc(name)} is ${esc(mb)} MB — the limit is ${limit} MB.</b>
      Nothing was uploaded.</p>
    <p class="help">The ${limit} MB limit is <b>per file</b>, not per upload. If you were sending
      several channels together, upload the channels separately — each one gets its own ${limit} MB.</p>
    <p class="help">Otherwise export a shorter time window or a single spectrum rather than a full
      route, or <a href="mailto:${CONTACT_EMAIL}">email us the file</a> and we’ll handle it.</p>`;
}

function duplicateDirectionCard(direction) {
  const label = DIRECTION_LABELS[direction] || direction;
  return `${statusLine('warn', 'Check the directions', false)}
    <p class="kv"><b class="warn">Two files are both marked ${esc(label)}.</b> Nothing was
      uploaded — each channel needs its own direction.</p>
    <p class="help">One machine gets one file per direction: radial–horizontal, radial–vertical
      and axial. If you measured the same direction twice, upload them as separate reports.</p>`;
}

function busyCard(seconds) {
  const known = typeof seconds === 'number' && seconds > 0;
  const line = known
    ? `<span id="poll-status" class="poll-note">waiting ${seconds}s</span>`
    : `<span id="poll-status" class="poll-note">retrying shortly</span>`;
  const why = known
    ? `<p class="help">The wait above is the server's own estimate of when a slot frees up, not a
       guess by this page.</p>`
    : `<p class="help">The server did not say how long, so neither will we — this page keeps
       trying rather than showing you a timer we made up.</p>`;
  return `${statusLine('warn', 'Server busy', false)}
    <p class="kv"><b>Too many requests just now.</b> Nothing is lost — this page is waiting
      and will try again on its own.</p>
    <p class="kv">${line}</p>
    ${why}`;
}

function transientCard() {
  return `${statusLine('warn', 'Server hiccup', false)}
    <p class="kv"><b>The server couldn’t take the upload just then.</b> That is usually
      temporary and your file was not analysed.</p>
    <div class="ready-actions">
      <button type="button" class="cta" id="post-retry">Try again</button>
    </div>`;
}

function offlineCard() {
  // RESERVED for a fetch that never reached the server, after retries.
  return `${statusLine('unknown', 'No connection', false)}
    <p class="kv"><b class="stop">Network error — the upload never reached us.</b>
      Nothing was analysed and nothing was charged against your code.</p>
    <p class="help">Check the connection and press Try again. On a plant Wi-Fi or a VPN this is
      usually a dropped link rather than anything wrong with your file.</p>
    <div class="ready-actions">
      <button type="button" class="cta" id="post-retry">Try again</button>
    </div>`;
}

function lostContactCard(jobId, cause) {
  const why = cause === 'busy'
    ? 'The server has been too busy to answer for a while.'
    : 'This page stopped being able to reach the server.';
  return `${statusLine('unknown', 'Lost contact', false)}
    <p class="kv"><b>Lost contact — your analysis may still be running.</b> ${why}
      The job was accepted, so it is very likely still working on the server.</p>
    <div class="ready-actions">
      <button type="button" class="cta" id="poll-resume" data-job="${esc(jobId)}">Check again</button>
      <a class="btn-ghost" href="/api/jobs/${esc(jobId)}/report.pdf">Open the report link</a>
    </div>
    <p class="help">The report link works as soon as the analysis finishes, for 60 minutes.</p>`;
}

function confirmOfflineCard(jobId) {
  // RESERVED copy (see offlineCard): a fetch that never reached the server.
  return `${statusLine('unknown', 'No connection', false)}
    <p class="kv"><b class="stop">Network error — your confirmation didn’t reach us.</b>
      Nothing has been analysed, and your file is still waiting on the server.</p>
    <div class="ready-actions">
      <button type="button" class="cta" id="poll-resume" data-job="${esc(jobId)}">Check again</button>
    </div>
    <p class="help">Check the connection, then press Check again to bring the interpretation back.</p>`;
}

function expiredCard() {
  return `${statusLine('unknown', 'Expired', false)}
    <p class="kv"><b>This analysis is no longer on the server.</b> Reports are kept for
      60 minutes after they finish, then deleted (<a href="/privacy">Privacy</a>).</p>
    <p class="help">Upload the file again to produce a fresh report.</p>`;
}

// ── the run, as one component (Session UX-3) ──────────────────────
// One place decides what the analyst sees for a job, and it reads NOTHING but
// the wire. Before this, the mapping lived twice — in `pollJob`'s tail and in
// `submitJob`'s error branch — and a state handled in one and not the other
// was a card nobody would notice was missing.
//
// The inputs are exactly the five fields `GET /api/jobs/{id}` publishes:
// `state`, `phase`, `failure_kind`, `retryable`, `degraded_reason`. The finer
// `error_code` is deliberately not on the wire (jobs.py: it is the operator's
// grep handle and lives in the log), and nothing here infers a state the
// server did not send — including `rendering`, which no phase reports.
const RUN_TERMINAL = ['done', 'degraded', 'gate_fail', 'error'];
// The states that ask the analyst for something — an outcome to read or a
// decision to make — and therefore take focus. `awaiting_confirm` is not
// terminal and belongs here anyway: a card that is waiting for a human is
// waiting for one who has to be able to find it.
const RUN_ANNOUNCE = RUN_TERMINAL.concat(['awaiting_confirm']);

function runCard(jobId, data) {
  const state = (data && data.state) || 'queued';
  if (state === 'queued' || state === 'running') return stepCard(state, data && data.phase);
  if (state === 'awaiting_confirm') return confirmCard(jobId, data.interpretation);
  if (state === 'gate_fail') return stoppedCard(jobId, data);
  if (state === 'error') return errorCard(data.safe_message, data.failure_kind, data.retryable);
  if (state === 'done' || state === 'degraded') {
    return readyCard(jobId, data, state === 'degraded');
  }
  // A state this build does not know. Keep showing work rather than inventing
  // an outcome: drawing a report card for an unrecognised word is how a UI
  // comes to announce a result the server never reported.
  return stepCard('queued', null);
}

// Which state last took focus, so a card `show()` rewrites on every poll does
// not steal it back every 2.5 seconds.
let announcedState = '';

function showRun(jobId, data) {
  const state = (data && data.state) || 'queued';
  // Session UX-5. Everything that must be TRUE before the outcome is drawn
  // happens here, before `show`, for PURGE-SYNC's reason one layer up: the
  // instant the analyst can read "Report ready", everything that card claims is
  // already so. A card that offered to save a reading it was about to save
  // itself, or that said "kept in this browser" before anything was kept, would
  // be describing an intention rather than a fact.
  let saved = null;
  if (state === 'done' || state === 'degraded') {
    saved = autoSaveReading(jobId, data);
    if (saved) {
      refreshTrendNote();
      // C6 asks for an undo, and this is the component every other lifecycle
      // action reports through. The sentence says what happened AND where it
      // went, because "Saved" alone does not tell an analyst which machine.
      // Session INTAKEFIX-1 (INTAKE-2's F-3). The sentence counts the POINTS,
      // because "Saved to Compressor train 01's trend" after a four-point route
      // does not tell an analyst that four series moved -- and an Undo whose
      // scope the analyst cannot see is worse than none.
      const points = 1 + saved.others.length;
      notify(
        points > 1
          ? `Saved ${points} readings to ${saved.key.split('|')[0]}\u2019s trend, one per `
            + 'measurement location, in this browser.'
          : `Saved to ${saved.key.split('|')[0]}\u2019s trend, in this browser.`,
        'ok', {
          label: 'Undo',
          run: () => {
            // EVERY point the run filed, through the one `undoAutoSave` that
            // already drops a reading together with its report facts and its
            // kept PDF. One upload is one action: an analyst who did not mean
            // to save it did not mean to save three quarters of it.
            undoAutoSave(saved.key, saved.ts);
            saved.others.forEach((other) => undoAutoSave(other.key, other.ts));
            if (lastReady) show(readyCard(lastReady.jobId, lastReady.data, lastReady.degraded));
            refreshTrendNote();
            notify(
              points > 1
                ? `Not saved. None of the ${points} readings is in this machine\u2019s trend.`
                : 'Not saved. The reading is not in this machine\u2019s trend.',
              'gone');
          },
        });
    }
    // RULED D-26. Fire-and-forget, deliberately: the report the analyst can
    // already open is unaffected by this, and a run must never wait on a
    // convenience. When the bytes land the card is re-rendered so its own
    // account of what is kept becomes true rather than hopeful.
    const point = data && data.trend_point;
    if (jobId && point && RETAINED.trendKey && readTrend(RETAINED.trendKey)
        .some((p) => p.ts === point.captured_at)) {
      keepReport(RETAINED.trendKey, point.captured_at, jobId).then((held) => {
        if (held && lastReady) show(readyCard(lastReady.jobId, lastReady.data, lastReady.degraded));
      }).catch(() => null);
    }
  }
  show(runCard(jobId, data));
  // A `queued` state IS the start of a run — it is what `submitJob` and the
  // confirm handler draw the moment they POST — so it is where the record of
  // "already announced" is cleared. A retry therefore announces its outcome
  // again instead of being swallowed by the failure before it.
  //
  // Deliberately here rather than in `markRunStart`, which is pinned to its
  // exact source line by `test_poll_resilience.py` (the clock derives from
  // THIS page's POST and needs no wire field). That pin is about the clock;
  // this is about the run, and the run's own first state is the honest signal.
  if (state === 'queued') announcedState = '';
  // The outcome enters the way a confirmation enters. Same keyframes, same
  // duration token, same reduced-motion behaviour -- the run is a lifecycle
  // action like any other and should not have its own private motion.
  //
  // The value ALTERNATES rather than naming the state, and that is the whole
  // mechanism: a CSS animation restarts only when its animation-name changes
  // or the element is recreated. `#state` is not recreated (show() rewrites
  // its innerHTML) and one selector per state would compute the SAME
  // animation-name, so the card would slide in once and then never again.
  // Two selectors, two identical keyframe names, flipped on each announced
  // state -- so it animates on every real change and, just as importantly,
  // NOT on the poll that redraws the same state 2.5 seconds later.
  if (RUN_ANNOUNCE.indexOf(state) === -1 || announcedState === state) return;
  if (stateCard && stateCard.setAttribute) {
    stateCard.setAttribute('data-enter',
      stateCard.getAttribute('data-enter') === 'a' ? 'b' : 'a');
  }
  announcedState = state;
  // STRANGER C5: "The result appears BELOW the still-fully-rendered review
  // form, with no scroll; on desktop I saw only the top of a DRAFTING bar and
  // had to scroll to find out I had a Zone B / BPFO result. On mobile the whole
  // result is off-screen."
  //
  // Here, on the same edge as the announcement, so it happens ONCE per real
  // state change and never on the poll that redraws the same state 2.5s later.
  // `focusRunStatus` alone does not do this: focus brings an element into view
  // by its NEAREST edge, so a tall card arrives showing its last line. This
  // puts the top of the card at the top of the viewport.
  //
  // Scrolled to rather than replacing the form, deliberately: the form is what
  // "Analyze another file" comes back to, and tearing it down would lose the
  // machine values the next run needs.
  if (stateCard) scrollToEl(stateCard);
  // `#state` is aria-live=polite, so the card announces itself; this is the
  // other half, for the analyst who is on the keyboard rather than listening.
  // The status line carries the state IN WORDS, so it is the right landing
  // place: what it reads out is what happened.
  //
  // ON THE NEXT FRAME, and that is not caution. `show()` DEFERS its first
  // innerHTML write by a frame -- it registers the aria-live region before
  // mutating it, so the opening announcement is not swallowed as an unhide --
  // and a focus call that does not wait reaches an element that is not in the
  // document yet. rAF callbacks run in the order they were queued, so this one
  // lands after that write; on every later render the card is already there
  // and a frame costs nothing. The node harness cannot see this on its own
  // (its rAF is synchronous), so `run_card_tests.js` queues frames by hand.
  focusRunStatus();
}

/** Put focus on the current card's status line, on the next frame.
 *
 *  ON THE NEXT FRAME, and that is not caution — see `showRun` above.
 *  Also called by the `#post-retry` handler, which knows it has just destroyed
 *  the button the analyst was standing on: without it, focus falls back to
 *  <body> and the next Tab starts at the top of the document. */
function focusRunStatus() {
  const land = () => {
    const head = document.getElementById('run-status');
    if (head && head.focus) head.focus();
  };
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(land);
  else land();
}

// ── live submission + polling ─────────────────────────────────
/** What the run button's note says, which depends on whether pressing it would
 *  be a new analysis or a repeat of one already paid for. */
function setSubmitNote(finished) {
  const note = document.getElementById('submit-note');
  if (!note) return;
  note.innerHTML = finished
    ? 'This file has been analysed — its report is above. Choose another file, or press '
      + '<b>Analyze another file for this machine</b>, to run again.'
    : 'Deterministic analysis in seconds; drafted report in about a minute. Your raw file is '
      + 'deleted as soon as it has been read.';
}

// A new file is a new run, so the button comes back. Bound on the three slots
// rather than on the form, because a `change` on any other field is not a new
// analysis and must not re-arm a button that is deliberately down.
[fileInput].concat(EXTRA_SLOTS.map(([fid]) => document.getElementById(fid)))
  .forEach((inp) => {
    if (inp && inp.addEventListener) {
      inp.addEventListener('change', () => {
        if (submitBtn) submitBtn.disabled = false;
        setSubmitNote(false);
      });
    }
  });

function oversizeFile() {
  const inputs = [fileInput].concat(EXTRA_SLOTS.map(([fid]) => document.getElementById(fid)));
  for (const inp of inputs) {
    const file = inp && inp.files && inp.files[0];
    if (file && file.size > MAX_UPLOAD_BYTES) return file;
  }
  return null;
}

async function submitJob() {
  submitBtn.disabled = true;
  // Client-side size check: refusing here costs the analyst nothing, while
  // uploading 40 MB to be told 413 costs them a minute on a plant connection.
  const tooBig = oversizeFile();
  if (tooBig) {
    show(sizeCard(tooBig.name, tooBig.size));
    submitBtn.disabled = false;
    return;
  }

  const fd = new FormData(form);
  // Demo path without DataTransfer support: the example blob never reached the
  // file input, so attach it here. Same field, same endpoint as any upload.
  if (!fileInput.files.length && demoFile) fd.set('file', demoFile, EXAMPLE_FILENAME);
  // Drop untouched optional slots so FastAPI sees them as absent (an empty file
  // part would otherwise post as a zero-byte file).
  let nChannels = (fileInput.files.length || demoFile) ? 1 : 0;
  EXTRA_SLOTS.forEach(([fid, did]) => {
    const inp = document.getElementById(fid);
    if (inp.files.length) { nChannels++; }
    else { fd.delete(fid); fd.delete(did); }
  });
  // The first slot's direction defaults to the empty value on purpose: a single
  // file with an unstated direction is the identity path, whose report stays
  // byte-identical to the pre-multi-axis one. As soon as a second channel is in
  // play every slot must name its direction, so state the default explicitly.
  if (nChannels > 1 && !fd.get('direction')) fd.set('direction', 'radial_h');
  // Session INTAKE-2 — the other locations' untouched file inputs, dropped for
  // the same reason and by the same rule as the three slots above: an empty
  // `<input type=file>` posts a zero-byte part, and the server would read that
  // as "a file the analyst chose that we cannot parse" rather than "no file".
  // `locations.py::_slot_is_present` mirrors this on the server as the backstop,
  // because a hand-built post can still carry one.
  dropEmptyLocationParts(fd);

  // U5. Refuse locally exactly what the server refuses at app.py:216-224, and
  // NOTHING MORE. A client check that is broader than the server's rejects
  // valid uploads and the analyst has no way to appeal it, so this mirrors the
  // server rule clause for clause:
  //   * it only applies to MULTI-FILE spectrum posts (n == 1 and the
  //     non-spectrum modes never reach the server's duplicate loop at all);
  //   * it reads the same three field names the server reads;
  //   * it only fires on an exact repeat of the same direction value.
  // Everything else -- a missing direction, an unknown value, a multi-file
  // trend post -- is left to the server, which has better words for it.
  if (nChannels > 1 && String(fd.get('mode') || 'spectrum') === 'spectrum') {
    const chosen = ['direction', 'direction_2', 'direction_3']
      .filter(k => fd.get(k) !== null)
      .map(k => String(fd.get(k)));
    const dupe = chosen.find((d, i) => d && chosen.indexOf(d) !== i);
    if (dupe) {
      show(duplicateDirectionCard(dupe));
      submitBtn.disabled = false;
      return;
    }
  }
  // Alias is optional (a first run needs only file + RPM + code); the report
  // still needs a name to carry.
  if (!String(fd.get('machine_alias') || '').trim()) fd.set('machine_alias', 'Unnamed machine');
  const alias = fd.get('machine_alias') || 'this machine';
  const fname = (fileInput.files[0] && fileInput.files[0].name) || (demoFile && EXAMPLE_FILENAME) || 'your file';
  const chan = nChannels > 1 ? `${nChannels} channels · ` : '';
  window._ctx = `Machine <code>${esc(alias)}</code> · <code>${esc(fname)}</code> · ${chan}`;
  // The ledger's flipping lines come from what was SUBMITTED, captured here,
  // rather than from the checkboxes' state when the card happens to render.
  RETAINED.trace = !!(form.elements.retain_trace && form.elements.retain_trace.checked);
  RETAINED.remember = !!(rememberBox && rememberBox.checked);
  // Session HIST-1 — the readings this machine already has, sent so the report
  // can assess the trend. Captured here with the other two, and for the same
  // reason: the ready card's offer must belong to the identity that was
  // SUBMITTED, not to whatever the form holds when the job finishes. Nothing is
  // set when there is nothing to send — an unconditional `fd.set` would make
  // `history` an always-present part carrying an empty list.
  RETAINED.trendKey = currentTrendKey();
  // RULED D-24, captured with the rest of what was SUBMITTED and for the same
  // reason the comment above gives: the card describes the run, not the form
  // as it stands when the run finishes. Nothing new is stored -- a third
  // localStorage key would owe a retention-ledger row and a /privacy
  // paragraph, and /privacy belongs to another session.
  RETAINED.duplicate = duplicateForecast();
  RETAINED.duplicate.mode = duplicateChoice;
  // RULED (Sep 7), C6: a reading run against a SELECTED machine is saved to it.
  // Captured here with the rest of what was submitted, and for the same reason:
  // the decision belongs to the run, not to whatever the form holds when it
  // finishes. "Selected" is answered from the STORE -- a card exists under this
  // identity -- or from Remember, which is the analyst saying it should. A
  // machine that was only typed keeps the button it always had; inventing a
  // series for it silently is the same error as losing one.
  RETAINED.autosave = !!RETAINED.trendKey
    && (!!rememberBox && rememberBox.checked
        || readMachines().some((m) => cardKey(m) === RETAINED.trendKey));
  // What this run was OF, for the readings table (C10). The names never leave
  // the browser -- they are not posted, and the log line has never carried a
  // filename (`app.py`, and the ledger says so).
  RETAINED.files = chosenFileNames().join(', ');
  const trendPoints = RETAINED.trendKey ? trendPayload(RETAINED.trendKey) : [];
  RETAINED.trendCount = trendPoints.length;
  if (trendPoints.length) fd.set('history', JSON.stringify(trendPoints));
  markRunStart();                      // the clock is measured from THIS POST
  showRun(null, { state: 'queued' });

  let res;
  try {
    res = await fetch('/api/jobs', { method: 'POST', body: fd });
  } catch (err) {
    retirePolling();                   // PARTC F-8, as below
    show(offlineCard());               // never reached the server
    submitBtn.disabled = false;
    return;
  }

  if (!res.ok) {
    // Session INTAKE-2 / PARTC F-8. FIRST, before any card is chosen: retire
    // the previous run. Without this the old job's poll loop is still alive and
    // repaints its "Accepted" panel over whichever card we pick below, within a
    // second, which is exactly what PART-C photographed -- a stale panel, a
    // running elapsed clock, and no error text anywhere on the page. It also
    // nulls the clock, so nothing is left counting up for a run that was
    // refused before it began.
    retirePolling();
    const detail = await res.json().then(d => d && d.detail).catch(() => null);
    submitBtn.disabled = false;
    if (res.status === 401) { show(inviteCard()); return; }
    if (res.status === 413) {
      const file = (fileInput.files[0] || {});
      show(sizeCard(file.name || fname, file.size || MAX_UPLOAD_BYTES + 1));
      return;
    }
    if (res.status === 429 || res.status === 503) {
      const stated = retryAfterSeconds(res);
      show(busyCard(stated));
      if (stated !== null) startCountdown(stated * 1000, 'server busy');
      return;
    }
    if (res.status >= 500 || typeof detail !== 'string') { show(transientCard()); return; }
    // 400 / 415 / 422 — the server has told us exactly what is wrong with the
    // request, in words written for the analyst. Say that, not a generic error.
    show(errorCard(detail));
    return;
  }

  let jobId;
  try {
    ({ job_id: jobId } = await res.json());
  } catch (err) {
    show(transientCard());
    submitBtn.disabled = false;
    return;
  }
  rememberCurrentMachine();  // only once the job was actually accepted
  pollJob(jobId);
}

form.addEventListener('submit', (e) => {
  e.preventDefault();
  submitJob();
});

// Polling lives outside the submit handler so a confirmed job — or a resumed
// one — can restart it.
function pollJob(jobId) {
  lastJobId = jobId;
  let failures = 0;
  let lastCause = 'network';
  // Session INTAKE-2 / PARTC F-8. This loop's own generation. Starting a loop
  // retires any earlier one, and every tick below checks it still owns the
  // screen before painting -- see `retirePolling`.
  pollGeneration += 1;
  const myGeneration = pollGeneration;
  const superseded = () => myGeneration !== pollGeneration;

  const backoffMs = () => POLL_BACKOFF_MS[Math.min(failures, POLL_BACKOFF_MS.length - 1)];

  const retry = (waitMs, cause, note) => {
    if (superseded()) return;
    failures += 1;
    lastCause = cause;
    if (failures >= POLL_MAX_FAILURES) {
      // Sustained, not a hiccup. Say so honestly — and keep the job id, because
      // the analysis itself is probably fine.
      show(lostContactCard(jobId, lastCause));
      submitBtn.disabled = false;
      return;
    }
    if (cause === 'busy') startCountdown(waitMs, 'server busy');
    else softStatus(note);
    setTimeout(poll, waitMs);
  };

  const poll = async () => {
    // Session INTAKE-2 / PARTC F-8. Checked here, before the fetch, AND again
    // after it below: a tick can be superseded while its request is in flight,
    // and it is the paint that must not happen, not the request.
    if (superseded()) return;
    let res;
    try {
      res = await fetch('/api/jobs/' + jobId);
    } catch (err) {
      retry(backoffMs(), 'network', 'connection hiccup — retrying');
      return;
    }
    // The request was in flight while this loop may have been superseded. Every
    // paint below this line belongs to a run the analyst has moved on from
    // (PARTC F-8).
    if (superseded()) return;
    if (res.status === 429) {
      retry(retryAfterMs(res, backoffMs()), 'busy', '');
      return;
    }
    if (res.status === 404 || res.status === 410) {
      show(expiredCard());
      submitBtn.disabled = false;
      return;
    }
    if (!res.ok) {                     // 5xx and anything else unexpected
      retry(backoffMs(), 'network', 'connection hiccup — retrying');
      return;
    }
    let data;
    try {
      data = await res.json();
    } catch (err) {                    // a proxy error page instead of JSON
      retry(backoffMs(), 'network', 'connection hiccup — retrying');
      return;
    }

    failures = 0;
    softStatus('');
    // Session UX-3: WHICH card is `showRun`'s decision, not this loop's. What
    // stays here is the only thing this loop knows and the component does not —
    // when to ask again, and when to give the submit button back.
    if (data.state === 'queued' || data.state === 'running') {
      showRun(jobId, data);
      setTimeout(poll, POLL_INTERVAL_MS);
      return;
    }
    if (data.state === 'awaiting_confirm') {
      // The job is paused on purpose: an inferred layout waits for the analyst.
      // Keep polling slowly so an expiry is noticed, but do not hammer a card
      // that is waiting for a human.
      showRun(jobId, data);
      submitBtn.disabled = false;
      setTimeout(poll, POLL_CONFIRM_MS);
      return;
    }
    // STRANGER C5: "The Analyze file button also re-enables after completion,
    // inviting a second paid run." It comes back for a failure -- a gate stop
    // or an error is something the analyst can act on and retry -- and stays
    // down for a finished one, where pressing it again would re-analyse the
    // same file for a second charge. `#again`, and choosing a new file, are
    // the two ways forward, and both re-enable it.
    const finished = data.state === 'done' || data.state === 'degraded';
    submitBtn.disabled = finished;
    setSubmitNote(finished);
    showRun(jobId, data);
  };
  poll();
}

// ── display-only preview mode for screenshots / review ────────
// ?preview=queued|analyzing|drafting|ready|ready-healthy|stopped|degraded|error
//         |ready-multiaxis|speed-mismatch|partial-gate|error-server
//         |confirm|confirm-multi|confirm-cached
//         |lost-contact|server-busy|server-busy-unknown|offline|oversize|invite
(function preview() {
  const p = new URLSearchParams(location.search).get('preview');
  if (!p) return;
  window._ctx = 'Machine <code>Pump A</code> · <code>route.uff</code> · ';
  markRunStart();
  const demoReady = { result_summary: { no_findings: false,
    faults: [{ label: 'Bearing outer-race fault (BPFO)', confidence: 'high' }],
    severity: 'unrated — ISO severity requires velocity data' } };
  const demoHealthy = { result_summary: { no_findings: true, faults: [],
    severity: 'unrated — ISO severity requires velocity data' } };
  const demoStop = { gate_summary: {
    reasons: ['Speed check failed — no 1× peak within ±5% of the stated 1780 RPM.'],
    collect: ['Re-measure at steady operating load and confirm running speed at the machine.'] } };
  const ch = (direction, label, status, opts) => Object.assign(
    { direction, label, status, has_velocity: true, assumed: false, detail: null }, opts || {});
  const demoMulti = { result_summary: { no_findings: false,
    faults: [{ label: 'Angular misalignment', confidence: 'high' }],
    severity: 'unrated — ISO severity requires velocity data' },
    channels: { speed_warning: null, channels: [
      ch('radial_h', 'Radial – horizontal', 'ok'),
      ch('radial_v', 'Radial – vertical', 'ok'),
      ch('axial', 'Axial', 'ok')] } };
  const demoSpeed = { result_summary: demoReady.result_summary,
    channels: { channels: [ch('radial_h', 'Radial – horizontal', 'ok'), ch('axial', 'Axial', 'ok')],
      speed_warning: 'Speed check warning: the uploaded files’ dominant running-speed peaks (Radial – horizontal 30.0 Hz; Axial 47.0 Hz) do not agree with the stated running speed (30.0 Hz) — these files may not be from the same machine or operating condition.' } };
  const demoPartial = { result_summary: demoReady.result_summary,
    channels: { speed_warning: null, channels: [
      ch('radial_h', 'Radial – horizontal', 'ok'),
      ch('axial', 'Axial', 'gate_fail', { has_velocity: false, detail: 'machine not running (level below the running threshold)' })] } };
  const demoFile = (over) => Object.assign({
    slot: 1, label: 'Radial – horizontal', direction: 'radial_h', source: 'inference',
    status: 'ok', kind: 'spectrum', headline: 'in/s peak spectrum', x_axis: 'Hz',
    amplitude_unit: 'in_s', amplitude_label: 'in/s', detection: 'peak', rpm: 1785,
    rpm_from: 'the file header', severity_available: true, from_cache: false,
    ignored_columns: 0, editable: { velocity_unit: 'in_s', detection_type: 'peak', rpm: 1785 },
  }, over || {});
  const demoConfirm = Object.assign({ multi: false, usable: 1, files: [demoFile()] }, demoFile());
  const demoMultiConfirm = { multi: true, usable: 2, rpm: 1785, files: [
    demoFile(),
    demoFile({ slot: 2, label: 'Radial – vertical', direction: 'radial_v',
               headline: 'mm/s RMS spectrum', amplitude_unit: 'mm_s', amplitude_label: 'mm/s',
               detection: 'rms', from_cache: true, ignored_columns: 1,
               editable: { velocity_unit: 'mm_s', detection_type: 'rms', rpm: 1785 } }),
    demoFile({ slot: 3, label: 'Axial', direction: 'axial', status: 'unknown',
               message: 'We couldn’t interpret this file’s layout.' }),
  ] };
  const map = {
    confirm:       () => confirmCard('demo', demoConfirm),
    'confirm-multi': () => confirmCard('demo', demoMultiConfirm),
    'confirm-cached': () => confirmCard('demo', (() => {
      const f = demoFile({ from_cache: true, headline: 'g (acceleration) RMS spectrum',
        amplitude_unit: 'g', amplitude_label: 'g (acceleration)', severity_available: false,
        rpm_from: 'the form', editable: { velocity_unit: '', detection_type: 'rms', rpm: 1785 } });
      return Object.assign({ multi: false, usable: 1, files: [f] }, f);
    })()),
    'lost-contact': () => lostContactCard('demo', 'network'),
    'server-busy': () => busyCard(45),
    'server-busy-unknown': () => busyCard(null),
    offline:       () => offlineCard(),
    oversize:      () => sizeCard('route_export_full.csv', 41_000_000),
    invite:        () => inviteCard(),
    queued:        () => stepCard('queued', null),
    analyzing:     () => stepCard('running', 'analyzing'),
    drafting:      () => stepCard('running', 'drafting'),
    ready:         () => readyCard('demo', demoReady, false),
    'ready-healthy': () => readyCard('demo', demoHealthy, false),
    'ready-multiaxis': () => readyCard('demo', demoMulti, false),
    'speed-mismatch': () => readyCard('demo', demoSpeed, false),
    'partial-gate': () => readyCard('demo', demoPartial, false),
    degraded:      () => readyCard('demo', demoHealthy, true),
    stopped:       () => stoppedCard('demo', demoStop),
    error:         () => errorCard('This file couldn’t be read. The format wasn’t recognized as CSV, XLSX, UFF/UNV, WAV, or .MAT.', 'bad_upload', false),
    'error-server': () => errorCard('The analysis failed on our side — your file was not the problem. Please try again, or email us the job reference and we will look.', 'server_error', true),
  };
  if (!map[p]) return;
  // Clean per-state view for screenshots / operator review: masthead + the one
  // state card + footer. Does not touch the live path (only runs with ?preview=).
  // Session UX-1: by id, through setHidden. Two reasons it stopped being a
  // querySelectorAll sweep. The bare `hidden` property is defeated by any
  // author rule declaring a `display`, which is the whole point of setHidden;
  // and `tests/js/harness.js` answers nothing at all about a querySelectorAll,
  // so this line was the one piece of view-switching in the file that no test
  // could see. `#state` is hidden with the rest and re-revealed by `show()`.
  LANDING_IDS.concat(['form-card', 'view-machines', 'view-machine',
    'view-machine-edit', 'state'])
    .forEach((id) => hideById(id, true));
  show(map[p]());
})();

// ── landing: scroll reveal ───────────────────────────────────────────
// Appended below everything the app needs, and reached only on the landing.
// Display-only: it adds a class and draws nothing, and it cannot change what
// is uploaded, what is computed or what is shown in a state card. It opens
// with the checks that make it safe to be absent -- under reduced motion,
// without IntersectionObserver, or inside the node harness that executes this
// file, it returns before touching anything.
//
// Session UX-4 DELETED the second block that lived here, `heroDepth`. It
// fetched /example-spectrum.csv a second time, injected /assets/three.min.js
// (614 KB) and crossfaded a WebGL waterfall in behind the hero after 2.8s.
// Three reasons, in order of weight: the figure it sat behind -- `svg.hv-svg`
// in index.html -- is drawn from the same file bin for bin, is labelled, and
// is already the thing an analyst reads, so the depth added nothing to read;
// the injected build was the only console output the whole site produced
// (three.js deprecates its non-ESM builds at r150+); and it was the only
// script the product loaded besides this file, on a page whose copy says it
// loads nothing from anyone else. The vendored build and its LICENSE are
// deleted with it. STRANGER_TEST_2026-09-07 U11.

(function landingReveal() {
  if (reduceMotion || typeof IntersectionObserver === 'undefined') return;
  const targets = document.querySelectorAll('.rv');
  if (!targets.length || !document.documentElement) return;
  document.documentElement.classList.add('rv-armed');
  const io = new IntersectionObserver((entries) => {
    entries.forEach((en) => {
      if (!en.isIntersecting) return;
      en.target.classList.add('rv-in');
      io.unobserve(en.target);
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });
  targets.forEach((el) => io.observe(el));
})();
