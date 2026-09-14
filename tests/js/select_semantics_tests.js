/**
 * Session UX-3: the two defects the node harness was structurally blind to,
 * run in node against the REAL app.js (see harness.js). Driven by pytest —
 * tests/test_ux3_progress.py — so `pytest` remains the one command.
 *
 * UX-2 found both of these in a browser and wrote into its own acceptance
 * checklist that no node test could ever see them, because `makeElement`
 * backed `.value` with a plain property: a `<select>` in this harness happily
 * held a value none of its options carried. That was a fact about the
 * instrument, not about the world. The harness now carries real option
 * semantics, and these are the two claims it could not make:
 *
 *   * UX-2 F-2 #2 — A BEARING WE HOLD NO GEOMETRY FOR IS DROPPED IN SILENCE.
 *     `config/bearings.json` is a closed catalogue of eight and the field is a
 *     select over it, so a card saved when the field was free text loses what
 *     the analyst typed the moment it is applied. That it never worked is not
 *     a defence — discarding what somebody entered is a thing to SAY. Both
 *     places a card is applied must name it.
 *   * UX-2 F-1 #4 — A FORM WHOSE VALUES LIVE IN `value=` ATTRIBUTES READS BACK
 *     BLANK. `editControl` renders controls with no value and `fillEditForm`
 *     assigns every one, because an attribute is not a selection. The first
 *     draft did the opposite and looked right on screen.
 *
 * The suite's own first check is a self-test of the instrument: if the harness
 * ever goes back to a plain property, every other check here would pass for
 * the wrong reason, so the property is asserted directly first.
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { loadApp } = require('./harness');

/** The catalogue, from the file that IS the catalogue.
 *
 *  Not from `app.js`: `BEARING_MODELS` is a top-level `const`, which is not a
 *  property of the sandbox global and so cannot be read from here at all
 *  (UX-2 F-1 #1, met again). Reading `config/bearings.json` is better than
 *  working around that anyway -- `tests/test_ux2_bearings.py` already pins
 *  app.js's copy and index.html's copy against this same file, so all three
 *  lists are tied to one source rather than to each other. */
const CATALOGUE = Object.keys(JSON.parse(fs.readFileSync(
  path.join(__dirname, '..', '..', 'config', 'bearings.json'), 'utf8')).bearings);

const results = [];
const pending = [];

function test(name, fn) {
  pending.push(
    Promise.resolve()
      .then(fn)
      .then(() => results.push(['PASS', name]))
      .catch((err) => results.push(['FAIL', name, err && err.message])),
  );
}

const MACHINES_KEY = 'vib.machines.v2';
const TREND_KEY = 'vib.trend.v1';
const UNLISTED = 'SKF 32222 J2';

const CARD_FIELDS = ['machine_alias', 'rpm', 'iso_group', 'iso_support', 'bearing_model',
  'velocity_unit', 'detection_type', 'mode', 'direction',
  'sensor_sensitivity_mv_per_g', 'fmax_hz', 'spectral_lines', 'window_type',
  'averages', 'integration',
  'measurement_location', 'coupling', 'blades', 'drive_type', 'poles', 'line_freq_hz',
  'rotor_bars', 'gear_teeth_driving', 'gear_teeth_driven',
  'drive_pulley_mm', 'driven_pulley_mm', 'pulley_center_distance_mm'];

function card(overrides) {
  const entry = {};
  CARD_FIELDS.forEach((f) => { entry[f] = ''; });
  entry.machine_alias = 'Pump A';
  entry.measurement_location = 'Motor DE';
  entry.rpm = '1780';
  entry.iso_group = '2';
  entry.iso_support = 'rigid';
  entry.bearing_model = '6206';
  entry.coupling = 'coupled';
  return Object.assign(entry, overrides || {});
}

const noFetch = () => { throw new Error('the browser backend must not make a request'); };
const browserApp = (opts) => loadApp(Object.assign({ fetchImpl: noFetch }, opts || {}));

function seeded(extra) {
  return Object.assign({
    [MACHINES_KEY]: JSON.stringify([card(extra || {})]),
    [TREND_KEY]: JSON.stringify({ 'Pump A|Motor DE': [
      { ts: '2026-07-01T10:00:00+00:00', v: 2.0, zone: 'A', axis: 'z' }] }),
  }, {});
}

// ── 0. the instrument itself ──────────────────────────────────────────────

test('the harness backs a select with its real options, not a plain property', () => {
  const app = browserApp();
  const brg = app.getEl('brg');                       // the upload form's, by id
  assert.notStrictEqual(brg._options, null, '#brg is not backed by any option list');
  assert.ok(brg._options.indexOf('6206') > -1, 'the catalogue is not in the option list');
  brg.value = '6206';
  assert.strictEqual(brg.value, '6206', 'a legal value must stick');
  brg.value = UNLISTED;
  assert.strictEqual(brg.value, '', 'a value no option carries must NOT stick');
  // The other door to the same field. `app.js` reaches every upload field
  // through `form.elements[name]`, so a select that is only real by id is
  // real in the half of the file that does not use it.
  const byName = app.getEl('field:bearing_model');
  assert.notStrictEqual(byName._options, null, 'form.elements[name] is not option-backed');
  byName.value = UNLISTED;
  assert.strictEqual(byName.value, '');
});

test('a plain input is untouched — it still holds whatever it is given', () => {
  const app = browserApp();
  const alias = app.getEl('field:machine_alias');
  assert.strictEqual(alias._options, null, 'a text input must carry no option list');
  alias.value = 'anything at all';
  assert.strictEqual(alias.value, 'anything at all');
});

test('a select takes its initial value from `selected`, and re-rendering resets it', () => {
  const app = browserApp();
  // index.html marks Group 2 selected; a browser starts there, not at ''.
  assert.strictEqual(app.getEl('grp').value, '2');
  // `#direction`'s selected option carries the EMPTY value and the label
  // "Radial – horizontal" -- the fact `declaredAxis('')` is written around.
  assert.strictEqual(app.getEl('direction').value, '');
  const sup = app.getEl('sup');
  sup.value = 'flexible';
  sup.innerHTML = '<option value="rigid" selected>Rigid</option>'
    + '<option value="flexible">Flexible</option>';
  assert.strictEqual(sup.value, 'rigid', 're-rendering a select re-selects, like a browser');
});

// ── 1. UX-2 F-2 #2 — the unlisted bearing, in both places a card is applied ──

test('the machine form DROPS an unlisted bearing — and names it on screen', async () => {
  const app = browserApp({ storage: seeded({ bearing_model: UNLISTED }) });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE/edit');
  // The drop itself, observable in node for the first time. This is not a
  // wish: it is what Chrome does, and it is why the note below has to exist.
  assert.strictEqual(app.getEl('me-bearing_model').value, '',
    'a select cannot hold a value none of its options carry');
  assert.strictEqual(app.sandbox.readEditForm().bearing_model, '',
    'and what the form submits is the dropped value, not the saved one');
  const html = app.getEl('machine-edit-body').innerHTML;
  assert.ok(html.includes(UNLISTED), 'the model that was discarded is not named');
  assert.ok(html.includes('not one we hold geometry for'), html.slice(0, 400));
});

test('the upload form drops it too — and step 3 says so before the run', async () => {
  const app = browserApp({ storage: seeded({ bearing_model: UNLISTED }) });
  await app.flush();
  // Choosing the saved machine is what applies the card (`applyCard`).
  const saved = app.getEl('saved');
  saved.value = '0';
  saved.dispatchEvent({ type: 'change' });
  assert.strictEqual(app.getEl('field:bearing_model').value, '',
    'the upload form silently reset — which is the defect the note exists for');
  app.sandbox.renderUnlocks();
  const html = app.getEl('unlocks').innerHTML;
  assert.ok(html.includes(UNLISTED), 'step 3 does not name the discarded model');
  assert.ok(html.includes('not one we hold geometry for'));
  // And the roster tells the truth about the consequence: the screen is OFF.
  assert.ok(html.includes('Rolling-element bearing faults</b> — off.'), html.slice(0, 600));
});

test('a card we DO hold geometry for is applied without a warning', async () => {
  const app = browserApp({ storage: seeded() });          // bearing_model 6206
  await app.goTo('#/m/Pump%20A%7CMotor%20DE/edit');
  assert.strictEqual(app.getEl('me-bearing_model').value, '6206');
  assert.ok(!app.getEl('machine-edit-body').innerHTML.includes('not one we hold geometry for'),
    'a listed bearing must not be reported as discarded');
});

// ── 2. the closed list, from both forms ───────────────────────────────────

test('every bearing the form OFFERS is assignable in both forms', async () => {
  // Session UX-5 (B5). This iterated the whole of `config/bearings.json`,
  // which is still the list we hold geometry for and still what
  // `bearing_spec_from_form` honours -- but three of its entries are
  // benchmark-rig keys that the form no longer offers a route analyst. The
  // claim being protected is unchanged: a value either select can be handed
  // must be one it can hold. The list it is made against is the OFFERED one,
  // and the entries that left are covered by the negative below.
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE/edit');
  const OFFERED = app.sandbox.offeredBearings();
  assert.ok(OFFERED.length > 0, 'the form offers no bearings at all');
  assert.ok(OFFERED.every((k) => CATALOGUE.includes(k)),
    'the form offers a bearing config/bearings.json has no geometry for');
  for (const model of OFFERED) {
    const me = app.getEl('me-bearing_model');
    me.value = model;
    assert.strictEqual(me.value, model, `the machine form cannot hold ${model}`);
    const up = app.getEl('field:bearing_model');
    up.value = model;
    assert.strictEqual(up.value, model, `the upload form cannot hold ${model}`);
  }
  // ...and the empty "Not listed" answer, which is an honest one.
  app.getEl('me-bearing_model').value = '';
  assert.strictEqual(app.getEl('me-bearing_model').value, '');
});

test('a benchmark-rig bearing is NOT offered, and neither select can hold one', async () => {
  // The other half of B5, and the reason the remap below has to exist: a
  // <select> silently resets to empty for a value its options do not carry, so
  // once these three stopped being offered they became droppable. That is the
  // UX-2 F-2 #2 defect, and this asserts the hazard is real rather than assumed.
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE/edit');
  const corpus = app.sandbox.corpusBearings();
  assert.strictEqual(corpus.length, 3);
  for (const model of corpus) {
    assert.ok(CATALOGUE.includes(model), `${model} should still be in the catalogue`);
    assert.ok(!app.sandbox.offeredBearings().includes(model), `${model} is still offered`);
    const me = app.getEl('me-bearing_model');
    me.value = model;
    assert.strictEqual(me.value, '', `the machine form silently held ${model}`);
  }
});

test('a card holding the CWRU rig key is carried over as the bearing it is', async () => {
  // `SKF_6205` and `6205` are the same bearing with the same geometry in
  // config/bearings.json -- the separate key exists so CWRU cases cite an
  // explicitly-sourced entry. Dropping such a card to empty would turn the
  // bearing screen OFF for a machine it had been running for, silently.
  const app = browserApp({
    storage: { [MACHINES_KEY]: JSON.stringify([card({ bearing_model: 'SKF_6205' })]) },
  });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE/edit');
  assert.strictEqual(app.getEl('me-bearing_model').value, '6205', 'carried over');
  const note = app.getEl('machine-edit-body').innerHTML;
  assert.ok(note.includes('SKF_6205') && note.includes('6205'), note);
  assert.ok(!note.includes('not one we hold geometry for'),
    'we DO hold its geometry; that sentence is for a bearing we do not');
});

test('a rig bearing with no route equivalent is named, not dropped in silence', async () => {
  const app = browserApp({
    storage: { [MACHINES_KEY]: JSON.stringify([card({ bearing_model: 'MFPT_NICE' })]) },
  });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE/edit');
  assert.strictEqual(app.getEl('me-bearing_model').value, '', 'nothing to carry it to');
  assert.ok(app.getEl('machine-edit-body').innerHTML.includes('MFPT_NICE'),
    'discarding what somebody entered is a thing to say');
});

// ── 3. UX-2 F-1 #4 — assigned, never rendered as a `value=` attribute ─────

test('the machine form is filled by ASSIGNMENT, so every field reads back', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE/edit');
  const read = app.sandbox.readEditForm();
  // A `value=` attribute on a select selects nothing, and on an input is
  // invisible to `.value` in this harness. Either way these come back blank.
  assert.strictEqual(read.machine_alias, 'Pump A');
  assert.strictEqual(read.measurement_location, 'Motor DE');
  assert.strictEqual(read.rpm, '1780');
  assert.strictEqual(read.iso_group, '2');
  assert.strictEqual(read.iso_support, 'rigid');
  assert.strictEqual(read.coupling, 'coupled');
  assert.strictEqual(read.bearing_model, '6206');
});

test('no control the machine form renders carries a value= attribute', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE/edit');
  const html = app.getEl('machine-edit-body').innerHTML;
  const controls = html.match(/<(?:select|input)\b[^>]*>/g) || [];
  assert.ok(controls.length >= 7, `only ${controls.length} controls rendered`);
  for (const tag of controls) {
    assert.ok(!/\bvalue="/.test(tag), `a control carries its value in markup: ${tag}`);
  }
  // The options are the only place a `value=` belongs in this fragment.
  assert.ok(/<option value="6206">/.test(html), 'the catalogue is not rendered as options');
});

test('an edit round-trips: fill, read, save — with the select values intact', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE/edit');
  app.getEl('me-iso_support').value = 'flexible';
  app.getEl('me-coupling').value = 'uncoupled';
  app.getEl('view-machine-edit').dispatchEvent({ type: 'click', target: { id: 'me-save' } });
  await app.flush();
  const saved = JSON.parse(app.sandbox.localStorage.getItem(MACHINES_KEY))[0];
  assert.strictEqual(saved.iso_support, 'flexible');
  assert.strictEqual(saved.coupling, 'uncoupled');
  assert.strictEqual(saved.bearing_model, '6206', 'an untouched select was carried, not blanked');
});

Promise.all(pending).then(() => {
  let failures = 0;
  for (const [status, name, why] of results) {
    if (status === 'FAIL') failures += 1;
    console.log(`${status}  ${name}${why ? `  — ${why}` : ''}`);
  }
  console.log(`\n${results.length - failures}/${results.length} passed`);
  process.exit(failures ? 1 : 0);
});
