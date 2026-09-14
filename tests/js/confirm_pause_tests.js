/**
 * Session UX-3: the confirm lane's PAUSE, as behaviour rather than as a string.
 * Run in node against the real app.js (see harness.js); driven by pytest —
 * tests/test_inference_webapp.py — so `pytest` remains the one command.
 *
 * WHY THIS FILE EXISTS. `test_polling_pauses_on_awaiting_confirm` used to
 * assert two literals were present in app.js:
 *
 *     "data.state === 'awaiting_confirm'"
 *     "show(confirmCard(jobId, data.interpretation))"
 *
 * The second is a spelling of ONE call site, and UX-3 moved that call into the
 * run component without changing a single thing the analyst experiences. The
 * pin went red; the product did not. That is the weakness worth naming: a pin
 * on a call's spelling fails on every refactor that preserves it, and — the
 * half that actually matters — it would have stayed GREEN through any change
 * that kept the line and broke the pause. It could not see the interval, could
 * not see that polling continues, and could not see whether the card was ever
 * reachable.
 *
 * So the claims are made against a running loop instead:
 *
 *   * the confirm card is drawn, and drawn FROM `data.interpretation` — proved
 *     with a marker value that can only have arrived through that object;
 *   * the poll actually SLOWS DOWN, measured off the scheduler, and to the
 *     interval app.js itself declares;
 *   * a pause is not a stop — it keeps asking, so an expiry is still noticed;
 *   * it speeds back up when the job resumes;
 *   * the submit button comes back, because the analyst has to be able to act;
 *   * focus lands on the card, once: it is waiting for a human.
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { loadApp } = require('./harness');

const APP_JS = fs.readFileSync(path.join(__dirname, '..', '..', 'src', 'vib_agent',
  'webapp', 'static', 'app.js'), 'utf8');

/** The two intervals, read from the file under test rather than retyped here.
 *  A magic 20000 in this suite would go on passing if someone set the constant
 *  to 2600 — which is not a pause, and is exactly what this is about. */
const constant = (name) => {
  const m = APP_JS.match(new RegExp(`const ${name} = (\\d+);`));
  assert.ok(m, `${name} is no longer a named constant in app.js`);
  return Number(m[1]);
};
const POLL_INTERVAL_MS = constant('POLL_INTERVAL_MS');
const POLL_CONFIRM_MS = constant('POLL_CONFIRM_MS');

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

//: A marker no template could invent: if it reaches the page, it came through
//: `data.interpretation` and nowhere else.
const MARKER = 'MARKER-INTERPRETATION-4Q7';
const FILE = {
  slot: 1, label: 'Radial – horizontal', direction: 'radial_h', source: 'inference',
  status: 'ok', kind: 'spectrum', headline: MARKER, x_axis: 'Hz', amplitude_unit: 'mm_s',
  amplitude_label: 'mm/s', detection: 'rms', rpm: 1785, rpm_from: 'the file header',
  severity_available: true, from_cache: false, ignored_columns: 0,
  editable: { velocity_unit: 'mm_s', detection_type: 'rms', rpm: 1785 },
};
const INTERPRETATION = Object.assign({ multi: false, usable: 1, files: [FILE] }, FILE);

const PAUSED = { state: 'awaiting_confirm', interpretation: INTERPRETATION };
const RUNNING = { state: 'running', phase: 'analyzing' };

/** Drive `pollJob` with a scripted wire. `script(n)` answers the n-th poll. */
function polling(script) {
  let calls = 0;
  const app = loadApp({
    hash: '#/new',
    fetchImpl: (url) => {
      calls += 1;
      return script(calls, url);
    },
  });
  app.sandbox.pollJob('job-confirm');
  return { app, polls: () => calls };
}

// ── the card ─────────────────────────────────────────────────────────────

test('a paused job draws the confirm card, from data.interpretation', async () => {
  const { app } = polling(() => jsonResponse(PAUSED));
  await app.flush();
  const html = app.stateCard.innerHTML;
  assert.ok(html.includes('Check the interpretation'), html.slice(0, 200));
  assert.ok(html.includes(MARKER),
    'the card was not built from the interpretation the wire sent');
  for (const control of ['c-unit', 'c-det', 'c-rpm', 'confirm-go']) {
    assert.ok(html.includes(control), `the confirm card is missing ${control}`);
  }
  assert.ok(!html.includes('Report ready'), 'a paused job must not claim a report');
});

// ── the pause itself, measured off the scheduler ─────────────────────────

test('the poll SLOWS DOWN while a job waits for a human', async () => {
  const { app } = polling(() => jsonResponse(PAUSED));
  await app.flush();
  assert.deepStrictEqual(app.pending(), [POLL_CONFIRM_MS],
    `a paused job should be re-checked in ${POLL_CONFIRM_MS}ms`);
  assert.ok(POLL_CONFIRM_MS > POLL_INTERVAL_MS,
    'the "pause" interval is not longer than the running one — nothing is paused');
});

test('a running job is re-checked at the ordinary interval — the control', async () => {
  const { app } = polling(() => jsonResponse(RUNNING));
  await app.flush();
  assert.deepStrictEqual(app.pending(), [POLL_INTERVAL_MS],
    'the ordinary interval moved, so the pause comparison means nothing');
});

test('a pause is not a stop — it keeps asking, so an expiry is still noticed', async () => {
  const { app, polls } = polling(() => jsonResponse(PAUSED));
  await app.flush();
  const first = polls();
  await app.advance(POLL_CONFIRM_MS * 3);
  assert.ok(polls() > first, 'polling stopped altogether while the job was paused');
});

test('an expiry DURING the pause is reported, not sat on', async () => {
  const { app } = polling((n) => (n === 1 ? jsonResponse(PAUSED)
    : jsonResponse({ detail: 'gone' }, { status: 410 })));
  await app.flush();
  await app.advance(POLL_CONFIRM_MS);
  assert.ok(app.stateCard.innerHTML.includes('no longer on the server'),
    app.stateCard.innerHTML.slice(0, 200));
});

test('the loop speeds back up once the job resumes', async () => {
  const { app } = polling((n) => jsonResponse(n === 1 ? PAUSED : RUNNING));
  await app.flush();
  assert.deepStrictEqual(app.pending(), [POLL_CONFIRM_MS]);
  await app.advance(POLL_CONFIRM_MS);
  assert.deepStrictEqual(app.pending(), [POLL_INTERVAL_MS],
    'the loop stayed slow after the job resumed');
});

// ── what the analyst can do while it is paused ───────────────────────────

test('the submit button comes back, because the card asks for a decision', async () => {
  const { app } = polling(() => jsonResponse(PAUSED));
  // Asserted as a TRANSITION, from the state `submitJob` leaves behind. The
  // first draft of this check read `getEl('go')` — an id index.html does not
  // have — and the harness invents an element for any id, whose `disabled`
  // defaults to false. It passed while the line under test was deleted.
  // `_known` is the guard: it is false for an element the real markup has no
  // definition for, and `visible()` already refuses to answer about one.
  const submit = app.getEl('submit');
  assert.ok(submit._known, 'index.html has no #submit — this check would be vacuous');
  submit.disabled = true;
  await app.flush();
  assert.strictEqual(submit.disabled, false,
    'the analyst is locked out of the form while being asked a question');
});

test('the paused card takes focus, once, however many times it is re-polled', async () => {
  const { app } = polling(() => jsonResponse(PAUSED));
  await app.flush();
  assert.strictEqual(app.getEl('run-status')._focused, 1,
    'a card waiting for a human never moved focus to itself');
  await app.advance(POLL_CONFIRM_MS * 3);
  assert.strictEqual(app.getEl('run-status')._focused, 1,
    'the card grabbed focus again on every slow poll');
});

Promise.all(pending).then(() => {
  let failures = 0;
  for (const [status, name, why] of results) {
    if (status === 'FAIL') failures += 1;
    console.log(`${status}  ${name}${why ? `  — ${why}` : ''}`);
  }
  console.log(`\n${results.length - failures}/${results.length} passed`);
  process.exit(failures ? 1 : 0);
});
