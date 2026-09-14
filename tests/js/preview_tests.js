/**
 * UX-WIRE U1: every ?preview= state renders, and none of them renders a
 * percentage or a `Verifying` step.
 *
 * The preview map is the operator's review surface -- it is how each state card
 * is looked at without provoking the state for real -- so a key that throws is
 * a card nobody can review. Run against the real app.js in the node harness
 * (see harness.js); driven by pytest via test_poll_resilience.py so `pytest`
 * stays the one command.
 */
const assert = require('assert');
const path = require('path');
const { loadApp } = require(path.join(__dirname, 'harness.js'));

const APP_JS = require('fs').readFileSync(
  path.join(__dirname, '..', '..', 'src', 'vib_agent', 'webapp', 'static', 'app.js'), 'utf8');

// Parsed from the map itself rather than hardcoded, so a key added without a
// preview -- or a preview added without a key -- shows up here as a failure
// rather than as a state nobody ever looked at.
const MAP = APP_JS.split('const map = {')[1].split('\n  };')[0];
const KEYS = [...MAP.matchAll(/^\s+'?([a-z-]+)'?:\s/gm)].map((m) => m[1]);

let failures = 0;
const check = (name, fn) => {
  try { fn(); console.log(`PASS  ${name}`); }
  catch (e) { failures += 1; console.log(`FAIL  ${name}  — ${e.message}`); }
};

check(`the preview map still covers every state (${KEYS.length} keys)`, () => {
  assert.ok(KEYS.length >= 20, `only ${KEYS.length} preview keys found`);
  // The doc comment above the IIFE and the map must not drift apart: the
  // comment is what an operator reads to know what is reviewable.
  const comment = APP_JS.split('// ── display-only preview mode')[1].split('(function preview()')[0];
  for (const k of KEYS) {
    assert.ok(comment.includes(k), `preview key ${k} is missing from the doc comment`);
  }
});

for (const key of KEYS) {
  check(`?preview=${key} renders an honest card`, () => {
    const html = loadApp({ search: `?preview=${key}` }).stateCard.innerHTML;
    assert.ok(html && html.length > 0, 'rendered nothing');
    // §5.1 -- no percentage anywhere. Nothing measures one.
    assert.ok(!/width:\s*\d+%/.test(html), 'renders a percentage-width bar');
    assert.ok(!/class="meter/.test(html), 'renders a meter');
    // §5.2 -- no step that can never be current.
    assert.ok(!/Verifying/.test(html), 'renders a Verifying step');
    // Every card opens with the status line: the state in WORDS, so colour is
    // never the only carrier.
    assert.ok(/class="statusline/.test(html), 'card has no status line');
  });
}

// §5.2's corollary: a step that did NOT run is dashed and grey -- never "done"
// (the meter's lie in a different font) and never "pending" (which implies it
// is still coming). On degraded and gate_fail the drafting pass genuinely did
// not happen.
for (const key of ['degraded', 'stopped']) {
  check(`?preview=${key} marks the drafting step stopped, not done`, () => {
    const html = loadApp({ search: `?preview=${key}` }).stateCard.innerHTML;
    const railHtml = html.match(/<div class="rail">.*?<\/div>/s);
    assert.ok(railHtml, 'no rail on a terminal card');
    assert.ok(/<span class="s stopped">Drafting</.test(railHtml[0]),
      `drafting is not dashed on ${key}: ${railHtml[0]}`);
    assert.ok(!/<span class="s pending">/.test(railHtml[0]),
      'a finished job must have no pending step');
  });
}

check('the rail has five positions and none of them is Verifying', () => {
  const html = loadApp({ search: '?preview=analyzing' }).stateCard.innerHTML;
  const cells = html.match(/<span class="s [a-z]+">/g) || [];
  assert.strictEqual(cells.length, 5, `rail has ${cells.length} steps`);
  for (const label of ['Uploaded', 'Accepted', 'Analysis', 'Drafting', 'Report']) {
    assert.ok(html.includes(label), `rail is missing ${label}`);
  }
});

check('the elapsed clock counts up and is never presented as a countdown', () => {
  const html = loadApp({ search: '?preview=queued' }).stateCard.innerHTML;
  assert.ok(/id="el-clock"/.test(html), 'no elapsed clock on a working card');
  assert.ok(/elapsed \d+:\d\d/.test(html), 'the clock does not read as elapsed');
  for (const word of ['remaining', 'left', 'ETA', 'until done']) {
    assert.ok(!html.includes(word), `the clock implies a countdown: ${word}`);
  }
});

// One map check, one per key, two terminal-rail checks, one five-position
// rail check and one clock check: KEYS.length + 5, not + 4. The printed
// total was one short of the checks actually RUN, and
// tests/test_poll_resilience.py asserts `total >= 25` -- so the floor was
// being met by arithmetic rather than by coverage, and deleting one
// preview key would have failed it for the wrong reason. Session UX-2.
console.log(`\n${KEYS.length + 5 - failures}/${KEYS.length + 5} passed`);
process.exit(failures ? 1 : 0);
