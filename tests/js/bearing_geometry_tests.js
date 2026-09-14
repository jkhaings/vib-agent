/**
 * Session GEOM-1 — the bearing the catalogue does not hold, in a real JS
 * runtime.
 *
 * STRANGER B5 was the last blocking row still standing after UX-5: "Nearly
 * every real machine on my route will be 'Not listed', which silently turns
 * the headline feature off." UX-5 could not close it — the four files that had
 * to change were files it was not given — and wrote the STOP into its own
 * close-out. This is the browser half of closing it.
 *
 * Why node and not a TestClient: every claim below is about something the
 * server cannot be asked. Whether a revealed block is actually SHOWN, whether
 * a hidden input still holds a value it would post, whether a confirmation
 * appeared and whether its undo puts both halves back — a `files={...}` post
 * reproduces none of it, and the two defects UX-5 found in its own shadow tree
 * were both of exactly this kind.
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { loadApp } = require('./harness');

const STATIC = path.join(__dirname, '..', '..', 'src', 'vib_agent', 'webapp', 'static');

const results = [];
function test(name, fn) { results.push({ name, fn }); }

const noFetch = () => Promise.reject(new Error('the intake makes no request'));
const intake = (opts) => loadApp(Object.assign({ fetchImpl: noFetch, hash: '#/new' }, opts || {}));

const GEOMETRY = ['bearing_n_balls', 'bearing_ball_dia_mm', 'bearing_pitch_dia_mm',
  'bearing_contact_angle_deg'];
const IDS = { bearing_n_balls: 'brgn', bearing_ball_dia_mm: 'brgball',
  bearing_pitch_dia_mm: 'brgpitch', bearing_contact_angle_deg: 'brgangle' };

/** Type into a control the way a browser does: set it, then fire `change`. */
function enter(app, name, value) {
  const el = app.strictEl(IDS[name]);
  el.value = String(value);
  el.dispatchEvent({ type: 'change', target: el });
}

function chooseModel(app, value) {
  const el = app.strictEl('brg');
  el.value = value;
  el.dispatchEvent({ type: 'change', target: el });
}

/** A complete, ordinary bearing: the 6205's own numbers, so the geometry path
 *  and the catalogue path can be compared on one bearing. */
async function withGeometry(app) {
  enter(app, 'bearing_n_balls', '9');
  enter(app, 'bearing_ball_dia_mm', '7.94');
  enter(app, 'bearing_pitch_dia_mm', '39.04');
  await app.flush();
}

const toastText = (app) => app.getEl('toasts').innerHTML;

// ── the block exists, and it is where the analyst can reach it ────────────

test('the four controls are on the form, with their units shown', () => {
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const block = html.split('id="brg-geometry"')[1].split('</div>\n            </div>')[0];
  for (const name of GEOMETRY) {
    assert.ok(block.includes(`name="${name}"`), `${name} is not on the form`);
  }
  // "with units shown" is the brief's own words, and a millimetre entered as a
  // metre is the same class of error as B7's velocity unit.
  assert.strictEqual((block.match(/\(mm\)/g) || []).length, 2, 'the two diameters need mm');
  assert.ok(/\(degrees\)/.test(block), 'the contact angle needs its unit');
});

test('the block is under More options, with the select it belongs to', () => {
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const head = html.split('<details class="more" id="more-options">')[0];
  assert.ok(!head.includes('brg-geometry'), 'the block escaped the disclosure');
  assert.ok(html.indexOf('id="brg"') < html.indexOf('id="brg-geometry"'),
    'the geometry must read as the answer to the select above it, not before it');
});

test('the select is NOT given a sixth option for it', () => {
  // The select means "a bearing whose geometry we hold" and its options are
  // diffed against config/bearings.json (tests/test_ux2_bearings.py). A
  // sentinel option would have to be excused from that diff -- and a value
  // that is not a bearing has no business in a field called `bearing_model`.
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const select = html.split('id="brg"')[1].split('</select>')[0];
  const values = (select.match(/<option value="([^"]*)"/g) || []);
  assert.ok(!/geometry|enter|custom|other/i.test(select), select);
  assert.strictEqual(values.length, 6, 'the catalogue-minus-rigs list plus one empty');
});

// ── revealed and hidden, so both can never be posted ──────────────────────

test('it is shown when no bearing is chosen — which is the default', async () => {
  const app = intake();
  await app.flush();
  assert.strictEqual(app.strictEl('brg').value, '', 'the default is Not listed');
  assert.strictEqual(app.visible('brg-geometry'), true);
});

test('choosing a catalogue bearing hides it', async () => {
  const app = intake();
  await app.flush();
  chooseModel(app, '6206');
  await app.flush();
  assert.strictEqual(app.visible('brg-geometry'), false);
});

test('...and CLEARS it, because a hidden input still posts', async () => {
  const app = intake();
  await app.flush();
  await withGeometry(app);
  chooseModel(app, '6206');
  await app.flush();
  for (const name of GEOMETRY) {
    assert.strictEqual(app.strictEl(IDS[name]).value, '',
      `${name} was hidden but still holds a value the form would post`);
  }
});

test('going back to Not listed shows it again', async () => {
  const app = intake();
  await app.flush();
  chooseModel(app, '6206');
  await app.flush();
  chooseModel(app, '');
  await app.flush();
  assert.strictEqual(app.visible('brg-geometry'), true);
});

// ── the confirmation, and it is UX-4's component ──────────────────────────

test('completing the geometry is confirmed through the one toast component', async () => {
  const app = intake();
  await app.flush();
  assert.strictEqual(toastText(app), '', 'nothing is announced before anything is entered');
  await withGeometry(app);
  const html = toastText(app);
  assert.ok(html.includes('class="toast'), 'it is not the UX-4 component');
  assert.ok(/BPFO/.test(html), html);
});

test('a partial geometry announces nothing — that sentence is the server\'s', async () => {
  const app = intake();
  await app.flush();
  enter(app, 'bearing_n_balls', '9');
  enter(app, 'bearing_ball_dia_mm', '7.94');
  await app.flush();
  assert.strictEqual(toastText(app), '');
});

test('it is said once, not on every keystroke after', async () => {
  const app = intake();
  await app.flush();
  await withGeometry(app);
  app.getEl('toasts').innerHTML = '';
  enter(app, 'bearing_contact_angle_deg', '15');
  await app.flush();
  assert.strictEqual(toastText(app), '', 'the confirmation repeated');
});

test('breaking the geometry re-arms it, so a correction is confirmed too', async () => {
  const app = intake();
  await app.flush();
  await withGeometry(app);
  enter(app, 'bearing_pitch_dia_mm', '');
  await app.flush();
  app.getEl('toasts').innerHTML = '';
  enter(app, 'bearing_pitch_dia_mm', '39.04');
  await app.flush();
  assert.ok(/BPFO/.test(toastText(app)), 'the corrected geometry was accepted silently');
});

// ── the clear is SAID, and it can be taken back ───────────────────────────

test('clearing the geometry for a catalogue bearing is announced, not silent', async () => {
  const app = intake();
  await app.flush();
  await withGeometry(app);
  app.getEl('toasts').innerHTML = '';
  chooseModel(app, '6206');
  await app.flush();
  const html = toastText(app);
  assert.ok(html.includes('6206'), html);
  assert.ok(/cleared/i.test(html), 'it discarded what was entered without saying so');
  assert.ok(html.includes('toast-action'), 'no way to take it back');
});

test('the undo puts BOTH halves back — the numbers and the empty select', async () => {
  const app = intake();
  await app.flush();
  await withGeometry(app);
  chooseModel(app, '6206');
  await app.flush();
  // The handler is on the REGION, not on the button (`app.js`: one delegated
  // listener for a component that is re-rendered by assigning innerHTML), so
  // the click is dispatched the way notify_tests.js and result_tests.js do.
  const region = app.strictEl('toasts');
  region.dispatchEvent({ type: 'click', target: { id: 'toast-action' } });
  await app.flush();
  assert.strictEqual(app.strictEl('brg').value, '',
    'restoring the numbers under a chosen model re-creates the state this refuses');
  assert.strictEqual(app.strictEl(IDS.bearing_n_balls).value, '9');
  assert.strictEqual(app.strictEl(IDS.bearing_pitch_dia_mm).value, '39.04');
  assert.strictEqual(app.visible('brg-geometry'), true);
});

test('choosing a bearing with nothing entered says nothing', async () => {
  const app = intake();
  await app.flush();
  chooseModel(app, '6206');
  await app.flush();
  assert.strictEqual(toastText(app), '', 'it reported discarding nothing');
});

// ── what the page says the run will check ─────────────────────────────────

test('entered geometry turns the bearing unlock ON', async () => {
  const app = intake();
  await app.flush();
  app.sandbox.renderUnlocks();
  assert.ok(/Rolling-element bearing faults<\/b> — off/.test(app.getEl('unlocks').innerHTML),
    'it should be off with no bearing at all');
  await withGeometry(app);
  const html = app.getEl('unlocks').innerHTML;
  assert.ok(/Rolling-element bearing faults<\/b> — on/.test(html), html);
  // The caveat travels WITH the screen, exactly as it does for a catalogue
  // bearing: roster row 17, and the thing UX-2 refused to let the page drop.
  assert.ok(/radial/.test(html) && /axial/.test(html), html);
});

test('the ask names the geometry, or the page reads "off" over numbers it will use', () => {
  const js = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
  const row = js.split("key: 'bearing_geometry'")[1].split('},')[0];
  assert.ok(/enter its geometry/.test(row), row);
});

test('the carried-facts list names the bearing by its geometry', async () => {
  const app = intake();
  await app.flush();
  await withGeometry(app);
  const rows = app.sandbox.analysisFacts();
  const bearing = rows.filter((r) => r[0] === 'Bearing')[0];
  assert.ok(bearing, 'the Bearing row vanished for a bearing that IS stated');
  assert.strictEqual(bearing[1],
    'geometry as entered — 9 elements, element 7.94 mm, pitch 39.04 mm, contact 0°');
});

test('a catalogue bearing still reads as its own name', async () => {
  const app = intake();
  await app.flush();
  chooseModel(app, '6206');
  await app.flush();
  const rows = app.sandbox.analysisFacts();
  assert.strictEqual(rows.filter((r) => r[0] === 'Bearing')[0][1], '6206');
});

// ── the label, which the REPORT prints ────────────────────────────────────

test('the label names the provenance and the four numbers', () => {
  const app = intake();
  const L = app.sandbox.bearingGeometryLabel;
  assert.strictEqual(L(9, 7.94, 39.04, 0),
    'geometry as entered — 9 elements, element 7.94 mm, pitch 39.04 mm, contact 0°');
  // Never a designation: the report has no other line that can say where the
  // numbers came from, and a name there would claim a catalogue we do not hold.
  assert.ok(!/6205|6206|SKF/.test(L(9, 7.94, 39.04, 0)));
});

test('the label trims the way the server does, from strings a form gives it', () => {
  const app = intake();
  const L = app.sandbox.bearingGeometryLabel;
  assert.ok(L('9', '7.940', '46.0', '').includes('element 7.94 mm'));
  assert.ok(L('9', '7.940', '46.0', '').includes('pitch 46 mm'));
  assert.ok(L(1, 24, 141, 10).includes('1 element,'), 'a one-element bearing is not "1 elements"');
});

// ── the honesty note the missing schema column made necessary ─────────────

test('the form says the geometry is not remembered with the machine', () => {
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const block = html.split('id="brg-geometry"')[1].split('\n            </div>')[0];
  assert.ok(/not<\/b> saved with/.test(block), block);
});

test('and MEMORY_FIELDS really does not carry it, so the note is true', () => {
  const js = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
  const fields = js.split('const MEMORY_FIELDS = [')[1].split('];')[0];
  for (const name of GEOMETRY) {
    assert.ok(!fields.includes(`'${name}'`),
      `${name} is remembered, so the note on the form is now a lie — `
      + 'and tests/test_db1_schema.py needs the column');
  }
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
