/**
 * Session RENAME+PRICE (A3) — the alias cap, in the browser.
 *
 * Run in node against the REAL app.js (see harness.js), driven by pytest —
 * tests/test_rename1_alias.py — so `pytest` remains the one command.
 *
 * `app.py::_ALIAS_MAX_CHARS` is the AUTHORITY and this is the immediate half,
 * exactly as `#lim-error` is for the severity trio. The claim only a browser
 * can be asked is the one that matters here:
 *
 *   **This mirror is reachable, and `maxlength` does not make it dead code.**
 *   `maxlength` constrains what a person types or pastes. It does not constrain
 *   a value assigned by script — and this form is populated by script every
 *   time a saved machine card is restored into it. A card written before the
 *   cap existed, or imported from another device, puts a longer alias in the
 *   box; without this the first the analyst hears of it is a 422 after they
 *   have already chosen a file.
 *
 * That is asserted by ASSIGNING an over-long value the way the restore path
 * does, not by typing one — a test that typed it would be testing `maxlength`.
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

const noFetch = async () => { throw new Error('no request expected'); };
const app0 = (opts) => loadApp(Object.assign({ fetchImpl: noFetch }, opts || {}));
const field = (app, name) => app.getEl('field:' + name);

/** Assign, as the card-restore path does — never type. */
function setAlias(app, value) {
  field(app, 'machine_alias').value = value;
  app.sandbox.onAliasChange();
  return app.getEl('alias-error');
}

// ── the boundary ─────────────────────────────────────────────────────────

test('an ordinary alias shows nothing', () => {
  const box = setAlias(app0(), 'Pump A');
  assert.strictEqual(box.hidden, true, 'the error box was up for a valid alias');
  assert.strictEqual(box.textContent, '');
});

test('exactly the cap is accepted', () => {
  const box = setAlias(app0(), 'x'.repeat(120));
  assert.strictEqual(box.hidden, true, '120 characters was refused');
});

test('one over the cap is refused, visibly', () => {
  const box = setAlias(app0(), 'x'.repeat(121));
  assert.strictEqual(box.hidden, false, 'the error box stayed hidden at 121');
  assert.ok(box.textContent.length > 0, 'the box is shown but says nothing');
});

test('an empty alias shows nothing — the field is optional', () => {
  const box = setAlias(app0(), '');
  assert.strictEqual(box.hidden, true);
});

// ── what it says ─────────────────────────────────────────────────────────

test('it names the actual length and the limit, and says what to do', () => {
  const box = setAlias(app0(), 'x'.repeat(200));
  assert.ok(box.textContent.includes('200'), `no actual length: ${box.textContent}`);
  assert.ok(box.textContent.includes('120'), `no limit: ${box.textContent}`);
  assert.ok(/shorten it/i.test(box.textContent), `no instruction: ${box.textContent}`);
});

test('it leaks no plumbing', () => {
  const box = setAlias(app0(), 'x'.repeat(200));
  ['machine_alias', 'VARCHAR', 'String(', 'http'].forEach((leak) => {
    assert.ok(!box.textContent.includes(leak),
      `the inline message says ${leak}: ${box.textContent}`);
  });
});

// ── it clears ────────────────────────────────────────────────────────────

test('shortening it takes the message away again', () => {
  // `onLimitChange`'s rule, and the reason the mirror listens on `input` as
  // well as `change`: an error should stop reading as one the moment it is
  // fixed, not when the field blurs.
  const app = app0();
  let box = setAlias(app, 'x'.repeat(300));
  assert.strictEqual(box.hidden, false, 'never showed in the first place');
  box = setAlias(app, 'Pump A');
  assert.strictEqual(box.hidden, true, 'the message stayed up after the fix');
  assert.strictEqual(box.textContent, '');
});

// ── the reachability claim itself ────────────────────────────────────────

test('a restored card longer than the cap trips it — the reason this exists', () => {
  // The whole justification, exercised end to end: the value arrives by
  // ASSIGNMENT, which is what `maxlength` does not cover. If this ever stops
  // being reachable the mirror is dead code and should be deleted, not kept.
  const app = app0();
  const el = field(app, 'machine_alias');
  el.value = 'A machine whose alias somebody pasted a whole plant description into, '
    + 'saved before there was any cap at all, and which is now far too long to store';
  assert.ok(el.value.length > 120, 'the fixture is not actually over the cap');
  app.sandbox.onAliasChange();
  assert.strictEqual(app.getEl('alias-error').hidden, false,
    'an over-long alias restored from a card said nothing');
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
