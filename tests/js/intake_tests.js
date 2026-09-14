/**
 * Session UX-5 — the intake, from a saved machine to the review step.
 *
 * Run in node against the REAL app.js (see harness.js), because every claim
 * here is about something a TestClient cannot be asked: what a `<select>` does
 * with a value its options do not carry, what `localStorage` holds after a
 * migration, and what the form says it is carrying.
 *
 * `strictEl` throughout (UX-3 F-12, closed by UX-4): `getEl` invents an element
 * for any id it is handed, and an invented one answers with a default rather
 * than a fact -- so a mistyped id would pass this file silently.
 *
 * The stranger-test rows this file protects:
 *   B7  the velocity unit rendered BLANK on a saved machine, under a caption
 *       reading "Stated, never assumed -- the #1 severity error"
 *   C3  a phantom "Unnamed machine" in the dropdown, and the dropdown not
 *       selecting the machine the analyst had just arrived from
 *   C4  the carried geometry findable only inside a collapsed disclosure
 *   C8  "(direction assumed)" printed for a direction that was stated
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { loadApp, plain } = require('./harness');

const STATIC = path.join(__dirname, '..', '..', 'src', 'vib_agent', 'webapp', 'static');
const MACHINES_KEY = 'vib.machines.v2';
const TREND_KEY = 'vib.trend.v1';

const results = [];
function test(name, fn) { results.push({ name, fn }); }

const noFetch = () => Promise.reject(new Error('the intake makes no request'));

function intake(opts) {
  return loadApp(Object.assign({ fetchImpl: noFetch, hash: '#/new' }, opts || {}));
}

/** A card as `migrateEntry` produces one: every MEMORY_FIELD, every value a
 *  string. Overrides are applied on top. */
function card(over) {
  const base = {
    machine_alias: 'Pump A', rpm: '1780', iso_group: '2', iso_support: 'rigid',
    bearing_model: '6206', velocity_unit: 'mm_s', detection_type: 'rms', mode: 'spectrum',
    direction: '', sensor_sensitivity_mv_per_g: '', fmax_hz: '', spectral_lines: '',
    window_type: '', averages: '', integration: '', measurement_location: 'Motor DE',
    coupling: 'coupled', blades: '', drive_type: '', poles: '', line_freq_hz: '',
    rotor_bars: '', gear_teeth_driving: '', gear_teeth_driven: '',
    drive_pulley_mm: '', driven_pulley_mm: '', pulley_center_distance_mm: '',
  };
  return Object.assign(base, over || {});
}

const stored = (app, key) => JSON.parse(app.sandbox.localStorage.getItem(key) || 'null');

// ── B7 · the unit is stated, and it is never blank ────────────────────────

test('B7: the unit control is required, and its default is visible in the markup', () => {
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const block = html.split('id="unit"')[1].split('</select>')[0];
  assert.ok(/required/.test(html.split('id="unit"')[0].slice(-80) + block.slice(0, 40))
    || /<select name="velocity_unit" id="unit" required>/.test(html),
    'the select is not required');
  assert.ok(/<option value="mm_s" selected>/.test(block), 'no visible default');
});

test('B7: a card saved with NO velocity unit shows mm/s rather than nothing', async () => {
  // The stranger measured selectedIndex -1 here. Three saved-card shapes
  // produce it and this is the one an old card has.
  const app = intake({ storage: { [MACHINES_KEY]: JSON.stringify([card({ velocity_unit: '' })]) } });
  await app.flush();
  const sel = app.strictEl('saved');
  sel.value = '0';
  sel.dispatchEvent({ type: 'change', target: sel });
  await app.flush();
  assert.strictEqual(app.strictEl('unit').value, 'mm_s',
    'a blank unit must not reach a select that cannot hold it');
});

test('B7: an in/s card is untouched — the fallback is for a BLANK, not a preference', async () => {
  const app = intake({ storage: { [MACHINES_KEY]: JSON.stringify([card({ velocity_unit: 'in_s' })]) } });
  await app.flush();
  const sel = app.strictEl('saved');
  sel.value = '0';
  sel.dispatchEvent({ type: 'change', target: sel });
  await app.flush();
  assert.strictEqual(app.strictEl('unit').value, 'in_s');
});

test('B7: the machine form asks for the unit, so the cards it writes have one', async () => {
  const app = intake({ hash: '#/m/new' });
  await app.flush();
  assert.ok(app.strictEl('machine-edit-body').innerHTML.includes('me-velocity_unit'),
    'the machine form wrote every card the machines page creates and never asked');
  const read = app.sandbox.readEditForm();
  assert.ok('velocity_unit' in read, 'and it must read back, or it is not submitted');
});

// ── C3 · no phantom machines, and the one you came from is selected ───────

test('C3: a stale card with no alias is not offered as a machine', async () => {
  const app = intake({
    storage: {
      [MACHINES_KEY]: JSON.stringify([card(), card({ machine_alias: '', rpm: '1185' })]),
    },
  });
  await app.flush();
  const html = app.strictEl('saved').innerHTML;
  assert.ok(html.includes('Pump A'), 'the real machine is still there');
  assert.ok(!html.includes('Unnamed machine'), 'the phantom is still offered');
  assert.strictEqual(app.sandbox.readMachines().length, 1);
});

test('C3: the literal "Unnamed machine" is refused too, not just a blank', async () => {
  const app = intake({
    storage: { [MACHINES_KEY]: JSON.stringify([card({ machine_alias: 'Unnamed machine' })]) },
  });
  await app.flush();
  assert.strictEqual(app.sandbox.readMachines().length, 0);
  assert.strictEqual(app.visible('saved-row'), false, 'an empty list hides the row');
});

test('C3: the phantom is not copied forward by the v1 migration either', async () => {
  const app = intake({
    storage: { 'vib.machines.v1': JSON.stringify([card(), card({ machine_alias: '' })]) },
  });
  await app.flush();
  const written = stored(app, MACHINES_KEY);
  assert.strictEqual(written.length, 1, 'the migration wrote the phantom into the new key');
  assert.strictEqual(written[0].machine_alias, 'Pump A');
  assert.strictEqual(app.sandbox.localStorage.getItem('vib.machines.v1'), null);
});

test('C3: arriving from a machine SELECTS that machine in the dropdown', async () => {
  const app = intake({
    hash: '#/new?m=' + encodeURIComponent('Pump A|Motor DE'),
    storage: { [MACHINES_KEY]: JSON.stringify([card({ machine_alias: 'Other' }), card()]) },
  });
  await app.flush();
  const sel = app.strictEl('saved');
  assert.strictEqual(sel.value, '1', 'the dropdown still says "Choose a saved machine…"');
  assert.strictEqual(app.strictEl('field:machine_alias').value, 'Pump A');
});

test('C3: a machine known only from its trend selects nothing, and says nothing false', async () => {
  const app = intake({
    hash: '#/new?m=' + encodeURIComponent('Fan 9|Brg 1'),
    storage: { [TREND_KEY]: JSON.stringify({ 'Fan 9|Brg 1': [{ ts: '2026-08-01T10:00:00+00:00', v: 2, zone: 'A', axis: 'y' }] }) },
  });
  await app.flush();
  assert.strictEqual(app.strictEl('saved').value, '', 'there is no saved card to point at');
  assert.strictEqual(app.strictEl('field:machine_alias').value, 'Fan 9');
});

// ── C4 · what is carried is said, before the run ─────────────────────────

test('C4: the step that loads a machine names what came with it', async () => {
  const app = intake({
    storage: { [MACHINES_KEY]: JSON.stringify([card({ blades: '6' })]) },
  });
  await app.flush();
  assert.strictEqual(app.strictEl('carried').innerHTML, '',
    'the line is written only when a card is actually applied');
  const sel = app.strictEl('saved');
  sel.value = '0';
  sel.dispatchEvent({ type: 'change', target: sel });
  await app.flush();
  assert.strictEqual(app.visible('carried'), true);
  const html = app.strictEl('carried').innerHTML;
  for (const fact of ['Motor DE', 'Group 2', 'Rigid', 'Coupled', '6206', 'mm/s', 'blades/vanes 6']) {
    assert.ok(html.includes(fact), `${fact} is carried and not named: ${html}`);
  }
});

test('C4: the review step lists the carried facts, not just the file', async () => {
  const app = intake({
    storage: { [MACHINES_KEY]: JSON.stringify([card({ blades: '6' })]) },
  });
  await app.flush();
  const sel = app.strictEl('saved');
  sel.value = '0';
  sel.dispatchEvent({ type: 'change', target: sel });
  app.strictEl('field:rpm').value = '1780';
  // A file is what makes step 4 reachable (`reachableStep` uses submitJob's own
  // predicate); the harness's File stands in for one the analyst chose.
  app.strictEl('file').files = [new app.sandbox.File(['x'], 'before.csv')];
  await app.goTo('#/new/4');
  const html = app.strictEl('review-body').innerHTML;
  for (const fact of ['Motor DE', 'Rigid', 'Coupled', '6206', 'mm/s', 'Direction']) {
    assert.ok(html.includes(fact), `review omits ${fact}: ${html}`);
  }
});

test('C4: the list is read from the FORM, so it cannot advertise an unsent field', async () => {
  // It reports what THIS RUN will use, which on an untouched form is the
  // form's own defaults -- those are real answers the report prints, not
  // blanks. What it must never do is name a value the form is not holding.
  const app = intake({});
  await app.flush();
  const before = app.sandbox.analysisFacts();
  assert.ok(!before.some(([label]) => label === 'Measurement point'), JSON.stringify(before));
  assert.ok(!before.some(([label]) => label === 'Bearing'), 'no bearing is chosen yet');
  app.strictEl('field:measurement_location').value = 'Pump NDE';
  const after = app.sandbox.analysisFacts();
  assert.deepStrictEqual(plain(after[0]), ['Measurement point', 'Pump NDE'], JSON.stringify(after));
  // ...and every row it does name is a value the form actually holds.
  for (const [, value] of after) assert.ok(value, 'a blank row was listed');
});

// ── C8 · a direction can be stated, and then it is not "assumed" ─────────

test('C8: radial-horizontal is a real option, alongside an empty one that says so', () => {
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const block = html.split('id="direction"')[1].split('</select>')[0];
  assert.ok(block.includes('<option value="radial_h">'),
    'a single-file analyst still cannot STATE radial-horizontal');
  assert.ok(/<option value="" selected>Not given/.test(block),
    'the empty option must say what it is rather than name a direction');
});

test('C8: the empty option still declares the y axis, so the D-24 forecast is unmoved', async () => {
  const app = intake({});
  await app.flush();
  assert.strictEqual(app.sandbox.declaredAxis(''), 'y');
  assert.strictEqual(app.sandbox.declaredAxis('radial_h'), 'y',
    'the stated form of the same direction must declare the same axis');
});

test('C8: the channels line says the direction was not GIVEN, never that we assumed it', async () => {
  const app = intake({});
  const ch = (assumed) => ({
    channels: { speed_warning: null,
      channels: [{ label: 'Radial – horizontal', status: 'ok', has_velocity: true, assumed }] },
  });
  const assumedHtml = app.sandbox.channelsBlock(ch(true).channels);
  assert.ok(!/assumed<|direction assumed/.test(assumedHtml), assumedHtml);
  assert.ok(assumedHtml.includes('not given'), assumedHtml);
  const statedHtml = app.sandbox.channelsBlock(ch(false).channels);
  assert.ok(!/not given/.test(statedHtml), 'a stated direction gets no qualifier at all');
});

// ── B5 · the bearing list is a route catalogue, not a lab one ────────────

test('B5: neither select offers a benchmark-rig key', () => {
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const select = html.split('id="brg"')[1].split('</select>')[0];
  for (const rig of ['SKF_6205', 'MAFAULDA_ABVT', 'MFPT_NICE']) {
    assert.ok(!select.includes(rig), `${rig} is still in the upload form`);
  }
  assert.ok(select.includes('6205') && select.includes('NU216'), 'the route bearings are still there');
});

test('B5: config/bearings.json is NOT edited — the filter is at the UI', () => {
  const cfg = JSON.parse(fs.readFileSync(
    path.join(__dirname, '..', '..', 'config', 'bearings.json'), 'utf8'));
  for (const rig of ['SKF_6205', 'MAFAULDA_ABVT', 'MFPT_NICE']) {
    assert.ok(cfg.bearings[rig], `${rig} lost its geometry; the benchmarks read it`);
  }
});

// Session GEOM-1 closed the STOP this row was the honest interim for. The
// claim moved with it: UX-5 could only promise to ADD a bearing on request,
// so it said "most route bearings are not on this list yet" and gave an
// address. The form can now run the screen on geometry the analyst types, so
// what it must say is where that is -- and it still gives the address, because
// getting a bearing onto the list is still worth doing.
test('B5: the form says what to do about a bearing that is not on the list', () => {
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const block = html.split('id="brg"')[1].split('</div></div>')[0];
  assert.ok(/enter the four numbers below/i.test(block), block);
  assert.ok(block.includes('mailto:'), 'no way to ask for one');
});

// ── the step-1 delete control (C11) ─────────────────────────────────────

test('C11: the intake control says Delete and does not delete on the spot', async () => {
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  assert.ok(html.includes('id="delete-saved"'), 'the control moved without its id');
  assert.ok(!/>Forget</.test(html), 'the intake still says Forget');
  const app = intake({ storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  await app.flush();
  const btn = app.strictEl('delete-saved');
  app.strictEl('saved').value = '0';
  btn.dispatchEvent({ type: 'click', target: btn });
  await app.flush();
  assert.strictEqual(stored(app, MACHINES_KEY).length, 1, 'it deleted without a confirmation');
});

(async () => {
  let passed = 0;
  for (const { name, fn } of results) {
    try { await fn(); passed += 1; console.log(`ok    ${name}`); }
    catch (err) { console.log(`FAIL  ${name}\n      ${err.message}`); }
  }
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})();
