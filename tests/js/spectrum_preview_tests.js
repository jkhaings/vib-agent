/**
 * Session UX-2: the in-browser spectrum preview, run in node against the REAL
 * app.js. Driven by pytest — tests/test_ux2_preview.py.
 *
 * The corpus is the one `tests/fixtures/intake_adversarial/` already holds: 26
 * files built to break an intake, committed as BYTES because half of them are
 * about a BOM, a bare CR, a cp1252 high byte or an embedded NUL. Every one of
 * them now has a second reader — this one, in the browser — and the expected
 * outcome per file is taken from that corpus's own README, not invented here.
 *
 * The rule the whole parser is built to: A WRONG PICTURE IS WORSE THAN NO
 * PICTURE. Thousands separators are refused rather than guessed. An axis that
 * goes backwards is refused, because the server's gate refuses that file and a
 * tidy drawing of it would set an expectation the run then breaks. Anything
 * that does not read as text is refused rather than drawn as noise.
 *
 * And the claim that matters most: NO ANALYSIS NUMBER IS BORN IN THE BROWSER.
 * The figure carries no zone, no severity, no mm/s, no ISO anything.
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { loadApp } = require('./harness');

const CORPUS = path.join(__dirname, '..', 'fixtures', 'intake_adversarial');
const EXAMPLE = path.join(__dirname, '..', '..', 'src', 'vib_agent', 'webapp',
  'static', 'example_spectrum.csv');

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

const app = loadApp({ fetchImpl: () => { throw new Error('the preview makes no request'); } });
const read = (f) => fs.readFileSync(path.join(CORPUS, f), 'utf8');
const parse = (f) => app.sandbox.parseSpectrumText(read(f), false);

/** What each corpus file must do here, and why — the README's own column.
 *  `bins` is asserted where the corpus states a count, because "it parsed" and
 *  "it parsed CORRECTLY" are different claims and only the second is useful. */
const EXPECTED = [
  // ── read, and read right ──
  ['crlf.txt', { bins: 501, xUnit: 'hz' }],
  ['bare_cr.dat', { bins: 501, xUnit: 'hz' }],
  ['cp1252_degrees.txt', { bins: 501, xUnit: 'hz' }],
  ['comments_blanks.dat', { bins: 501, xUnit: 'hz' }],
  ['accel_ms2.asc', { bins: 501, xUnit: 'hz' }],
  ['db_units.txt', { bins: 501, xUnit: 'hz' }],
  ['negative_amps.txt', { bins: 501, xUnit: 'hz' }],
  ['all_zero.txt', { bins: 501, xUnit: 'hz' }],
  ['binary_tail.txt', { bins: 501, xUnit: 'hz' }],
  ['utf8_bom_nohdr.txt', { bins: 501, xUnit: 'unstated' }],
  ['scientific.txt', { bins: 501, xUnit: 'unstated' }],
  ['scientific_eu.asc', { bins: 501, xUnit: 'unstated' }],
  ['numeric_preamble.dat', { bins: 501, xUnit: 'unstated' }],
  // the corpus states these two counts explicitly
  ['fixed_width.txt', { bins: 498, xUnit: 'unstated' }],
  ['truncated.txt', { bins: 500, xUnit: 'hz' }],
  // read, but the axis is NOT Hz and the file says so
  ['decimal_comma_cpm.txt', { bins: 501, xUnit: 'other', xmax: 15000 }],
  ['orders_no_speed.txt', { bins: 501, xUnit: 'other' }],
  // ── refused, each for its own stated reason ──
  ['utf16le_bom.txt', { refusal: 'binary' }],
  ['utf16be_bom.txt', { refusal: 'binary' }],
  ['binary_head.txt', { refusal: 'binary' }],
  ['thousands_eu_dot.txt', { refusal: 'thousands' }],
  ['thousands_space.txt', { refusal: 'thousands' }],
  ['numeric_footer.txt', { refusal: 'nonmono' }],
  ['two_blocks.txt', { refusal: 'nonmono' }],
  ['non_monotonic.txt', { refusal: 'nonmono' }],
  ['prose_report.txt', { refusal: 'toofew' }],
];

test('every file in the adversarial corpus has a stated expectation here', () => {
  const listed = EXPECTED.map(([f]) => f).sort();
  const onDisk = fs.readdirSync(CORPUS)
    .filter((f) => f !== 'README.md' && f !== '.gitattributes').sort();
  // A file added to the corpus and not to this table would otherwise be a file
  // the browser reader was never pointed at.
  assert.deepStrictEqual(listed, onDisk);
  assert.strictEqual(onDisk.length, 26, 'the corpus is 26 files');
});

EXPECTED.forEach(([file, want]) => {
  test(`corpus: ${file}`, () => {
    const got = parse(file);
    if (want.refusal) {
      assert.ok(got.refusal, `${file} should have been refused, drew ${got.bins && got.bins.length}`);
      assert.strictEqual(got.refusal, app.sandbox.previewRefusal(want.refusal),
        `${file} was refused for the wrong reason: ${got.refusal}`);
      assert.ok(!got.bins, 'a refusal draws nothing');
      return;
    }
    assert.strictEqual(got.refusal, '', `${file}: ${got.refusal}`);
    assert.strictEqual(got.bins.length, want.bins, `${file} bin count`);
    assert.strictEqual(got.xUnit, want.xUnit, `${file} x unit`);
    if (want.xmax !== undefined) {
      assert.strictEqual(got.bins[got.bins.length - 1][0], want.xmax);
    }
  });
});

test('the shipped example spectrum reads, and reads as Hz', () => {
  const got = app.sandbox.parseSpectrumText(fs.readFileSync(EXAMPLE, 'utf8'), false);
  assert.strictEqual(got.refusal, '');
  assert.ok(got.bins.length > 400, `only ${got.bins.length} bins`);
});

// ── the thousands guard refuses grouping, and ONLY grouping ──────────────

test('grouped thousands are refused, in each of the three conventions', () => {
  const rows = (line) => 'Hz;Amp\n' + Array.from({ length: 60 }, () => line).join('\n');
  ['1.800,00;0,012', '1 800,00;0,012', '1,800.00,0.012'].forEach((line) => {
    const got = app.sandbox.parseSpectrumText(rows(line), false);
    assert.strictEqual(got.refusal, app.sandbox.previewRefusal('thousands'), line);
  });
});

test('an ordinary 3-decimal file is NOT mistaken for a grouped one', () => {
  // The guard's first version probed for the grouped SHAPE as a substring, and
  // that shape sits inside ordinary comma-delimited data with three decimal
  // places -- so it refused three perfectly readable files. "We would rather
  // show nothing than the wrong picture" is a rule about files we cannot read.
  const body = ['Hz,Amp']
    .concat(Array.from({ length: 200 }, (_, i) => `${(i * 1.5).toFixed(3)},${(i % 7).toFixed(3)}`))
    .join('\n');
  const got = app.sandbox.parseSpectrumText(body, false);
  assert.strictEqual(got.refusal, '', got.refusal);
  assert.strictEqual(got.bins.length, 200);
  assert.strictEqual(got.xUnit, 'hz');
});

test('the guard is a whole-token match, case by case', () => {
  const grouped = (line) => {
    const got = app.sandbox.parseSpectrumText(
      'Hz;Amp\n' + Array.from({ length: 60 }, () => line).join('\n'), false);
    return got.refusal === app.sandbox.previewRefusal('thousands');
  };
  [['1.234.567,89;1', true], ['12,345.67,0.5', true],
    ['1.500,0.012', false], ['250.000,120.5', false], ['12.500,0.0120', false],
    ['1000.000,999.999', false], ['0.5000,0.01200', false],
  ].forEach(([line, want]) => assert.strictEqual(grouped(line), want, line));
});

// ── decimation must not be able to hide a peak ────────────────────────────

test('decimate keeps the loudest bin in each bucket, never a stride sample', () => {
  // 10,000 bins of noise with ONE spike, in a position a stride would skip.
  const bins = [];
  for (let i = 0; i < 10000; i += 1) bins.push([i * 0.5, 0.01]);
  bins[3457][1] = 9.5;
  const out = app.sandbox.decimate(bins, 1200);
  assert.strictEqual(out.length, 1200);
  const peak = out.filter((b) => b[1] === 9.5);
  assert.strictEqual(peak.length, 1, 'the one line in the spectrum survived decimation');
  assert.strictEqual(peak[0][0], 3457 * 0.5, 'and kept its frequency');
});

test('decimate leaves a short spectrum alone', () => {
  const bins = [[0, 1], [1, 2], [2, 3]];
  assert.strictEqual(app.sandbox.decimate(bins, 1200).length, 3);
});

// ── the figure says what it is, and never what it is not ──────────────────

function figureFor(file, rpm) {
  const got = parse(file);
  return app.sandbox.spectrumFigure({
    bins: got.bins, xUnit: got.xUnit, name: file, rpm, truncated: false,
  });
}

test('the figure is labelled PREVIEW and carries no analysis vocabulary', () => {
  const html = figureFor('crlf.txt', 1800);
  assert.ok(html.includes('PREVIEW'));
  assert.ok(html.includes('nothing here is\n      analysed, graded, or sent anywhere.'), html.slice(-400));
  // The whole point. A browser-drawn number that reads like a verdict is the
  // failure this feature could most easily become.
  ['Zone', 'mm/s RMS', 'ISO', 'severity', 'Severity'].forEach((word) => {
    assert.ok(!html.includes(word), `the preview must never say "${word}"`);
  });
});

test('the figure is one <svg> with a computed alt text, not an image request', () => {
  const html = figureFor('crlf.txt', 1800);
  assert.ok(html.includes('role="img"'));
  assert.ok(/aria-label="Preview of crlf\.txt: 501 rows drawn across 0\.0 to 250\.0/.test(html), html.slice(0, 400));
  assert.ok(!html.includes('<img'), 'inline SVG only — no request leaves the page');
  assert.strictEqual((html.match(/<polyline/g) || []).length, 1);
});

test('shaft orders are drawn on a Hz axis when a speed is known', () => {
  const html = figureFor('crlf.txt', 1800);      // 1800 rpm -> 30 Hz
  assert.strictEqual((html.match(/class="spx-o"/g) || []).length, 3);
  assert.ok(html.includes('>1×</text>') && html.includes('>2×</text>') && html.includes('>3×</text>'));
  assert.ok(html.includes('Shaft orders marked at 30.00 Hz'));
});

test('shaft orders are WITHHELD when the file says its axis is not Hz', () => {
  // CPM axis, 0..15000. 30 Hz would land at x=30, which is a real position on
  // that axis and the wrong one — the exact way a marker invents a reading.
  const html = figureFor('decimal_comma_cpm.txt', 1800);
  assert.ok(!html.includes('class="spx-o"'), 'no markers on an axis we cannot place them on');
  assert.ok(html.includes('does not state its frequency axis in Hz'));
  assert.ok(html.includes('No shaft-order markers are drawn.'), 'and the alt text says so too');
});

test('an order axis gets no markers either', () => {
  const html = figureFor('orders_no_speed.txt', 1800);
  assert.ok(!html.includes('class="spx-o"'));
});

test('with no running speed yet, the caption asks for one instead of guessing', () => {
  const html = figureFor('crlf.txt', '');
  assert.ok(!html.includes('class="spx-o"'));
  assert.ok(html.includes('Give the running speed and the shaft-order markers appear here.'));
});

test('an unstated axis is only treated as Hz when 3x actually fits on it', () => {
  const got = parse('scientific.txt');           // no header, 0..250 Hz
  const fits = app.sandbox.spectrumFigure({
    bins: got.bins, xUnit: 'unstated', name: 'x', rpm: 1800, truncated: false,
  });
  assert.strictEqual((fits.match(/class="spx-o"/g) || []).length, 3);
  // 100,000 rpm is 1666 Hz; 3x is far past this file's 250 Hz, so the axis is
  // almost certainly not Hz and no marker is placed.
  const doesNot = app.sandbox.spectrumFigure({
    bins: got.bins, xUnit: 'unstated', name: 'x', rpm: 100000, truncated: false,
  });
  assert.ok(!doesNot.includes('class="spx-o"'));
});

test('an order that falls past Fmax is not drawn, and the omission is published', () => {
  const got = parse('crlf.txt');                 // 0..250 Hz
  const html = app.sandbox.spectrumFigure({
    bins: got.bins, xUnit: 'hz', name: 'x', rpm: 6000, truncated: false,
  });                                            // 100 Hz, so 3x = 300 > 250
  assert.strictEqual((html.match(/class="spx-o"/g) || []).length, 2);
  assert.ok(html.includes("1 of the three shaft orders sits past this file's highest frequency"));
});

test('the figure names what the analysis will refuse, rather than drawing it prettily', () => {
  assert.ok(figureFor('all_zero.txt', 1800).includes('Every amplitude in this file is zero'));
  assert.ok(figureFor('negative_amps.txt', 1800).includes('negative amplitudes'));
});

test('a truncated read says which part of the file is drawn', () => {
  const got = parse('crlf.txt');
  const html = app.sandbox.spectrumFigure({
    bins: got.bins, xUnit: 'hz', name: 'x', rpm: 1800, truncated: true,
  });
  assert.ok(html.includes('Only the first 4 MB of this file is drawn — the analysis reads all of it.'));
});

// ── the one seam that touches a File ──────────────────────────────────────

let mountN = 0;
const nextMount = () => `spx-probe-${mountN += 1}`;

function fileOf(name, body) {
  return new app.sandbox.File([body === undefined ? read(name) : body], name);
}

test('a text spectrum is drawn into its mount', async () => {
  const MOUNT = nextMount();
  const out = await app.sandbox.previewFile(fileOf('crlf.txt'), MOUNT, 1800);
  assert.strictEqual(out, 'drawn');
  assert.ok(app.getEl(MOUNT).innerHTML.includes('PREVIEW'));
  assert.ok(app.getEl(MOUNT).hidden === false);
});

test('a format we do not read here says so, and does not call it an error', async () => {
  const MOUNT = nextMount();
  const out = await app.sandbox.previewFile(fileOf('route.wav', 'RIFFxxxx'), MOUNT, 1800);
  assert.strictEqual(out, 'unavailable');
  const html = app.getEl(MOUNT).innerHTML;
  assert.ok(html.includes('NO PREVIEW'));
  assert.ok(html.includes('is\n      uploaded and analysed exactly as normal'), html);
  // Every format the form accepts and this reader does not.
  ['a.xlsx', 'a.uff', 'a.unv', 'a.mat', 'a.wav'].forEach((n) => {
    assert.strictEqual(app.sandbox.previewable(n), false, n);
  });
  ['a.csv', 'a.txt', 'a.dat', 'a.asc', 'A.CSV'].forEach((n) => {
    assert.strictEqual(app.sandbox.previewable(n), true, n);
  });
});

test('a refused file explains itself in the same panel', async () => {
  const MOUNT = nextMount();
  const out = await app.sandbox.previewFile(fileOf('two_blocks.txt'), MOUNT, 1800);
  assert.strictEqual(out, 'refused');
  assert.ok(app.getEl(MOUNT).innerHTML.includes('goes backwards partway through'));
});

test('clearing the slot empties and hides the mount', async () => {
  const MOUNT = nextMount();
  await app.sandbox.previewFile(fileOf('crlf.txt'), MOUNT, 1800);
  const out = await app.sandbox.previewFile(null, MOUNT, 1800);
  assert.strictEqual(out, '');
  assert.strictEqual(app.getEl(MOUNT).innerHTML, '');
  assert.strictEqual(app.getEl(MOUNT).hidden, true);
});

test('a reader that throws is a panel, never a rejected promise', async () => {
  const MOUNT = nextMount();
  const broken = { name: 'x.csv', size: 20, text() { throw new Error('nope'); } };
  const out = await app.sandbox.previewFile(broken, MOUNT, 1800);
  assert.strictEqual(out, 'refused');
  assert.ok(app.getEl(MOUNT).innerHTML.includes('NO PREVIEW'));
});

test('a file over the head limit is sliced, and only the head is read', async () => {
  const MOUNT = nextMount();
  const big = fileOf('crlf.txt');
  big.size = 40 * 1024 * 1024;                   // claims to be 40 MB
  const out = await app.sandbox.previewFile(big, MOUNT, 1800);
  assert.strictEqual(out, 'drawn');
  assert.ok(app.getEl(MOUNT).innerHTML.includes('Only the first 4 MB'));
});

Promise.all(pending).then(() => {
  results.forEach(([status, name, err]) => {
    console.log(`${status}  ${name}${err ? `\n      ${err}` : ''}`);
  });
  const passed = results.filter(([s]) => s === 'PASS').length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
});
