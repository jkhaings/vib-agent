/**
 * Session HIST-2 — the compare-mode slot behaviour in app.js, run in node
 * against the real file (see harness.js). Driven by pytest —
 * tests/test_compare_webapp.py — so `pytest` remains the one command.
 *
 * These are the rules a TestClient post can never exercise, because they are
 * about what the BROWSER puts on the wire: a revealed-but-unfilled input posts
 * an empty file part, so every slot the mode does not ask about has to be
 * cleared, and a slot-3 file left over from spectrum mode would make a compare
 * post three files and earn a 400 the analyst did not cause. That is the exact
 * class of bug Session E wrote down and this mode re-opens.
 */

const assert = require('assert');
const { loadApp } = require('./harness');

const results = [];

function test(name, fn) {
  try {
    fn();
    results.push(['PASS', name]);
  } catch (err) {
    results.push(['FAIL', name, err && err.message]);
  }
}

/** Load app.js and drive the mode selector the way a person would. */
function withMode(mode, { reveal = false } = {}) {
  const app = loadApp({ fetchImpl: () => { throw new Error('no fetch in these tests'); } });
  const sel = app.getEl('mode');
  sel.value = mode;
  sel.dispatchEvent({ type: 'change' });
  if (reveal) app.getEl('add-channels').click();   // sets channelsRevealed, re-syncs
  return app;
}

// ── every surface the mode selector owns ────────────────────────────────
//
// Session HIST-2-FIX-2. These used to be asserted as `el.hidden === true`,
// which is a property write read back in a DOM with no stylesheet — and it
// passed while Chrome showed the third slot, its Direction select and
// "+ Add a channel" all through compare mode. `app.visible()` asks the
// question a browser answers: it refuses to answer about an element index.html
// does not define, and it knows that an author-origin `display` outranks the
// UA stylesheet's `[hidden]{display:none}` (harness.js::loadMarkup).

const SURFACES = {
  'sel:.extra-channels': 'the second-slot panel',
  'add-channels': 'the "+ Add a channel" control',
  direction_row: "slot 1's Direction control",
  direction_2_row: "slot 2's Direction control",
  direction_3_row: "slot 3's Direction control",
  slot_3_row: 'the third slot',
  'channels-help': 'the three-directions helper',
  'slots-help': 'the in-panel slot helper',
};

//: true = a browser shows it. Note the collapsed spectrum row: the harness does
//: not walk ancestors, so a control inside the closed panel reports its OWN
//: state, which is "shown". That is a real limit of a stub DOM and it is
//: written down rather than papered over — the panel above them is hidden, and
//: only Part C's browser pass can see the result.
const EXPECTED = {
  compare: {
    'sel:.extra-channels': true,
    'add-channels': false,
    direction_row: false,
    direction_2_row: false,
    direction_3_row: false,
    slot_3_row: false,
    'channels-help': false,
    'slots-help': true,
  },
  'spectrum, channels revealed': {
    'sel:.extra-channels': true,
    'add-channels': false,
    direction_row: true,
    direction_2_row: true,
    direction_3_row: true,
    slot_3_row: true,
    'channels-help': true,
    'slots-help': true,
  },
  'spectrum, as the page loads': {
    'sel:.extra-channels': false,
    'add-channels': true,
    direction_row: true,
    direction_2_row: true,
    direction_3_row: true,   // inside the closed panel; see the note above
    slot_3_row: true,        // ditto
    'channels-help': true,
    'slots-help': true,      // ditto
  },
};

function assertSurfaces(app, state) {
  const want = EXPECTED[state];
  Object.keys(want).forEach((key) => {
    assert.strictEqual(
      app.visible(key), want[key],
      `${SURFACES[key]} should be ${want[key] ? 'shown' : 'hidden'} in ${state}`,
    );
  });
}

test('compare leaves every surface in the state the spec names', () => {
  assertSurfaces(withMode('compare'), 'compare');
});

test('spectrum with the channels revealed leaves every surface shown', () => {
  assertSurfaces(withMode('spectrum', { reveal: true }), 'spectrum, channels revealed');
});

test('spectrum as the page loads offers the add-channel control', () => {
  assertSurfaces(withMode('spectrum'), 'spectrum, as the page loads');
});

test('the compare surfaces round-trip: spectrum -> compare -> spectrum', () => {
  const app = withMode('spectrum', { reveal: true });
  assertSurfaces(app, 'spectrum, channels revealed');
  const sel = app.getEl('mode');
  sel.value = 'compare';
  sel.dispatchEvent({ type: 'change' });
  assertSurfaces(app, 'compare');
  sel.value = 'spectrum';
  sel.dispatchEvent({ type: 'change' });
  assertSurfaces(app, 'spectrum, channels revealed');
});

test('hiding is done in a way a stylesheet cannot undo', () => {
  // The specific defect, pinned at its mechanism rather than its symptom.
  // `#slot_3_row` carries `.grid`, which declares a `display` in style.css
  // with NO `[hidden]` guard — so the `hidden` attribute alone leaves it on
  // screen and the inline `display:none` is what actually hides it. If
  // someone drops the inline display and goes back to `el.hidden = true`,
  // this is the test that says so.
  //
  // Session UX-4: `#add-channels` carries `.btn-link`, which used to be the
  // same hazard and is not one any more — the UX-4 layer gave it its
  // `.btn-link[hidden]{display:none}` guard, so `tests/js/harness.js` now
  // models that element correctly. Its inline display is kept regardless
  // (app.js hides both through one call) and is still asserted below. The
  // hazard state is now pinned in BOTH directions rather than asserted as
  // true for both: a `.grid` that acquires a guard and a `.btn-link` that
  // loses one are each a change somebody should have to look at.
  const app = withMode('compare');
  const OUTRANKS_HIDDEN = { slot_3_row: true, 'add-channels': false };
  Object.keys(OUTRANKS_HIDDEN).forEach((id) => {
    const el = app.getEl(id);
    assert.strictEqual(
      el._classes.some((c) => app.markup.displayClasses.has(c)), OUTRANKS_HIDDEN[id],
      `${id}: whether its own CSS out-ranks [hidden] has changed — re-check this test`);
    assert.strictEqual(el.style.display, 'none',
      `${id} relies on the hidden attribute, which its own CSS outranks`);
  });
});

test('every element the mode selector hides exists in index.html', () => {
  // The other half of the browser gap: hiding an id the page does not have is a
  // silent no-op in a stub DOM. `visible()` throws on an unknown element, so
  // this is a direct assertion that each surface is really in the markup.
  const app = withMode('compare');
  Object.keys(SURFACES).forEach((key) => {
    assert.doesNotThrow(() => app.visible(key), `${SURFACES[key]} is not in index.html`);
  });
});

test('a slot-3 file chosen in spectrum mode is cleared when compare is selected', () => {
  // The bug this prevents: three file parts posted, a 400 the analyst did not
  // cause, on a file they had already forgotten choosing.
  const app = loadApp({ fetchImpl: () => { throw new Error('no fetch'); } });
  const third = app.getEl('file_3');
  third.files = [{ name: 'leftover.csv', size: 10 }];
  third.value = 'leftover.csv';
  const sel = app.getEl('mode');
  sel.value = 'compare';
  sel.dispatchEvent({ type: 'change' });
  assert.strictEqual(third.value, '', 'the third slot still holds a file');
  assert.strictEqual(app.getEl('file-name-3').textContent, '', 'the third slot still shows a name');
});

test('the slot copy says which side it is asking for', () => {
  const app = withMode('compare');
  assert.ok(/After/.test(app.getEl('file_2_label').innerHTML),
    'slot 2 is not labelled as the after reading');
  assert.ok(/before/i.test(app.getEl('slots-help').textContent),
    'the help text never says which file is the before reading');
  assert.ok(/before/i.test(app.getEl('slots-eyebrow').textContent),
    'the section eyebrow still describes channels');
});

test('switching back to spectrum restores the channel copy and hides the slots', () => {
  const app = withMode('compare');
  const sel = app.getEl('mode');
  sel.value = 'spectrum';
  sel.dispatchEvent({ type: 'change' });
  assert.strictEqual(app.visible('sel:.extra-channels'), false,
    'the extra slots stay revealed after leaving compare');
  assert.strictEqual(app.visible('direction_row'), true, 'direction stayed hidden');
  assert.strictEqual(app.visible('slot_3_row'), true, 'the third slot stayed hidden');
  assert.strictEqual(app.visible('channels-help'), true,
    'the three-directions helper stayed hidden');
  assert.ok(/channel/i.test(app.getEl('slots-eyebrow').textContent),
    'the eyebrow still reads as before/after');
});

test('trend mode is unchanged by this session', () => {
  const app = withMode('trend');
  assert.strictEqual(app.visible('sel:.extra-channels'), false, 'trend reveals extra slots');
  assert.strictEqual(app.visible('add-channels'), false, 'trend offers "+ Add a channel"');
  assert.strictEqual(app.visible('direction_row'), true, 'trend hid the direction control');
  assert.strictEqual(app.visible('channels-help'), true, 'trend hid the channels helper');
});

for (const [status, name, message] of results) {
  console.log(`${status}  ${name}${message ? ` — ${message}` : ''}`);
}
const passed = results.filter((r) => r[0] === 'PASS').length;
console.log(`\n${passed}/${results.length} passed`);
process.exit(passed === results.length ? 0 : 1);
