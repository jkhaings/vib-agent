/**
 * Session UX-5 — the machine page: what a returning analyst reads.
 *
 * Funnel step 7 (POSITIONING §9): the analyst comes back with the next reading
 * and has to be able to tell last quarter's from this one. The stranger could
 * not: two rows, same date, no time, no diagnosis, and no way to reach the
 * report that had been produced for either.
 *
 *   C10  time, file name, committed diagnosis and confidence per row; the
 *        trend line names the fault rather than only the overall value
 *   B4   the report is on the row  (RULED D-26, from the browser's own copy)
 *   C11  the control says Delete, and one word for an empty field
 */
const assert = require('assert');
const { loadApp } = require('./harness');

const TREND_KEY = 'vib.trend.v1';
const MACHINES_KEY = 'vib.machines.v2';
const REPORTS_KEY = 'vib.reports.v1';
const KEY = 'Pump A|Motor DE';
const T1 = '2026-07-01T09:15:00+00:00';
const T2 = '2026-08-01T14:40:00+00:00';

const results = [];
function test(name, fn) { results.push({ name, fn }); }

function card(over) {
  return Object.assign({
    machine_alias: 'Pump A', rpm: '1780', iso_group: '2', iso_support: 'rigid',
    bearing_model: '6206', velocity_unit: 'mm_s', detection_type: 'rms', mode: 'spectrum',
    direction: '', sensor_sensitivity_mv_per_g: '', fmax_hz: '', spectral_lines: '',
    window_type: '', averages: '', integration: '', measurement_location: 'Motor DE',
    coupling: 'coupled', blades: '', drive_type: '', poles: '', line_freq_hz: '',
    rotor_bars: '', gear_teeth_driving: '', gear_teeth_driven: '',
    drive_pulley_mm: '', driven_pulley_mm: '', pulley_center_distance_mm: '',
  }, over || {});
}

const SERIES = [
  { ts: T1, v: 1.77, zone: 'B', axis: 'y' },
  { ts: T2, v: 0.90, zone: 'A', axis: 'y' },
];

const FACTS = {
  [KEY]: {
    [T1]: { file: 'before.csv', fault: 'Bearing outer-race fault (BPFO)',
      confidence: 'high', job: 'JOB1', pdf: true },
    [T2]: { file: 'after.csv', fault: 'Bearing outer-race fault (BPFO)',
      confidence: 'medium', job: 'JOB2', pdf: true },
  },
};

function machinePage(opts) {
  const options = opts || {};
  return loadApp(Object.assign({
    fetchImpl: () => Promise.reject(new Error('the machine page makes no request')),
    storage: {
      [MACHINES_KEY]: JSON.stringify([card()]),
      [TREND_KEY]: JSON.stringify({ [KEY]: SERIES }),
      [REPORTS_KEY]: JSON.stringify(FACTS),
    },
  }, options));
}

async function open_(app, key) {
  await app.goTo('#/m/' + encodeURIComponent(key || KEY));
  await app.flush();
  return app.strictEl('machine-body').innerHTML;
}

/** Put two real PDFs in the browser's own store, as a finished run would. */
async function seedReports(app) {
  await app.sandbox.putReport(KEY, T1, new app.sandbox.Blob(['%PDF-1.7 one']));
  await app.sandbox.putReport(KEY, T2, new app.sandbox.Blob(['%PDF-1.7 two']));
}

// ── C10 · the four columns ───────────────────────────────────────────────

test('C10: every row carries its time, file, call and confidence', async () => {
  const app = machinePage();
  const html = await open_(app);
  assert.ok(html.includes('<td>2026-08-01</td>'), 'the date');
  assert.ok(html.includes('14:40'), 'the time — the stranger could not tell two runs apart');
  assert.ok(html.includes('after.csv') && html.includes('before.csv'), 'the file');
  assert.ok(html.includes('Bearing outer-race fault (BPFO)'), 'the committed call');
  assert.ok(html.includes('high') && html.includes('medium'), 'the confidence');
});

test('C10: newest first, so the row an analyst looks for is the top one', async () => {
  const app = machinePage();
  const html = await open_(app);
  assert.ok(html.indexOf('2026-08-01') < html.indexOf('2026-07-01'), 'newest first');
});

test('C10: a reading saved before this session shows dashes, not guesses', async () => {
  const app = machinePage({
    storage: {
      [MACHINES_KEY]: JSON.stringify([card()]),
      [TREND_KEY]: JSON.stringify({ [KEY]: SERIES }),
    },
  });
  const html = await open_(app);
  assert.ok(!html.includes('before.csv'), 'a fact we never recorded was invented');
  assert.ok((html.match(/—/g) || []).length >= 6, 'each unknown fact is an em dash');
  assert.ok(html.includes('1.77') && html.includes('Zone B'),
    'what WAS recorded is still rendered');
});

test('C10: the trend line names the committed fault, not just the fall', async () => {
  const app = machinePage();
  const html = await open_(app);
  assert.ok(/49% below the first reading/.test(html), 'the band is unchanged');
  assert.ok(html.includes('Latest reading: <b>Bearing outer-race fault (BPFO)</b>'), html.slice(0, 900));
  assert.ok(/not the same thing as a fault clearing/.test(html),
    'a falling value must not be readable as a clean bill');
});

test('C10: with nothing recorded the trend card says only what it knows', async () => {
  const app = machinePage({
    storage: {
      [MACHINES_KEY]: JSON.stringify([card()]),
      [TREND_KEY]: JSON.stringify({ [KEY]: SERIES }),
    },
  });
  const html = await open_(app);
  assert.ok(/49% below the first reading/.test(html));
  assert.ok(!html.includes('Latest reading:'), 'a call was invented for a reading that has none');
});

// ── B4 / D-26 · the report on the row ────────────────────────────────────

test('D-26: a held report is offered from THIS browser, never from the server', async () => {
  const app = machinePage();
  await seedReports(app);
  const html = await open_(app);
  assert.ok(html.includes('blob:'), 'no blob URL was made');
  assert.ok(!html.includes('/api/jobs/'), 'the row must not link to a purged job');
  assert.ok(html.includes('>Open</a>') && html.includes('>Save</a>'));
  assert.ok(html.includes('download="report-2026-08-01.pdf"'), html.slice(0, 900));
});

test('D-26: one URL per held report, and the previous set is released', async () => {
  const app = machinePage();
  await seedReports(app);
  await open_(app);
  assert.strictEqual(app.objectUrls().length, 2, 'one per report');
  await open_(app);
  assert.strictEqual(app.objectUrls().length, 4, 'the second render makes its own');
  assert.strictEqual(app.revokedUrls().length, 2, 'the first set leaked');
});

test('D-26: leaving the page releases them', async () => {
  const app = machinePage();
  await seedReports(app);
  await open_(app);
  await app.goTo('#/');
  await app.flush();
  assert.strictEqual(app.revokedUrls().length, 2, 'a blob URL outlived its page');
});

test('D-26: a browser with no IndexedDB renders the page and offers no report', async () => {
  const app = machinePage({ indexedDB: false });
  const html = await open_(app);
  assert.ok(html.includes('Bearing outer-race fault'), 'the rest of the row must still render');
  assert.ok(!html.includes('blob:'));
});

test('D-26: deleting a reading from the page takes its report', async () => {
  const app = machinePage();
  await seedReports(app);
  await open_(app);
  // The row's own two-step confirmation, driven the way a click does.
  app.strictEl('view-machine').dispatchEvent({
    type: 'click', target: { dataset: { ryes: T1 } },
  });
  await app.flush();
  assert.deepStrictEqual(Object.keys(await app.sandbox.reportsFor(KEY)), [T2]);
  const facts = JSON.parse(app.sandbox.localStorage.getItem(REPORTS_KEY))[KEY];
  assert.strictEqual(facts[T1], undefined, 'the facts outlived the reading');
});

// ── C11 · one verb, one word ─────────────────────────────────────────────

test('C11: the machine page says Delete on the browser backend too', async () => {
  const app = machinePage();
  const html = await open_(app);
  assert.ok(html.includes('>Delete machine</button>'), html.slice(-600));
  assert.ok(!/Forget/.test(html), 'the softer word is back');
});

test('C11: the typed confirmation names the reports that go with it', async () => {
  const app = machinePage();
  await seedReports(app);
  await open_(app);
  app.strictEl('view-machine').dispatchEvent({ type: 'click', target: { id: 'm-delete' } });
  await app.flush();
  const html = app.strictEl('machine-body').innerHTML;
  assert.ok(html.includes('Type the machine’s alias to confirm'), 'the guard is gone');
  assert.ok(/2 saved reports kept with them go too/.test(html), html.slice(0, 900));
});

test('C11: an empty field says Not stated, the same word the form uses', async () => {
  const app = machinePage({
    storage: {
      [MACHINES_KEY]: JSON.stringify([card({ bearing_model: '', coupling: '' })]),
      [TREND_KEY]: JSON.stringify({ [KEY]: SERIES }),
    },
  });
  const html = await open_(app);
  assert.ok(html.includes('Not stated'));
  assert.ok(!html.includes('Not recorded'), 'two words for one blank, two screens apart');
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
