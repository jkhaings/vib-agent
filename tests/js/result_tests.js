/**
 * Session UX-5 — the result, from a finished job to a trusted reading.
 *
 * Driven against the real `app.js`. Every claim here is about something that
 * happens between the wire answering and the analyst reading it: what is saved,
 * what is kept, what the card says, and what the run button then allows.
 *
 * The stranger-test rows this file protects:
 *   B3  "send us the job reference" with no job reference on the page (3/3 runs)
 *   B4  the report is the one thing the app will not keep  (RULED D-26)
 *   C5  the result renders below the fold; the paid button re-arms
 *   C6  the reading is silently not saved                  (RULED, with undo)
 *   C9  "Analyze another file" lands on Review with the old result beneath
 *   U8  forty lines of retention essay under every result
 *   U9  no mm/s on the severity card
 */
const assert = require('assert');
const { loadApp, plain } = require('./harness');

const TREND_KEY = 'vib.trend.v1';
const MACHINES_KEY = 'vib.machines.v2';
const REPORTS_KEY = 'vib.reports.v1';
const KEY = 'Pump A|Motor DE';

const results = [];
function test(name, fn) { results.push({ name, fn }); }

const POINT = {
  captured_at: '2026-09-08T10:00:00+00:00', severity_rms_mms: 1.77,
  iso_zone: 'B', dominant_axis: 'y',
};
const DONE = {
  state: 'done',
  result_summary: { no_findings: false, severity: 'ISO Zone B',
    faults: [{ label: 'Bearing outer-race fault (BPFO)', confidence: 'high' }] },
  trend_point: POINT,
};

/** A card as the store holds one. */
function card(over) {
  const base = {
    machine_alias: 'Pump A', rpm: '1780', iso_group: '2', iso_support: 'rigid',
    bearing_model: '6206', velocity_unit: 'mm_s', detection_type: 'rms', mode: 'spectrum',
    direction: '', sensor_sensitivity_mv_per_g: '', fmax_hz: '', spectral_lines: '',
    window_type: '', averages: '', integration: '', measurement_location: 'Motor DE',
    coupling: '', blades: '', drive_type: '', poles: '', line_freq_hz: '',
    rotor_bars: '', gear_teeth_driving: '', gear_teeth_driven: '',
    drive_pulley_mm: '', driven_pulley_mm: '', pulley_center_distance_mm: '',
  };
  return Object.assign(base, over || {});
}

/** An app whose next job finishes with `wire`. The PDF response is a real
 *  blob-bearing one, so the D-26 fetch has something to keep. */
function appWithJob(wire, opts) {
  const options = opts || {};
  const posted = [];
  const app = loadApp(Object.assign({
    fetchImpl: (url, init) => {
      const u = String(url);
      if (u === '/api/jobs' && init && init.method === 'POST') {
        posted.push(init.body);
        return { ok: true, status: 202, json: () => ({ job_id: 'JOB123' }) };
      }
      if (/\/report\.pdf$/.test(u)) {
        return {
          ok: true,
          status: 200,
          blob: () => Promise.resolve(new app.sandbox.Blob(['%PDF-1.7 report'],
                                                          { type: 'application/pdf' })),
        };
      }
      return { ok: true, status: 200, json: () => Object.assign({ state: 'done' }, wire || DONE) };
    },
  }, options));
  return { app, posted };
}

/** Fill the form so a submit is a real one for `Pump A · Motor DE`. */
function primeForm(app, remember) {
  app.getEl('field:machine_alias').value = 'Pump A';
  app.getEl('field:measurement_location').value = 'Motor DE';
  app.getEl('field:rpm').value = '1780';
  app.getEl('file').files = [new app.sandbox.File(['x'], 'before.csv')];
  if (remember) app.getEl('remember').checked = true;
}

async function runJob(app) {
  await app.sandbox.submitJob();
  await app.advance(5000);
  await app.flush();
}

const stored = (app, key) => JSON.parse(app.sandbox.localStorage.getItem(key) || 'null');
const toast = (app) => {
  const html = app.getEl('toasts').innerHTML;
  const m = html.match(/<span class="t-msg">([^<]*)</);
  return m ? m[1] : null;
};

// ── C6 · the reading is saved, and it can be taken back ──────────────────

test('C6: a run against a SAVED machine files the reading without being asked', async () => {
  const { app } = appWithJob(DONE, { storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  primeForm(app);
  await runJob(app);
  const series = stored(app, TREND_KEY)[KEY];
  assert.strictEqual(series.length, 1, 'the reading was not saved');
  assert.strictEqual(series[0].v, 1.77);
  assert.ok(/Saved to Pump A/.test(toast(app) || ''), toast(app));
});

test('C6: the confirmation carries an Undo, and the Undo takes it back', async () => {
  const { app } = appWithJob(DONE, { storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  primeForm(app);
  await runJob(app);
  const region = app.strictEl('toasts');
  assert.ok(region.innerHTML.includes('id="toast-action"'), region.innerHTML);
  region.dispatchEvent({ type: 'click', target: { id: 'toast-action' } });
  await app.flush();
  assert.deepStrictEqual(plain(stored(app, TREND_KEY)[KEY]), [], 'the reading is still there');
  assert.ok(/Not saved/.test(toast(app) || ''), toast(app));
});

test('C6: a machine that was only TYPED is not saved to silently', async () => {
  const { app } = appWithJob(DONE);
  primeForm(app);
  await runJob(app);
  assert.strictEqual(stored(app, TREND_KEY), null, 'a typed alias must not start a series');
  assert.ok(app.stateCard.innerHTML.includes('id="save-trend"'), 'it keeps the offer instead');
});

test('C6: ticking Remember is the analyst saying it is one of theirs', async () => {
  const { app } = appWithJob(DONE);
  primeForm(app, true);
  await runJob(app);
  assert.strictEqual(stored(app, TREND_KEY)[KEY].length, 1);
});

test('C6: the saved card renders the saved state, never an offer it contradicts', async () => {
  const { app } = appWithJob(DONE, { storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  primeForm(app);
  await runJob(app);
  const html = app.stateCard.innerHTML;
  assert.ok(!html.includes('id="save-trend"'), 'it offered to do what it had already done');
  assert.ok(html.includes('Saved to this machine'), html.slice(0, 400));
});

// ── B4 / D-26 · the report is kept with the reading ──────────────────────

test('D-26: the PDF is kept in this browser, under the reading it belongs to', async () => {
  const { app } = appWithJob(DONE, { storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  primeForm(app);
  await runJob(app);
  const held = await app.sandbox.reportsFor(KEY);
  assert.deepStrictEqual(Object.keys(held), [POINT.captured_at]);
  assert.ok(held[POINT.captured_at].size > 0, 'an empty blob was kept');
  const facts = stored(app, REPORTS_KEY)[KEY][POINT.captured_at];
  assert.strictEqual(facts.pdf, true);
  assert.strictEqual(facts.job, 'JOB123');
  assert.strictEqual(facts.file, 'before.csv');
  assert.strictEqual(facts.fault, 'Bearing outer-race fault (BPFO)');
  assert.strictEqual(facts.confidence, 'high');
});

test('D-26: the card says so, once it is true and not before', async () => {
  const { app } = appWithJob(DONE, { storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  primeForm(app);
  await runJob(app);
  assert.ok(app.stateCard.innerHTML.includes('kept with the\n      reading')
    || app.stateCard.innerHTML.includes('kept with the reading'), app.stateCard.innerHTML.slice(0, 600));
});

test('D-26: nothing new crosses the wire — the PDF comes from the link we already had', async () => {
  const seen = [];
  const app = loadApp({
    storage: { [MACHINES_KEY]: JSON.stringify([card()]) },
    fetchImpl: (url, init) => {
      seen.push(String(url));
      if (String(url) === '/api/jobs' && init && init.method === 'POST') {
        return { ok: true, status: 202, json: () => ({ job_id: 'JOB123' }) };
      }
      if (/report\.pdf$/.test(String(url))) {
        return { ok: true, status: 200, blob: () => Promise.resolve(new app.sandbox.Blob(['%PDF'])) };
      }
      return { ok: true, status: 200, json: () => DONE };
    },
  });
  primeForm(app);
  await runJob(app);
  const extra = seen.filter((u) => u !== '/api/jobs' && !/^\/api\/jobs\/JOB123$/.test(u)
    && !/report\.pdf$/.test(u));
  assert.deepStrictEqual(extra, [], `an unexpected request: ${extra.join(', ')}`);
  assert.ok(seen.some((u) => u === '/api/jobs/JOB123/report.pdf'),
    'the report was fetched from somewhere other than its own endpoint');
});

test('D-26: a browser with no IndexedDB gets the whole run, and is told plainly', async () => {
  const { app } = appWithJob(DONE, {
    indexedDB: false, storage: { [MACHINES_KEY]: JSON.stringify([card()]) },
  });
  primeForm(app);
  await runJob(app);
  assert.strictEqual(stored(app, TREND_KEY)[KEY].length, 1, 'the reading must still be saved');
  assert.ok(app.stateCard.innerHTML.includes('will not let us keep'), 'it must SAY so');
  assert.ok(app.stateCard.innerHTML.includes('/api/jobs/JOB123/report.pdf'),
    'and the server copy is still offered');
});

test('D-26: storage that refuses us is not an error the analyst has to read', async () => {
  const { app } = appWithJob(DONE, {
    indexedDB: 'fail', storage: { [MACHINES_KEY]: JSON.stringify([card()]) },
  });
  primeForm(app);
  await runJob(app);
  assert.strictEqual(stored(app, TREND_KEY)[KEY].length, 1);
  assert.ok(app.stateCard.innerHTML.includes('Report ready'), 'the run itself must be unaffected');
});

test('D-26: deleting the reading takes the report with it', async () => {
  const { app } = appWithJob(DONE, { storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  primeForm(app);
  await runJob(app);
  assert.strictEqual(Object.keys(await app.sandbox.reportsFor(KEY)).length, 1);
  app.sandbox.deleteReadingLocal(KEY, POINT.captured_at);
  await app.flush();
  assert.deepStrictEqual(Object.keys(await app.sandbox.reportsFor(KEY)), [],
    'a report outliving its reading is undeletable and unpromised');
  assert.strictEqual(stored(app, REPORTS_KEY)[KEY], undefined);
});

test('D-26: forgetting the machine takes every report filed under it', async () => {
  const { app } = appWithJob(DONE, { storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  primeForm(app);
  await runJob(app);
  app.sandbox.forgetMachineLocal(KEY);
  await app.flush();
  assert.deepStrictEqual(Object.keys(await app.sandbox.reportsFor(KEY)), []);
});

test('D-26: a rename carries the reports, like the readings', async () => {
  const { app } = appWithJob(DONE, { storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  primeForm(app);
  await runJob(app);
  const res = app.sandbox.updateMachineLocal(KEY, { machine_alias: 'Pump A1' });
  await app.flush();
  assert.strictEqual(res.ok, true);
  assert.deepStrictEqual(Object.keys(await app.sandbox.reportsFor(KEY)), [], 'left behind');
  assert.deepStrictEqual(Object.keys(await app.sandbox.reportsFor(res.id)), [POINT.captured_at]);
  assert.ok(stored(app, REPORTS_KEY)[res.id], 'the facts must move too');
});

// ── B3 · the job reference is on the page that asks you to quote it ──────

test('B3: a degraded run shows the reference, with the reason, and a copy control', async () => {
  const wire = Object.assign({}, DONE, { state: 'degraded', degraded_reason: 'draft_failure' });
  const { app } = appWithJob(wire, { storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  primeForm(app);
  await runJob(app);
  const html = app.stateCard.innerHTML;
  assert.ok(html.includes('JOB123'), 'the id is still only in the href');
  assert.ok(html.includes('id="copy-ref"'), 'nothing to press');
  assert.ok(/failed on our side/.test(html), 'the reason is not stated');
});

test('B3: the spend-cap reason is named as itself, not as a failure', async () => {
  const wire = Object.assign({}, DONE, { state: 'degraded', degraded_reason: 'spend_budget' });
  const { app } = appWithJob(wire);
  primeForm(app);
  await runJob(app);
  const html = app.stateCard.innerHTML;
  assert.ok(/budget for today was spent/.test(html), html.slice(0, 500));
  assert.ok(html.includes('JOB123'));
});

test('B3: Copy puts the reference on the clipboard and says it did', async () => {
  const wire = Object.assign({}, DONE, { state: 'degraded', degraded_reason: 'draft_failure' });
  const { app } = appWithJob(wire);
  primeForm(app);
  await runJob(app);
  app.stateCard.dispatchEvent({
    type: 'click',
    target: { id: 'copy-ref', getAttribute: (n) => (n === 'data-ref' ? 'JOB123' : null) },
  });
  await app.flush();
  assert.deepStrictEqual(app.copied(), ['JOB123']);
  assert.ok(/copied/i.test(toast(app) || ''), toast(app));
});

test('B3: a healthy run does not carry the block at all', async () => {
  const { app } = appWithJob(DONE);
  primeForm(app);
  await runJob(app);
  assert.ok(!app.stateCard.innerHTML.includes('id="copy-ref"'),
    'nothing failed, so there is nothing to quote');
});

// ── U9 · the number on the severity card ─────────────────────────────────

test('U9: the severity card carries the overall mm/s value', async () => {
  const { app } = appWithJob(DONE);
  primeForm(app);
  await runJob(app);
  const html = app.stateCard.innerHTML;
  assert.ok(html.includes('1.77 mm/s RMS · ISO 20816-3'), html.slice(0, 800));
});

test('U9: with no trendable value the card is exactly what it was', async () => {
  const wire = { state: 'done', result_summary: DONE.result_summary };
  const { app } = appWithJob(wire);
  primeForm(app);
  await runJob(app);
  assert.ok(app.stateCard.innerHTML.includes('ISO 20816-3'));
  assert.ok(!/mm\/s RMS · ISO/.test(app.stateCard.innerHTML), 'a number was invented');
});

// ── C5 · the run button, after a run ─────────────────────────────────────

test('C5: the button stays down after a finished run, and says why', async () => {
  const { app } = appWithJob(DONE);
  primeForm(app);
  await runJob(app);
  assert.strictEqual(app.strictEl('submit').disabled, true, 'a second paid run is one click away');
  assert.ok(/has been analysed/.test(app.strictEl('submit-note').innerHTML));
});

test('C5: a gate stop or an error gives the button back', async () => {
  for (const wire of [{ state: 'gate_fail', gate_summary: { reasons: ['r'], collect: ['c'] } },
    { state: 'error', safe_message: 'nope', failure_kind: 'bad_upload', retryable: false }]) {
    const { app } = appWithJob(wire);
    primeForm(app);
    await runJob(app);
    assert.strictEqual(app.strictEl('submit').disabled, false, `${wire.state} must be retryable`);
  }
});

test('C5: choosing another file re-arms it', async () => {
  const { app } = appWithJob(DONE);
  primeForm(app);
  await runJob(app);
  assert.strictEqual(app.strictEl('submit').disabled, true);
  const input = app.strictEl('file');
  input.files = [new app.sandbox.File(['y'], 'after.csv')];
  input.dispatchEvent({ type: 'change', target: input });
  assert.strictEqual(app.strictEl('submit').disabled, false);
});

// ── C9 · the next run starts where a next run starts ─────────────────────

test('C9: Analyze another file clears the file, the result, and lands on step 2', async () => {
  const { app } = appWithJob(DONE);
  primeForm(app);
  await runJob(app);
  assert.ok(app.stateCard.innerHTML.includes('Report ready'));
  app.stateCard.dispatchEvent({ type: 'click', target: { id: 'again' } });
  await app.flush();
  assert.strictEqual(app.stateCard.innerHTML, '', 'the old result is still on screen');
  assert.strictEqual(app.sandbox.location.hash, '#/new/2', app.sandbox.location.hash);
  assert.strictEqual(app.strictEl('file').value, '');
  assert.strictEqual(app.strictEl('submit').disabled, false);
});

test('C9: the machine values survive it — that is what "for this machine" means', async () => {
  const { app } = appWithJob(DONE);
  primeForm(app);
  await runJob(app);
  app.stateCard.dispatchEvent({ type: 'click', target: { id: 'again' } });
  await app.flush();
  assert.strictEqual(app.getEl('field:machine_alias').value, 'Pump A');
  assert.strictEqual(app.getEl('field:rpm').value, '1780');
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
