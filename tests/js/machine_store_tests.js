/**
 * Session UX-2: the machine and reading LIFECYCLE, run in node against the
 * REAL app.js (see harness.js). Driven by pytest — tests/test_ux2_store.py —
 * so `pytest` remains the one command.
 *
 * UX-1 built the view and deliberately wrote nothing. These are the writes,
 * and the claims under test are the ones only a store on the analyst's own
 * device can be asked:
 *
 *   * A machine is an alias AND a measurement point. Session F2's dedupe was
 *     by alias alone, so saving one machine at two bearings DESTROYED the
 *     first card while `browserMachines` kept both entries — a machine whose
 *     every fact then rendered "Not recorded". Pinned in both directions.
 *   * A rename MOVES the trend. The id is derived from the two answers being
 *     edited, so a rename that forgot the series would orphan every reading
 *     the analyst had saved, silently and permanently.
 *   * A collision REFUSES and changes nothing. Merging two series is a claim
 *     about which readings came from the same place; a wrong one fabricates a
 *     trend the report then assesses.
 *   * RULED D-24: same machine, same date, same axis is a REPLACEMENT — and a
 *     two-argument `saveTrendPoint` left from HIST-1 must get that default.
 *   * The same four verbs answer over both backends, with the same
 *     {ok, reason} shape, so a refusal reads the same on either.
 */

const assert = require('assert');
const { loadApp } = require('./harness');

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

function jsonResponse(body, { status = 200 } = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: () => null },
    json: async () => body,
  };
}

const MACHINES_KEY = 'vib.machines.v2';
const TREND_KEY = 'vib.trend.v1';

// The THIRD declaration of this list: `app.js::MEMORY_FIELDS` is the original,
// `db/models.py::CARD_FIELDS` mirrors it server-side, and this is the node
// suite's own copy. The first two are diffed tuple-for-tuple by
// tests/test_db1_schema.py; this one is diffed against app.js by
// tests/test_intake2_machine_js.py, added in Session INTAKE-2 precisely because
// this copy drifting is what a `deepStrictEqual` on it cannot tell you.
const CARD_FIELDS = ['machine_alias', 'rpm', 'iso_group', 'iso_support', 'bearing_model',
  'velocity_unit', 'detection_type', 'mode', 'direction',
  'sensor_sensitivity_mv_per_g', 'fmax_hz', 'spectral_lines', 'window_type',
  'averages', 'integration',
  'measurement_location', 'coupling', 'blades', 'drive_type', 'poles', 'line_freq_hz',
  'rotor_bars', 'gear_teeth_driving', 'gear_teeth_driven',
  'drive_pulley_mm', 'driven_pulley_mm', 'pulley_center_distance_mm',
  // Session INTAKE-2 — the machine kind (PARTC F-2) and the nameplate the ISO
  // group is derived from.
  'machine_type', 'rated_kw', 'driven_rpm',
  // Session LIMITS-1c — the machine's own severity limits.
  'limit_ab', 'limit_bc', 'limit_cd'];

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

function seeded(extra) {
  return Object.assign({
    [MACHINES_KEY]: JSON.stringify([card()]),
    [TREND_KEY]: JSON.stringify({ 'Pump A|Motor DE': SERIES }),
  }, extra || {});
}

const noFetch = () => { throw new Error('the browser backend must not make a request'); };
const browserApp = (opts) => loadApp(Object.assign({ fetchImpl: noFetch }, opts || {}));

/** Read a store back from INSIDE the vm realm — the only honest way to see
 *  what app.js actually wrote. (Also sidesteps SESSION_HIST1.md F-4: a value
 *  built in that realm is never reference-equal to one built in this one.) */
function stored(app, key) {
  const raw = app.sandbox.localStorage.getItem(key);
  return raw === null ? null : JSON.parse(raw);
}

const body = (app, id) => app.getEl(id).innerHTML;

// ── identity: a machine is an alias AND a measurement point ───────────────

test('two measurement points under one alias are two machines, and both survive a save', async () => {
  const app = browserApp({ storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  // The analyst fills the form for the SAME alias at a DIFFERENT bearing and
  // ticks Remember — Session F2's dedupe deleted the first card here.
  app.getEl('field:machine_alias').value = 'Pump A';
  app.getEl('field:measurement_location').value = 'Pump NDE';
  app.getEl('remember').checked = true;
  app.sandbox.rememberCurrentMachine();
  const cards = stored(app, MACHINES_KEY);
  assert.strictEqual(cards.length, 2, 'the first card must not be destroyed by the second');
  assert.deepStrictEqual(cards.map((c) => c.measurement_location).sort(), ['Motor DE', 'Pump NDE']);
});

test('saving the SAME machine twice still replaces its one card', async () => {
  const app = browserApp({ storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  app.getEl('field:machine_alias').value = 'Pump A';
  app.getEl('field:measurement_location').value = 'Motor DE';
  app.getEl('field:rpm').value = '3600';
  app.getEl('remember').checked = true;
  app.sandbox.rememberCurrentMachine();
  const cards = stored(app, MACHINES_KEY);
  assert.strictEqual(cards.length, 1);
  assert.strictEqual(cards[0].rpm, '3600');
});

test('the saved-machine dropdown names the point, not just the alias', async () => {
  const two = [card(), card({ measurement_location: 'Pump NDE' })];
  const app = browserApp({ storage: { [MACHINES_KEY]: JSON.stringify(two) } });
  const html = app.getEl('saved').innerHTML;
  assert.ok(html.includes('Pump A · Motor DE'), html);
  assert.ok(html.includes('Pump A · Pump NDE'), html);
});

// ── create ───────────────────────────────────────────────────────────────

test('create writes a card the upload form could have written', async () => {
  const app = browserApp();
  const res = app.sandbox.createMachineLocal({ machine_alias: 'Fan 3', measurement_location: 'NDE', rpm: '990' });
  assert.strictEqual(res.ok, true);
  assert.strictEqual(res.id, 'Fan 3|NDE');
  const cards = stored(app, MACHINES_KEY);
  assert.strictEqual(cards.length, 1);
  // Every whitelisted field is present, so the card round-trips through
  // migrateEntry unchanged and applyCard can put it back into the form.
  assert.deepStrictEqual(Object.keys(cards[0]).sort(), CARD_FIELDS.slice().sort());
  assert.strictEqual(cards[0].rpm, '990');
});

test('create refuses an unnamed machine', async () => {
  const app = browserApp();
  assert.strictEqual(app.sandbox.createMachineLocal({ machine_alias: '   ' }).reason, 'unnamed');
  assert.strictEqual(app.sandbox.createMachineLocal({ machine_alias: 'Unnamed machine' }).reason, 'unnamed');
  assert.strictEqual(stored(app, MACHINES_KEY), null, 'a refusal writes nothing');
});

test('create refuses a collision — with a card, and with a trend that has no card', async () => {
  const withCard = browserApp({ storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  assert.strictEqual(
    withCard.sandbox.createMachineLocal({ machine_alias: 'Pump A', measurement_location: 'Motor DE' }).reason,
    'exists',
  );
  assert.strictEqual(stored(withCard, MACHINES_KEY).length, 1);
  const trendOnly = browserApp({ storage: { [TREND_KEY]: JSON.stringify({ 'Pump A|Motor DE': SERIES }) } });
  assert.strictEqual(
    trendOnly.sandbox.createMachineLocal({ machine_alias: 'Pump A', measurement_location: 'Motor DE' }).reason,
    'exists',
    'a series with no card is still a machine — browserMachines lists it',
  );
});

// ── rename ───────────────────────────────────────────────────────────────

test('a rename moves the readings with the name', async () => {
  const app = browserApp({ storage: seeded() });
  const res = app.sandbox.updateMachineLocal('Pump A|Motor DE',
    { machine_alias: 'Pump A1', measurement_location: 'Motor DE' });
  assert.strictEqual(res.ok, true);
  assert.strictEqual(res.id, 'Pump A1|Motor DE');
  const trend = stored(app, TREND_KEY);
  assert.strictEqual(trend['Pump A|Motor DE'], undefined, 'the old series must not linger');
  assert.strictEqual(trend['Pump A1|Motor DE'].length, 2, 'both readings moved');
  assert.strictEqual(stored(app, MACHINES_KEY)[0].machine_alias, 'Pump A1');
});

test('a rename onto an existing machine refuses and moves nothing', async () => {
  const app = browserApp({
    storage: {
      [MACHINES_KEY]: JSON.stringify([card(), card({ machine_alias: 'Pump B' })]),
      [TREND_KEY]: JSON.stringify({ 'Pump A|Motor DE': SERIES, 'Pump B|Motor DE': [SERIES[0]] }),
    },
  });
  const res = app.sandbox.updateMachineLocal('Pump A|Motor DE',
    { machine_alias: 'Pump B', measurement_location: 'Motor DE' });
  assert.strictEqual(res.ok, false);
  assert.strictEqual(res.reason, 'exists');
  const trend = stored(app, TREND_KEY);
  assert.strictEqual(trend['Pump A|Motor DE'].length, 2, 'the refused move left the series alone');
  assert.strictEqual(trend['Pump B|Motor DE'].length, 1, 'and did not append to the other one');
  assert.strictEqual(stored(app, MACHINES_KEY).length, 2);
});

test('a rename into an unnamed machine refuses', async () => {
  const app = browserApp({ storage: seeded() });
  const res = app.sandbox.updateMachineLocal('Pump A|Motor DE', { machine_alias: '' });
  assert.strictEqual(res.reason, 'unnamed');
  assert.strictEqual(stored(app, TREND_KEY)['Pump A|Motor DE'].length, 2);
});

test('editing a field that is not part of the identity leaves the series where it is', async () => {
  const app = browserApp({ storage: seeded() });
  const res = app.sandbox.updateMachineLocal('Pump A|Motor DE', { rpm: '3550' });
  assert.strictEqual(res.id, 'Pump A|Motor DE');
  assert.strictEqual(stored(app, MACHINES_KEY)[0].rpm, '3550');
  assert.strictEqual(stored(app, MACHINES_KEY)[0].bearing_model, '6206', 'untouched fields survive');
  assert.strictEqual(stored(app, TREND_KEY)['Pump A|Motor DE'].length, 2);
});

// ── delete ───────────────────────────────────────────────────────────────

test('forgetting a machine takes its readings and says how many', async () => {
  const app = browserApp({ storage: seeded() });
  const res = app.sandbox.forgetMachineLocal('Pump A|Motor DE');
  assert.strictEqual(res.readings, 2);
  assert.deepStrictEqual(stored(app, MACHINES_KEY), []);
  assert.strictEqual(stored(app, TREND_KEY)['Pump A|Motor DE'], undefined);
});

test('deleting one reading leaves the rest, and refuses a stamp it does not hold', async () => {
  const app = browserApp({ storage: seeded() });
  const res = app.sandbox.deleteReadingLocal('Pump A|Motor DE', SERIES[0].ts);
  assert.strictEqual(res.ok, true);
  assert.strictEqual(res.left, 1);
  assert.strictEqual(stored(app, TREND_KEY)['Pump A|Motor DE'][0].ts, SERIES[1].ts);
  const missing = app.sandbox.deleteReadingLocal('Pump A|Motor DE', '1999-01-01T00:00:00+00:00');
  assert.strictEqual(missing.ok, false);
  assert.strictEqual(missing.reason, 'missing');
});

// ── RULED D-24 ───────────────────────────────────────────────────────────

const DAY = '2026-09-06';
const sameDay = (axis, v) => ({
  captured_at: `${DAY}T09:00:00+00:00`, severity_rms_mms: v, iso_zone: 'B', dominant_axis: axis,
});

test('D-24 AMENDED: a second reading on the same day on the same axis KEEPS BOTH', async () => {
  // The flagship flow is before-and-after one repair: same machine, same point,
  // same axis, same day. Under the original ruling this test asserted the
  // second reading REPLACED the first -- i.e. that the product's own worked
  // example destroyed its "before" unless the analyst noticed a radio two
  // screens earlier. Amended Sep 7; the assertion inverts with it.
  const app = browserApp({
    storage: { [TREND_KEY]: JSON.stringify({ 'Pump A|Motor DE': [{ ts: `${DAY}T06:00:00+00:00`, v: 1.0, zone: 'A', axis: 'y' }] }) },
  });
  const out = app.sandbox.saveTrendPoint('Pump A|Motor DE', sameDay('y', 2.5));
  assert.strictEqual(out.length, 2, 'a same-day pair is kept, not collapsed');
  assert.deepStrictEqual(out.map((p) => p.v), [1.0, 2.5], 'oldest first, both there');
});

test('D-24 AMENDED: replace is the EXPLICIT choice, and it still replaces', async () => {
  const app = browserApp({
    storage: { [TREND_KEY]: JSON.stringify({ 'Pump A|Motor DE': [{ ts: `${DAY}T06:00:00+00:00`, v: 1.0, zone: 'A', axis: 'y' }] }) },
  });
  const out = app.sandbox.saveTrendPoint('Pump A|Motor DE', sameDay('y', 2.5), 'replace');
  assert.strictEqual(out.length, 1, 'asked for, so done');
  assert.strictEqual(out[0].v, 2.5);
});

test('D-24 AMENDED: the two-argument call left from HIST-1 gets KEEP BOTH', async () => {
  // The same claim UX-2 pinned, pointed at the amended default: a call site
  // that passes no mode must get the RULING's default rather than its
  // opposite. Written as `mode === 'replace'` for exactly this reason, which
  // is the inverse of the expression UX-2 wrote for the inverse ruling.
  const app = browserApp({
    storage: { [TREND_KEY]: JSON.stringify({ 'K|L': [{ ts: `${DAY}T06:00:00+00:00`, v: 1.0, zone: 'A', axis: 'y' }] }) },
  });
  assert.strictEqual(app.sandbox.saveTrendPoint('K|L', sameDay('y', 2.5)).length, 2);
  // ...and an unknown mode is not a replace. Only the word replaces.
  // Its own stamp, because `sameDay` reuses one and re-saving that stamp is
  // the idempotence rule firing rather than the mode being read.
  assert.strictEqual(app.sandbox.saveTrendPoint('K|L', {
    captured_at: `${DAY}T11:00:00+00:00`, severity_rms_mms: 2.6, iso_zone: 'B', dominant_axis: 'y',
  }, 'something_else').length, 3);
});

test('D-24: keep-both keeps both', async () => {
  const app = browserApp({
    storage: { [TREND_KEY]: JSON.stringify({ 'K|L': [{ ts: `${DAY}T06:00:00+00:00`, v: 1.0, zone: 'A', axis: 'y' }] }) },
  });
  const out = app.sandbox.saveTrendPoint('K|L', sameDay('y', 2.5), 'keep_both');
  assert.strictEqual(out.length, 2);
});

test('D-24: a different axis on the same day is not a duplicate', async () => {
  const app = browserApp({
    storage: { [TREND_KEY]: JSON.stringify({ 'K|L': [{ ts: `${DAY}T06:00:00+00:00`, v: 1.0, zone: 'A', axis: 'y' }] }) },
  });
  const out = app.sandbox.saveTrendPoint('K|L', sameDay('z', 2.5));
  assert.strictEqual(out.length, 2, 'a three-channel route is not three duplicates');
});

test('D-24: a different DAY is not a duplicate, and the exact stamp is still idempotent', async () => {
  const app = browserApp({ storage: seeded() });
  const key = 'Pump A|Motor DE';
  const grown = app.sandbox.saveTrendPoint(key, sameDay('z', 3.0));
  assert.strictEqual(grown.length, 3, 'HIST-1 series on other days is untouched');
  const again = app.sandbox.saveTrendPoint(key, sameDay('z', 3.0));
  assert.strictEqual(again.length, 3, 'a second Save on the same job keeps one reading');
});

test('duplicateMatches forecasts by day, and by axis when one was declared', async () => {
  const app = browserApp({
    storage: {
      [TREND_KEY]: JSON.stringify({
        'K|L': [{ ts: `${DAY}T06:00:00+00:00`, v: 1.0, zone: 'A', axis: 'y' },
          { ts: `${DAY}T07:00:00+00:00`, v: 1.1, zone: 'A', axis: 'x' }],
      }),
    },
  });
  assert.strictEqual(app.sandbox.duplicateMatches('K|L', DAY, '').length, 2);
  assert.strictEqual(app.sandbox.duplicateMatches('K|L', DAY, 'y').length, 1);
  assert.strictEqual(app.sandbox.duplicateMatches('K|L', '2020-01-01', '').length, 0);
  assert.strictEqual(app.sandbox.duplicateMatches('', DAY, '').length, 0);
  // The declared-direction map is the server's, plus the fact that this form's
  // empty default option is labelled "Radial – horizontal".
  assert.strictEqual(app.sandbox.declaredAxis('axial'), 'x');
  assert.strictEqual(app.sandbox.declaredAxis('radial_v'), 'z');
  assert.strictEqual(app.sandbox.declaredAxis('radial_h'), 'y');
  assert.strictEqual(app.sandbox.declaredAxis(''), 'y', "the form's default IS radial-horizontal");
  assert.strictEqual(app.sandbox.declaredAxis('nonsense'), '', 'an unknown direction claims no axis');
  assert.strictEqual(app.sandbox.todayUTC(), new Date().toISOString().slice(0, 10));
});

// ── the views ────────────────────────────────────────────────────────────

test('#/m/new shows the edit view and hides the other two', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/new');
  assert.strictEqual(app.visible('view-machine-edit'), true);
  assert.strictEqual(app.getEl('me-machine_alias').value, '', 'a new machine starts empty');
  assert.strictEqual(app.visible('view-machines'), false);
  assert.strictEqual(app.visible('view-machine'), false);
  assert.strictEqual(app.visible('form-card'), false);
  assert.strictEqual(app.getEl('machine-edit-head').textContent, 'Add a machine');
  const html = body(app, 'machine-edit-body');
  assert.ok(html.includes('id="me-machine_alias"'));
  assert.ok(html.includes('id="me-measurement_location"'));
  assert.ok(html.includes('id="me-rpm"'));
});

test('#/m/<id>/edit prefills from the machine, and says what the readings will do', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE/edit');
  assert.strictEqual(app.getEl('machine-edit-head').textContent, 'Edit machine');
  const html = body(app, 'machine-edit-body');
  // The values are ASSIGNED, not rendered into attributes -- see editControl.
  assert.strictEqual(app.getEl('me-machine_alias').value, 'Pump A');
  assert.strictEqual(app.getEl('me-measurement_location').value, 'Motor DE');
  assert.strictEqual(app.getEl('me-rpm').value, '1780');
  assert.strictEqual(app.getEl('me-iso_group').value, '2');
  assert.strictEqual(app.getEl('me-bearing_model').value, '6206');
  assert.ok(html.includes('<b>2</b>'), 'the reading count is stated before the alias is changed');
  assert.ok(html.includes('moves them with it'));
});

test('saving an edit navigates to the id the adapter came back with', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE/edit');
  app.getEl('me-machine_alias').value = 'Pump A1';
  app.getEl('view-machine-edit').dispatchEvent({ type: 'click', target: { id: 'me-save' } });
  await app.flush();
  // The id MOVED, because it is derived from the answer that was just edited.
  // Everything NOT edited came from the form, which the render had filled --
  // so an unedited field is carried, not blanked.
  assert.strictEqual(stored(app, MACHINES_KEY)[0].bearing_model, '6206');
  assert.strictEqual(app.sandbox.location.hash, '#/m/Pump%20A1%7CMotor%20DE');
  assert.strictEqual(stored(app, TREND_KEY)['Pump A1|Motor DE'].length, 2);
});

test('a colliding save is refused on screen and changes nothing', async () => {
  const app = browserApp({
    storage: {
      [MACHINES_KEY]: JSON.stringify([card(), card({ machine_alias: 'Pump B' })]),
      [TREND_KEY]: JSON.stringify({ 'Pump A|Motor DE': SERIES }),
    },
  });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE/edit');
  app.getEl('me-machine_alias').value = 'Pump B';
  app.getEl('view-machine-edit').dispatchEvent({ type: 'click', target: { id: 'me-save' } });
  await app.flush();
  assert.ok(app.getEl('me-error').textContent.includes('never merged automatically'),
    app.getEl('me-error').textContent);
  assert.strictEqual(app.sandbox.location.hash, '#/m/Pump%20A%7CMotor%20DE/edit',
    'a refused save leaves the analyst on the form, with what they typed');
  assert.strictEqual(stored(app, TREND_KEY)['Pump A|Motor DE'].length, 2);
});

test('deleting a machine needs its alias typed, and refuses anything else', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE');
  const view = app.getEl('view-machine');
  view.dispatchEvent({ type: 'click', target: { id: 'm-delete' } });
  await app.flush();
  assert.ok(body(app, 'machine-body').includes('id="md-confirm"'));
  assert.ok(body(app, 'machine-body').includes('<b>2</b>'), 'the count of readings is named');
  app.getEl('md-confirm').value = 'DELETE';
  view.dispatchEvent({ type: 'click', target: { id: 'md-go' } });
  await app.flush();
  assert.ok(app.getEl('md-error').textContent.includes('not this machine'));
  assert.strictEqual(stored(app, MACHINES_KEY).length, 1, 'nothing was deleted');
  app.getEl('md-confirm').value = 'Pump A';
  view.dispatchEvent({ type: 'click', target: { id: 'md-go' } });
  await app.flush();
  assert.deepStrictEqual(stored(app, MACHINES_KEY), []);
  assert.strictEqual(stored(app, TREND_KEY)['Pump A|Motor DE'], undefined);
  assert.strictEqual(app.sandbox.location.hash, '#/');
});

test('deleting a reading recomputes the trend rather than patching the table', async () => {
  // The band is DERIVED from points[0] as the baseline, so deleting the oldest
  // reading must change it. A table patched in place beside a stale card is
  // two answers to one question — and it is the failure a test that only
  // counted rows would miss.
  const app = browserApp({
    storage: {
      [MACHINES_KEY]: JSON.stringify([card()]),
      [TREND_KEY]: JSON.stringify({
        'Pump A|Motor DE': [
          { ts: '2026-06-01T10:00:00+00:00', v: 1.0, zone: 'A', axis: 'z' },
          { ts: '2026-07-01T10:00:00+00:00', v: 2.0, zone: 'B', axis: 'z' },
          { ts: '2026-08-01T10:00:00+00:00', v: 2.2, zone: 'B', axis: 'z' },
        ],
      }),
    },
  });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE');
  const view = app.getEl('view-machine');
  const before = body(app, 'machine-body');
  assert.ok(before.includes('120% above the first reading'),
    before.slice(before.indexOf('Since'), 400));
  view.dispatchEvent({ type: 'click', target: { id: 'x', dataset: { rdel: '2026-06-01T10:00:00+00:00' } } });
  await app.flush();
  assert.ok(body(app, 'machine-body').includes('data-ryes="2026-06-01T10:00:00+00:00"'),
    'the row asks before it deletes');
  view.dispatchEvent({ type: 'click', target: { id: 'x', dataset: { ryes: '2026-06-01T10:00:00+00:00' } } });
  await app.flush();
  const after = body(app, 'machine-body');
  assert.strictEqual(stored(app, TREND_KEY)['Pump A|Motor DE'].length, 2);
  assert.ok(!after.includes('2026-06-01'), 'the row is gone from the table');
  assert.ok(after.includes('10% above the first reading'),
    'the band was recomputed off the new baseline');
  assert.ok(!after.includes('120% above'), 'and the stale band is gone');
});

test('cancelling a row delete deletes nothing', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE');
  const view = app.getEl('view-machine');
  view.dispatchEvent({ type: 'click', target: { id: 'x', dataset: { rdel: SERIES[0].ts } } });
  await app.flush();
  view.dispatchEvent({ type: 'click', target: { id: 'x', dataset: { rno: '1' } } });
  await app.flush();
  assert.strictEqual(stored(app, TREND_KEY)['Pump A|Motor DE'].length, 2);
  assert.ok(!body(app, 'machine-body').includes('data-ryes='), 'the confirm is gone');
});

test('the facts row no longer calls the ISO group a machine type', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/Pump%20A%7CMotor%20DE');
  const html = body(app, 'machine-body');
  assert.ok(html.includes('<dt>ISO group</dt>'), html.slice(0, 300));
  assert.ok(!html.includes('Machine type'), 'a group is a size class, not a type');
});

test('the machines panel carries no second Add-a-machine, in either state', async () => {
  // Session UX-4 MOVED this control rather than removing it. It used to be
  // rendered INTO #machines-body twice — once under the card list and once
  // inside the empty state — so the panel had a header with one action and a
  // footer with another. It now lives once, in the static `.mhead` above that
  // container (the operator's "one row, title left, both actions right").
  //
  // The original claim — home offers a way to add a machine, with and without
  // machines in it — is unchanged and still pinned, at
  // `tests/test_ux4_shell.py::TestTheMachinesHeader`. It moved because `.mhead`
  // has no id and `tests/js/harness.js` backs elements by id, so node cannot
  // read it. What node can still prove is the half that would regress
  // silently: that neither branch grows its own copy back.
  const empty = browserApp();
  await empty.flush();
  assert.ok(body(empty, 'machines-body').length > 0, 'the empty state renders nothing at all');
  assert.ok(!body(empty, 'machines-body').includes('href="/#/m/new"'),
    'the empty state has its own Add a machine again — that is the duplicate UX-4 removed');
  const full = browserApp({ storage: seeded() });
  await full.goTo('#/');
  assert.ok(body(full, 'machines-body').includes('class="mcards"'), 'the card list is gone');
  assert.ok(!body(full, 'machines-body').includes('href="/#/m/new"'),
    'the card list has its own Add a machine again — that is the duplicate UX-4 removed');
});

// ── the same four verbs, over the db backend ─────────────────────────────

const DB_MACHINE = {
  id: 'abc123', alias: 'Pump A', location: 'Motor DE', card: card(), readings: SERIES,
};

function dbApp(handler) {
  const calls = [];
  const app = loadApp({
    accounts: true,
    fetchImpl: (url, init) => {
      calls.push({ url, method: (init && init.method) || 'GET', init });
      return handler(url, init, calls);
    },
  });
  app.calls = calls;
  return app;
}

test('create, edit, delete and reading-delete each issue their own request', async () => {
  const app = dbApp((url, init) => {
    if ((init && init.method) === 'POST') return jsonResponse({ id: 'newid' });
    if ((init && init.method) === 'PUT') return jsonResponse({ id: 'abc123' });
    if ((init && init.method) === 'DELETE') return jsonResponse({ ok: true });
    if (url === '/api/machines/abc123') return jsonResponse(DB_MACHINE);
    return jsonResponse({ machines: [DB_MACHINE] });
  });
  await app.flush();
  app.getEl('field:csrf').value = 'tok';

  const made = await app.sandbox.createMachine({ machine_alias: 'Fan 3' });
  assert.strictEqual(made.ok, true);
  assert.strictEqual(made.id, 'newid');
  const post = app.calls[app.calls.length - 1];
  assert.strictEqual(post.url, '/api/machines');
  assert.strictEqual(post.method, 'POST');
  // The token is rendered INTO the page by the server; the cookie is HttpOnly
  // and a header cannot be set cross-origin without a preflight we grant nobody.
  assert.strictEqual(post.init.headers['X-CSRF-Token'], 'tok');
  assert.deepStrictEqual(JSON.parse(post.init.body), { card: { machine_alias: 'Fan 3' } });

  await app.sandbox.updateMachine('abc123', { rpm: '10' });
  assert.strictEqual(app.calls[app.calls.length - 1].url, '/api/machines/abc123');
  assert.strictEqual(app.calls[app.calls.length - 1].method, 'PUT');

  await app.sandbox.removeMachine('abc123', 'Pump A');
  const del = app.calls[app.calls.length - 1];
  assert.strictEqual(del.method, 'DELETE');
  assert.deepStrictEqual(JSON.parse(del.init.body), { confirm: 'Pump A' });

  await app.sandbox.removeReading('abc123', '2026-08-01T10:00:00+00:00');
  assert.strictEqual(app.calls[app.calls.length - 1].url,
    '/api/machines/abc123/readings/2026-08-01T10%3A00%3A00%2B00%3A00');
  assert.strictEqual(app.calls[app.calls.length - 1].method, 'DELETE');
});

test('a db collision reads exactly like a browser collision', async () => {
  const app = dbApp((url, init) => ((init && init.method) === 'PUT'
    ? jsonResponse({ reason: 'exists', detail: 'x' }, { status: 409 })
    : jsonResponse({ machines: [] })));
  await app.flush();
  const res = await app.sandbox.updateMachine('abc123', {});
  assert.strictEqual(res.ok, false);
  assert.strictEqual(res.reason, 'exists');
  assert.ok(app.sandbox.editRefusal(res).includes('never merged automatically'),
    'one sentence, whichever store refused');
});

test('a write that never reaches us says so, and never throws', async () => {
  const app = dbApp(() => { throw new Error('down'); });
  await app.flush();
  const res = await app.sandbox.removeMachine('abc123', 'Pump A');
  assert.strictEqual(res.ok, false);
  assert.strictEqual(res.reason, 'offline');
  assert.ok(app.sandbox.editRefusal(res).includes('nothing has been changed'));
});

test('the browser backend makes no request for any of the four', async () => {
  // `noFetch` throws, so this passes only if not one of them reaches fetch.
  const app = browserApp({ storage: seeded() });
  await app.sandbox.createMachine({ machine_alias: 'Fan 3' });
  await app.sandbox.updateMachine('Fan 3|', { rpm: '10' });
  await app.sandbox.removeReading('Pump A|Motor DE', SERIES[0].ts);
  await app.sandbox.removeMachine('Pump A|Motor DE', 'Pump A');
  const left = stored(app, MACHINES_KEY);
  assert.deepStrictEqual(left.map((c) => c.machine_alias), ['Fan 3'],
    'the created machine stayed; the forgotten one went');
  assert.strictEqual(stored(app, TREND_KEY)['Pump A|Motor DE'], undefined);
});

Promise.all(pending).then(() => {
  results.forEach(([status, name, err]) => {
    console.log(`${status}  ${name}${err ? `\n      ${err}` : ''}`);
  });
  const passed = results.filter(([s]) => s === 'PASS').length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
});
