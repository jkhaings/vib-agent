/**
 * hotfix-net: a real (if tiny) environment for app.js, so the polling loop can
 * be TESTED rather than read.
 *
 * app.js is a classic script, so it is loaded into a `vm` context whose globals
 * are stubs: enough DOM for the module-level code to run, a scripted fetch, and
 * a fake clock. Fake timers matter — the backoff schedule is 3s/6s/12s, and a
 * test that actually waited for it would take a minute and flake.
 *
 * Nothing here is a mock of app.js itself: the file under test is the exact one
 * the server ships.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const STATIC = path.join(__dirname, '..', '..', 'src', 'vib_agent', 'webapp', 'static');
const APP_JS = path.join(STATIC, 'app.js');
const INDEX_HTML = path.join(STATIC, 'index.html');
const STYLE_CSS = path.join(STATIC, 'style.css');

/**
 * Session UX-3. What a `<select>` actually HOLDS, so the harness can answer the
 * one question a plain `value` property cannot: is the value just assigned to
 * this element a value it can carry?
 *
 * Two defects reached a real browser because it could not answer that. A card
 * saved before the bearing field was closed to `config/bearings.json` holds a
 * model none of the options carry, and a browser SILENTLY RESETS such a select
 * to empty -- so an analyst's `SKF 32222 J2` was discarded with nothing on
 * screen saying so (UX-2 F-2 #2). And a JS-rendered form whose values live in
 * `value=` attributes reads back blank, because an attribute is not a selection
 * (UX-2 F-1 #4). Both were invisible here: `makeElement` backed `.value` with a
 * plain property, which accepts anything and forgets nothing. UX-2 wrote
 * "no node test can see this" into its own acceptance checklist; this is the
 * instrument catching up with the finding.
 *
 * `<optgroup>` is walked THROUGH rather than around -- the bearing select puts
 * five of its eight models inside two groups, so a parser that stopped at the
 * group would report a catalogue of three and pass for the wrong reason.
 */
function parseOptions(html) {
  const values = [];
  let selected = null;
  // Stop each option's text at the next boundary rather than requiring
  // `</option>`, which HTML does not.
  const optRe = /<option\b([^>]*)>([\s\S]*?)(?=<\/option>|<option\b|<optgroup\b|<\/optgroup>|<\/select>|$)/g;
  let m;
  while ((m = optRe.exec(html)) !== null) {
    const attrs = m[1];
    const explicit = (attrs.match(/\bvalue="([^"]*)"/) || [])[1];
    // No `value=` attribute means the value IS the text content (HTML spec).
    const value = explicit === undefined ? m[2].trim() : explicit;
    values.push(value);
    if (/\bselected\b/.test(attrs) && selected === null) selected = value;
  }
  return { values, selected };
}

/** Every `<select>` in a fragment, with its id, its name and its options. */
function parseSelects(html) {
  const out = [];
  const re = /<select\b([^>]*)>([\s\S]*?)<\/select>/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    const attrs = m[1];
    const parsed = parseOptions(m[2]);
    out.push({
      id: (attrs.match(/\bid="([^"]+)"/) || [])[1] || null,
      name: (attrs.match(/\bname="([^"]+)"/) || [])[1] || null,
      values: parsed.values,
      selected: parsed.selected,
    });
  }
  return out;
}

/**
 * The real markup and the real stylesheet, reduced to the two facts a stub DOM
 * needs before it can answer "would a browser SHOW this?".
 *
 * Session HIST-2-FIX-2. Without this the harness invents an element for every
 * id app.js asks for, so `el.hidden = true` is a property write with nothing to
 * contradict it — which is exactly why compare mode passed here while leaving
 * the third slot, its Direction select and "+ Add a channel" on screen in
 * Chrome. Two facts close that gap:
 *
 *   * **which ids exist, and what classes they carry** — read from index.html,
 *     so hiding an id the page does not have is a failure rather than a no-op.
 *   * **which classes declare a `display`** — read from style.css, because an
 *     author-origin `display` outranks the UA stylesheet's `[hidden]{display:none}`.
 *     A class whose rule is guarded with `.cls[hidden]{display:none}` is not a
 *     hazard and is subtracted again.
 *
 * What it can prove: that an element the page really has is hidden in a way a
 * real stylesheet cannot undo. What it CANNOT prove: layout, specificity beyond
 * bare-class rules, ancestor visibility (an element inside a hidden parent still
 * reports its own state), media queries, or anything about pixels. Part C's
 * real-browser pass is still the only thing that sees the page.
 */
function loadMarkup() {
  const html = fs.readFileSync(INDEX_HTML, 'utf8');
  const ids = new Map();
  const byClass = new Map();
  const tagRe = /<([a-zA-Z][\w-]*)\b([^>]*)>/g;
  let m;
  while ((m = tagRe.exec(html)) !== null) {
    const attrs = m[2];
    const classes = ((attrs.match(/\bclass="([^"]*)"/) || [])[1] || '').split(/\s+/).filter(Boolean);
    const node = { tag: m[1].toLowerCase(), classes };
    const id = (attrs.match(/\bid="([^"]+)"/) || [])[1];
    if (id) ids.set(id, node);
    classes.forEach((c) => { if (!byClass.has(c)) byClass.set(c, node); });
  }

  // Bare single-class rules only (`.grid{...}`), because those are the ones
  // whose `display` lands on any element carrying the class. A descendant
  // selector like `.extra-channels .grid > div.over` says nothing about an
  // element that merely has one of those classes, and counting it would make
  // this over-report.
  const css = fs.readFileSync(STYLE_CSS, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  const declaresDisplay = new Set();
  const guarded = new Set();
  const ruleRe = /([^{}]+)\{([^{}]*)\}/g;
  while ((m = ruleRe.exec(css)) !== null) {
    if (!/(^|[;\s])display\s*:/.test(m[2])) continue;
    m[1].split(',').forEach((part) => {
      const sel = part.trim();
      const bare = sel.match(/^\.([A-Za-z][\w-]*)$/);
      if (bare) declaresDisplay.add(bare[1]);
      const guard = sel.match(/^\.([A-Za-z][\w-]*)\[hidden\]$/);
      if (guard && /display\s*:\s*none/.test(m[2])) guarded.add(guard[1]);
    });
  }
  guarded.forEach((c) => declaresDisplay.delete(c));

  // Session UX-3: the selects, indexed by BOTH id and name and sharing one
  // node. `app.js` reaches every upload field through `form.elements[name]`
  // (`setField`, `fieldValue`, `rememberCurrentMachine`) -- which this harness
  // keys as `field:<name>` -- and reaches `#mode` and `#saved` by id. A select
  // is only as real as the door the code under test happens to use, so both
  // doors resolve to the same option list.
  const byName = new Map();
  // Session UX-5. Every named form control, not only the `<select>`s.
  //
  // UX-3 registered the selects by both id and name because `app.js` reaches
  // upload fields through `form.elements[name]` and reaches `#mode`/`#saved` by
  // id -- "a select is only as real as the door the code under test happens to
  // use". That reasoning is not about selects: an `<input name="rpm">` was
  // reachable only as an INVENTED element, so `strictEl('field:rpm')` -- the
  // refusal UX-4 added precisely so a mistyped id cannot pass -- threw for
  // every text and number field on the form. Registering them closes that.
  const namedRe = /<(input|textarea)\b([^>]*)>/g;
  let nm;
  while ((nm = namedRe.exec(html)) !== null) {
    const attrs = nm[2];
    const name = (attrs.match(/\bname="([^"]+)"/) || [])[1];
    if (!name || byName.has(name)) continue;
    const id = (attrs.match(/\bid="([^"]+)"/) || [])[1];
    const classes = ((attrs.match(/\bclass="([^"]*)"/) || [])[1] || '').split(/\s+/).filter(Boolean);
    const node = (id && ids.get(id)) || { tag: nm[1].toLowerCase(), classes };
    if (id) ids.set(id, node);
    byName.set(name, node);
  }
  parseSelects(html).forEach((sel) => {
    const node = (sel.id && ids.get(sel.id)) || { tag: 'select', classes: [] };
    node.options = { values: sel.values, selected: sel.selected };
    if (sel.id) ids.set(sel.id, node);
    if (sel.name) byName.set(sel.name, node);
  });
  //: Which ids and names are THE SAME CONTROL. `loadMarkup` has shared one
  //: node between them since UX-3; `getEl` still built a separate stub per
  //: KEY, so `#unit` and `field:velocity_unit` were two objects and a value
  //: assigned through one was invisible through the other. A test could then
  //: assert a default and pass while the code under test had written the other
  //: copy -- the same class as UX-3 F-12, one layer down.
  const aliasOf = new Map();
  byName.forEach((node, name) => {
    ids.forEach((idNode, id) => { if (idNode === node) aliasOf.set('field:' + name, id); });
  });
  return { ids, byClass, byName, aliasOf, displayClasses: declaresDisplay };
}

/**
 * An in-memory IndexedDB, enough for the report store and no more.
 *
 * Session UX-5. `indexedDB` is not in the sandbox (nor are `Blob`, `URL` or
 * `navigator`), so D-26's store had no instrument at all: the only thing a
 * suite could prove without this is that the feature degrades when the API is
 * missing, which is the branch that does nothing.
 *
 * Deliberately narrow. It models the five things `app.js` actually uses -- a
 * versioned open with `onupgradeneeded`, a keyPath store, one index, a cursor
 * over `IDBKeyRange.only`, and transaction completion -- and models them the
 * way the spec sequences them: requests settle on a microtask and `oncomplete`
 * fires after the requests queued inside the transaction. What it CANNOT
 * decide is quota, durability, cross-tab behaviour or a real `onblocked`; a
 * real browser is still the only thing that sees those, which is why the
 * acceptance checklist keeps its own row for it.
 *
 * `fail` makes every open answer with `onerror`, which is how a suite reaches
 * the "storage refused us" path without waiting for a real quota.
 */
function fakeIndexedDB(opts) {
  const options = opts || {};
  const dbs = new Map();
  const soon = (fn) => Promise.resolve().then(fn);

  function makeStore(name, keyPath) {
    return { name, keyPath, rows: new Map(), indexes: new Map() };
  }

  function objectStoreHandle(store, tx) {
    const request = (compute) => {
      const req = {};
      tx._pending += 1;
      soon(() => {
        try { req.result = compute(); if (req.onsuccess) req.onsuccess(); }
        catch (err) { req.error = err; if (req.onerror) req.onerror(); }
        tx._settle();
      });
      return req;
    };
    return {
      put(value) {
        return request(() => {
          if (tx.mode !== 'readwrite') throw new Error('read-only transaction');
          store.rows.set(value[store.keyPath], value);
          return value[store.keyPath];
        });
      },
      get(id) { return request(() => store.rows.get(id)); },
      delete(id) {
        return request(() => {
          if (tx.mode !== 'readwrite') throw new Error('read-only transaction');
          store.rows.delete(id);
        });
      },
      createIndex(name, path) { store.indexes.set(name, path); return { name }; },
      index(name) {
        if (!store.indexes.has(name)) throw new Error(`no index ${name}`);
        const path = store.indexes.get(name);
        return {
          openCursor(range) {
            const req = {};
            tx._pending += 1;
            const matches = [...store.rows.values()]
              .filter((row) => !range || row[path] === range.only);
            let i = 0;
            const step = () => soon(() => {
              if (i >= matches.length) {
                req.result = null;
                if (req.onsuccess) req.onsuccess();
                tx._settle();
                return;
              }
              const row = matches[i];
              req.result = {
                value: row,
                delete() { store.rows.delete(row[store.keyPath]); },
                continue() { i += 1; step(); },
              };
              if (req.onsuccess) req.onsuccess();
            });
            step();
            return req;
          },
        };
      },
    };
  }

  return {
    _dbs: dbs,
    open(name) {
      const req = {};
      soon(() => {
        if (options.fail) { if (req.onerror) req.onerror(); return; }
        const fresh = !dbs.has(name);
        if (fresh) dbs.set(name, { stores: new Map() });
        const raw = dbs.get(name);
        const db = {
          objectStoreNames: { contains: (n) => raw.stores.has(n) },
          createObjectStore(n, cfg) {
            const store = makeStore(n, (cfg || {}).keyPath);
            raw.stores.set(n, store);
            return objectStoreHandle(store, { mode: 'readwrite', _pending: 0, _settle() {} });
          },
          transaction(n, mode) {
            const store = raw.stores.get(n);
            if (!store) throw new Error(`no store ${n}`);
            const tx = {
              mode: mode || 'readonly',
              _pending: 0,
              _done: false,
              _settle() {
                this._pending -= 1;
                if (this._pending <= 0 && !this._done) {
                  this._done = true;
                  soon(() => { if (tx.oncomplete) tx.oncomplete(); });
                }
              },
            };
            tx.objectStore = () => objectStoreHandle(store, tx);
            // A transaction with no request in it still completes.
            soon(() => {
              if (tx._pending === 0 && !tx._done) {
                tx._done = true;
                if (tx.oncomplete) tx.oncomplete();
              }
            });
            return tx;
          },
        };
        req.result = db;
        if (fresh && req.onupgradeneeded) req.onupgradeneeded();
        if (req.onsuccess) req.onsuccess();
      });
      return req;
    },
  };
}

function makeElement(id, classes, options, onInnerHtml) {
  const listeners = {};
  // Session UX-3. `value` and `innerHTML` stop being plain properties. Both
  // changes exist to make ONE thing observable: a `<select>` that cannot hold
  // the value it was handed. See `parseOptions` above for the two defects that
  // reached a browser while this harness reported green.
  let value = '';
  let html = '';
  let optionValues = null;
  const el = {
    id,
    tagName: 'DIV',
    textContent: '',
    checked: false,
    disabled: false,
    hidden: false,
    files: [],
    style: {},
    open: false,
    classList: { add() {}, remove() {}, toggle() {} },
    dataset: {},
    _attrs: {},
    //: The classes this element carries in the REAL index.html, and whether the
    //: page has it at all. Empty/false for an element the markup does not
    //: define -- see `visible()`, which refuses to answer about one.
    _classes: classes || [],
    _known: Boolean(classes),
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener() {},
    dispatchEvent(event) {
      (listeners[event.type] || []).forEach((fn) => fn(event));
      return true;
    },
    getAttribute(name) { return el._attrs[name] === undefined ? null : el._attrs[name]; },
    setAttribute(name, value_) { el._attrs[name] = value_; },
    focus() { el._focused = (el._focused || 0) + 1; },
    scrollIntoView() {},
    click() { el.dispatchEvent({ type: 'click', target: el }); },
    _listeners: listeners,
    //: The options this element carries, or null for anything that is not a
    //: select. Read by tests; written by `_setOptions` alone.
    get _options() { return optionValues === null ? null : optionValues.slice(); },
    //: How many times focus() has been called on this element. Session UX-3:
    //: "focus lands on the outcome" is a claim about a COUNT as well as a
    //: target -- a card re-rendered on every poll must not steal focus again.
    _focused: 0,
  };

  /** Replace this element's option list, the way a browser does when a
   *  `<select>`'s children change: the `selected` option wins, else the first
   *  one, else nothing. Never keeps a value the new options do not carry. */
  el._setOptions = (values, selected) => {
    optionValues = values ? values.slice() : null;
    if (optionValues === null) return;
    if (selected !== null && selected !== undefined) value = selected;
    else value = optionValues.length ? optionValues[0] : '';
  };

  Object.defineProperty(el, 'value', {
    enumerable: true,
    configurable: true,
    get() { return value; },
    set(next) {
      const want = String(next === undefined || next === null ? '' : next);
      // THE POINT OF ALL OF THIS. A `<select>` handed a value none of its
      // options carry resets to empty -- it does not hold it, and it does not
      // say so. Anything that is not a select keeps whatever it is given.
      value = (optionValues !== null && optionValues.indexOf(want) === -1) ? '' : want;
    },
  });

  Object.defineProperty(el, 'innerHTML', {
    enumerable: true,
    configurable: true,
    get() { return html; },
    set(next) {
      html = String(next === undefined || next === null ? '' : next);
      // Two shapes, both of which app.js uses. Assigning options INTO a select
      // is how `renderSavedMachines` fills `#saved`; assigning a fragment that
      // CONTAINS selects is how the machine form and the confirm card are
      // drawn. Register both, or every JS-rendered select stays as unreal as
      // it was before this session.
      if (optionValues !== null) {
        const own = parseOptions(html);
        el._setOptions(own.values, own.selected);
      }
      if (onInnerHtml) onInnerHtml(html);
    },
  });

  if (options) el._setOptions(options.values, options.selected);
  return el;
}

/**
 * Load app.js with a scripted fetch and a fake clock.
 *   fetchImpl(url, init) -> {status, ok, headers:{get}, json()}  (may throw/reject)
 *   storage             -> {key: string} pre-seeded into localStorage BEFORE
 *                          app.js runs. Session GEOM-A: the saved-machine
 *                          migration happens at module load, so a test that
 *                          writes storage afterwards has missed it entirely.
 *   accounts            -> AUTH-1. true renders the <meta name="accounts">
 *                          tag the server writes only on STORE_BACKEND=db.
 *                          Default false, which is the shipped deployment.
 *   hash                -> UX-1. location.hash at load, i.e. which view the
 *                          analyst arrived on. Default '' — the home route.
 *   reduceMotion        -> UX-4. What `matchMedia('(prefers-reduced-motion:
 *                          reduce)')` answers. Read once at module load, so
 *                          it belongs here rather than in a setter.
 *   indexedDB           -> UX-5 / D-26. `true` (the default) gives the
 *                          in-memory store above; `false` leaves the global
 *                          UNDEFINED, which is the browser this feature has to
 *                          degrade for and is where every `typeof` guard in
 *                          the report store is proved; `'fail'` opens and then
 *                          errors, which is storage refusing us.
 */
function loadApp({ fetchImpl, search, hash, storage, accounts, reduceMotion, indexedDB }) {
  const markup = loadMarkup();
  const elements = new Map();
  // `sel:.foo` is how this harness keys a querySelector('.foo') lookup; back it
  // with the first element in the real markup carrying that class, so a class
  // selector is as markup-backed as an id is.
  const nodeFor = (key) => {
    const cls = (String(key).match(/^sel:\.([A-Za-z][\w-]*)$/) || [])[1];
    if (cls) return markup.byClass.get(cls);
    // Session UX-3. `field:<name>` is how this harness keys
    // `form.elements[name]`, which is the ONLY door app.js uses to reach an
    // upload field. Backed by the markup it names, so the upload form's
    // selects are as real as the machine form's.
    const named = (String(key).match(/^field:(.+)$/) || [])[1];
    if (named) return markup.byName.get(named);
    return markup.ids.get(key);
  };
  const getEl = (rawKey) => {
    // One control, one stub, whichever door the code under test came through.
    const id = markup.aliasOf.get(rawKey) || rawKey;
    if (!elements.has(id)) {
      const node = nodeFor(id);
      elements.set(id, makeElement(id, node ? node.classes : null,
                                   node ? node.options : null, onInnerHtml));
    }
    const el = elements.get(id);
    if (id !== rawKey && !elements.has(rawKey)) elements.set(rawKey, el);
    return el;
  };
  // A function DECLARATION, so `getEl` above can be handed it before this line
  // is reached: it is only ever called when something assigns innerHTML, which
  // is long after both are initialised.
  function onInnerHtml(fragment) {
    parseSelects(fragment).forEach((sel) => {
      if (sel.id) getEl(sel.id)._setOptions(sel.values, sel.selected);
    });
  }

  // ── fake clock ───────────────────────────────────────────────────────
  let now = 0;
  let seq = 0;
  const timers = [];
  const setTimeoutStub = (fn, ms) => {
    const timer = { id: ++seq, fn, at: now + (ms || 0) };
    timers.push(timer);
    return timer.id;
  };
  const clearTimeoutStub = (id) => {
    const i = timers.findIndex((t) => t.id === id);
    if (i > -1) timers.splice(i, 1);
  };

  const form = getEl('upload-form');
  form.elements = new Proxy({}, { get: (_t, name) => getEl('field:' + String(name)) });
  form.requestSubmit = () => form.dispatchEvent({ type: 'submit', preventDefault() {} });

  const document = {
    getElementById: getEl,
    querySelector: (sel) => {
      if (sel === 'meta[name="contact-email"]') return { content: 'ops@example.test' };
      // Session AUTH-1. The server writes this tag only when STORE_BACKEND=db;
      // on the backend production runs it is not in the markup at all, so the
      // default here is its ABSENCE rather than an empty value.
      if (sel === 'meta[name="accounts"]') return accounts ? { content: 'on' } : null;
      if (sel.startsWith('.c-skip')) {
        const slot = (sel.match(/data-slot="(\d+)"/) || [])[1];
        return getEl('skip:' + slot);
      }
      return getEl('sel:' + sel);
    },
    querySelectorAll: () => [],
    createElement: () => makeElement('created'),
  };

  // Session UX-1. `window` takes listeners and `location` has a hash, because
  // the views are hash-routed and `route()` is wired to `hashchange`. Without
  // these two the router would be reachable only by calling it by hand, which
  // proves the renderers and nothing about the wiring that reaches them.
  const windowListeners = {};
  const sandbox = {
    document,
    window: {
      // Session UX-4. `reduceMotion` is read ONCE, at module load
      // (`app.js:30`), so a suite that wants the reduced-motion path has to
      // ask for it before the file runs — there is no flag to flip
      // afterwards. Default false, which is the setting almost every analyst
      // has; `reduceMotion: true` is the other branch.
      matchMedia: (query) => ({
        matches: Boolean(reduceMotion) && String(query).indexOf('reduced-motion') !== -1,
      }),
      addEventListener(type, fn) { (windowListeners[type] = windowListeners[type] || []).push(fn); },
      removeEventListener() {},
      _ctx: '',
    },
    location: { search: search || '', hash: hash || '' },
    localStorage: {
      _data: Object.assign({}, storage || {}),
      getItem(k) { return this._data[k] === undefined ? null : this._data[k]; },
      setItem(k, v) { this._data[k] = String(v); },
      removeItem(k) { delete this._data[k]; },
    },
    fetch: (...args) => Promise.resolve().then(() => fetchImpl(...args)),
    setTimeout: setTimeoutStub,
    clearTimeout: clearTimeoutStub,
    setInterval: () => 0,
    clearInterval: () => {},
    requestAnimationFrame: (fn) => { fn(); return 1; },
    URLSearchParams,
    //: Session UX-5 / D-26. Absent from this sandbox until now, so anything
    //: the browser-held report touches had to be reachable through `typeof`
    //: and could not be exercised at all. A Blob here is a value with a size
    //: and a type -- the store keeps it whole and never reads it, which is
    //: exactly what a real one does with PDF bytes.
    Blob: class {
      constructor(parts, opts) {
        this._parts = parts || [];
        this.size = this._parts.reduce((n, p) => n + String(p).length, 0);
        this.type = (opts && opts.type) || '';
      }
    },
    URL: {
      _created: [],
      _revoked: [],
      createObjectURL(blob) {
        const url = `blob:vib/${URLStub._created.length}`;
        URLStub._created.push({ url, blob });
        return url;
      },
      revokeObjectURL(url) { URLStub._revoked.push(url); },
    },
    navigator: {
      _copied: [],
      clipboard: {
        writeText(text) { sandbox.navigator._copied.push(String(text)); return Promise.resolve(); },
      },
    },
    FormData: class { constructor() { this._d = {}; } get(k) { return this._d[k]; }
      set(k, v) { this._d[k] = v; } delete(k) { delete this._d[k]; } },
    //: Session UX-2. Retains its parts and answers the two calls
    //: `previewFile` makes -- `slice(0, n)` and `text()` -- so the one async
    //: seam between a chosen file and a drawn spectrum is testable rather than
    //: merely asserted about. `size` still defaults to 10 for the call sites
    //: that only ever read a name.
    File: class {
      constructor(parts, name, opts) {
        this.name = name;
        this._text = (parts || []).join('');
        this.size = (opts && opts.size) || this._text.length || 10;
      }
      slice(start, end) {
        const cut = new sandbox.File([this._text.slice(start, end)], this.name);
        cut.size = cut._text.length;
        return cut;
      }
      text() { return Promise.resolve(this._text); }
    },
    DataTransfer: undefined,
    Event: class { constructor(type, init) { this.type = type; Object.assign(this, init || {}); } },
    console,
    JSON,
    Math,
    Object,
    Array,
    String,
    Number,
    Boolean,
    Promise,
    parseFloat,
    parseInt,
    isNaN,
    Date,
    Error,
  };
  // `URL`'s two methods refer to the object they hang off, which does not exist
  // as a binding while the literal above is being built.
  const URLStub = sandbox.URL;
  if (indexedDB !== false) {
    sandbox.indexedDB = fakeIndexedDB({ fail: indexedDB === 'fail' });
    sandbox.IDBKeyRange = { only: (value) => ({ only: value }) };
  }
  sandbox.window.document = document;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(APP_JS, 'utf8'), sandbox, { filename: 'app.js' });

  // Drain microtasks so awaited fetches settle before the clock moves.
  const flush = async () => { for (let i = 0; i < 50; i += 1) await Promise.resolve(); };

  return {
    sandbox,
    elements,
    markup,
    //: What the page handed the browser to download, and what it gave back.
    //: A blob URL that is created on every render and never revoked is a leak
    //: the harness can see and a browser cannot be asked about cheaply.
    objectUrls: () => sandbox.URL._created.slice(),
    revokedUrls: () => sandbox.URL._revoked.slice(),
    //: What reached the clipboard, in order.
    copied: () => sandbox.navigator._copied.slice(),
    stateCard: getEl('state'),
    getEl,
    /**
     * Would a browser show this element? Throws for an element index.html does
     * not define, because "is the id the JS hides actually on the page?" is
     * half of the question and a stub DOM would otherwise answer yes to both.
     * See `loadMarkup` for what this can and cannot decide.
     */
    visible(key) {
      const el = getEl(key);
      if (!el._known) throw new Error(`index.html has no element for "${key}"`);
      if (el.style && el.style.display === 'none') return false;
      if (!el.hidden) return true;
      // `hidden` alone: the UA rule wins only if no author rule outranks it.
      return el._classes.some((c) => markup.displayClasses.has(c));
    },
    /**
     * The element with this id, or a THROW if index.html does not define one.
     *
     * Session UX-3 F-12, closed here as it asked. `getEl` invents an element
     * for any id it is handed, and an invented one has `disabled: false` and
     * an empty `innerHTML` -- so a mistyped id passes silently. UX-3 caught
     * `app.getEl('go').disabled` (the button is `#submit`) only because it
     * was the one negative control of four that did NOT go red: the check
     * passed while the line under test was deleted.
     *
     * Not fixed by making `getEl` throw, and that is still the right call --
     * thirteen suites lean on the invented-element behaviour and this session
     * has no more evidence about which than UX-3 did. `visible()` already
     * refuses the same way, on the same `_known` flag; this is that refusal
     * for a direct handle, so a new suite can opt into it one call at a time.
     */
    strictEl(key) {
      const el = getEl(key);
      if (!el._known) {
        throw new Error(
          `index.html has no element for "${key}" — the harness invented one, `
          + 'so anything read off it would be a default rather than a fact');
      }
      return el;
    },
    now: () => now,
    /** Set the hash and fire `hashchange`, the way a link click does. UX-1. */
    async goTo(next) {
      sandbox.location.hash = next;
      (windowListeners.hashchange || []).forEach((fn) => fn());
      await flush();
    },
    pending: () => timers.map((t) => t.at - now).sort((a, b) => a - b),
    /** Run every timer due within `ms`, in order, draining promises between. */
    async advance(ms) {
      const target = now + ms;
      for (;;) {
        await flush();
        const due = timers.filter((t) => t.at <= target).sort((a, b) => a.at - b.at)[0];
        if (!due) break;
        timers.splice(timers.indexOf(due), 1);
        now = due.at;
        due.fn();
      }
      now = target;
      await flush();
    },
    flush,
  };
}

/**
 * A value app.js built, as THIS realm can compare it. Session UX-5, lifting
 * HIST-1 F-4 out of `trend_card_tests.js` where it was written down and asked
 * to be moved here the next time this file was open.
 *
 * `app.js` runs inside a `vm` context, so an object or array it constructs
 * carries THAT realm's `Object.prototype`. `assert.deepStrictEqual` compares
 * prototypes, so it rejects such a value on identity alone and reports
 * "Values have same structure but are not reference-equal" -- which reads like
 * a real mismatch and costs whoever meets it the same twenty minutes it cost
 * HIST-1 and cost this session again. A JSON round-trip rebuilds the value
 * here. Primitives, and anything that already came back through `JSON.parse`
 * in this realm, do not need it.
 */
const plain = (value) => JSON.parse(JSON.stringify(value));

module.exports = { loadApp, makeElement, loadMarkup, plain };
