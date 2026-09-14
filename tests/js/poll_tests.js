/**
 * hotfix-net: behavioural tests for app.js's polling loop, run in node against
 * the real file (see harness.js). Driven by pytest — see
 * tests/test_poll_resilience.py — so `pytest` remains the one command.
 *
 * Every scenario here is one the analyst actually hits on a plant network: a
 * dropped poll, a busy server, a long outage. The claim under test is always
 * the same: a network problem must never be reported as an analysis failure.
 */

const assert = require('assert');
const { loadApp } = require('./harness');

const results = [];
const pending = [];

function jsonResponse(body, { status = 200, headers = {} } = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (name) => headers[name] || headers[name.toLowerCase()] || null },
    json: async () => body,
  };
}

function textResponse(status, headers = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (name) => headers[name] || headers[name.toLowerCase()] || null },
    json: async () => { throw new Error('not json'); },
  };
}

function test(name, fn) {
  pending.push(
    Promise.resolve()
      .then(fn)
      .then(() => results.push(['PASS', name]))
      .catch((err) => results.push(['FAIL', name, err && err.message])),
  );
}

const RUNNING = { state: 'running', phase: 'analyzing' };
const DONE = {
  state: 'done',
  result_summary: { no_findings: false, severity: 'ISO Zone C',
                    faults: [{ label: 'Bearing outer-race fault (BPFO)', confidence: 'high' }] },
};

// ── 1. a flaky network drops polls; the job still completes in the UI ──────
test('a dropped poll is a hiccup, not an error card', async () => {
  let calls = 0;
  const app = loadApp({
    fetchImpl: () => {
      calls += 1;
      if (calls % 3 === 0) throw new Error('network down');   // drop 1 in 3
      return calls >= 8 ? jsonResponse(DONE) : jsonResponse(RUNNING);
    },
  });
  app.sandbox.pollJob('job-flaky');
  await app.advance(120000);

  const html = app.stateCard.innerHTML;
  assert.ok(html.includes('Report ready'), 'the job should finish in the UI');
  assert.ok(!html.includes('Network error'), 'a dropped poll must not say network error');
  assert.ok(!html.includes('Stopped — error'), 'a dropped poll must not show the error card');
  assert.ok(calls >= 8, 'polling should have continued through the drops');
});

test('a dropped poll shows a soft inline status while retrying', async () => {
  let calls = 0;
  const app = loadApp({
    fetchImpl: () => {
      calls += 1;
      if (calls === 2) throw new Error('network down');
      return jsonResponse(RUNNING);
    },
  });
  app.sandbox.pollJob('job-soft');
  await app.advance(2500);                       // first poll ok, second fails
  await app.advance(10);
  const status = app.getEl('poll-status');
  assert.strictEqual(status.textContent, 'connection hiccup — retrying');
  assert.ok(app.stateCard.innerHTML.includes('class="statusline'), 'the step card is still on screen');
});

// ── 2. backoff, and only then an honest lost-contact card ────────────────
test('backoff is 3s then 6s then 12s, not a tight loop', async () => {
  const app = loadApp({ fetchImpl: () => { throw new Error('down'); } });
  app.sandbox.pollJob('job-backoff');
  await app.flush();
  assert.deepStrictEqual(app.pending(), [3000], 'first retry after 3s');
  await app.advance(3000);
  assert.deepStrictEqual(app.pending(), [6000], 'then 6s');
  await app.advance(6000);
  assert.deepStrictEqual(app.pending(), [12000], 'then 12s');
});

test('sustained failure ends on the lost-contact card with a resume button', async () => {
  const app = loadApp({ fetchImpl: () => { throw new Error('down'); } });
  app.sandbox.pollJob('job-lost');
  await app.advance(300000);
  const html = app.stateCard.innerHTML;
  assert.ok(html.includes('Lost contact'), 'must admit it lost contact');
  assert.ok(html.includes('may still be running'), 'must not claim the analysis failed');
  assert.ok(html.includes('id="poll-resume"'), 'must offer a way back');
  assert.ok(html.includes('job-lost'), 'the job id is retained for the resume');
});

test('the resume button recovers a job that is still alive', async () => {
  let mode = 'down';
  const app = loadApp({
    fetchImpl: () => {
      if (mode === 'down') throw new Error('down');
      return jsonResponse(DONE);
    },
  });
  app.sandbox.pollJob('job-resume');
  await app.advance(300000);
  assert.ok(app.stateCard.innerHTML.includes('Lost contact'));

  mode = 'up';
  const button = { id: 'poll-resume', getAttribute: () => 'job-resume' };
  app.stateCard.dispatchEvent({ type: 'click', target: button });
  await app.advance(5000);
  assert.ok(app.stateCard.innerHTML.includes('Report ready'), 'resume should recover the job');
});

// ── 3. a 429 is an honoured wait, never an error ─────────────────────────
test('a 429 on poll backs off by Retry-After instead of failing', async () => {
  let calls = 0;
  const app = loadApp({
    fetchImpl: () => {
      calls += 1;
      if (calls === 1) return textResponse(429, { 'Retry-After': '5' });
      return jsonResponse(DONE);
    },
  });
  app.sandbox.pollJob('job-429');
  await app.flush();
  assert.ok(app.pending().includes(5000), 'Retry-After: 5 must be honoured exactly');
  assert.ok(!app.stateCard.innerHTML.includes('Stopped — error'), 'a 429 is not an error card');
  await app.advance(6000);
  assert.ok(app.stateCard.innerHTML.includes('Report ready'), 'and then it completes');
});

test('an absurd Retry-After is clamped', async () => {
  const app = loadApp({
    fetchImpl: () => textResponse(429, { 'Retry-After': '99999' }),
  });
  app.sandbox.pollJob('job-clamp');
  await app.flush();
  const waits = app.pending().filter((ms) => ms > 1000);
  assert.ok(waits.every((ms) => ms <= 300000), 'never wait more than 5 minutes: ' + waits);
});

// ── 4. the confirm pause polls slowly ────────────────────────────────────
test('awaiting_confirm drops the poll interval to 20s', async () => {
  const interpretation = {
    multi: false, usable: 1, files: [{
      slot: 1, label: 'Radial – horizontal', direction: 'radial_h', source: 'inference',
      status: 'ok', headline: 'mm/s RMS spectrum', x_axis: 'Hz', rpm: 1800,
      rpm_from: 'the form', severity_available: true, ignored_columns: 0, from_cache: false,
      editable: { velocity_unit: 'mm_s', detection_type: 'rms', rpm: 1800 },
    }],
  };
  const app = loadApp({
    fetchImpl: () => jsonResponse({ state: 'awaiting_confirm', interpretation }),
  });
  app.sandbox.pollJob('job-confirm');
  await app.flush();
  assert.deepStrictEqual(app.pending(), [20000], 'the confirm card polls at 20s');
  assert.ok(app.stateCard.innerHTML.includes('Check the interpretation'));
});

test('the normal poll interval is never faster than 2.5s', async () => {
  const app = loadApp({ fetchImpl: () => jsonResponse(RUNNING) });
  app.sandbox.pollJob('job-interval');
  await app.flush();
  assert.deepStrictEqual(app.pending(), [2500]);
});

// ── 5. an expired job is said plainly, not retried forever ───────────────
test('a 410 on poll says the report expired', async () => {
  const app = loadApp({ fetchImpl: () => textResponse(410) });
  app.sandbox.pollJob('job-gone');
  await app.advance(1000);
  assert.ok(app.stateCard.innerHTML.includes('no longer on the server'));
  assert.deepStrictEqual(app.pending(), [], 'and stops polling');
});

// ── 6. non-JSON (a proxy error page) is transient, not fatal ─────────────
test('a non-JSON 200 is retried rather than shown as an error', async () => {
  let calls = 0;
  const app = loadApp({
    fetchImpl: () => {
      calls += 1;
      return calls === 1 ? textResponse(200) : jsonResponse(DONE);
    },
  });
  app.sandbox.pollJob('job-html');
  await app.advance(10000);
  assert.ok(app.stateCard.innerHTML.includes('Report ready'));
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
