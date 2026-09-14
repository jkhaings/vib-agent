/**
 * Session UX-4, slice 2 — the two shell behaviours that live in app.js and can
 * only be proved by running it: which way the home page orders itself, and
 * what the step rail publishes about where the analyst is.
 *
 * Both are ATTRIBUTES rather than classes, and that is the reason this file
 * can exist at all: `tests/js/harness.js` records `setAttribute` in `_attrs`
 * and stubs `classList` to no-ops, so the same behaviour expressed as a class
 * would be true of the page and invisible to every suite here. UX-2 F-1 and
 * UX-3 F-12 are both instances of that trap; this is the first slice to pick
 * the testable mechanism on purpose because of them.
 */
const assert = require('assert');
const { loadApp } = require('./harness.js');

let pass = 0, fail = 0;
function test(name, fn) {
  try { const r = fn(); if (r && r.then) return r.then(
    () => { pass += 1; }, (e) => { fail += 1; console.error('FAIL ', name); console.error('     ', e.message); });
    pass += 1; }
  catch (e) { fail += 1; console.error('FAIL ', name); console.error('     ', e.message); }
  return Promise.resolve();
}

// The store's real shape, taken from tests/js/machine_store_tests.js rather
// than invented: `vib.machines.v2` is a flat array of CARD entries (not
// {id, alias, readings} objects — that is the SHAPE `getMachines()` builds
// FROM it), and the trend lives in its own key. A fixture guessed from the
// rendered view rather than from the store is a test that proves the guess.
const MACHINES_KEY = 'vib.machines.v2';
const TREND_KEY = 'vib.trend.v1';
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

const SERIES = [
  { ts: '2026-07-01T10:00:00+00:00', v: 2.0, zone: 'A', axis: 'z' },
  { ts: '2026-08-01T10:00:00+00:00', v: 2.4, zone: 'B', axis: 'z' },
];

function seeded() {
  return {
    [MACHINES_KEY]: JSON.stringify([card()]),
    [TREND_KEY]: JSON.stringify({ 'Pump A|Motor DE': SERIES }),
  };
}

function browserApp(opts) {
  return loadApp(Object.assign({
    fetchImpl: () => { throw new Error('the browser backend must not make a request'); },
  }, opts || {}));
}

// ── C2 · the home page's order ───────────────────────────────────────────

(async () => {

await test('an empty browser puts the landing first', async () => {
  const app = browserApp({ hash: '#/' });
  await app.flush();
  assert.strictEqual(app.getEl('view-machines').getAttribute('data-first'), 'landing',
    'a first-time visitor still lands on the empty dashboard — STRANGER C2');
});

await test('a browser that has a machine puts the machines panel first', async () => {
  const app = browserApp({ hash: '#/', storage: seeded() });
  await app.goTo('#/');
  assert.strictEqual(app.getEl('view-machines').getAttribute('data-first'), 'machines');
});

await test('the order flips when the last machine is forgotten', async () => {
  // The state that made C2 worth fixing in the client rather than the server:
  // it is not "first visit", it is "has no machines", and that can become
  // true again at any time.
  const app = browserApp({ hash: '#/', storage: seeded() });
  await app.goTo('#/');
  assert.strictEqual(app.getEl('view-machines').getAttribute('data-first'), 'machines');
  // BOTH keys, and that is a fact about the store rather than test hygiene:
  // `browserMachines()` builds its list from the cards AND from every id in
  // the trend store, so a machine whose card is gone but whose readings
  // remain is still a machine. Clearing only the cards leaves the list
  // non-empty and this test would have pinned the wrong thing.
  app.sandbox.localStorage.setItem(MACHINES_KEY, '[]');
  app.sandbox.localStorage.setItem(TREND_KEY, '{}');
  await app.goTo('#/');
  assert.strictEqual(app.getEl('view-machines').getAttribute('data-first'), 'landing');
});

await test('a signed-out account backend gets the pitch, not an error panel alone', async () => {
  // The third call site, and the one a refactor drops: getMachines() rejects
  // with 401 and the catch branch has to order the page too.
  const app = loadApp({
    accounts: true,
    hash: '#/',
    fetchImpl: () => Promise.resolve({
      ok: false, status: 401, headers: { get: () => null },
      json: () => Promise.resolve({ detail: 'sign in' }),
    }),
  });
  await app.goTo('#/');
  assert.strictEqual(app.getEl('view-machines').getAttribute('data-first'), 'landing');
});

// ── the machines panel carries no second Add-a-machine ───────────────────

await test('neither branch renders its own Add a machine any more', async () => {
  const empty = browserApp({ hash: '#/' });
  await empty.flush();
  assert.ok(!empty.getEl('machines-body').innerHTML.includes('/#/m/new'),
    'the empty state grew its duplicate back');
  const full = browserApp({ hash: '#/', storage: seeded() });
  await full.goTo('#/');
  assert.ok(!full.getEl('machines-body').innerHTML.includes('/#/m/new'),
    'the card list grew its duplicate back');
  assert.ok(full.getEl('machines-body').innerHTML.includes('mcards'),
    'the card list is not rendering at all — the check above would pass vacuously');
});

// ── U5 · what the step rail publishes ────────────────────────────────────

await test('the rail publishes the step it is on, and the total', async () => {
  const app = browserApp({ hash: '#/new' });
  await app.goTo('#/new');
  const rail = app.getEl('step-rail');
  assert.strictEqual(rail.getAttribute('data-step'), '1');
  assert.strictEqual(rail.getAttribute('data-of'), '4');
});

await test('the published step follows the analyst through the wizard', async () => {
  // `reachableStep()` clamps to 1 without an rpm and to 2 without a file, so
  // the rpm is filled first — asking for step 2 with an empty form is asking
  // to be clamped, and a test that did not know that would be pinning the
  // clamp while claiming to pin the rail.
  const app = browserApp({ hash: '#/new' });
  await app.goTo('#/new');
  assert.strictEqual(app.getEl('step-rail').getAttribute('data-step'), '1');
  app.sandbox.document.getElementById('field:rpm').value = '1780';
  await app.goTo('#/new/2');
  assert.strictEqual(app.getEl('step-rail').getAttribute('data-step'), '2',
    'the rail did not follow the analyst to step 2');
  await app.goTo('#/new/1');
  assert.strictEqual(app.getEl('step-rail').getAttribute('data-step'), '1');
});

await test('the total is published, and matches the rail the page ships', async () => {
  // A magic "4" in the stylesheet would outlive a fifth step, so the compact
  // phone rail reads BOTH numbers from the DOM. `STEP_COUNT` itself is a
  // top-level const and therefore not a property of the sandbox global
  // (UX-2 F-1 #1's trap, met again) — so the total is checked against the
  // number of rail items index.html actually renders, which is the thing it
  // has to agree with anyway.
  const app = browserApp({ hash: '#/new' });
  await app.goTo('#/new');
  const published = Number(app.getEl('step-rail').getAttribute('data-of'));
  const inMarkup = [1, 2, 3, 4].filter((i) => app.getEl('rail-' + i)._known).length;
  assert.strictEqual(published, inMarkup);
  assert.strictEqual(published, 4);
});

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);
})();
