/**
 * Session HIST-1: the browser half of the trend card, run in node against the
 * REAL app.js (see harness.js). Driven by pytest — tests/test_hist1_browser.py
 * — so `pytest` remains the one command.
 *
 * The claims under test are the ones a python test cannot make, because they
 * are about a store on the analyst's own device and a payload their browser
 * assembles:
 *
 *   * the machine card at `vib.machines.v2` is NOT touched — a sibling key was
 *     chosen precisely so `migrateEntry`'s whitelist could stay untouched, and
 *     a session that quietly broke that would still pass every python test;
 *   * the zone and the axis the card stores are NEVER posted back — the
 *     server's ClientHistoryPoint is closed and would 422 them;
 *   * an UNNAMED machine gets no trend, so two of them cannot accumulate into
 *     one series that belongs to neither;
 *   * nothing is sent in a mode the server would refuse.
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

const TREND_KEY = 'vib.trend.v1';
const MACHINES_KEY = 'vib.machines.v2';
const KEY = 'Pump A|Motor DE';

const POINT = {
  severity_rms_mms: 3.4,
  iso_zone: 'C',
  dominant_axis: 'z',
  captured_at: '2026-09-04T10:00:00+00:00',
};
const DONE = {
  state: 'done',
  result_summary: { no_findings: false, severity: 'ISO Zone C', faults: [] },
  trend_point: POINT,
};

const SERIES = [
  { ts: '2026-07-01T10:00:00+00:00', v: 2.0, zone: 'A', axis: 'z' },
  { ts: '2026-08-01T10:00:00+00:00', v: 2.4, zone: 'B', axis: 'z' },
];

function withStore(store) {
  return loadApp({ fetchImpl: () => { throw new Error('no fetch in these tests'); }, storage: store });
}

// app.js runs inside a `vm` context, so an object it builds has that realm's
// Object.prototype and NOT this file's. `deepStrictEqual` compares prototypes
// and rejects them on identity alone, with a message that reads like a real
// mismatch ("same structure but not reference-equal"). Round-tripping through
// JSON puts the value in this realm; it is also exactly what the browser does
// to the payload on its way out, so it is the honest comparison to make.
const { plain } = require('./harness');   // UX-5: lifted into the harness

function stored(app, key) {
  const raw = app.sandbox.localStorage.getItem(key);
  return raw === null ? null : JSON.parse(raw);
}

// ── 1. the store ──────────────────────────────────────────────────────────

test('a saved point round-trips, oldest first', () => {
  const app = withStore({});
  app.sandbox.saveTrendPoint(KEY, POINT);
  app.sandbox.saveTrendPoint(KEY, Object.assign({}, POINT, {
    captured_at: '2026-06-01T10:00:00+00:00', severity_rms_mms: 1.1, iso_zone: 'A',
  }));
  const points = app.sandbox.readTrend(KEY);
  assert.strictEqual(points.length, 2);
  assert.strictEqual(points[0].v, 1.1, 'the series must read oldest first');
  assert.strictEqual(points[1].v, 3.4);
});

test('saving the same analysis twice keeps one reading', () => {
  const app = withStore({});
  app.sandbox.saveTrendPoint(KEY, POINT);
  app.sandbox.saveTrendPoint(KEY, POINT);
  assert.strictEqual(app.sandbox.readTrend(KEY).length, 1,
    'a second Save on the same job must not duplicate the reading');
});

test('the payload posted back carries ts and value ONLY', () => {
  const app = withStore({ [TREND_KEY]: JSON.stringify({ [KEY]: SERIES }) });
  const payload = plain(app.sandbox.trendPayload(KEY));
  assert.deepStrictEqual(payload, [
    { ts: '2026-07-01T10:00:00+00:00', value: 2.0 },
    { ts: '2026-08-01T10:00:00+00:00', value: 2.4 },
  ]);
  payload.forEach((p) => {
    assert.deepStrictEqual(Object.keys(p).sort(), ['ts', 'value'],
      'the zone and axis stay in this browser — the server refuses extra keys');
  });
});

test('a corrupt store is read as empty rather than posted back', () => {
  assert.deepStrictEqual(plain(withStore({ [TREND_KEY]: 'not json' }).sandbox.readTrend(KEY)), []);
  assert.deepStrictEqual(plain(withStore({ [TREND_KEY]: '[]' }).sandbox.readTrend(KEY)), []);
  const junk = { [KEY]: [{ ts: 'x' }, { v: 1 }, null, { ts: 'ok', v: -2 }, { ts: 'ok2', v: 1.5 }] };
  const app = withStore({ [TREND_KEY]: JSON.stringify(junk) });
  assert.deepStrictEqual(app.sandbox.readTrend(KEY).map((p) => p.v), [1.5],
    'only usable readings survive a read — a bad one posted back is a 422 the analyst cannot act on');
});

test('the machine card at vib.machines.v2 is never touched', () => {
  const card = { machine_alias: 'Pump A', rpm: '1800', measurement_location: 'Motor DE' };
  const app = withStore({ [MACHINES_KEY]: JSON.stringify([card]) });
  const before = JSON.stringify(stored(app, MACHINES_KEY));
  app.sandbox.saveTrendPoint(KEY, POINT);
  assert.strictEqual(JSON.stringify(stored(app, MACHINES_KEY)), before,
    'the trend is a SIBLING key: migrateEntry would destroy an array on a card');
  assert.ok(stored(app, TREND_KEY)[KEY].length === 1, 'the trend went to its own key');
});

// ── 2. the band ───────────────────────────────────────────────────────────

test('the band is rise over the FIRST reading, in three steps', () => {
  const app = withStore({});
  const rise = (a, b) => app.sandbox.trendRise([{ ts: '1', v: a }, { ts: '2', v: b }]);
  assert.strictEqual(app.sandbox.trendRise([{ ts: '1', v: 2 }]), null, 'one point is no trend');
  assert.strictEqual(Math.round(rise(2.0, 2.2)), 10);
  assert.strictEqual(app.sandbox.trendBand(rise(2.0, 2.2)).cls, 'zA');
  assert.strictEqual(app.sandbox.trendBand(rise(2.0, 2.8)).cls, 'zC');   // +40%
  assert.strictEqual(app.sandbox.trendBand(rise(2.0, 3.5)).cls, 'zD');   // +75%
  assert.strictEqual(app.sandbox.trendBand(rise(2.0, 1.0)).cls, 'zA');   // falling
  assert.ok(app.sandbox.trendBand(rise(2.0, 1.0)).text.includes('below'));
});

test('a zero first reading is no baseline rather than an infinite rise', () => {
  const app = withStore({});
  assert.strictEqual(app.sandbox.trendRise([{ ts: '1', v: 0 }, { ts: '2', v: 2 }]), null);
});

// ── 3. the offer on the ready card ────────────────────────────────────────

async function runJob(app) {
  app.sandbox.submitJob();
  await app.advance(60000);
}

function primeForm(app, { alias = 'Pump A', loc = 'Motor DE', mode = 'spectrum' } = {}) {
  app.getEl('field:machine_alias').value = alias;
  app.getEl('field:measurement_location').value = loc;
  app.getEl('field:rpm').value = '1800';
  app.getEl('field:invite_code').value = 'demo-code';
  app.getEl('mode').value = mode;
  app.getEl('file').files = [{ name: 'spec.csv', size: 10 }];
}

function appWithJob(storage, opts = {}) {
  let posted = null;
  const app = loadApp({
    storage,
    ...opts,
    fetchImpl: (url, init) => {
      if (String(url).endsWith('/api/jobs')) {
        posted = init.body;
        return jsonResponse({ job_id: 'j1' });
      }
      return jsonResponse(DONE);
    },
  });
  return { app, posted: () => posted };
}

test('a named machine sends its saved readings with the upload', async () => {
  const { app, posted } = appWithJob({ [TREND_KEY]: JSON.stringify({ [KEY]: SERIES }) });
  primeForm(app);
  await runJob(app);
  const sent = JSON.parse(posted().get('history'));
  assert.strictEqual(sent.length, 2);
  assert.deepStrictEqual(Object.keys(sent[0]).sort(), ['ts', 'value']);
});

test('an unnamed machine sends nothing and is told why', async () => {
  const { app, posted } = appWithJob({ [TREND_KEY]: JSON.stringify({ '|': SERIES }) });
  primeForm(app, { alias: '', loc: '' });
  await runJob(app);
  assert.strictEqual(posted().get('history'), undefined,
    'two different unnamed machines would otherwise share one series');
  assert.ok(app.stateCard.innerHTML.includes('alias'),
    'the card should say what to do to keep a trend');
  assert.ok(!app.stateCard.innerHTML.includes('id="save-trend"'));
});

test('nothing is sent in a mode the server would refuse', async () => {
  for (const mode of ['trend', 'compare']) {
    const { app, posted } = appWithJob({ [TREND_KEY]: JSON.stringify({ [KEY]: SERIES }) });
    primeForm(app, { mode });
    await runJob(app);
    assert.strictEqual(posted().get('history'), undefined, `history was sent in ${mode} mode`);
  }
});

test('a machine with nothing saved sends no history part at all', async () => {
  const { app, posted } = appWithJob({});
  primeForm(app);
  await runJob(app);
  assert.strictEqual(posted().get('history'), undefined,
    'an empty card must not become an always-present empty part');
});

test('the offer appears, saves, and becomes the card', async () => {
  const { app } = appWithJob({});
  primeForm(app);
  await runJob(app);
  assert.ok(app.stateCard.innerHTML.includes('id="save-trend"'), 'the offer should be on the card');

  app.stateCard.dispatchEvent({
    type: 'click',
    target: { id: 'save-trend', getAttribute: (n) => (n === 'data-key' ? KEY : null) },
  });
  await app.flush();

  assert.strictEqual(stored(app, TREND_KEY)[KEY].length, 1, 'the reading should be stored');
  assert.strictEqual(stored(app, TREND_KEY)[KEY][0].v, 3.4);
  assert.ok(app.stateCard.innerHTML.includes('Trend for Pump A'), 'the card should render');
  assert.ok(!app.stateCard.innerHTML.includes('id="save-trend"'), 'the offer should be spent');
});

test('a reading with no trendable scalar offers nothing', async () => {
  const app = loadApp({
    storage: {},
    fetchImpl: (url) => (String(url).endsWith('/api/jobs')
      ? jsonResponse({ job_id: 'j1' })
      : jsonResponse({ state: 'done', result_summary: { no_findings: true, faults: [] } })),
  });
  primeForm(app);
  await runJob(app);
  assert.ok(!app.stateCard.innerHTML.includes('save-trend'),
    'an acceleration-only reading has no mm/s value to trend');
});

// ── 4. the promises on the page ───────────────────────────────────────────

test('the form says the readings will be sent BEFORE the analyst submits', () => {
  const app = withStore({ [TREND_KEY]: JSON.stringify({ [KEY]: SERIES }) });
  app.getEl('field:machine_alias').value = 'Pump A';
  app.getEl('field:measurement_location').value = 'Motor DE';
  app.getEl('field:machine_alias').dispatchEvent({ type: 'input' });
  assert.ok(app.visible('trend-note'), 'the note should be shown');
  assert.ok(app.getEl('trend-note').innerHTML.includes('2 saved readings'));
});

test('the form says nothing when there is nothing to send', () => {
  const app = withStore({ [TREND_KEY]: JSON.stringify({ [KEY]: SERIES }) });
  app.getEl('field:machine_alias').value = 'Another Pump';
  app.getEl('field:machine_alias').dispatchEvent({ type: 'input' });
  assert.ok(!app.visible('trend-note'));
});

test('the retention ledger has a line for the trend and the report, and all seven others', async () => {
  const { app } = appWithJob({ [TREND_KEY]: JSON.stringify({ [KEY]: SERIES }) });
  primeForm(app);
  await runJob(app);
  const html = app.stateCard.innerHTML;
  const rows = (html.match(/<li class="(gone|kept|timed)"/g) || []).length;
  // Session UX-5 adds the eighth: the report itself, now kept in this browser
  // with the reading (RULED D-26). A live row on both backends, because it is a
  // promise about THIS browser -- like the two above it -- rather than about an
  // account that does not exist yet.
  //
  // Session TIDY-1 adds the ninth, which SESSION_LEGAL1 §7.4 said was owed:
  // `format_pings.jsonl`. Live on both backends and in both of its own
  // branches -- it says "not kept" when the box was not ticked, which is a
  // promise too -- so the count does not move with the flag.
  assert.strictEqual(rows, 9, `the ledger should have nine lines, found ${rows}`);
  assert.ok(html.includes('trend readings'), 'the ledger must name the trend store');
  assert.ok(html.includes('Your uploaded file'), 'the original six lines must survive');
  assert.ok(html.includes('One log line'));
  assert.ok(html.includes('kept for <b>14 days</b>'),
    'the log line is the one row with a period nobody had written down');
  assert.ok(html.includes('The format note'),
    'the file this product keeps forever has no line in the ledger');
});

test('the format note row says which of the two things happened', async () => {
  // Written from what the run DID, like the trace and report rows: an analyst
  // who did not tick the box is told nothing was recorded, and one who did is
  // told what was -- and that it has no expiry, because nothing rotates that
  // file (deploy/setup_server.sh covers app.log and only app.log).
  const { app } = appWithJob({});
  primeForm(app);
  await runJob(app);
  const html = app.stateCard.innerHTML;
  assert.ok(html.includes('You did not tick'), html.slice(0, 200));
  assert.ok(!html.includes('no expiry'),
    'an unticked run must not be told about a record it did not create');
});

test('with accounts on, all seven future rows become live ones', async () => {
  // Session AUTH-1's half of the same-commit rule. The second list is a promise
  // about a thing that does not exist on the shipped backend, and a statement of
  // fact on the one with accounts. Both are asserted, because a ledger that said
  // the same thing either way would be false in one of them.
  const { app } = appWithJob({ [TREND_KEY]: JSON.stringify({ [KEY]: SERIES }) },
                             { accounts: true });
  primeForm(app);
  await runJob(app);
  const html = app.stateCard.innerHTML;
  const live = (html.match(/<li class="(gone|kept|timed)"/g) || []).length;
  const future = (html.match(/<li class="future"/g) || []).length;
  // Session BILL-1 added the seventh account row (credit purchases), in the same
  // commit as the `checkout_sessions` table and the /privacy paragraph — D-22.
  assert.strictEqual(live, 16, `nine live rows plus seven that flipped, found ${live}`);
  assert.strictEqual(future, 0, `nothing in that list is future any more, found ${future}`);
  assert.ok(html.includes('Kept with your account'), 'the heading must change too');
  assert.ok(!html.includes('None of this is in effect'),
    'the closing sentence still says accounts do not exist');
  assert.ok(html.includes('Job metadata'), 'the row that flipped last must still be there');
  assert.ok(html.includes('Credit purchases'),
    'a table was added and the ledger an analyst reads does not mention it');
});

test('U8: the ledger is one line until it is asked for, and loses no row', async () => {
  // STRANGER U8: "~40 lines of WHAT WAS KEPT plus a second list NOT YET IN
  // EFFECT appear under every result. One line + link to /privacy. A results
  // page is not the place to tell me about features you haven't shipped."
  //
  // Shortened, not weakened: the summary is one sentence computed from what
  // this run actually did, every row is still in the document, and /privacy is
  // still linked. A `<details>` is the difference between collapsing a promise
  // and deleting one.
  const { app } = appWithJob({});
  primeForm(app);
  await runJob(app);
  const html = app.stateCard.innerHTML;
  assert.ok(html.includes('<details class="ledger">'), 'the ledger is not collapsible');
  const summary = html.split('<summary>')[1].split('</summary>')[0];
  assert.ok(summary.includes('60 minutes'), summary);
  assert.ok(!summary.includes('Not yet in effect'),
    'the unshipped list must not be what an analyst reads first');
  assert.ok(html.includes('Not yet in effect'), 'and it must still be there when asked for');
  assert.ok(html.includes('/privacy'), 'U8 asks for the link and it has to survive');
});

test('the purchases row promises no card number and no amount', async () => {
  // The claim /privacy makes in longer form. Both are written in one commit and
  // this is where they are checked against each other rather than remembered.
  const { app } = appWithJob({}, { accounts: true });
  primeForm(app);
  await runJob(app);
  const html = app.stateCard.innerHTML;
  const row = html.match(/<li class="([a-z]+)"><span class="m">[^<]*<\/span><span><b>Credit purchases<\/b>([\s\S]*?)<\/span><\/li>/);
  assert.ok(row, 'the Credit purchases row is not in the ledger');
  assert.strictEqual(row[1], 'kept', 'it must flip with the accounts flag like its siblings');
  assert.ok(row[2].includes('No card number'), 'the row must say what is NOT kept');
  assert.ok(row[2].includes('Stripe'), 'the row must name who does hold it');
});

test('job metadata flips too, because Session JOB-DB gave it a writer', async () => {
  const { app } = appWithJob({}, { accounts: true });
  primeForm(app);
  await runJob(app);
  const html = app.stateCard.innerHTML;
  const row = html.match(/<li class="([a-z]+)"><span class="m">[^<]*<\/span><span><b>Job metadata/);
  assert.ok(row, 'the Job metadata row is gone from the ledger entirely');
  assert.strictEqual(row[1], 'kept',
    'a finished analysis records a job row now, so the ledger must promise it');
});

// ── report ────────────────────────────────────────────────────────────────

Promise.all(pending).then(() => {
  results.forEach(([status, name, detail]) => {
    console.log(`${status}  ${name}${detail ? `\n        ${detail}` : ''}`);
  });
  const passed = results.filter(([s]) => s === 'PASS').length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
});
