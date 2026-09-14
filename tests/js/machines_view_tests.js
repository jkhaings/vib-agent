/**
 * Session UX-1: the machines views, run in node against the REAL app.js
 * (see harness.js). Driven by pytest — tests/test_ux1_views.py — so `pytest`
 * remains the one command.
 *
 * The claims under test are the ones a python test cannot make, because they
 * are about a store on the analyst's own device and a view switch a browser
 * performs:
 *
 *   * ONE adapter answers for both backends. The same renderers run over
 *     localStorage and over /api/machines, and the db half is exercised here
 *     against a scripted fetch — a shape that drifts does not fail loudly, it
 *     renders a slightly different machine page on one backend.
 *   * A failed list is NEVER an empty list. "You have no machines" is a claim,
 *     and a 401 or a 500 has not earned it.
 *   * Hiding goes through setHidden, so a class that declares a `display`
 *     cannot leave a view on screen — the Session HIST-2-FIX-2 defect, which
 *     passed every property-reading test while showing in Chrome.
 *   * A zone the browser never recorded renders `unrated`, never `A`.
 *   * An UNNAMED machine is in no list, so HIST-1's rule survives the new
 *     surface that would otherwise re-expose it.
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

const CARD_FIELDS = ['machine_alias', 'rpm', 'iso_group', 'iso_support', 'bearing_model',
  'velocity_unit', 'detection_type', 'mode', 'direction',
  'sensor_sensitivity_mv_per_g', 'fmax_hz', 'spectral_lines', 'window_type',
  'averages', 'integration',
  'measurement_location', 'coupling', 'blades', 'drive_type', 'poles', 'line_freq_hz',
  'rotor_bars', 'gear_teeth_driving', 'gear_teeth_driven',
  'drive_pulley_mm', 'driven_pulley_mm', 'pulley_center_distance_mm'];

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

/** A browser that has one machine with two readings. */
function seeded(extra) {
  return Object.assign({
    [MACHINES_KEY]: JSON.stringify([card()]),
    [TREND_KEY]: JSON.stringify({ 'Pump A|Motor DE': SERIES }),
  }, extra || {});
}

const noFetch = () => { throw new Error('the browser backend must not make a request'); };

function browserApp(opts) {
  return loadApp(Object.assign({ fetchImpl: noFetch }, opts || {}));
}

const body = (app, id) => app.getEl(id).innerHTML;

// ── the browser adapter ───────────────────────────────────────────────────

test('a saved card and its trend become one machine', async () => {
  const app = browserApp({ storage: seeded() });
  const machines = app.sandbox.browserMachines();
  assert.strictEqual(machines.length, 1);
  assert.strictEqual(machines[0].id, 'Pump A|Motor DE');
  assert.strictEqual(machines[0].alias, 'Pump A');
  assert.strictEqual(machines[0].location, 'Motor DE');
  assert.strictEqual(machines[0].readings.length, 2);
  assert.strictEqual(machines[0].card.bearing_model, '6206');
});

test('a card with no trend is still a machine', async () => {
  const app = browserApp({ storage: { [MACHINES_KEY]: JSON.stringify([card()]) } });
  const machines = app.sandbox.browserMachines();
  assert.strictEqual(machines.length, 1, 'a machine you typed but never analysed must be findable');
  // `.length`, not deepStrictEqual: an array literal from inside the vm realm
  // is never reference-equal to a host `[]` (SESSION_HIST1.md F-4).
  assert.strictEqual(machines[0].readings.length, 0);
});

test('a trend with no card is still a machine', async () => {
  // Saved readings without ticking Remember. Dropping these would hide
  // readings the retention ledger promises are being kept.
  const app = browserApp({
    storage: { [TREND_KEY]: JSON.stringify({ 'Fan 2|Brg 1': SERIES }) },
  });
  const machines = app.sandbox.browserMachines();
  assert.strictEqual(machines.length, 1);
  assert.strictEqual(machines[0].alias, 'Fan 2');
  assert.strictEqual(machines[0].location, 'Brg 1');
  assert.strictEqual(machines[0].card, null);
  assert.strictEqual(machines[0].readings.length, 2);
});

test('an unnamed machine is in no list, from either store', async () => {
  const app = browserApp({
    storage: {
      [MACHINES_KEY]: JSON.stringify([card({ machine_alias: 'Unnamed machine' })]),
      [TREND_KEY]: JSON.stringify({ 'Unnamed machine|Motor DE': SERIES, '|X': SERIES }),
    },
  });
  assert.strictEqual(app.sandbox.browserMachines().length, 0,
    'HIST-1 withheld the trend from an unnamed machine so two could not merge; listing them undoes it');
});

test('machines sort by alias then location, the way the server orders them', async () => {
  const app = browserApp({
    storage: {
      [TREND_KEY]: JSON.stringify({
        'Pump A|Pump NDE': SERIES, 'Fan 2|Brg 1': SERIES, 'Pump A|Motor DE': SERIES,
      }),
    },
  });
  assert.strictEqual(app.sandbox.browserMachines().map((m) => m.id).join(' / '),
    'Fan 2|Brg 1 / Pump A|Motor DE / Pump A|Pump NDE');
});

test('a corrupt store is an empty list, not a crash', async () => {
  const app = browserApp({
    storage: { [MACHINES_KEY]: 'not json at all', [TREND_KEY]: '[1,2,3]' },
  });
  assert.strictEqual(app.sandbox.browserMachines().length, 0);
});

// ── routing ───────────────────────────────────────────────────────────────

test('the default route shows machines and hides the upload form', async () => {
  const app = browserApp({ storage: seeded() });
  await app.flush();
  assert.strictEqual(app.visible('view-machines'), true);
  assert.strictEqual(app.visible('view-machine'), false);
  assert.strictEqual(app.visible('form-card'), false);
  assert.strictEqual(app.visible('state'), false);
});

test('#/new shows the upload form and hides the machines list', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/new');
  assert.strictEqual(app.visible('form-card'), true);
  assert.strictEqual(app.visible('view-machines'), false);
  assert.strictEqual(app.visible('view-machine'), false);
});

test('#/m/<id> shows one machine and nothing else', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/' + encodeURIComponent('Pump A|Motor DE'));
  assert.strictEqual(app.visible('view-machine'), true);
  assert.strictEqual(app.visible('view-machines'), false);
  assert.strictEqual(app.visible('form-card'), false);
  assert.strictEqual(app.getEl('machine-head').textContent, 'Pump A · Motor DE');
});

test('a plain anchor hash is not a route and lands on home', async () => {
  // `#showcase` is a link ON the landing, which lives in the home view.
  const app = browserApp({ storage: {} });
  await app.goTo('#showcase');
  assert.strictEqual(app.visible('view-machines'), true);
  assert.strictEqual(app.visible('form-card'), false);
});

test('a per-person invite link still lands on the form', async () => {
  // Session F2: `/?code=XXXX` is "one tap from a run". The form is a VIEW now,
  // so without this the code is prefilled into a field the analyst cannot see.
  const app = browserApp({ storage: {}, search: '?code=demo-code' });
  await app.flush();
  assert.strictEqual(app.visible('form-card'), true);
  assert.strictEqual(app.visible('view-machines'), false);
  assert.strictEqual(app.getEl('code').value, 'demo-code');
});

test('and a plain visit with machines still lands on the list', async () => {
  const app = browserApp({ storage: seeded() });
  await app.flush();
  assert.strictEqual(app.visible('view-machines'), true);
  assert.strictEqual(app.visible('form-card'), false);
});

test('an id containing the separator survives the round trip', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/' + encodeURIComponent('Pump A|Motor DE'));
  assert.strictEqual(app.sandbox.parseRoute().id, 'Pump A|Motor DE');
});

test('every view switch is a real hide, not a defeated property', async () => {
  // The Session HIST-2-FIX-2 defect: `hidden` alone is outranked by any author
  // rule declaring a `display`, and `.hero` declares one.
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/new');
  assert.strictEqual(app.getEl('hero').style.display, 'none');
  assert.strictEqual(app.getEl('view-machines').style.display, 'none');
  assert.strictEqual(app.getEl('form-card').style.display, '');
});

test('a job finishing on another view does not paint its card there', async () => {
  // The state card belongs to the upload view. Polling calls `show()` on every
  // status tick, so without this a report that lands while the analyst is
  // reading their machines drops a status card onto the machines list.
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/');
  app.sandbox.show('<p>Report ready.</p>');
  assert.strictEqual(app.visible('state'), false, 'not on the machines view');
  await app.goTo('#/new');
  assert.strictEqual(app.visible('state'), true, 'but waiting when they come back');
  assert.ok(app.getEl('state').innerHTML.includes('Report ready.'));
});

// ── the empty state, and the landing that goes with it ────────────────────

test('with machines, the landing is gone and the cards are there', async () => {
  const app = browserApp({ storage: seeded() });
  await app.flush();
  assert.strictEqual(app.visible('hero'), false, 'a returning analyst gets their machines, not the pitch');
  assert.strictEqual(app.visible('showcase'), false);
  assert.ok(body(app, 'machines-body').includes('Pump A'));
});

test('with no machines, the landing is shown and the panel says what to do', async () => {
  const app = browserApp({ storage: {} });
  await app.flush();
  assert.strictEqual(app.visible('hero'), true);
  const html = body(app, 'machines-body');
  assert.ok(html.includes('No machines yet'));
  // Session UX-4 cut this from four lines to one, and moved the two ACTIONS
  // into the panel's header — static markup the harness cannot read, pinned at
  // the source in tests/test_ux4_copy.py. This assertion used to require the
  // words "Remember this machine", which was the empty state describing a
  // checkbox on another screen; the panel is not the only instruction any more.
  //
  // What must stay true is the reason the original was written: the empty
  // state is never a bare "no results". It still names BOTH ways a machine
  // comes into being, and it is now pinned SHORT as well, which the phrase
  // match could not do.
  assert.ok(/add one/i.test(html), 'the empty state stopped naming the direct route');
  assert.ok(/analyse a file/i.test(html), 'the empty state stopped naming the on-the-way route');
  assert.ok(html.length < 260, `the empty state is long again (${html.length} chars)`);
});

// ── the machine page ──────────────────────────────────────────────────────

test('the readings timeline carries date, value and zone, newest first', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/' + encodeURIComponent('Pump A|Motor DE'));
  const html = body(app, 'machine-body');
  assert.ok(html.includes('<th scope="col">Date</th>'));
  assert.ok(html.includes('2026-08-01'));
  assert.ok(html.includes('2026-07-01'));
  assert.ok(html.indexOf('2026-08-01') < html.indexOf('2026-07-01'), 'newest first');
  assert.ok(html.includes('2.40') && html.includes('2.00'));
  assert.ok(html.includes('Zone B') && html.includes('Zone A'));
});

test('the date is the ISO day, not a locale rendering', async () => {
  // `toLocaleDateString` varies with the machine's locale and timezone, which
  // would make this suite depend on the box it runs on.
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/' + encodeURIComponent('Pump A|Motor DE'));
  assert.ok(body(app, 'machine-body').includes('<td>2026-08-01</td>'));
});

test('a zone the browser never recorded is unrated, never A', async () => {
  const app = browserApp({
    storage: {
      [TREND_KEY]: JSON.stringify({
        'Fan 2|Brg 1': [
          { ts: '2026-07-01T10:00:00+00:00', v: 2.0, zone: '', axis: '' },
          { ts: '2026-08-01T10:00:00+00:00', v: 2.4, zone: 'not_assessable', axis: '' },
        ],
      }),
    },
  });
  await app.goTo('#/m/' + encodeURIComponent('Fan 2|Brg 1'));
  const html = body(app, 'machine-body');
  assert.ok(html.includes('unrated'));
  assert.ok(!html.includes('Zone A'), 'a non-verdict must never be rendered as a clean bill');
});

test('the machine page carries HIST-1 trend card verbatim', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/' + encodeURIComponent('Pump A|Motor DE'));
  const html = body(app, 'machine-body');
  assert.ok(html.includes('id="trend-card"'), 'the card is reused, not re-implemented');
  assert.ok(html.includes('Trend for Pump A · Motor DE'));
  assert.ok(html.includes('20% above the first reading'));
});

test('the machine page never links a report to the SERVER', async () => {
  // Session UX-5, RULED D-26. The claim this test was written for is
  // unchanged and is the important half: a job whose report the server has
  // purged must never be linked to, because that link is a 410 dressed as a
  // download. What changed is that the browser now keeps its own copy, so the
  // page can offer THAT -- a blob URL, from this device, with no request.
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/' + encodeURIComponent('Pump A|Motor DE'));
  const html = body(app, 'machine-body');
  assert.ok(!html.includes('report.pdf'), 'a purged job must not be linked to');
  assert.ok(!html.includes('/api/jobs/'), 'the machine page asks the server for nothing');
  assert.ok(html.includes('Reports are kept in this browser'), html.slice(0, 300));
});

test('a reading with no kept report says so, and never guesses', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/' + encodeURIComponent('Pump A|Motor DE'));
  const html = body(app, 'machine-body');
  assert.ok(html.includes('<th scope="col">Report</th>'), 'the column is missing');
  // Two seeded readings, neither with a report: an em dash, not a dead link.
  assert.ok((html.match(/—/g) || []).length >= 2, html.slice(0, 600));
});

test('C10: the table carries time, file, diagnosis and confidence', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/' + encodeURIComponent('Pump A|Motor DE'));
  const html = body(app, 'machine-body');
  for (const col of ['Date', 'Time (UTC)', 'File', 'Overall mm/s RMS', 'ISO zone',
    'Committed diagnosis', 'Confidence', 'Report']) {
    assert.ok(html.includes(`<th scope="col">${col}</th>`), `no ${col} column`);
  }
  // STRANGER C10: "Two rows both dated 2026-09-07 with no time... I can't tell
  // which is before and which is after."
  assert.ok(html.includes('<td class="num">10:00</td>'), html.slice(0, 700));
});

test('the facts row says Not stated rather than inventing one', async () => {
  const app = browserApp({
    storage: { [MACHINES_KEY]: JSON.stringify([card({ bearing_model: '', coupling: '' })]) },
  });
  await app.goTo('#/m/' + encodeURIComponent('Pump A|Motor DE'));
  const html = body(app, 'machine-body');
  assert.ok(html.includes('Group 2 — medium'));
  assert.ok(html.includes('1780 rpm'));
  // Session UX-5 (C11): the form's word for a blank field is "Not stated" and
  // this said "Not recorded" about the same blank, two screens apart.
  assert.ok(html.includes('Not stated'));
  assert.ok(!html.includes('Not recorded'), 'one word for an empty field');
});

test('a machine that is not there says so, and does not render blank', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/' + encodeURIComponent('Ghost|Nowhere'));
  assert.ok(body(app, 'machine-body').includes('not in this list any more'));
  assert.strictEqual(app.getEl('machine-head').textContent, '');
});

// ── prefill ───────────────────────────────────────────────────────────────

test('#/new?m= fills the form from the machine card', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/new?m=' + encodeURIComponent('Pump A|Motor DE'));
  const field = (name) => app.getEl('field:' + name).value;
  assert.strictEqual(field('machine_alias'), 'Pump A');
  assert.strictEqual(field('measurement_location'), 'Motor DE');
  assert.strictEqual(field('rpm'), '1780');
  assert.strictEqual(field('iso_group'), '2');
  assert.strictEqual(field('iso_support'), 'rigid');
  assert.strictEqual(field('bearing_model'), '6206');
  assert.strictEqual(field('coupling'), 'coupled');
  assert.strictEqual(app.getEl('remember').checked, true);
});

test('a machine known only from its trend still fills its identity', async () => {
  const app = browserApp({
    storage: { [TREND_KEY]: JSON.stringify({ 'Fan 2|Brg 1': SERIES }) },
  });
  await app.goTo('#/new?m=' + encodeURIComponent('Fan 2|Brg 1'));
  assert.strictEqual(app.getEl('field:machine_alias').value, 'Fan 2');
  assert.strictEqual(app.getEl('field:measurement_location').value, 'Brg 1');
});

test('the prefilled form knows the readings it is about to send', async () => {
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/new?m=' + encodeURIComponent('Pump A|Motor DE'));
  assert.strictEqual(app.visible('trend-note'), true);
  assert.ok(app.getEl('trend-note').innerHTML.includes('2 saved readings'));
});

// ── the db backend, through the same views ────────────────────────────────

const DB_MACHINE = {
  id: 'abc123',
  alias: 'Pump A',
  location: 'Motor DE',
  card: card(),
  readings: SERIES,
};

test('on the db backend the list comes from /api/machines', async () => {
  const calls = [];
  const app = loadApp({
    accounts: true,
    fetchImpl: (url) => { calls.push(url); return jsonResponse({ machines: [DB_MACHINE] }); },
  });
  await app.flush();
  assert.deepStrictEqual(calls, ['/api/machines']);
  assert.ok(body(app, 'machines-body').includes('Pump A'));
  assert.strictEqual(app.visible('hero'), false);
});

test('the db machine page renders from the same renderers', async () => {
  const app = loadApp({
    accounts: true,
    fetchImpl: (url) => (url === '/api/machines/abc123'
      ? jsonResponse(DB_MACHINE)
      : jsonResponse({ machines: [DB_MACHINE] })),
  });
  await app.goTo('#/m/abc123');
  const html = body(app, 'machine-body');
  assert.strictEqual(app.getEl('machine-head').textContent, 'Pump A · Motor DE');
  assert.ok(html.includes('id="trend-card"'), 'the same trend card, over server readings');
  // The card derives its heading from a `alias|location` key. Handed `m.id` it
  // printed the database ROW ID on this backend and the machine name on the
  // other -- the one place the two were not rendering the same thing, and the
  // assertion above could not see it.
  assert.ok(html.includes('Trend for Pump A · Motor DE'),
    'the db backend must not print a row id where the browser prints a name');
  assert.ok(!html.includes('Trend for abc123'), 'no internal id is shown as a name');
  // The id belongs in the link that carries it, and nowhere a reader looks.
  assert.ok(html.includes('/#/new?m=abc123'));
  assert.ok(html.includes('2026-08-01'));
  assert.ok(html.includes('Reports are kept in this browser'));
});

test('the first paint does not steal focus', async () => {
  // Moving focus to a view heading is right when the analyst NAVIGATED. Doing
  // it on load moves the caret of somebody who has just opened the site.
  let focused = 0;
  const app = browserApp({ storage: seeded() });
  app.getEl('machines-head').focus = () => { focused += 1; };
  app.getEl('machine-head').focus = () => { focused += 1; };
  await app.flush();
  assert.strictEqual(focused, 0, 'a page load is not a navigation');
  await app.goTo('#/m/' + encodeURIComponent('Pump A|Motor DE'));
  assert.strictEqual(focused, 1, 'but a navigation moves focus to the new view');
  // And BACK again: without this, a keyboard user returning to the list is
  // left focused on a link inside a view that is no longer on screen.
  await app.goTo('#/');
  assert.strictEqual(focused, 2, 'returning to the list moves focus to the list');
});

test('a 401 asks the analyst to sign in and is NOT an empty list', async () => {
  const app = loadApp({
    accounts: true,
    fetchImpl: () => jsonResponse({ detail: 'Sign in to run an analysis.' }, { status: 401 }),
  });
  await app.flush();
  const html = body(app, 'machines-body');
  assert.ok(html.includes('Sign in'));
  assert.ok(!html.includes('No machines'), '"you have no machines" is a claim a failed request has not earned');
  assert.strictEqual(app.visible('hero'), true, 'a signed-out visitor is who the landing is for');
});

test('a server error says so, and still does not claim an empty list', async () => {
  const app = loadApp({ accounts: true, fetchImpl: () => jsonResponse({}, { status: 500 }) });
  await app.flush();
  const html = body(app, 'machines-body');
  assert.ok(html.includes('could not be read'));
  assert.ok(!html.includes('No machines'));
});

test('the browser backend never makes a request for machines', async () => {
  // `noFetch` throws; reaching the network here would mean the flag was read
  // from something other than the page the server rendered.
  const app = browserApp({ storage: seeded() });
  await app.goTo('#/m/' + encodeURIComponent('Pump A|Motor DE'));
  assert.strictEqual(app.sandbox.storeMode(), 'browser');
  // `noFetch` throws, so a request would have been caught and rendered as
  // "could not be read" instead of the machine.
  assert.ok(body(app, 'machine-body').includes('1780 rpm'));
});

// ── preview mode still owns the page ──────────────────────────────────────

test('?preview= hides both new views and the router stands down', async () => {
  const app = browserApp({ storage: seeded(), search: '?preview=ready' });
  await app.flush();
  assert.strictEqual(app.visible('view-machines'), false);
  assert.strictEqual(app.visible('view-machine'), false);
  assert.strictEqual(app.visible('hero'), false);
  assert.strictEqual(app.visible('form-card'), false);
  assert.strictEqual(app.visible('state'), true, 'the one state card is the whole point of preview mode');
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
