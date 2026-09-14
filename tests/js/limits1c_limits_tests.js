/**
 * Session LIMITS-1c item 4 — the machine's own severity limits, in the browser.
 *
 * Run in node against the REAL app.js (see harness.js), driven by pytest —
 * tests/test_limits1c_store.py — so `pytest` remains the one command.
 *
 * Three claims, and they are the ones only a store on the analyst's own device
 * can be asked:
 *
 *   * The card REMEMBERS the three numbers and PRE-FILLS them when the machine
 *     is picked again. A limit an analyst has to retype every run is a limit
 *     they will eventually mistype, and a mistyped severity boundary is a wrong
 *     zone on a real machine.
 *   * A machine saved BEFORE this session pre-fills BLANK, not zero. `''` means
 *     "not provided" everywhere in this store, and `0` would be a boundary
 *     nobody set — one `MachineThresholds` itself refuses.
 *   * A bad trio is refused VISIBLY, at the field, in the same order the server
 *     refuses it. `app.py::_thresholds_422` is the authority; this is the
 *     immediate half, and the two must not tell the analyst different stories
 *     about the same input.
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

const MACHINES_KEY = 'vib.machines.v2';
const noFetch = async () => { throw new Error('no request expected'); };
const app0 = (opts) => loadApp(Object.assign({ fetchImpl: noFetch }, opts || {}));

function stored(app, key) {
  const raw = app.sandbox.localStorage.getItem(key);
  return raw === null ? null : JSON.parse(raw);
}

/** Fill the form the way an analyst does, then submit-save the card. */
const field = (app, name) => app.getEl('field:' + name);

function fillMachine(app, extra) {
  const set = (name, value) => { field(app, name).value = value; };
  set('machine_alias', 'Pump A');
  set('measurement_location', 'Motor DE');
  set('rpm', '1780');
  set('iso_group', '2');
  set('iso_support', 'rigid');
  Object.keys(extra || {}).forEach((k) => set(k, extra[k]));
  const box = app.getEl('remember');
  if (box) box.checked = true;
  app.sandbox.rememberCurrentMachine();
}

// ── the card remembers them ──────────────────────────────────────────────

test('the saved card carries the three limits, at the tail, in order', () => {
  // `MEMORY_FIELDS` is a module-scope `const` inside the vm and is NOT a
  // property of the sandbox, so it cannot be read directly. Its order is
  // observable anyway: `rememberCurrentMachine` builds the card by iterating
  // it, so the stored object's key order IS MEMORY_FIELDS' order.
  //
  // The roster itself is diffed against db/models.py::CARD_FIELDS by
  // tests/test_db1_schema.py and against this directory's own third copy by
  // tests/test_intake2_machine_js.py. This is the behavioural half.
  const app = app0();
  fillMachine(app, { limit_ab: '5', limit_bc: '8', limit_cd: '12' });
  const keys = Object.keys(stored(app, MACHINES_KEY)[0]);
  assert.strictEqual(JSON.stringify(keys.slice(-3)),
    JSON.stringify(['limit_ab', 'limit_bc', 'limit_cd']),
    `card key tail is ${JSON.stringify(keys.slice(-3))}`);
});

test('a saved machine keeps the three numbers', () => {
  const app = app0();
  fillMachine(app, { limit_ab: '5', limit_bc: '8', limit_cd: '12' });
  const cards = stored(app, MACHINES_KEY);
  assert.strictEqual(cards.length, 1);
  assert.strictEqual(cards[0].limit_ab, '5');
  assert.strictEqual(cards[0].limit_bc, '8');
  assert.strictEqual(cards[0].limit_cd, '12');
});

test('picking that machine again pre-fills them', () => {
  const app = app0();
  fillMachine(app, { limit_ab: '5', limit_bc: '8', limit_cd: '12' });
  ['limit_ab', 'limit_bc', 'limit_cd'].forEach((n) => { field(app, n).value = ''; });
  app.sandbox.applyCard(stored(app, MACHINES_KEY)[0]);
  assert.strictEqual(field(app, 'limit_ab').value, '5');
  assert.strictEqual(field(app, 'limit_bc').value, '8');
  assert.strictEqual(field(app, 'limit_cd').value, '12');
});

test('a machine with no limits pre-fills BLANK, never zero', () => {
  const app = app0();
  fillMachine(app, {});
  const card = stored(app, MACHINES_KEY)[0];
  assert.strictEqual(card.limit_ab, '', 'a blank limit must store as ""');
  field(app, 'limit_ab').value = '99';
  app.sandbox.applyCard(card);
  assert.strictEqual(field(app, 'limit_ab').value, '',
    'applyCard left a stale value behind');
});

test('a card saved before this session (no limit keys at all) pre-fills blank', () => {
  const app = app0();
  // A v2 card as it existed before LIMITS-1c: the three keys simply absent.
  const legacy = { machine_alias: 'Old Pump', measurement_location: 'Motor DE', rpm: '1780' };
  app.sandbox.localStorage.setItem(MACHINES_KEY, JSON.stringify([legacy]));
  field(app, 'limit_ab').value = '7';
  app.sandbox.applyCard(app.sandbox.readMachines()[0]);
  ['limit_ab', 'limit_bc', 'limit_cd'].forEach((n) => {
    assert.strictEqual(field(app, n).value, '',
      `${n} was not cleared for a pre-LIMITS-1c card`);
  });
});

// ── the refusal, and its ORDER ───────────────────────────────────────────

const problem = (app, trio) => app.sandbox.limitProblem((name) => trio[name] || '');

test('no limits at all is not a problem', () => {
  const app = app0();
  assert.strictEqual(problem(app, {}), '');
});

test('a complete, increasing, positive trio is not a problem', () => {
  const app = app0();
  assert.strictEqual(problem(app, { limit_ab: '5', limit_bc: '8', limit_cd: '12' }), '');
});

test('a partial trio names the boxes that are still blank', () => {
  const app = app0();
  const msg = problem(app, { limit_ab: '5' });
  assert.ok(/Zone B\/C/.test(msg) && /Zone C\/D/.test(msg), msg);
  assert.ok(/are still blank/.test(msg), msg);
});

test('one missing box uses the singular', () => {
  const app = app0();
  const msg = problem(app, { limit_ab: '5', limit_bc: '8' });
  assert.ok(/Zone C\/D is still blank/.test(msg), msg);
});

test('a non-positive boundary is refused, and named', () => {
  const app = app0();
  const msg = problem(app, { limit_ab: '0', limit_bc: '8', limit_cd: '12' });
  assert.ok(/Zone A\/B/.test(msg) && /greater than 0/.test(msg), msg);
});

test('a non-increasing trio is refused', () => {
  const app = app0();
  const msg = problem(app, { limit_ab: '12', limit_bc: '8', limit_cd: '5' });
  assert.ok(/must increase/.test(msg), msg);
});

test('equal boundaries are refused too — strictly increasing', () => {
  const app = app0();
  const msg = problem(app, { limit_ab: '5', limit_bc: '5', limit_cd: '12' });
  assert.ok(/must increase/.test(msg), msg);
});

test('blankness is checked BEFORE positivity, as the server checks it', () => {
  // A partial trio whose one stated value is also invalid must complain about
  // the blanks, not the value -- otherwise the analyst fixes the number, and is
  // then told about the blanks anyway.
  const app = app0();
  const msg = problem(app, { limit_ab: '0' });
  assert.ok(/still blank/.test(msg), msg);
  assert.ok(!/greater than 0/.test(msg), msg);
});

test('limitValues returns null for anything limitProblem complains about', () => {
  const app = app0();
  const get = (trio) => (name) => trio[name] || '';
  assert.strictEqual(app.sandbox.limitValues(get({})), null);
  assert.strictEqual(app.sandbox.limitValues(get({ limit_ab: '5' })), null);
  assert.strictEqual(app.sandbox.limitValues(get({ limit_ab: '12', limit_bc: '8', limit_cd: '5' })), null);
  assert.strictEqual(
    JSON.stringify(Array.from(
      app.sandbox.limitValues(get({ limit_ab: '5', limit_bc: '8', limit_cd: '12' })))),
    JSON.stringify([5, 8, 12]));
});

// ── the error is actually SHOWN ──────────────────────────────────────────

test('the error box is hidden until there is something to say', () => {
  const app = app0();
  const box = app.getEl('lim-error');
  app.sandbox.showLimitError('');
  assert.strictEqual(box.hidden, true);
  assert.strictEqual(box.textContent, '');
});

test('a bad trio puts the sentence on screen', () => {
  const app = app0();
  field(app, 'limit_ab').value = '12';
  field(app, 'limit_bc').value = '8';
  field(app, 'limit_cd').value = '5';
  app.sandbox.onLimitChange();
  const box = app.getEl('lim-error');
  assert.strictEqual(box.hidden, false, 'the error box stayed hidden');
  assert.ok(/must increase/.test(box.textContent), box.textContent);
});

test('fixing the trio clears the sentence again', () => {
  const app = app0();
  field(app, 'limit_ab').value = '12';
  field(app, 'limit_bc').value = '8';
  field(app, 'limit_cd').value = '5';
  app.sandbox.onLimitChange();
  field(app, 'limit_ab').value = '5';
  field(app, 'limit_cd').value = '12';
  app.sandbox.onLimitChange();
  const box = app.getEl('lim-error');
  assert.strictEqual(box.hidden, true, 'the error box stayed up after the fix');
  assert.strictEqual(box.textContent, '');
});

(async () => {
  await Promise.all(pending);
  let failed = 0;
  for (const [status, name, message] of results) {
    if (status === 'FAIL') failed += 1;
    console.log(`${status}  ${name}${message ? '  — ' + message : ''}`);
  }
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})();
