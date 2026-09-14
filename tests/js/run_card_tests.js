/**
 * Session UX-3: the run, as ONE component, run in node against the real app.js
 * (see harness.js). Driven by pytest — tests/test_ux3_progress.py — so
 * `pytest` remains the one command.
 *
 * What is under test is a mapping and two behaviours:
 *
 *   * EVERY state the wire can report draws exactly one card, and the decision
 *     is made in one place. It used to be made twice — in `pollJob`'s tail and
 *     in `submitJob`'s error branch — so a state handled in one and not the
 *     other was a card nobody would notice was missing.
 *   * A RETRYABLE failure offers a retry that resubmits the same inputs.
 *     SESSION_RENDERPROC.md §6: a render child killed by SIGSEGV/SIGBUS
 *     becomes `internal_error` — server_error, retryable, "the file was fine".
 *     The card said exactly that and gave the analyst nothing to press.
 *   * FOCUS LANDS ON THE OUTCOME, once. `#state` is aria-live, which serves
 *     the analyst who is listening; the keyboard needs the other half. And it
 *     must move once per state, because `show()` rewrites this card on every
 *     poll and a card that grabs focus every 2.5 seconds cannot be used.
 *
 * The component reads NOTHING but the wire: `state`, `phase`, `failure_kind`,
 * `retryable`, `degraded_reason`. `rendering` is not among them and is not
 * invented — no phase publishes it (jobs.py), so the render is named in the
 * Drafting copy instead of drawn as a step that could only ever be lit in the
 * past tense.
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { loadApp } = require('./harness');

const APP_JS = fs.readFileSync(path.join(__dirname, '..', '..', 'src', 'vib_agent',
  'webapp', 'static', 'app.js'), 'utf8');
//: Parsed from the preview map itself, the way `preview_tests.js` does, so a
//: card added without a focus target shows up as a failure rather than as a
//: state nobody checked.
const PREVIEW_KEYS = [...APP_JS.split('const map = {')[1].split('\n  };')[0]
  .matchAll(/^\s+'?([a-z-]+)'?:\s/gm)].map((m) => m[1]);

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

const RESULT = { no_findings: false, severity: 'ISO Zone C',
  faults: [{ label: 'Bearing outer-race fault (BPFO)', confidence: 'high' }] };
const GATE = { reasons: ['Speed check failed.'], collect: ['Re-measure at load.'] };

const noFetch = () => { throw new Error('this test makes no request'); };
const app = (opts) => loadApp(Object.assign({ fetchImpl: noFetch, hash: '#/new' }, opts || {}));

/** Render one wire payload through the component and hand back the card. */
function drawn(payload, opts) {
  const a = app(opts);
  a.sandbox.showRun('job-1', payload);
  return { app: a, html: a.stateCard.innerHTML };
}

// ── 1. the mapping: one card per state the wire can report ────────────────

test('queued draws the working card, not an outcome', () => {
  const { html } = drawn({ state: 'queued' });
  assert.ok(html.includes('Accepted — preparing your file.'), html.slice(0, 200));
  assert.ok(!html.includes('Report ready'), 'a queued job must not claim a report');
});

test('running/analyzing and running/drafting are two different cards', () => {
  const a = drawn({ state: 'running', phase: 'analyzing' }).html;
  const d = drawn({ state: 'running', phase: 'drafting' }).html;
  assert.ok(a.includes('Computing — deterministic pipeline.'), a.slice(0, 200));
  assert.ok(d.includes('Drafting the narrative'), d.slice(0, 200));
  assert.notStrictEqual(a, d);
});

test('the drafting copy names the render, and no rail step claims it', () => {
  const { html } = drawn({ state: 'running', phase: 'drafting' });
  assert.ok(/rendering your PDF/.test(html), 'the render is never named to the analyst');
  // The rail keeps its five honest positions. A `Rendering` cell could only
  // ever be lit retroactively — the objection that removed `Verifying`.
  const cells = html.match(/<span class="s [a-z]+">/g) || [];
  assert.strictEqual(cells.length, 5, `rail has ${cells.length} positions`);
  assert.ok(!/Rendering<|>Rendering/.test(html), 'a Rendering STEP is being drawn');
  assert.ok(!/Verifying/.test(html));
});

test('done draws the report page and degraded says it is the deterministic one', () => {
  const done = drawn({ state: 'done', result_summary: RESULT }).html;
  const deg = drawn({ state: 'degraded', result_summary: RESULT,
    degraded_reason: 'spend_budget' }).html;
  assert.ok(done.includes('Report ready</div>') || done.includes('Report ready'), done.slice(0, 200));
  assert.ok(done.includes('/api/jobs/job-1/report.pdf'));
  assert.ok(deg.includes('Report ready — deterministic'), deg.slice(0, 200));
  assert.ok(deg.includes('drafting budget'), 'the degraded reason is not explained');
});

test('gate_fail draws the stopped card with the gate’s own reasons', () => {
  const { html } = drawn({ state: 'gate_fail', gate_summary: GATE });
  assert.ok(html.includes('Analysis stopped before diagnosis.'), html.slice(0, 200));
  assert.ok(html.includes('Speed check failed.'));
  assert.ok(html.includes('Re-measure at load.'));
});

test('error draws the error card, branching on the wire’s failure_kind', () => {
  const ours = drawn({ state: 'error', safe_message: 'It broke.',
    failure_kind: 'server_error', retryable: true }).html;
  const theirs = drawn({ state: 'error', safe_message: 'Unreadable.',
    failure_kind: 'bad_upload', retryable: false }).html;
  assert.ok(ours.includes('Nothing was wrong with your file'), ours.slice(0, 300));
  assert.ok(theirs.includes('gives the same answer'), theirs.slice(0, 400));
  assert.ok(!theirs.includes('Nothing was wrong with your file'));
});

test('awaiting_confirm draws the confirm card, not a working card', () => {
  const { html } = drawn({ state: 'awaiting_confirm', interpretation: {
    multi: false, usable: 1, slot: 1, label: 'Radial – horizontal', direction: 'radial_h',
    source: 'inference', status: 'ok', kind: 'spectrum', headline: 'mm/s RMS spectrum',
    x_axis: 'Hz', amplitude_unit: 'mm_s', amplitude_label: 'mm/s', detection: 'rms',
    rpm: 1785, rpm_from: 'the file header', severity_available: true, from_cache: false,
    ignored_columns: 0, editable: { velocity_unit: 'mm_s', detection_type: 'rms', rpm: 1785 },
    files: [{ slot: 1, label: 'Radial – horizontal', direction: 'radial_h',
      source: 'inference', status: 'ok', kind: 'spectrum', headline: 'mm/s RMS spectrum',
      x_axis: 'Hz', amplitude_unit: 'mm_s', amplitude_label: 'mm/s', detection: 'rms',
      rpm: 1785, rpm_from: 'the file header', severity_available: true, from_cache: false,
      ignored_columns: 0, editable: { velocity_unit: 'mm_s', detection_type: 'rms', rpm: 1785 } }],
  } });
  assert.ok(html.includes('Check the interpretation'), html.slice(0, 200));
});

test('a state this build does not know NEVER draws a report card', () => {
  const { html } = drawn({ state: 'transcending', result_summary: RESULT });
  assert.ok(!html.includes('Report ready'), 'an unknown state announced a result');
  assert.ok(!html.includes('report.pdf'), 'an unknown state offered a download');
  assert.ok(html.includes('Accepted — preparing your file.'), 'it should keep showing work');
});

// ── 2. retry: offered on retryable, and on nothing else ───────────────────

test('a retryable failure offers Try again; a non-retryable one does not', () => {
  const yes = drawn({ state: 'error', safe_message: 'Our fault.',
    failure_kind: 'server_error', retryable: true }).html;
  const no = drawn({ state: 'error', safe_message: 'Your file.',
    failure_kind: 'bad_upload', retryable: false }).html;
  assert.ok(yes.includes('id="post-retry"'), 'a retryable failure has no retry control');
  assert.ok(!no.includes('id="post-retry"'), 'a non-retryable failure offered a pointless retry');
});

test('a failure the wire said nothing about offers no retry either', () => {
  // `submitJob`'s 4xx branch calls errorCard with no kind and no flag: the
  // server has told us what is wrong with the REQUEST, and re-sending it
  // unchanged fails again. Gated on `=== true`, never on `!== false`.
  const app_ = app();
  const html = app_.sandbox.errorCard('That upload was malformed.');
  assert.ok(!html.includes('id="post-retry"'), 'an unclassified failure offered a retry');
});

test('a render crash reads as ours, retryable, and never blames the file', () => {
  // The exact wire a RenderChildCrashed produces: app.py::_run_guarded maps it
  // to `internal_error`, whose taxonomy row is (server_error, retryable=True).
  const { html } = drawn({ state: 'error', failure_kind: 'server_error', retryable: true,
    safe_message: 'The analysis failed on our side; the file was fine.' });
  assert.ok(html.includes('id="post-retry"'), 'a retryable crash gives nothing to press');
  assert.ok(html.includes('Nothing was wrong with your file'));
  for (const blame of ['template', 'unsupported', 'reformat', 'could not be read']) {
    assert.ok(!html.includes(blame), `a crash of ours blames the file: ${blame}`);
  }
});

test('pressing Try again re-POSTs, and the file is still the one that failed', async () => {
  const calls = [];
  const a = loadApp({
    hash: '#/new',
    fetchImpl: (url, init) => {
      calls.push([url, (init && init.method) || 'GET']);
      if (url === '/api/jobs') return jsonResponse({ job_id: 'job-2' });
      return jsonResponse({ state: 'running', phase: 'analyzing' });
    },
  });
  const file = new a.sandbox.File(['freq_hz,amplitude\n10,1\n'], 'route.csv');
  a.getEl('file').files = [file];
  a.getEl('field:rpm').value = '1780';
  a.sandbox.showRun('job-1', { state: 'error', failure_kind: 'server_error', retryable: true,
    safe_message: 'Ours.' });
  a.stateCard.dispatchEvent({ type: 'click', target: { id: 'post-retry' } });
  await a.flush();
  const posts = calls.filter(([url, method]) => url === '/api/jobs' && method === 'POST');
  assert.strictEqual(posts.length, 1, `expected one re-POST, saw ${JSON.stringify(calls)}`);
  // "the same inputs" is the claim, so the inputs must still be there: the
  // error path never calls clearFileSlots, which is reached only from
  // `#again` and `#confirm-cancel`.
  assert.strictEqual(a.getEl('file').files[0], file, 'the failed file was cleared');
  assert.strictEqual(a.getEl('field:rpm').value, '1780');
});

// ── 3. focus lands on the outcome, once ──────────────────────────────────

const focusCount = (a) => a.getEl('run-status')._focused;

test('every card carries the focus target in its markup', () => {
  for (const payload of [{ state: 'queued' }, { state: 'running', phase: 'drafting' },
    { state: 'done', result_summary: RESULT }, { state: 'gate_fail', gate_summary: GATE },
    { state: 'error', failure_kind: 'bad_upload', retryable: false }]) {
    const { html } = drawn(payload);
    assert.ok(html.includes('id="run-status"'),
      `${payload.state} renders no focus target: ${html.slice(0, 160)}`);
    assert.ok(html.includes('tabindex="-1"'), `${payload.state} target is not focusable`);
  }
});

test('a working state does NOT take focus', () => {
  for (const payload of [{ state: 'queued' }, { state: 'running', phase: 'analyzing' },
    { state: 'running', phase: 'drafting' }]) {
    const { app: a } = drawn(payload);
    assert.strictEqual(focusCount(a), 0, `${payload.state}/${payload.phase} stole focus`);
  }
});

test('each outcome takes focus exactly once, however many polls repeat it', () => {
  for (const payload of [{ state: 'done', result_summary: RESULT },
    { state: 'degraded', result_summary: RESULT, degraded_reason: 'draft_failure' },
    { state: 'gate_fail', gate_summary: GATE },
    { state: 'error', failure_kind: 'server_error', retryable: true }]) {
    const a = app();
    a.sandbox.showRun('job-1', payload);
    assert.strictEqual(focusCount(a), 1, `${payload.state} did not take focus`);
    a.sandbox.showRun('job-1', payload);
    a.sandbox.showRun('job-1', payload);
    assert.strictEqual(focusCount(a), 1, `${payload.state} grabbed focus on a re-render`);
  }
});

test('a run that goes queued → running → done takes focus once, at the end', () => {
  const a = app();
  a.sandbox.showRun('job-1', { state: 'queued' });
  a.sandbox.showRun('job-1', { state: 'running', phase: 'analyzing' });
  a.sandbox.showRun('job-1', { state: 'running', phase: 'drafting' });
  assert.strictEqual(focusCount(a), 0);
  a.sandbox.showRun('job-1', { state: 'done', result_summary: RESULT });
  assert.strictEqual(focusCount(a), 1);
});

test('a paused job takes focus too — it is waiting for a decision', () => {
  const a = app();
  a.sandbox.showRun('job-1', { state: 'awaiting_confirm', interpretation: {
    multi: false, usable: 1, files: [{ slot: 1, label: 'Radial – horizontal',
      direction: 'radial_h', source: 'inference', status: 'ok', kind: 'spectrum',
      headline: 'mm/s RMS spectrum', x_axis: 'Hz', amplitude_unit: 'mm_s',
      amplitude_label: 'mm/s', detection: 'rms', rpm: 1785, rpm_from: 'the file header',
      severity_available: true, from_cache: false, ignored_columns: 0,
      editable: { velocity_unit: 'mm_s', detection_type: 'rms', rpm: 1785 } }] } });
  assert.strictEqual(focusCount(a), 1);
  // ...and the outcome that follows it is a DIFFERENT state, so it announces
  // itself rather than being swallowed by the pause that came before.
  a.sandbox.showRun('job-1', { state: 'done', result_summary: RESULT });
  assert.strictEqual(focusCount(a), 2);
});

test('a retry is a new run, and its outcome is announced again', () => {
  const a = app();
  a.sandbox.showRun('job-1', { state: 'error', failure_kind: 'server_error', retryable: true });
  assert.strictEqual(focusCount(a), 1);
  // What a retry actually does: POST, then paint the queued card. That queued
  // state is the signal a new run began — asserted through the real sequence
  // rather than by poking the flag, because the sequence is the claim.
  a.sandbox.markRunStart();
  a.sandbox.showRun('job-2', { state: 'queued' });
  assert.strictEqual(focusCount(a), 1, 'a queued card must not take focus');
  a.sandbox.showRun('job-2', { state: 'error', failure_kind: 'server_error', retryable: true });
  assert.strictEqual(focusCount(a), 2, 'the second failure was never announced');
});

// ── 4. the loop still drives the component ───────────────────────────────

test('the polling loop reaches an outcome through the component', async () => {
  let polls = 0;
  const a = loadApp({
    hash: '#/new',
    fetchImpl: () => {
      polls += 1;
      return polls >= 3
        ? jsonResponse({ state: 'done', result_summary: RESULT })
        : jsonResponse({ state: 'running', phase: 'analyzing' });
    },
  });
  a.sandbox.pollJob('job-9');
  await a.advance(20000);
  assert.ok(a.stateCard.innerHTML.includes('Report ready'), a.stateCard.innerHTML.slice(0, 200));
  assert.strictEqual(focusCount(a), 1, 'the finished run never took focus');
});

test('the outcome takes focus AFTER the card is written, never before', () => {
  // `show()` defers its FIRST innerHTML write by a frame. A focus call that
  // does not wait reaches an element that is not in the document yet — and
  // this harness cannot see that on its own, because its rAF runs
  // synchronously. So the frames are queued by hand here, which is the only
  // way to assert the ORDER rather than the outcome.
  //
  // Today the first card of a run is always `queued`, which does not take
  // focus, so the live path happens not to hit it. That is luck, and luck
  // held in place by nothing is what this file is for.
  const a = app();
  const frames = [];
  a.sandbox.requestAnimationFrame = (fn) => { frames.push(fn); return frames.length; };
  assert.strictEqual(a.stateCard.hidden, true, 'the card is not hidden before the first show');
  a.sandbox.showRun('job-1', { state: 'done', result_summary: RESULT });
  assert.strictEqual(a.stateCard.innerHTML, '', 'the first write was not deferred after all');
  assert.strictEqual(focusCount(a), 0, 'focus moved to an element that does not exist yet');
  frames.forEach((fn) => fn());
  assert.ok(a.stateCard.innerHTML.includes('Report ready'), 'the card never rendered');
  assert.strictEqual(focusCount(a), 1, 'focus never landed once the card was there');
});

test('pressing Try again leaves focus on the new card, not on nothing', async () => {
  // The control the analyst just pressed is destroyed by the re-render. Focus
  // would fall back to <body>, so the next Tab would start at the top of the
  // document — a regression introduced by ADDING the button, which is the
  // kind that ships because nobody thinks to look for it.
  const a = loadApp({
    hash: '#/new',
    fetchImpl: (url) => (url === '/api/jobs'
      ? jsonResponse({ job_id: 'job-2' })
      : jsonResponse({ state: 'running', phase: 'analyzing' })),
  });
  a.getEl('file').files = [new a.sandbox.File(['freq_hz,amplitude\n10,1\n'], 'route.csv')];
  a.sandbox.showRun('job-1', { state: 'error', failure_kind: 'server_error', retryable: true });
  const before = focusCount(a);
  a.stateCard.dispatchEvent({ type: 'click', target: { id: 'post-retry' } });
  await a.flush();
  // By now the retry has been accepted and the first poll has landed, so the
  // card is the WORKING one — asserted as "no longer the failure" rather than
  // as one particular working state, which is a race with the poll.
  assert.ok(!a.stateCard.innerHTML.includes('Nothing was wrong with your file'),
    'the retry did not repaint the card');
  assert.ok(a.stateCard.innerHTML.includes('<div class="rail">'), 'no working card rendered');
  assert.strictEqual(focusCount(a), before + 1, 'focus was dropped by the retry');
});

test('every card in the whole preview map has exactly ONE focus target', () => {
  // Not just the cards the component draws: `#run-status` must be unique
  // inside `#state` for `getElementById` to mean anything, and four of these
  // cards (busy, offline, oversize, invite) are rendered by `submitJob`
  // before a job exists at all. `readyCard` is the one that could go wrong --
  // it names `statusLine` twice, as the two arms of a ternary -- so this is
  // asserted on the RENDER rather than on the source.
  assert.ok(PREVIEW_KEYS.length >= 20, `only ${PREVIEW_KEYS.length} preview keys`);
  for (const key of PREVIEW_KEYS) {
    const html = loadApp({ search: `?preview=${key}` }).stateCard.innerHTML;
    assert.strictEqual((html.match(/id="run-status"/g) || []).length, 1,
      `?preview=${key} has the wrong number of focus targets`);
    assert.strictEqual((html.match(/tabindex="-1"/g) || []).length, 1,
      `?preview=${key} has the wrong number of focusable elements`);
  }
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
