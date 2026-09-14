/**
 * Session GEOM-A — the machine card's v1 -> v2 migration, run in node against
 * the real app.js (see harness.js). Driven by pytest —
 * tests/test_geometry_webapp_js.py — so `pytest` stays the one command.
 *
 * This is the half a TestClient cannot see: the card lives in localStorage on
 * the analyst's device, the migration runs on PAGE LOAD, and the thing that
 * would go wrong is silent — an analyst who saved six machines last week opens
 * the page and the dropdown is empty, with no error anywhere. Nothing on the
 * server would notice.
 */

const assert = require('assert');
const { loadApp } = require('./harness');

const results = [];

function test(name, fn) {
  try {
    fn();
    results.push(['PASS', name]);
  } catch (err) {
    results.push(['FAIL', name, err && err.message]);
  }
}

const V1_KEY = 'vib.machines.v1';
const V2_KEY = 'vib.machines.v2';

/** A card exactly as Session INTAKE-HONEST wrote it — no geometry keys. */
const V1_CARD = {
  machine_alias: 'Pump A', rpm: '1780', iso_group: '2', iso_support: 'rigid',
  bearing_model: '6206', velocity_unit: 'mm_s', detection_type: 'rms', mode: 'spectrum',
  direction: '', sensor_sensitivity_mv_per_g: '100', fmax_hz: '1000',
  spectral_lines: '1600', window_type: 'hanning', averages: '4', integration: 'none',
};

function withStore(store) {
  return loadApp({ fetchImpl: () => { throw new Error('no fetch in these tests'); }, storage: store });
}

function stored(app, key) {
  const raw = app.sandbox.localStorage.getItem(key);
  return raw === null ? null : JSON.parse(raw);
}

// ── the migration ────────────────────────────────────────────────────────
test('a v1 card is adopted under the v2 key on load', () => {
  const app = withStore({ [V1_KEY]: JSON.stringify([V1_CARD]) });
  const cards = stored(app, V2_KEY);
  assert.ok(Array.isArray(cards) && cards.length === 1, 'the v1 card did not migrate');
  assert.strictEqual(cards[0].machine_alias, 'Pump A');
});

test('every value the analyst typed survives the migration', () => {
  const app = withStore({ [V1_KEY]: JSON.stringify([V1_CARD]) });
  const card = stored(app, V2_KEY)[0];
  Object.keys(V1_CARD).forEach((k) => {
    assert.strictEqual(card[k], V1_CARD[k], `field ${k} changed during migration`);
  });
});

test('the new geometry keys arrive empty, never invented', () => {
  const app = withStore({ [V1_KEY]: JSON.stringify([V1_CARD]) });
  const card = stored(app, V2_KEY)[0];
  ['coupling', 'blades', 'drive_type', 'poles', 'line_freq_hz', 'rotor_bars',
   'gear_teeth_driving', 'gear_teeth_driven', 'drive_pulley_mm', 'driven_pulley_mm',
   'pulley_center_distance_mm', 'measurement_location'].forEach((k) => {
    assert.strictEqual(card[k], '', `geometry field ${k} was not empty after migration`);
  });
});

test('the v1 key is dropped, so one machine is never stored twice', () => {
  const app = withStore({ [V1_KEY]: JSON.stringify([V1_CARD]) });
  assert.strictEqual(app.sandbox.localStorage.getItem(V1_KEY), null);
});

test('the migrated card appears in the saved-machines dropdown', () => {
  const app = withStore({ [V1_KEY]: JSON.stringify([V1_CARD]) });
  assert.ok(app.getEl('saved').innerHTML.indexOf('Pump A') > -1,
            'the migrated machine is not offered in the dropdown');
  assert.strictEqual(app.getEl('saved-row').hidden, false);
});

test('a v2 card is read as-is and the v1 key is left alone', () => {
  const v2 = Object.assign({}, V1_CARD, { machine_alias: 'Fan B', blades: '6', coupling: 'uncoupled' });
  const app = withStore({ [V2_KEY]: JSON.stringify([v2]), [V1_KEY]: JSON.stringify([V1_CARD]) });
  const cards = stored(app, V2_KEY);
  assert.strictEqual(cards.length, 1);
  assert.strictEqual(cards[0].machine_alias, 'Fan B');
  assert.strictEqual(cards[0].blades, '6');
  // v2 exists, so no migration ran and nothing was deleted behind the analyst.
  assert.ok(app.sandbox.localStorage.getItem(V1_KEY) !== null);
});

test('no stored cards at all is not an error', () => {
  const app = withStore({});
  assert.strictEqual(app.getEl('saved-row').hidden, true);
  assert.strictEqual(stored(app, V2_KEY), null);
});

test('a corrupt stored value is ignored, not thrown', () => {
  const app = withStore({ [V2_KEY]: 'not json at all' });
  assert.strictEqual(app.getEl('saved-row').hidden, true);
});

// ── selecting a saved machine restores the geometry ──────────────────────
test('choosing a saved machine fills the geometry fields back in', () => {
  const card = Object.assign({}, V1_CARD, {
    machine_alias: 'Blower C', blades: '8', coupling: 'uncoupled', poles: '4',
    drive_type: 'vfd', drive_pulley_mm: '150', driven_pulley_mm: '300',
    pulley_center_distance_mm: '600', measurement_location: 'Motor DE',
  });
  const app = withStore({ [V2_KEY]: JSON.stringify([card]) });
  const sel = app.getEl('saved');
  sel.value = '0';
  sel.dispatchEvent({ type: 'change' });
  const form = app.getEl('upload-form');
  assert.strictEqual(form.elements.blades.value, '8');
  assert.strictEqual(form.elements.coupling.value, 'uncoupled');
  assert.strictEqual(form.elements.poles.value, '4');
  assert.strictEqual(form.elements.drive_type.value, 'vfd');
  assert.strictEqual(form.elements.drive_pulley_mm.value, '150');
  assert.strictEqual(form.elements.measurement_location.value, 'Motor DE');
});

test('choosing a MIGRATED machine leaves every geometry field blank', () => {
  const app = withStore({ [V1_KEY]: JSON.stringify([V1_CARD]) });
  const sel = app.getEl('saved');
  sel.value = '0';
  sel.dispatchEvent({ type: 'change' });
  const form = app.getEl('upload-form');
  assert.strictEqual(form.elements.blades.value, '');
  assert.strictEqual(form.elements.coupling.value, '');
  // ...and the fields the analyst DID fill in are back.
  assert.strictEqual(form.elements.rpm.value, '1780');
  assert.strictEqual(form.elements.bearing_model.value, '6206');
});

// ── report ───────────────────────────────────────────────────────────────
const failed = results.filter((r) => r[0] === 'FAIL');
results.forEach(([status, name, message]) => {
  console.log(`${status}  ${name}${message ? `\n      ${message}` : ''}`);
});
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
