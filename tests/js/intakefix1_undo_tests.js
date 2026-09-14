/**
 * Session INTAKEFIX-1 — one Undo puts back every point the run saved
 * (INTAKE-2's F-3), and a point the gate refused to diagnose is never saved
 * at all.
 *
 * Run in node against the REAL app.js (see harness.js), driven by pytest —
 * tests/test_intakefix1_undo.py — so `pytest` remains the one command.
 *
 * INTAKE-2 filed four points of a route under four trend keys and then wrote
 * its own F-3: "UX-5's undo restores one reading; a run that saved four would
 * need four, or one that restores all of them." An analyst who let a four-point
 * route autosave and then realised they had the wrong machine selected had one
 * control that removed a quarter of it. That is worse than no control: the
 * confirmation said the run was undone and three series still held a reading.
 *
 * The claims here cannot be made from python. `undoAutoSave` reaches
 * `localStorage`, an IndexedDB report store and the toast component, and
 * `RETAINED` is a top-level `const` — a lexical binding, not a property of the
 * vm sandbox's global — so the browser is the only place the whole chain is
 * observable.
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
const TREND_KEY = 'vib.trend.v1';
const ALIAS = 'Compressor train 01';
const LABELS = ['Motor DE', 'Motor NDE', 'Compressor DE', 'Compressor NDE'];

const noFetch = async () => { throw new Error('no network in this suite'); };
const app = (opts) => loadApp(Object.assign({ fetchImpl: noFetch }, opts || {}));

/** A saved machine card for location 1, so autosave is armed: a machine that
 *  was only TYPED is deliberately not saved to (HIST-1's rule). */
function seededCard() {
  return {
    [MACHINES_KEY]: JSON.stringify([{
      machine_alias: ALIAS, measurement_location: LABELS[0], rpm: '1800',
      iso_group: '2', iso_support: 'rigid', machine_type: 'compressor',
    }]),
  };
}

function point(ts, value, zone, axis) {
  return {
    severity_rms_mms: value, iso_zone: zone || 'B',
    dominant_axis: axis || 'y', captured_at: ts,
  };
}

/** The wire payload for a route: location 1 plus `labels.length - 1` others. */
function wire(labels, opts) {
  const o = opts || {};
  return {
    state: 'done',
    trend_point: point('2026-03-01T08:00:00Z', 2.0),
    result_summary: { no_findings: false, faults: [{ label: 'BPFO', confidence: 'high' }] },
    locations: labels.slice(1).map((label, i) => Object.assign(
      { label, status: 'ok', trend_point: point(`2026-03-01T08:0${i + 1}:00Z`, 3.0 + i) },
      (o.overrides && o.overrides[label]) || {},
    )),
  };
}

/** The alias the form names, which is what `saveOtherLocationReadings` reads.
 *
 *  `RETAINED` is a top-level `const` in app.js -- a lexical binding, not a
 *  property of the vm sandbox's global (measured: `sandbox.RETAINED` is
 *  `undefined` while every `function` declaration beside it is reachable). So
 *  the SAVE half is driven through `saveOtherLocationReadings`, which reads the
 *  form, exactly as `tests/js/location_trend_tests.js` drives it; and the TOAST
 *  half is driven through `showRun` with `autoSaveReading` stubbed to report a
 *  known set of pairs. Those are two separate claims and they are better tested
 *  separately: one is "which points get filed", the other is "does the control
 *  remove everything the run reported". The one claim neither can make -- that
 *  `undoAutoSave` updates the ledger count only for its own point -- is
 *  asserted on the source in tests/test_intakefix1_undo.py, the idiom
 *  tests/test_intake2_trend.py established. */
function named(handle) {
  handle.getEl('field:machine_alias').value = ALIAS;
  handle.getEl('field:measurement_location').value = LABELS[0];
}

function store(handle) {
  const raw = handle.sandbox.localStorage.getItem(TREND_KEY);
  return raw === null ? {} : JSON.parse(raw);
}

function counts(handle) {
  const s = store(handle);
  return LABELS.map((label) => (s[`${ALIAS}|${label}`] || []).length);
}

/** File location 1's reading the way `autoSaveReading` does, so the store looks
 *  like a finished run before the undo is exercised. */
function fileFirst(handle, ts) {
  const key = handle.sandbox.trendKey(ALIAS, LABELS[0]);
  handle.sandbox.saveTrendPoint(key, point(ts, 2.0), 'keep_both');
  return { key, ts };
}

// ── saving: every point, under its own key ───────────────────────────────

test('a four-point route files three other readings and reports all three', async () => {
  const handle = app({ storage: seededCard() });
  named(handle);
  const filed = [];
  const count = handle.sandbox.saveOtherLocationReadings(wire(LABELS), 'keep_both', filed);
  assert.strictEqual(count, 3);
  assert.strictEqual(filed.length, 3, 'the pairs an Undo needs were not reported back');
  assert.deepStrictEqual(
    filed.map((o) => o.key).sort(),
    LABELS.slice(1).map((l) => `${ALIAS}|${l}`).sort(),
  );
});

test('every reported pair is a reading that is actually in the store', async () => {
  // An Undo can only remove what it was told about, so a pair that is wrong
  // here is a reading that outlives the undo silently.
  const handle = app({ storage: seededCard() });
  named(handle);
  const filed = [];
  handle.sandbox.saveOtherLocationReadings(wire(LABELS), 'keep_both', filed);
  filed.forEach((o) => {
    const series = store(handle)[o.key];
    assert.ok(series && series.some((p) => p.ts === o.ts), `${o.key} has no ${o.ts}`);
  });
});

test('the out-parameter is optional and the count is unchanged without it', async () => {
  // `tests/js/location_trend_tests.js` asserts this return value in four
  // places. The undo is additive precisely so those pins keep asserting it.
  const handle = app({ storage: seededCard() });
  named(handle);
  assert.strictEqual(handle.sandbox.saveOtherLocationReadings(wire(LABELS), 'keep_both'), 3);
});

test('a duplicate point is filed once and reported once', async () => {
  // A point whose stamp is already held is skipped. An Undo that removed it
  // would delete a reading THIS run did not save.
  const handle = app({ storage: seededCard() });
  named(handle);
  handle.sandbox.saveOtherLocationReadings(wire(LABELS), 'keep_both');
  const filed = [];
  const again = handle.sandbox.saveOtherLocationReadings(wire(LABELS), 'keep_both', filed);
  assert.strictEqual(again, 0);
  assert.deepStrictEqual(filed, []);
  assert.deepStrictEqual(counts(handle), [0, 1, 1, 1]);
});

// ── a point that was not diagnosed is never filed ────────────────────────

test('a gate-failed point is not saved even if a trend_point arrives with it', async () => {
  // The browser half of contract section 5 rule 1. The server no longer sends
  // numbers for a non-ok point (fixed this session in app.py), and this refuses
  // them anyway: a trend is what an analyst comes back for, and an undiagnosed
  // point in it is a line on a chart nobody can account for.
  const handle = app({ storage: seededCard() });
  named(handle);
  const filed = [];
  handle.sandbox.saveOtherLocationReadings(wire(LABELS, {
    overrides: {
      'Compressor NDE': {
        status: 'gate_fail',
        gate_reasons: ['sample rate not stated'],
        trend_point: point('2026-03-01T08:03:00Z', 20.0, 'D'),
      },
    },
  }), 'keep_both', filed);
  assert.deepStrictEqual(counts(handle), [0, 1, 1, 0],
    'a point the data-quality gate refused to diagnose joined the trend');
  assert.strictEqual(filed.length, 2);
  assert.ok(!filed.some((o) => /Compressor NDE/.test(o.key)));
});

test('an unreadable point is not saved either', async () => {
  const handle = app({ storage: seededCard() });
  named(handle);
  handle.sandbox.saveOtherLocationReadings(wire(LABELS, {
    overrides: {
      'Motor NDE': { status: 'unreadable', message: 'could not be read',
                     trend_point: point('2026-03-01T08:01:00Z', 5.0) },
    },
  }), 'keep_both');
  assert.deepStrictEqual(counts(handle), [0, 0, 1, 1]);
});

test('an ok point with no status field is still saved', async () => {
  // Non-vacuity for the two tests above: a gate reading "absent means not ok"
  // would pass them and silently stop saving every point on an older server.
  const handle = app({ storage: seededCard() });
  named(handle);
  const payload = wire(LABELS);
  payload.locations.forEach((entry) => { delete entry.status; });
  handle.sandbox.saveOtherLocationReadings(payload, 'keep_both');
  assert.deepStrictEqual(counts(handle), [0, 1, 1, 1]);
});

// ── undoing: all of it, or the confirmation is a lie ─────────────────────

test('undoAutoSave removes one point and leaves the others', async () => {
  const handle = app({ storage: seededCard() });
  named(handle);
  const filed = [];
  handle.sandbox.saveOtherLocationReadings(wire(LABELS), 'keep_both', filed);
  const one = filed.find((o) => /Motor NDE/.test(o.key));
  handle.sandbox.undoAutoSave(one.key, one.ts);
  assert.deepStrictEqual(counts(handle), [0, 0, 1, 1]);
});

/** A run that has just autosaved: location 1 plus the three others, in the
 *  store, with the pairs `autoSaveReading` would have reported. */
function finishedRun(handle) {
  named(handle);
  const first = fileFirst(handle, '2026-03-01T08:00:00Z');
  const others = [];
  handle.sandbox.saveOtherLocationReadings(wire(LABELS), 'keep_both', others);
  return { key: first.key, ts: first.ts, replaced: false, others };
}

test('one undo removes every reading the run filed', async () => {
  const handle = app({ storage: seededCard() });
  const saved = finishedRun(handle);
  assert.deepStrictEqual(counts(handle), [1, 1, 1, 1]);
  handle.sandbox.undoAutoSave(saved.key, saved.ts);
  saved.others.forEach((o) => handle.sandbox.undoAutoSave(o.key, o.ts));
  assert.deepStrictEqual(counts(handle), [0, 0, 0, 0], 'a reading outlived the undo');
});

test('the undo BUTTON on the confirmation does it, not just the function', async () => {
  // The function being right is not the feature. UX-5's control is a toast
  // action, and the wiring between them is what an analyst actually touches.
  //
  // `autoSaveReading` is stubbed to report the run `finishedRun` staged, because
  // arming it for real needs `RETAINED`, which the sandbox does not expose. What
  // is under test here is the WIRING: given a run that reported four pairs, does
  // one click remove four readings. Which points get filed is the section above.
  const handle = app({ storage: seededCard() });
  const saved = finishedRun(handle);
  handle.sandbox.autoSaveReading = () => saved;
  handle.sandbox.showRun('job1', { state: 'queued' });
  handle.sandbox.showRun('job1', wire(LABELS));
  const toasts = handle.getEl('toasts');
  assert.ok(/id="toast-action"/.test(toasts.innerHTML), 'the confirmation offers no Undo');
  toasts.dispatchEvent({ type: 'click', target: { id: 'toast-action' } });
  assert.deepStrictEqual(counts(handle), [0, 0, 0, 0],
    'the Undo button left readings behind — INTAKE-2 F-3 unfixed');
});

test('the confirmation says how many points it saved', async () => {
  // An Undo whose scope the analyst cannot see is worse than none: "Saved to
  // Compressor train 01's trend" does not say that four series moved.
  const handle = app({ storage: seededCard() });
  const saved = finishedRun(handle);
  handle.sandbox.autoSaveReading = () => saved;
  handle.sandbox.showRun('job1', { state: 'queued' });
  handle.sandbox.showRun('job1', wire(LABELS));
  const shown = handle.getEl('toasts').innerHTML;
  assert.ok(/4 readings/.test(shown), `the count is not in the sentence: ${shown}`);
  assert.ok(/measurement location/.test(shown), 'it does not say what the four are');
});

test('the undone sentence accounts for all of them too', async () => {
  const handle = app({ storage: seededCard() });
  const saved = finishedRun(handle);
  handle.sandbox.autoSaveReading = () => saved;
  handle.sandbox.showRun('job1', { state: 'queued' });
  handle.sandbox.showRun('job1', wire(LABELS));
  const toasts = handle.getEl('toasts');
  toasts.dispatchEvent({ type: 'click', target: { id: 'toast-action' } });
  assert.ok(/None of the 4 readings/.test(toasts.innerHTML), toasts.innerHTML);
});

test('a single-location run still reads in the singular', async () => {
  // The pre-INTAKE-2 sentence, unchanged for the job shape that is still most
  // of the product's use.
  const handle = app({ storage: seededCard() });
  named(handle);
  const first = fileFirst(handle, '2026-03-01T08:00:00Z');
  handle.sandbox.autoSaveReading = () => ({ key: first.key, ts: first.ts, others: [] });
  handle.sandbox.showRun('job1', { state: 'queued' });
  handle.sandbox.showRun('job1', { state: 'done',
    trend_point: point('2026-03-01T08:00:00Z', 2.0) });
  const shown = handle.getEl('toasts').innerHTML;
  assert.ok(/Saved to Compressor train 01/.test(shown), shown);
  assert.ok(!/readings to/.test(shown), `a one-point run was announced in the plural: ${shown}`);
  handle.getEl('toasts').dispatchEvent({ type: 'click', target: { id: 'toast-action' } });
  assert.deepStrictEqual(counts(handle), [0, 0, 0, 0]);
  assert.ok(/The reading is not/.test(handle.getEl('toasts').innerHTML));
});

Promise.all(pending).then(() => {
  results.forEach(([status, name, err]) => {
    console.log(`${status}  ${name}${err ? `\n      ${err}` : ''}`);
  });
  const passed = results.filter(([s]) => s === 'PASS').length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
});
