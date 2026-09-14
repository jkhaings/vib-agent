/**
 * Session INTAKE-2: the trend key becomes machine + location + date + axis, and
 * the history already in an analyst's browser survives the change.
 *
 * Run in node against the REAL app.js (see harness.js), driven by pytest —
 * tests/test_intake2_trend.py — so `pytest` remains the one command.
 *
 * The key has always been `alias|location`, and D-24's replacement has always
 * been within it on date + axis, so the four-part tuple is not new. What is new
 * is what `location` MEANS: it was one machine-level field, and it is now the
 * label of one of up to eight points.
 *
 * Two claims, and the second is the one that can lose an analyst's data:
 *
 *   * every location's reading is filed under ITS OWN point. Filing four points
 *     of one machine under one key interleaves them into a series belonging to
 *     none of them, and the report would then assess that series as a trend.
 *   * a series saved with a BLANK location migrates to `Default`, exactly once,
 *     and a series saved with a REAL label keeps it (operator ruling R-2 — the
 *     brief's literal reading would have renamed "Motor DE" to "Default" and
 *     merged two points into one).
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

const TREND_KEY = 'vib.trend.v1';
const noFetch = async () => { throw new Error('no network in this suite'); };
const app = (opts) => loadApp(Object.assign({ fetchImpl: noFetch }, opts || {}));

function stored(handle, key) {
  const raw = handle.sandbox.localStorage.getItem(key);
  return raw === null ? null : JSON.parse(raw);
}

function reading(ts, value, zone, axis) {
  return { ts, v: value, zone, axis: axis || 'y' };
}

function trendStore(entries) {
  return { [TREND_KEY]: JSON.stringify(entries) };
}

// ── the key itself ────────────────────────────────────────────────────────

test('trendKey normalises a blank location to Default', async () => {
  const handle = app();
  assert.strictEqual(handle.sandbox.trendKey('Pump A', ''), 'Pump A|Default');
  assert.strictEqual(handle.sandbox.trendKey('Pump A', '   '), 'Pump A|Default');
  assert.strictEqual(handle.sandbox.trendKey('Pump A', undefined), 'Pump A|Default');
});

test('a real label is never replaced by Default', async () => {
  const handle = app();
  assert.strictEqual(handle.sandbox.trendKey('Pump A', 'Motor DE'), 'Pump A|Motor DE');
  assert.strictEqual(handle.sandbox.trendKey('Pump A', '  Motor NDE  '), 'Pump A|Motor NDE');
});

// The browser/server agreement on the word "Default" is checked in
// tests/test_intake2_trend.py, which can read app.js AND app.py. A `const` at
// app.js top level is a lexical binding rather than a property of the sandbox
// global, so this suite cannot see the constant itself -- only its effect, which
// the two tests above assert.

// ── the migration (operator ruling R-2) ───────────────────────────────────

test('R-2: a blank-location series migrates to Default', async () => {
  const handle = app({
    storage: trendStore({ 'Pump A|': [reading('2026-01-01T00:00:00Z', 1.1, 'A')] }),
  });
  const after = handle.sandbox.readTrendStore();
  assert.deepStrictEqual(Object.keys(after), ['Pump A|Default']);
  assert.strictEqual(after['Pump A|Default'].length, 1);
  assert.strictEqual(after['Pump A|Default'][0].v, 1.1);
});

test('R-2: a REAL label is kept, not renamed', async () => {
  const handle = app({
    storage: trendStore({
      'Pump A|Motor DE': [reading('2026-01-01T00:00:00Z', 1.1, 'A')],
      'Pump A|Pump NDE': [reading('2026-01-02T00:00:00Z', 2.2, 'B')],
    }),
  });
  const after = handle.sandbox.readTrendStore();
  assert.deepStrictEqual(Object.keys(after).sort(), ['Pump A|Motor DE', 'Pump A|Pump NDE']);
  assert.strictEqual(after['Pump A|Default'], undefined,
    'a label the analyst typed was renamed to Default');
});

test('R-2: two points of one machine are not merged', async () => {
  // The failure the brief's literal reading would have caused, as its own
  // assertion: both series renamed to Default means one of them is gone.
  const handle = app({
    storage: trendStore({
      'Pump A|Motor DE': [reading('2026-01-01T00:00:00Z', 1.1, 'A')],
      'Pump A|Motor NDE': [reading('2026-01-01T00:00:00Z', 9.9, 'D')],
    }),
  });
  const after = handle.sandbox.readTrendStore();
  assert.strictEqual(Object.keys(after).length, 2);
  assert.strictEqual(after['Pump A|Motor DE'][0].v, 1.1);
  assert.strictEqual(after['Pump A|Motor NDE'][0].v, 9.9);
});

test('the migration is idempotent', async () => {
  const handle = app({
    storage: trendStore({ 'Pump A|': [reading('2026-01-01T00:00:00Z', 1.1, 'A')] }),
  });
  const first = JSON.stringify(handle.sandbox.readTrendStore());
  const second = JSON.stringify(handle.sandbox.readTrendStore());
  assert.strictEqual(second, first);
  assert.deepStrictEqual(Object.keys(JSON.parse(second)), ['Pump A|Default']);
});

test('the migration is written back, not recomputed each read', async () => {
  const handle = app({
    storage: trendStore({ 'Pump A|': [reading('2026-01-01T00:00:00Z', 1.1, 'A')] }),
  });
  handle.sandbox.readTrendStore();
  // Observed through localStorage, which is what survives a page load.
  assert.deepStrictEqual(Object.keys(stored(handle, TREND_KEY)), ['Pump A|Default']);
});

test('a blank and an existing Default MERGE rather than one overwriting the other', async () => {
  const handle = app({
    storage: trendStore({
      'Pump A|': [reading('2026-01-01T00:00:00Z', 1.1, 'A')],
      'Pump A|Default': [reading('2026-02-01T00:00:00Z', 2.2, 'B')],
    }),
  });
  const after = handle.sandbox.readTrendStore();
  assert.deepStrictEqual(Object.keys(after), ['Pump A|Default']);
  assert.deepStrictEqual(after['Pump A|Default'].map((p) => p.v), [1.1, 2.2],
    'readings were lost to a key collision, and sorted oldest-first');
});

test('a duplicate stamp across the two keys is not duplicated', async () => {
  const handle = app({
    storage: trendStore({
      'Pump A|': [reading('2026-01-01T00:00:00Z', 1.1, 'A')],
      'Pump A|Default': [reading('2026-01-01T00:00:00Z', 1.1, 'A')],
    }),
  });
  const after = handle.sandbox.readTrendStore();
  assert.strictEqual(after['Pump A|Default'].length, 1);
});

test('a store with nothing to migrate is untouched', async () => {
  const before = { 'Pump A|Motor DE': [reading('2026-01-01T00:00:00Z', 1.1, 'A')] };
  const handle = app({ storage: trendStore(before) });
  // Compared through JSON, not deepStrictEqual: a value built inside the vm
  // realm is never reference-equal to one built out here, prototypes included
  // (SESSION_HIST1.md F-4, and harness.js says so above `stored`).
  assert.strictEqual(JSON.stringify(handle.sandbox.readTrendStore()),
                     JSON.stringify(before));
});

test('an empty and a corrupt store are both survivable', async () => {
  const empty = (handle) => Object.keys(handle.sandbox.readTrendStore()).length;
  assert.strictEqual(empty(app()), 0);
  assert.strictEqual(empty(app({ storage: { [TREND_KEY]: 'not json' } })), 0);
  assert.strictEqual(empty(app({ storage: { [TREND_KEY]: '[1,2,3]' } })), 0);
});

// ── one reading per location ──────────────────────────────────────────────

test('every location on the wire is filed under its own point', async () => {
  const handle = app();
  handle.getEl('field:machine_alias').value = 'Pump A';
  const saved = handle.sandbox.saveOtherLocationReadings({
    locations: [
      { label: 'Motor NDE', trend_point: { severity_rms_mms: 2.0, iso_zone: 'B',
        dominant_axis: 'y', captured_at: '2026-03-01T00:00:00Z' } },
      { label: 'Pump DE', trend_point: { severity_rms_mms: 5.5, iso_zone: 'C',
        dominant_axis: 'z', captured_at: '2026-03-01T00:00:00Z' } },
    ],
  }, 'keep_both');
  assert.strictEqual(saved, 2);
  const store = handle.sandbox.readTrendStore();
  assert.deepStrictEqual(Object.keys(store).sort(), ['Pump A|Motor NDE', 'Pump A|Pump DE']);
  assert.strictEqual(store['Pump A|Motor NDE'][0].v, 2.0);
  assert.strictEqual(store['Pump A|Pump DE'][0].v, 5.5);
});

test('an unnamed machine gets no per-location trend', async () => {
  // Two different unnamed machines would otherwise accumulate into one series
  // belonging to neither — HIST-1's rule, and it has to hold per location too.
  const handle = app();
  handle.getEl('field:machine_alias').value = '';
  const saved = handle.sandbox.saveOtherLocationReadings({
    locations: [{ label: 'Motor NDE', trend_point: { severity_rms_mms: 2.0,
      iso_zone: 'B', dominant_axis: 'y', captured_at: '2026-03-01T00:00:00Z' } }],
  }, 'keep_both');
  assert.strictEqual(saved, 0);
  assert.strictEqual(stored(handle, TREND_KEY), null, 'a refusal wrote something');
});

test('a location with no trendable scalar is skipped, not filed empty', async () => {
  const handle = app();
  handle.getEl('field:machine_alias').value = 'Pump A';
  const saved = handle.sandbox.saveOtherLocationReadings({
    locations: [
      { label: 'Motor NDE', status: 'gate_fail' },
      { label: 'Pump DE', status: 'unreadable', message: 'nope' },
    ],
  }, 'keep_both');
  assert.strictEqual(saved, 0);
});

test('an unlabelled location is never filed', async () => {
  const handle = app();
  handle.getEl('field:machine_alias').value = 'Pump A';
  const saved = handle.sandbox.saveOtherLocationReadings({
    locations: [{ label: '', trend_point: { severity_rms_mms: 2.0, iso_zone: 'B',
      dominant_axis: 'y', captured_at: '2026-03-01T00:00:00Z' } }],
  }, 'keep_both');
  assert.strictEqual(saved, 0);
});

test('D-24 still replaces on date + axis, per location', async () => {
  // The tuple the brief names, end to end: same machine, same LOCATION, same
  // day, same axis is one reading; a different axis is two.
  const handle = app();
  handle.getEl('field:machine_alias').value = 'Pump A';
  const point = (axis, value, ts) => ({
    locations: [{ label: 'Motor NDE', trend_point: { severity_rms_mms: value,
      iso_zone: 'B', dominant_axis: axis, captured_at: ts } }],
  });
  handle.sandbox.saveOtherLocationReadings(point('y', 1.0, '2026-03-01T08:00:00Z'), 'replace');
  handle.sandbox.saveOtherLocationReadings(point('y', 3.0, '2026-03-01T15:00:00Z'), 'replace');
  let series = handle.sandbox.readTrendStore()['Pump A|Motor NDE'];
  assert.strictEqual(series.length, 1, 'same day + same axis should have replaced');
  assert.strictEqual(series[0].v, 3.0);
  handle.sandbox.saveOtherLocationReadings(point('z', 4.0, '2026-03-01T16:00:00Z'), 'replace');
  series = handle.sandbox.readTrendStore()['Pump A|Motor NDE'];
  assert.strictEqual(series.length, 2, 'a different axis is not a duplicate');
});

test('the same day at two different LOCATIONS is two readings', async () => {
  // The whole point of adding the location to the key: before this, two points
  // measured on one day would have been a same-day duplicate of each other.
  const handle = app();
  handle.getEl('field:machine_alias').value = 'Pump A';
  handle.sandbox.saveOtherLocationReadings({
    locations: [
      { label: 'Motor NDE', trend_point: { severity_rms_mms: 1.0, iso_zone: 'A',
        dominant_axis: 'y', captured_at: '2026-03-01T08:00:00Z' } },
      { label: 'Pump DE', trend_point: { severity_rms_mms: 9.0, iso_zone: 'D',
        dominant_axis: 'y', captured_at: '2026-03-01T08:05:00Z' } },
    ],
  }, 'replace');
  const store = handle.sandbox.readTrendStore();
  assert.strictEqual(store['Pump A|Motor NDE'].length, 1);
  assert.strictEqual(store['Pump A|Pump DE'].length, 1);
  assert.strictEqual(store['Pump A|Motor NDE'][0].v, 1.0);
  assert.strictEqual(store['Pump A|Pump DE'][0].v, 9.0);
});

Promise.all(pending).then(() => {
  results.forEach(([status, name, err]) => {
    console.log(`${status}  ${name}${err ? `\n      ${err}` : ''}`);
  });
  const passed = results.filter(([s]) => s === 'PASS').length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
});
