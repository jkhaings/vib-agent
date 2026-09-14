/**
 * Session UX-4, slice 3 — ONE confirmation component, wired to every
 * lifecycle action (STRANGER U12).
 *
 * Driven against the shipped app.js. Every check that reads an element uses
 * `strictEl`, the refusal UX-3 F-12 asked the next session with this file
 * open to add: `getEl` invents an element for any id, and an invented one has
 * an empty innerHTML — so `getEl('toast').innerHTML === ''` would pass for a
 * component that was never built AND for one whose id is misspelt. F-12 was
 * found exactly that way, and it was luck that found it.
 */
const assert = require('assert');
const { loadApp } = require('./harness.js');

let pass = 0, fail = 0;
const queue = [];
function test(name, fn) {
  queue.push(async () => {
    try { await fn(); pass += 1; }
    catch (e) { fail += 1; console.error('FAIL ', name); console.error('     ', e.message); }
  });
}

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
  const e = {};
  CARD_FIELDS.forEach((f) => { e[f] = ''; });
  e.machine_alias = 'Pump A'; e.measurement_location = 'Motor DE'; e.rpm = '1780';
  e.iso_group = '2'; e.iso_support = 'rigid'; e.bearing_model = '6206'; e.coupling = 'coupled';
  return Object.assign(e, overrides || {});
}
const SERIES = [
  { ts: '2026-07-01T10:00:00+00:00', v: 2.0, zone: 'A', axis: 'z' },
  { ts: '2026-08-01T10:00:00+00:00', v: 2.4, zone: 'B', axis: 'z' },
];
const seeded = () => ({
  [MACHINES_KEY]: JSON.stringify([card()]),
  [TREND_KEY]: JSON.stringify({ 'Pump A|Motor DE': SERIES }),
});
const app_ = (opts) => loadApp(Object.assign({
  fetchImpl: () => { throw new Error('the browser backend must not make a request'); },
}, opts || {}));

const shown = (a) => a.strictEl('toasts').innerHTML;
const message = (a) => {
  const m = shown(a).match(/<span class="t-msg">([\s\S]*?)<\/span>/);
  return m ? m[1] : null;
};

// ── the component ────────────────────────────────────────────────────────

test('the region is empty at rest, and it is a real element of the page', () => {
  const a = app_();
  assert.strictEqual(shown(a), '', 'something is in the toast region before anything happened');
  // strictEl already threw if index.html had no #toasts; say so out loud.
  assert.ok(a.strictEl('toasts')._known);
});

test('a confirmation appears, carrying its sentence', () => {
  const a = app_();
  a.sandbox.notify('Reading of 2026-09-07 deleted.', 'gone');
  assert.strictEqual(message(a), 'Reading of 2026-09-07 deleted.');
  assert.ok(shown(a).includes('t-gone'), 'the kind did not reach the markup');
});

test('the kind is never the only thing that says what happened', () => {
  // Colour alone fails a colour-blind analyst and a greyscale screenshot.
  const a = app_();
  a.sandbox.notify('“Pump A” added.', 'ok');
  assert.ok(message(a).length > 4, 'the sentence carries nothing');
  a.sandbox.notify('“Pump A” and its readings are gone from this browser.', 'gone');
  assert.ok(/gone/.test(message(a)), 'the destructive case reads the same as the additive one');
});

test('one at a time: a second confirmation replaces the first', () => {
  const a = app_();
  a.sandbox.notify('first', 'ok');
  a.sandbox.notify('second', 'ok');
  assert.strictEqual(message(a), 'second');
  assert.strictEqual((shown(a).match(/class="toast/g) || []).length, 1);
});

test('it leaves on its own, and the exit is a second phase', async () => {
  const a = app_();
  a.sandbox.notify('held', 'ok');
  // Stepped so the two phases are observed SEPARATELY. The hold is 5200ms and
  // the exit 200ms, so a single advance past 5400 runs both timers and the
  // exit phase is never seen — which is what the first draft of this test did,
  // and it reported "no exit animation phase" for a component that has one.
  await a.advance(5000);
  assert.strictEqual(message(a), 'held', 'it left before the analyst could read it');
  await a.advance(250);
  assert.ok(shown(a).includes('leaving'), 'no exit animation phase');
  assert.strictEqual(message(a), 'held', 'it vanished instead of animating out');
  await a.advance(300);
  assert.strictEqual(shown(a), '', 'it never actually went away');
});

test('the dismiss button takes it away', async () => {
  const a = app_();
  a.sandbox.notify('dismiss me', 'ok');
  a.strictEl('toasts').dispatchEvent({ type: 'click', target: { id: 'toast-dismiss' } });
  assert.ok(shown(a).includes('leaving'));
  await a.advance(300);
  assert.strictEqual(shown(a), '');
});

test('a click that is not the dismiss button changes nothing', () => {
  const a = app_();
  a.sandbox.notify('stay', 'ok');
  a.strictEl('toasts').dispatchEvent({ type: 'click', target: { id: 'something-else' } });
  assert.strictEqual(message(a), 'stay');
});

test('under reduced motion it goes at once, with no exit phase to wait for', async () => {
  const a = app_({ reduceMotion: true });
  a.sandbox.notify('quick', 'ok');
  await a.advance(5300);
  assert.strictEqual(shown(a), '',
    'the exit phase ran under reduced motion — the global CSS block kills that '
    + 'animation with !important, so the message would sit there for 200ms of nothing');
});

test('an empty message is not a confirmation', () => {
  const a = app_();
  a.sandbox.notify('', 'ok');
  assert.strictEqual(shown(a), '');
});

// ── every lifecycle action reports, exactly once ─────────────────────────

test('adding a machine says which one', async () => {
  const a = app_({ hash: '#/m/new' });
  await a.goTo('#/m/new');
  a.sandbox.document.getElementById('me-machine_alias').value = 'Boiler feed 2A';
  a.sandbox.document.getElementById('me-measurement_location').value = 'Motor DE';
  await a.sandbox.submitMachineEdit();
  assert.ok(/Boiler feed 2A/.test(message(a) || ''), message(a));
  assert.ok(/added/.test(message(a)), message(a));
});

test('renaming says so — U12: it used to say nothing at all', async () => {
  const a = app_({ hash: '#/', storage: seeded() });
  await a.goTo('#/m/' + encodeURIComponent('Pump A|Motor DE') + '/edit');
  a.sandbox.document.getElementById('me-machine_alias').value = 'Pump P-7';
  await a.sandbox.submitMachineEdit();
  assert.ok(/Pump P-7/.test(message(a) || ''), message(a));
  assert.ok(/moved with it/.test(message(a)), message(a));
});

test('deleting a saved machine from the intake goes to the confirmed path', async () => {
  // Session UX-5 (C11). This control used to splice the card out of the store
  // on the spot, with no confirmation, and it deleted only the CARD -- leaving
  // the machine's readings behind under the same key, i.e. UX-2's card-less
  // trend, reachable in one click. It now opens the machine's own page, where
  // the typed-alias panel names what will go and takes both.
  //
  // The claim this test protects moves with it: the toast is no longer the
  // report for this action, because the action no longer finishes here.
  const a = app_({ storage: seeded() });
  await a.flush();
  a.strictEl('saved').value = '0';
  a.strictEl('delete-saved').dispatchEvent({ type: 'click', target: a.strictEl('delete-saved') });
  await a.flush();
  assert.strictEqual(a.sandbox.location.hash, '#/m/' + encodeURIComponent('Pump A|Motor DE'),
    'it should land on that machine, not delete it where it stands');
  assert.strictEqual(message(a), null, 'nothing has happened yet, so nothing is reported');
});

test('nothing is deleted by that click, in either store', async () => {
  const a = app_({ storage: seeded() });
  await a.flush();
  a.strictEl('saved').value = '0';
  a.strictEl('delete-saved').dispatchEvent({ type: 'click', target: a.strictEl('delete-saved') });
  await a.flush();
  const cards = JSON.parse(a.sandbox.localStorage.getItem('vib.machines.v2') || '[]');
  assert.strictEqual(cards.length, 1, 'the card is still there');
  const trends = JSON.parse(a.sandbox.localStorage.getItem('vib.trend.v1') || '{}');
  assert.ok(trends['Pump A|Motor DE'], 'and so are its readings');
});

test('deleting a reading says which one', async () => {
  const a = app_({ hash: '#/', storage: seeded() });
  await a.goTo('#/m/' + encodeURIComponent('Pump A|Motor DE'));
  await a.sandbox.machinePageClick({ target: { dataset: { ryes: '2026-08-01T10:00:00+00:00' } } });
  await a.flush();
  assert.ok(/2026-08-01/.test(message(a) || ''), message(a));
  assert.ok(/deleted/.test(message(a)), message(a));
});

// ── the run's outcome shares the motion ──────────────────────────────────

function wire(state) { return { state, phase: null, failure_kind: null, retryable: null }; }

test('the outcome animates on a state change', async () => {
  const a = app_();
  a.sandbox.showRun('job1', wire('queued'));
  const first = a.strictEl('state').getAttribute('data-enter');
  a.sandbox.showRun('job1', wire('done'));
  const second = a.strictEl('state').getAttribute('data-enter');
  assert.notStrictEqual(second, first, 'the outcome did not restart the enter animation');
  assert.ok(second === 'a' || second === 'b', second);
});

test('and NOT on the poll that redraws the same state', async () => {
  // The whole reason the attribute alternates rather than naming the state:
  // a card that re-animates every 2.5 seconds is unusable, and one selector
  // per state would compute the same animation-name and never animate at all.
  const a = app_();
  a.sandbox.showRun('job1', wire('queued'));
  a.sandbox.showRun('job1', wire('done'));
  const settled = a.strictEl('state').getAttribute('data-enter');
  a.sandbox.showRun('job1', wire('done'));
  a.sandbox.showRun('job1', wire('done'));
  assert.strictEqual(a.strictEl('state').getAttribute('data-enter'), settled,
    'the outcome re-animates on every poll');
});

test('a retry is a new run and announces itself again', async () => {
  const a = app_();
  a.sandbox.showRun('j', wire('queued'));
  a.sandbox.showRun('j', wire('error'));
  const first = a.strictEl('state').getAttribute('data-enter');
  a.sandbox.showRun('j', wire('queued'));   // markRunStart's twin: a new run
  a.sandbox.showRun('j', wire('error'));
  assert.notStrictEqual(a.strictEl('state').getAttribute('data-enter'), first,
    'a second failure was swallowed by the first');
});

(async () => {
  for (const t of queue) await t();
  console.log(`\n${pass}/${pass + fail} passed`);
  process.exit(fail ? 1 : 0);
})();
