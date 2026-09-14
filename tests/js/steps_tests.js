/**
 * Session UX-2: the stepped intake, run in node against the REAL app.js.
 * Driven by pytest — tests/test_ux2_steps.py.
 *
 * The claims a python test cannot make:
 *
 *   * A step is a HASH ROUTE, so Back and Forward work through the form — and
 *     it is CLAMPED, because a File cannot survive a reload and `#/new/4`
 *     typed cold would otherwise paint a review of inputs that are gone.
 *   * The preview draws when a file lands, and REDRAWS when the running speed
 *     arrives, so an analyst who fills the fields in the other order still
 *     gets their shaft markers.
 *   * The unlocks panel reproduces the roster's three states — on, one field
 *     away, and ANSWERED — because "coupled" is an answer the report prints,
 *     not a gap to nag about.
 *   * RULED D-24 is asked before the run, defaults to replace, and the answer
 *     rides to the save.
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { loadApp } = require('./harness');

const CORPUS = path.join(__dirname, '..', 'fixtures', 'intake_adversarial');
const SPECTRUM = fs.readFileSync(path.join(CORPUS, 'crlf.txt'), 'utf8');

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
const noFetch = () => { throw new Error('the intake makes no request until submit'); };

function intake(opts) {
  return loadApp(Object.assign({ fetchImpl: noFetch, hash: '#/new' }, opts || {}));
}

/** Fill in what a step needs, the way an analyst would. */
function giveSpeed(app, rpm) {
  app.getEl('field:rpm').value = String(rpm);
}

function giveFile(app, name, body) {
  app.getEl('file').files = [new app.sandbox.File([body === undefined ? SPECTRUM : body], name)];
}

const shown = (app) => [1, 2, 3, 4].filter((i) => app.visible('step-' + i));
const railOf = (app) => [1, 2, 3, 4].map((i) => app.getEl('rail-' + i).className);

// ── one step at a time, and only the ones the inputs have earned ──────────

test('arriving at #/new shows step 1 and nothing else', async () => {
  const app = intake();
  await app.flush();
  assert.deepStrictEqual(shown(app), [1]);
  assert.strictEqual(app.visible('form-card'), true);
  assert.strictEqual(app.visible('view-machines'), false);
});

test('#/new/4 typed cold is clamped to what the form can actually show', async () => {
  // A File cannot survive a reload, and neither can a typed running speed.
  const app = intake();
  await app.goTo('#/new/4');
  assert.deepStrictEqual(shown(app), [1]);
  assert.strictEqual(app.sandbox.location.hash, '#/new');
});

test('the speed unlocks step 2, and step 4 still clamps to it', async () => {
  const app = intake();
  await app.flush();
  giveSpeed(app, 1780);
  await app.goTo('#/new/2');
  assert.deepStrictEqual(shown(app), [2]);
  await app.goTo('#/new/4');
  assert.deepStrictEqual(shown(app), [2], 'no file yet, so review is not reachable');
});

test('a file and a speed together unlock every step', async () => {
  const app = intake();
  await app.flush();
  giveSpeed(app, 1780);
  giveFile(app, 'route.csv');
  await app.goTo('#/new/3');
  assert.deepStrictEqual(shown(app), [3]);
  await app.goTo('#/new/4');
  assert.deepStrictEqual(shown(app), [4]);
});

test('the rail says where you are and what is still locked', async () => {
  const app = intake();
  await app.flush();
  assert.deepStrictEqual(railOf(app),
    ['s now', 's pending locked', 's pending locked', 's pending locked']);
  giveSpeed(app, 1780);
  giveFile(app, 'route.csv');
  await app.goTo('#/new/3');
  assert.deepStrictEqual(railOf(app), ['s done', 's done', 's now', 's pending']);
  assert.strictEqual(app.getEl('rail-3').getAttribute('aria-current'), 'step');
  assert.strictEqual(app.getEl('rail-1').getAttribute('aria-current'), 'false');
});

test('the Next and Back buttons are the same routes', async () => {
  const app = intake();
  await app.flush();
  giveSpeed(app, 1780);
  giveFile(app, 'route.csv');
  app.getEl('form-card').dispatchEvent({
    type: 'click', target: { dataset: { step: '2' }, preventDefault() {} },
  });
  await app.flush();
  assert.strictEqual(app.sandbox.location.hash, '#/new/2');
  app.getEl('form-card').dispatchEvent({
    type: 'click', target: { dataset: { step: '1' }, preventDefault() {} },
  });
  await app.flush();
  assert.strictEqual(app.sandbox.location.hash, '#/new');
  assert.deepStrictEqual(shown(app), [1]);
});

test('the submit button is not a step button', async () => {
  // The click handler is delegated on the whole card; a target with no
  // data-step must fall through to the form's own submit handler untouched.
  const app = intake();
  await app.flush();
  const before = app.sandbox.location.hash;
  app.getEl('form-card').dispatchEvent({ type: 'click', target: { id: 'submit' } });
  await app.flush();
  assert.strictEqual(app.sandbox.location.hash, before);
});

test('each step moves focus to its own heading', async () => {
  let focused = [];
  const app = intake();
  await app.flush();
  [1, 2, 3, 4].forEach((i) => {
    app.getEl(`step-${i}-head`).focus = () => focused.push(i);
  });
  giveSpeed(app, 1780);
  giveFile(app, 'route.csv');
  focused = [];
  await app.goTo('#/new/2');
  await app.goTo('#/new/3');
  assert.deepStrictEqual(focused, [2, 3], 'a navigation is announced, both times');
});

// ── the preview, wired to the slots ──────────────────────────────────────

test('choosing a file draws it, in this browser, before anything is uploaded', async () => {
  const app = intake();
  await app.flush();
  giveSpeed(app, 1800);
  giveFile(app, 'route.csv');
  app.getEl('file').dispatchEvent({ type: 'change' });
  await app.flush();
  const html = app.getEl('spx-1').innerHTML;
  assert.ok(html.includes('PREVIEW'), html.slice(0, 200));
  assert.ok(html.includes('<polyline'));
  assert.strictEqual((html.match(/class="spx-o"/g) || []).length, 3, '1x/2x/3x at 1800 rpm');
});

test('the markers arrive when the speed does, not only when the file does', async () => {
  // The other filling order. Without the redraw the analyst is left looking at
  // a chart that never gained the markers their speed just earned.
  const app = intake();
  await app.flush();
  giveFile(app, 'route.csv');
  app.getEl('file').dispatchEvent({ type: 'change' });
  await app.flush();
  assert.ok(!app.getEl('spx-1').innerHTML.includes('class="spx-o"'));
  assert.ok(app.getEl('spx-1').innerHTML.includes('Give the running speed'));
  giveSpeed(app, 1800);
  app.getEl('field:rpm').dispatchEvent({ type: 'input' });
  await app.flush();
  assert.strictEqual((app.getEl('spx-1').innerHTML.match(/class="spx-o"/g) || []).length, 3);
});

test('a format we do not draw here says so rather than staying blank', async () => {
  const app = intake();
  await app.flush();
  giveFile(app, 'route.wav', 'RIFFxxxx');
  app.getEl('file').dispatchEvent({ type: 'change' });
  await app.flush();
  assert.ok(app.getEl('spx-1').innerHTML.includes('NO PREVIEW'));
  assert.ok(app.getEl('spx-1').innerHTML.includes('analysed exactly as normal'));
});

test('each channel has its own chart, and the direction sits under it', async () => {
  const html = require('fs').readFileSync(
    path.join(__dirname, '..', '..', 'src', 'vib_agent', 'webapp', 'static', 'index.html'),
    'utf8');
  [1, 2, 3].forEach((i) => {
    const mount = html.indexOf(`id="spx-${i}"`);
    const dir = html.indexOf(i === 1 ? 'id="direction_row"' : `id="direction_${i}_row"`);
    assert.ok(mount > -1 && dir > -1, `slot ${i}`);
    assert.ok(mount < dir, `the direction selector for slot ${i} sits under its chart`);
  });
});

// ── step 3: the roster, read forwards ────────────────────────────────────

const unlocks = (app) => app.getEl('unlocks').innerHTML;

async function atStep3(app) {
  giveSpeed(app, 1780);
  giveFile(app, 'route.csv');
  await app.goTo('#/new/3');
}

test('an unlock is off, and says the one field that turns it on', async () => {
  const app = intake();
  await app.flush();
  await atStep3(app);
  const html = unlocks(app);
  assert.ok(html.includes('Rolling-element bearing faults</b> — off.'), html);
  assert.ok(html.includes('Choose the bearing under'));
  assert.ok(html.includes('Blade and vane pass</b> — off.'));
});

test('naming a bearing turns it on — and states the caveat that comes with it', async () => {
  const app = intake();
  await app.flush();
  app.getEl('field:bearing_model').value = '6206';
  await atStep3(app);
  const html = unlocks(app);
  assert.ok(html.includes('Rolling-element bearing faults</b> — on.'), html);
  assert.ok(html.includes('BPFO, BPFI, BSF and FTF'));
  // Roster row 17: the screen reads the radial channels only. An unlock that
  // promised four frequencies and omitted this would over-promise exactly
  // where the roster is careful.
  assert.ok(html.includes('axial, thrust-loaded fault is still'), html);
});

test('the belt unlock needs all three pulley answers AND the speed', async () => {
  const app = intake();
  await app.flush();
  app.getEl('field:drive_pulley_mm').value = '120';
  app.getEl('field:driven_pulley_mm').value = '250';
  await atStep3(app);
  assert.ok(unlocks(app).includes('Belt and pulley faults</b> — off.'), 'two of three is off');
  app.getEl('field:pulley_center_distance_mm').value = '600';
  await app.goTo('#/new/2');
  await app.goTo('#/new/3');
  assert.ok(unlocks(app).includes('Belt and pulley faults</b> — on.'));
});

test('"coupled" is an ANSWER, not a gap — the roster\'s own third state', async () => {
  const app = intake();
  await app.flush();
  app.getEl('field:coupling').value = 'coupled';
  await atStep3(app);
  const html = unlocks(app);
  assert.ok(html.includes('class="ul answered"'), html);
  assert.ok(html.includes('which is an answer rather than a gap'));
  assert.ok(!html.includes('Bent shaft, on an uncoupled machine</b> — off.'),
    'a machine declared coupled must not be nagged to declare itself uncoupled');
});

test('"not coupled" is the one state that turns the bent-shaft branch on', async () => {
  const app = intake();
  await app.flush();
  app.getEl('field:coupling').value = 'uncoupled';
  await atStep3(app);
  assert.ok(unlocks(app).includes('Bent shaft, on an uncoupled machine</b> — on.'));
});

test('geometry with no detector is a third state, never sold as a screen', async () => {
  const app = intake();
  await app.flush();
  app.getEl('field:gear_teeth_driving').value = '31';
  await atStep3(app);
  const html = unlocks(app);
  assert.ok(html.includes('no detector\n       reads them yet'), html.slice(-700));
  assert.ok(html.includes('Gear mesh, sidebands and hunting tooth'));
  assert.ok(html.includes('They do not turn a check on'));
});

test('nothing supplied means no "recorded" block at all', async () => {
  const app = intake();
  await app.flush();
  await atStep3(app);
  assert.ok(!unlocks(app).includes('no detector'), 'an empty block is not rendered');
});

test('a saved bearing we hold no geometry for is NAMED, not dropped in silence', async () => {
  // A <select> handed a value none of its options carry resets to empty in a
  // real browser. The card is from before this field was closed; the value
  // never worked, and losing it quietly is still the wrong way to say so.
  const app = intake({
    storage: {
      'vib.machines.v2': JSON.stringify([{
        machine_alias: 'Pump A', measurement_location: 'Motor DE', rpm: '1780',
        bearing_model: 'SKF 32222 J2',
      }]),
    },
  });
  await app.flush();
  app.sandbox.applyCard({ machine_alias: 'Pump A', bearing_model: 'SKF 32222 J2' });
  giveSpeed(app, 1780);
  giveFile(app, 'route.csv');
  await app.goTo('#/new/3');
  const html = app.getEl('unlocks').innerHTML;
  assert.ok(html.includes('SKF 32222 J2'), html.slice(0, 400));
  assert.ok(html.includes('not one we hold geometry for'));
});

test('a bearing that IS in the catalogue says nothing of the kind', async () => {
  const app = intake();
  await app.flush();
  app.sandbox.applyCard({ machine_alias: 'Pump A', bearing_model: '6206' });
  giveSpeed(app, 1780);
  giveFile(app, 'route.csv');
  await app.goTo('#/new/3');
  assert.ok(!app.getEl('unlocks').innerHTML.includes('not one we hold geometry for'));
});

// ── step 4: review, and RULED D-24 ───────────────────────────────────────

async function atStep4(app) {
  giveSpeed(app, 1780);
  giveFile(app, 'route.csv');
  await app.goTo('#/new/4');
}

test('the review says what will be analysed', async () => {
  const app = intake();
  await app.flush();
  app.getEl('field:machine_alias').value = 'Pump A';
  app.getEl('field:measurement_location').value = 'Motor DE';
  app.getEl('field:bearing_model').value = '6206';
  await atStep4(app);
  const html = app.getEl('review-body').innerHTML;
  assert.ok(html.includes('Pump A') && html.includes('Motor DE'));
  assert.ok(html.includes('1780 rpm'));
  assert.ok(html.includes('route.csv'));
  assert.ok(html.includes('1 of 4'), 'the count of checks switched on');
});

test('no duplicate, no notice', async () => {
  const app = intake();
  await app.flush();
  app.getEl('field:machine_alias').value = 'Pump A';
  await atStep4(app);
  assert.strictEqual(app.visible('dup-notice'), false);
});

test('D-24: a reading for this machine today is named before the run', async () => {
  const today = new Date().toISOString().slice(0, 10);
  const app = intake({
    storage: {
      [TREND_KEY]: JSON.stringify({
        'Pump A|Motor DE': [{ ts: `${today}T06:00:00+00:00`, v: 1.42, zone: 'B', axis: 'y' }],
      }),
    },
  });
  await app.flush();
  app.getEl('field:machine_alias').value = 'Pump A';
  app.getEl('field:measurement_location').value = 'Motor DE';
  await atStep4(app);
  assert.strictEqual(app.visible('dup-notice'), true);
  const html = app.getEl('dup-notice').innerHTML;
  assert.ok(html.includes('1.42 mm/s RMS on the y axis'), html);
  // RULED D-24 AMENDED (Sep 7). Keep both is the default and replace is the
  // explicit choice -- the reverse of what UX-2 shipped, because a same-day
  // pair is the before/after of one repair, which is the flow this product is
  // sold on. Both halves asserted: what is CHECKED, and what is OFFERED.
  assert.ok(html.includes('value="keep_both" checked'), 'keep both is the default');
  assert.ok(html.includes('value="replace"'), 'replace is still offered');
  assert.ok(!html.includes('value="replace" checked'), 'replace must not be pre-selected');
  // ...and it is asked ONCE. The old copy promised a second ask on the report.
  assert.ok(!/asked again/i.test(html), html);
});

test('D-24 AMENDED: the two choices are in ONE source order, at every width', async () => {
  // STRANGER C7: "On mobile the button order flips (Keep both / Replace it)
  // versus desktop (Replace it / Keep both)." A destructive option that moves
  // under the pointer between widths is how the wrong one gets pressed. The
  // radios are emitted in one order and no rule reverses them -- a claim about
  // the SOURCE, because a stylesheet cannot be asked about a width here.
  const today = new Date().toISOString().slice(0, 10);
  const app = intake({
    storage: {
      [TREND_KEY]: JSON.stringify({
        'Pump A|Motor DE': [{ ts: `${today}T06:00:00+00:00`, v: 1.42, zone: 'B', axis: 'y' }],
      }),
    },
  });
  await app.flush();
  app.getEl('field:machine_alias').value = 'Pump A';
  app.getEl('field:measurement_location').value = 'Motor DE';
  await atStep4(app);
  const html = app.getEl('dup-notice').innerHTML;
  assert.ok(html.indexOf('value="keep_both"') < html.indexOf('value="replace"'),
    'keep both comes first');
  const css = fs.readFileSync(
    path.join(__dirname, '..', '..', 'src', 'vib_agent', 'webapp', 'static', 'style.css'), 'utf8');
  assert.ok(!/\.dup[^{]*\{[^}]*(column-reverse|row-reverse)/.test(css),
    'a rule reverses the duplicate choices at some width');
});

test('D-24: the forecast asks about the DECLARED direction, not a guessed one', async () => {
  const today = new Date().toISOString().slice(0, 10);
  const app = intake({
    storage: {
      [TREND_KEY]: JSON.stringify({
        'Pump A|Motor DE': [{ ts: `${today}T06:00:00+00:00`, v: 1.42, zone: 'B', axis: 'y' }],
      }),
    },
  });
  await app.flush();
  app.getEl('field:machine_alias').value = 'Pump A';
  app.getEl('field:measurement_location').value = 'Motor DE';
  // Axial declares the x axis; the stored reading is on y, so it is not the
  // same measurement and must not be offered for replacement.
  app.getEl('field:direction').value = 'axial';
  await atStep4(app);
  assert.strictEqual(app.visible('dup-notice'), false);
  const f = app.sandbox.duplicateForecast();
  assert.strictEqual(f.axis, 'x');
  assert.strictEqual(f.matches.length, 0);
});

test('D-24: choosing keep-both is remembered for the run', async () => {
  const today = new Date().toISOString().slice(0, 10);
  const app = intake({
    storage: {
      [TREND_KEY]: JSON.stringify({
        'Pump A|Motor DE': [{ ts: `${today}T06:00:00+00:00`, v: 1.42, zone: 'B', axis: 'y' }],
      }),
    },
  });
  await app.flush();
  app.getEl('field:machine_alias').value = 'Pump A';
  app.getEl('field:measurement_location').value = 'Motor DE';
  await atStep4(app);
  app.getEl('form-card').dispatchEvent({
    type: 'change', target: { name: 'dup_mode', value: 'keep_both' },
  });
  await app.goTo('#/new/3');
  await app.goTo('#/new/4');
  assert.ok(app.getEl('dup-notice').innerHTML.includes('value="keep_both" checked'),
    'the choice survives leaving the step and coming back');
});

test('an unnamed machine is asked nothing, because it keeps no trend', async () => {
  const today = new Date().toISOString().slice(0, 10);
  const app = intake({
    storage: {
      [TREND_KEY]: JSON.stringify({
        'Pump A|Motor DE': [{ ts: `${today}T06:00:00+00:00`, v: 1.42, zone: 'B', axis: 'y' }],
      }),
    },
  });
  await app.flush();
  await atStep4(app);
  assert.strictEqual(app.visible('dup-notice'), false);
  assert.strictEqual(app.sandbox.duplicateForecast().key, '');
});

Promise.all(pending).then(() => {
  results.forEach(([status, name, err]) => {
    console.log(`${status}  ${name}${err ? `\n      ${err}` : ''}`);
  });
  const passed = results.filter(([s]) => s === 'PASS').length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
});
