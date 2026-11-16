// Clash Detector: the page. Talks to Revit through the WebView2 host,
// keeps the setup and the clash list, and drives the viewer.
//
// Messages, both ways, are plain objects with a `type`:
//   out:  ready, refresh, run, cancel, select, sectionbox, log
//   in:   init, progress, mesh, result, aborted, notice, error

import { Viewer } from './viewer.js';

const $ = (id) => document.getElementById(id);
const ROWS_AT_ONCE = 400;
const INIT_WAIT = 6000;     // ms before saying the model list has not come

// -- the host ---------------------------------------------------------------

async function connect(onMessage) {
  const webview = window.chrome && window.chrome.webview;
  if (webview) {
    webview.addEventListener('message', (event) => onMessage(event.data));
    return (message) => webview.postMessage(message);
  }
  // Opened in an ordinary browser: stand in for Revit with made up data.
  const demo = await import('./demo.js');
  return demo.connect(onMessage);
}

// -- errors -------------------------------------------------------------------
//
// An exception in here would otherwise go nowhere a person can see:
// WebView2 has no console open inside Revit. Show it, and send it to
// the pyRevit log.

function reportError(message, detail, tag) {
  toast(message, 'error', detail || '', tag);
  try {
    post({ type: 'log', message, detail: detail || '' });
  } catch (ignored) { /* the host is not there yet */ }
}

window.addEventListener('error', (event) => {
  reportError(`Page error: ${event.message}`,
    event.error && event.error.stack ? event.error.stack : `${event.filename}:${event.lineno}`);
});
window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason || {};
  reportError(`Page error: ${reason.message || reason}`, reason.stack || '');
});

// -- state ------------------------------------------------------------------

const state = {
  init: null,
  initTimer: null,
  models: { a: null, b: null },             // model choice key per side
  cats: { a: new Set(), b: new Set() },     // category ids turned on per side
  closedGroups: new Set(),                  // discipline headings folded shut
  elements: new Map(),                      // key -> {key, src, id, cat, name, set}
  clashes: [],
  levels: [],
  stats: null,
  shown: [],                                // clashes in list order after filters
  selected: null,
  reviewed: new Set(),
  collapsed: new Set(),
  rowLimit: ROWS_AT_ONCE,
  busy: false,
  touring: null,
  pullAnimation: null,
  cutAxis: 2,
  contextMode: 'ghost',
};

let post = () => {};
const viewer = new Viewer($('view'), {
  onPickClash: (number) => select(state.clashes[number]),
  onPickElement: (key) => pickElement(key),
  onHover: (number, x, y) => showHover(number, x, y),
});

// -- small helpers ------------------------------------------------------------

const number = new Intl.NumberFormat('en-GB');

function formatDepth(clash) {
  if (clash.depth === null || clash.depth === undefined) return 'depth n/a';
  const mm = clash.depth * 1000;
  return mm >= 1000 ? `${(mm / 1000).toFixed(2)} m` : `${Math.round(mm)} mm`;
}

function formatVolume(clash) {
  if (clash.vol === null || clash.vol === undefined) return 'n/a';
  const litres = clash.vol * 1000;
  if (litres >= 1000) return `${(litres / 1000).toFixed(2)} m³`;
  if (litres >= 10) return `${litres.toFixed(0)} L`;
  return `${litres.toFixed(litres >= 1 ? 1 : 2)} L`;
}

function element(key) {
  return state.elements.get(key) || { key, cat: 'Element', name: '', id: '', src: '' };
}

function sourceLabel(src) {
  if (!state.init) return src;
  const found = state.init.sources.find((source) => source.key === src);
  return found ? found.label : src;
}

function levelOf(z) {
  if (!state.levels.length) return '';
  let name = state.levels[0].name;
  for (const level of state.levels) {
    if (level.z <= z + 0.05) name = level.name;
    else break;
  }
  return name;
}

// `tag` names a toast that something later can take back down.
function toast(text, kind = 'info', detail = '', tag = '') {
  const box = document.createElement('div');
  box.className = `toast ${kind}`;
  if (tag) box.dataset.tag = tag;
  const close = document.createElement('button');
  close.textContent = '✕';
  close.setAttribute('aria-label', 'Dismiss');
  close.onclick = () => box.remove();
  box.append(close, document.createTextNode(text));
  if (detail) {
    const pre = document.createElement('pre');
    pre.textContent = detail;
    box.append(pre);
  }
  $('toasts').append(box);
  if (kind !== 'error') setTimeout(() => box.remove(), kind === 'warn' ? 9000 : 3500);
}

function themeColors() {
  const style = getComputedStyle(document.documentElement);
  const read = (name) => style.getPropertyValue(name).trim();
  return { a: read('--a'), b: read('--b'), clash: read('--clash'),
    done: read('--done'), ink: read('--ink'), stage: read('--stage') };
}

// -- setup --------------------------------------------------------------------

function showTab(name) {
  for (const tab of document.querySelectorAll('.tabs button')) {
    tab.setAttribute('aria-selected', String(tab.dataset.tab === name));
  }
  $('panel-setup').hidden = name !== 'setup';
  $('panel-clashes').hidden = name !== 'clashes';
}

// What can be chosen as model A or B: each real source, and a couple of
// combinations. `keys` are the real sources it stands for.
function modelOptions() {
  const sources = state.init ? state.init.sources : [];
  const links = sources.filter((source) => source.kind === 'link');
  const options = [];
  for (const source of sources) {
    let label = source.label;
    if (source.kind === 'host') label = `${source.label} (this model)`;
    if (source.kind === 'selection') {
      const n = source.cats.reduce((sum, cat) => sum + cat.count, 0);
      label = `Current selection (${number.format(n)})`;
    }
    options.push({ key: source.key, label, keys: [source.key] });
  }
  const host = sources.find((source) => source.kind === 'host');
  if (links.length >= 2) {
    options.push({ key: 'all:links', label: `All links (${links.length})`,
      keys: links.map((link) => link.key) });
  }
  if (host && links.length) {
    options.push({ key: 'all:everything', label: 'This model and all links',
      keys: [host.key, ...links.map((link) => link.key)] });
  }
  return options;
}

function modelOption(side) {
  return modelOptions().find((option) => option.key === state.models[side]) || null;
}

function sourcesOf(side) {
  const option = modelOption(side);
  if (!option) return [];
  return state.init.sources.filter((source) => option.keys.includes(source.key));
}

// id -> element count, over the sources model `side` stands for.
function countsOf(side) {
  const counts = new Map();
  for (const source of sourcesOf(side)) {
    for (const cat of source.cats) counts.set(cat.id, (counts.get(cat.id) || 0) + cat.count);
  }
  return counts;
}

// The models chosen when the window opens, or when a choice disappears.
function defaultModels() {
  const options = modelOptions();
  const has = (key) => options.some((option) => option.key === key);
  const links = state.init.sources.filter((source) => source.kind === 'link');
  let a = 'host';
  let b = 'host';
  if (has('selection')) {
    a = 'selection';
    b = has('all:everything') ? 'all:everything' : 'host';
  } else if (links.length === 1) {
    b = links[0].key;
  } else if (links.length > 1) {
    b = 'all:links';
  }
  return { a: has(a) ? a : (options[0] && options[0].key), b: has(b) ? b : (options[0] && options[0].key) };
}

function applyInit(init) {
  const previous = state.init;
  state.init = init;
  clearTimeout(state.initTimer);
  for (const stale of document.querySelectorAll('.toast[data-tag="no-init"]')) stale.remove();
  $('refresh').disabled = false;
  $('use-selection').disabled = false;
  $('model-name').textContent = init.model || '';
  document.title = `Clash Detector${init.version ? ' ' + init.version : ''}`;

  const view = init.view || {};
  const viewRadio = document.querySelector('input[name="scope"][value="view"]');
  viewRadio.disabled = !view.usable;
  const hint = $('scope-view-hint');
  if (!view.usable) {
    hint.textContent = view.name
      ? `"${view.name}" shows no model elements.` : 'There is no active view.';
  } else {
    hint.textContent = `Host elements that "${view.name}" shows`
      + (view.sectionBox ? ', and everything inside its section box.'
        : '. Linked models are tested whole unless the view has a section box.');
  }
  const viewOption = document.querySelector('input[name="scope"]:checked');
  if (!previous || !viewOption || (viewOption.value === 'view' && !view.usable)) {
    const scope = view.usable && view.is3d ? 'view' : 'model';
    document.querySelector(`input[name="scope"][value="${scope}"]`).checked = true;
  }

  const has = (kind) => init.sources.some((source) => source.kind === kind);
  if (previous && init.refreshed) {
    // Keep both choices where they still exist.
    const fallback = defaultModels();
    const keys = new Set(modelOptions().map((option) => option.key));
    for (const side of ['a', 'b']) {
      if (!keys.has(state.models[side])) state.models[side] = fallback[side];
    }
    if (init.useSelection) {
      if (has('selection')) {
        state.models.a = 'selection';
        state.cats.a = new Set(countsOf('a').keys());
        toast('Model A is what is selected in Revit now.', 'ok');
      } else {
        toast('Nothing selected in Revit can clash. Select model elements '
          + '(walls, pipes, ducts and so on) and try again.', 'warn');
      }
    } else {
      const links = init.sources.filter((source) => source.kind === 'link').length;
      toast(`Read from Revit again: this model, ${links} link${links === 1 ? '' : 's'}`
        + `${has('selection') ? ' and the current selection' : ''}.`, 'ok');
    }
  } else {
    state.models = defaultModels();
    applyPreset('everything');
  }
  renderPickers();
  renderCategories();
}

function renderPickers() {
  const options = modelOptions();
  for (const side of ['a', 'b']) {
    const select = $(`model-${side}`);
    select.textContent = '';
    for (const option of options) {
      const element = document.createElement('option');
      element.value = option.key;
      element.textContent = option.label;
      select.append(element);
    }
    select.value = state.models[side];
    select.disabled = !options.length;
  }
  $('use-selection').hidden = false;
}

// Every category either model has, under its discipline heading.
function categoryRows() {
  const counts = { a: countsOf('a'), b: countsOf('b') };
  const rows = new Map();
  for (const side of ['a', 'b']) {
    for (const source of sourcesOf(side)) {
      for (const cat of source.cats) {
        if (!rows.has(cat.id)) {
          rows.set(cat.id, { id: cat.id, name: cat.name, group: cat.group || 'Other',
            a: counts.a.get(cat.id) || 0, b: counts.b.get(cat.id) || 0 });
        }
      }
    }
  }
  const order = (state.init.groups || []);
  const rank = (group) => {
    const at = order.indexOf(group);
    return at < 0 ? order.length : at;
  };
  return [...rows.values()].sort((p, q) => rank(p.group) - rank(q.group)
    || p.name.localeCompare(q.name));
}

function visibleRows() {
  const words = $('cat-search').value.toLowerCase().split(/\s+/).filter(Boolean);
  return categoryRows().filter((row) => {
    const text = `${row.name} ${row.group}`.toLowerCase();
    return words.every((word) => text.includes(word));
  });
}

// "true", "false" or "mixed" for a toggle that stands for `rows`.
function pressedFor(side, rows) {
  const able = rows.filter((row) => row[side] > 0);
  if (!able.length) return 'false';
  const on = able.filter((row) => state.cats[side].has(row.id)).length;
  if (!on) return 'false';
  return on === able.length ? 'true' : 'mixed';
}

// Turns `rows` on for `side`, or off when they are all on already.
function toggleMany(side, rows) {
  const able = rows.filter((row) => row[side] > 0);
  const turnOn = pressedFor(side, able) !== 'true';
  for (const row of able) {
    if (turnOn) state.cats[side].add(row.id);
    else state.cats[side].delete(row.id);
  }
  renderCategories();
}

function toggleButton(side, pressed, disabled, title) {
  const button = document.createElement('button');
  button.className = `toggle ${side}`;
  button.textContent = side.toUpperCase();
  button.setAttribute('aria-pressed', pressed);
  button.disabled = disabled;
  button.title = title;
  return button;
}

function renderCategories() {
  const list = $('cat-list');
  const scroll = list.scrollTop;
  list.textContent = '';
  if (!state.init) return;
  const same = state.models.a === state.models.b;
  const rows = visibleRows();
  const labelA = (modelOption('a') || {}).label || 'model A';
  const labelB = (modelOption('b') || {}).label || 'model B';

  if (!categoryRows().length) {
    list.innerHTML = '<div class="none">Neither model has anything that can clash.</div>';
  } else if (!rows.length) {
    list.innerHTML = '<div class="none">No category matches the search.</div>';
  }

  const groups = [];
  for (const row of rows) {
    const last = groups[groups.length - 1];
    if (last && last.name === row.group) last.rows.push(row);
    else groups.push({ name: row.group, rows: [row] });
  }

  for (const group of groups) {
    const open = !state.closedGroups.has(group.name) || $('cat-search').value.trim() !== '';
    const head = document.createElement('div');
    head.className = 'cat-group';
    const fold = document.createElement('button');
    fold.className = 'fold-btn';
    fold.setAttribute('aria-expanded', String(open));
    const name = document.createElement('span');
    name.textContent = group.name;
    const n = document.createElement('span');
    n.className = 'n';
    n.textContent = `(${group.rows.length})`;
    fold.append(name, n);
    fold.onclick = () => {
      if (open) state.closedGroups.add(group.name);
      else state.closedGroups.delete(group.name);
      renderCategories();
    };
    const spacer = document.createElement('span');
    spacer.className = 'cat-count';
    head.append(fold, spacer);
    for (const side of ['a', 'b']) {
      const able = group.rows.some((row) => row[side] > 0);
      const button = toggleButton(side, pressedFor(side, group.rows), !able,
        `Every ${group.name} category in set ${side.toUpperCase()}`);
      button.onclick = () => toggleMany(side, group.rows);
      head.append(button);
    }
    list.append(head);
    if (!open) continue;

    for (const row of group.rows) {
      const line = document.createElement('div');
      line.className = 'cat-row';
      const label = document.createElement('span');
      label.className = 'cat-name';
      label.textContent = row.name;
      label.title = row.name;
      const count = document.createElement('span');
      count.className = 'cat-count';
      if (same) {
        count.textContent = number.format(row.a);
        count.title = `${number.format(row.a)} in ${labelA}`;
      } else {
        count.textContent = `${row.a ? number.format(row.a) : '-'} / ${row.b ? number.format(row.b) : '-'}`;
        count.title = `${number.format(row.a)} in ${labelA}, ${number.format(row.b)} in ${labelB}`;
      }
      line.append(label, count);
      for (const side of ['a', 'b']) {
        const label = side === 'a' ? labelA : labelB;
        const present = row[side] > 0;
        const button = toggleButton(side,
          String(present && state.cats[side].has(row.id)), !present,
          present ? `${row.name} from ${label}, in set ${side.toUpperCase()}`
            : `${label} has no ${row.name}`);
        button.onclick = () => {
          if (state.cats[side].has(row.id)) state.cats[side].delete(row.id);
          else state.cats[side].add(row.id);
          renderCategories();
        };
        line.append(button);
      }
      list.append(line);
    }
  }
  list.scrollTop = scroll;

  for (const side of ['a', 'b']) {
    const button = $(`all-${side}`);
    button.setAttribute('aria-pressed', pressedFor(side, rows));
    button.disabled = !rows.some((row) => row[side] > 0);
  }
  updateRunState();
}

// Category ids for a preset side, by discipline.
function presetIds(groups) {
  const ids = new Set();
  for (const source of state.init.sources) {
    for (const cat of source.cats) {
      if (groups === '*' || groups.some((group) => (cat.group || '').startsWith(group))) {
        ids.add(cat.id);
      }
    }
  }
  return ids;
}

const PRESETS = {
  'structure-mep': [['Structure'], ['MEP']],
  'architecture-mep': [['Architecture'], ['MEP']],
  'mep-mep': [['MEP'], ['MEP']],
  everything: ['*', '*'],
  clear: [[], []],
};

function applyPreset(name) {
  const preset = PRESETS[name];
  if (!preset || !state.init) return false;
  state.cats = { a: presetIds(preset[0]), b: presetIds(preset[1]) };
  return true;
}

function refresh(useSelection) {
  if (state.busy) return;
  $('refresh').disabled = true;
  $('use-selection').disabled = true;
  post({ type: 'refresh', useSelection: Boolean(useSelection) });
  // Revit answers with init; if it answers with an error instead, the
  // buttons must come back.
  setTimeout(() => {
    $('refresh').disabled = false;
    $('use-selection').disabled = false;
  }, 4000);
}

function waitForInit() {
  clearTimeout(state.initTimer);
  state.initTimer = setTimeout(() => {
    if (state.init) return;
    $('cat-list').innerHTML = '<div class="none">Revit has not listed the models. '
      + 'Click Refresh from Revit above.</div>';
    $('run-hint').textContent = 'Waiting for the list of models from Revit.';
    reportError('The list of models has not arrived from Revit. Click '
      + 'Refresh from Revit; if nothing comes, the pyRevit output window has the reason.',
    '', 'no-init');
  }, INIT_WAIT);
}

// Elements in a set: the ticked categories, from that side's model.
function setTotal(side) {
  let elements = 0;
  let categories = 0;
  for (const [id, count] of countsOf(side)) {
    if (state.cats[side].has(id)) {
      elements += count;
      categories += 1;
    }
  }
  return { elements, categories };
}

function updateRunState() {
  if (!state.init) {
    $('run').disabled = true;
    return;
  }
  const totals = { a: setTotal('a'), b: setTotal('b') };
  const totalsLine = $('cat-totals');
  totalsLine.textContent = '';
  for (const side of ['a', 'b']) {
    const part = document.createElement('span');
    part.className = side;
    part.textContent = `${side.toUpperCase()} ${number.format(totals[side].elements)}`;
    if (side === 'b') totalsLine.append(' · ');
    totalsLine.append(part);
  }
  totalsLine.title = `Set A: ${number.format(totals.a.elements)} elements in `
    + `${totals.a.categories} categories. Set B: ${number.format(totals.b.elements)} `
    + `elements in ${totals.b.categories} categories.`;
  const ready = totals.a.elements > 0 && totals.b.elements > 0;
  $('run').disabled = !ready || state.busy;
  let hint = '';
  if (!totals.a.elements) hint = 'Turn on A for at least one category.';
  else if (!totals.b.elements) hint = 'Turn on B for at least one category.';
  else if (totals.a.elements * totals.b.elements > 4e8) {
    hint = 'Two large sets. This can take several minutes; you can stop it part way.';
  }
  $('run-hint').textContent = hint;
}

// What the service is sent: each real source of a side, with the ticked
// categories that source has.
function runConfig() {
  const side = (name) => {
    const entries = [];
    for (const source of sourcesOf(name)) {
      const cats = source.cats.map((cat) => cat.id).filter((id) => state.cats[name].has(id));
      if (cats.length) entries.push({ source: source.key, cats });
    }
    return entries;
  };
  return {
    a: side('a'), b: side('b'),
    scope: document.querySelector('input[name="scope"]:checked').value,
    detail: $('detail').value,
    context: $('context').checked,
  };
}

function setTolerance(mm, from) {
  const value = Math.max(0, Math.min(100, Math.round(Number(mm) || 0)));
  if (from !== 'run') $('run-tolerance').value = value;
  if (from !== 'list') $('tolerance').value = value;
  $('tolerance-value').textContent = `${value} mm`;
}

function setBusy(on, stage = 'Working') {
  state.busy = on;
  $('busy').hidden = !on;
  if (on) {
    $('busy-stage').textContent = stage;
    $('busy-count').textContent = '';
    $('busy-bar').parentElement.classList.add('unknown');
    $('cancel').disabled = false;
    $('cancel').textContent = 'Stop and show what was found';
  }
  updateRunState();
}

function run() {
  stopTour();
  select(null);
  viewer.clear();
  state.elements.clear();
  state.clashes = [];
  state.shown = [];
  state.reviewed.clear();
  state.collapsed.clear();
  $('empty').hidden = true;
  setBusy(true, 'Collecting elements');
  post({ type: 'run', config: runConfig() });
}

// -- results in -----------------------------------------------------------------

function decode(text, Kind) {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Kind(bytes.buffer);
}

function takeMesh(message) {
  const pos = decode(message.pos, Float32Array);
  const idx = decode(message.idx, Uint32Array);
  for (const item of message.items) {
    const p = pos.slice(item.v[0], item.v[0] + item.v[1]);
    const i = idx.slice(item.i[0], item.i[0] + item.i[1]);
    if (item.kind === 'c') viewer.addVolumeMesh(item.key, p, i);
    else viewer.addElementMesh(item.key, p, i);
  }
}

function takeResult(message) {
  state.stats = message.stats;
  state.levels = message.levels || [];
  state.elements = new Map(message.elements.map((meta) => [meta.key, meta]));
  state.clashes = message.clashes;
  for (const clash of state.clashes) clash.level = levelOf(clash.c[2]);
  viewer.build(message.elements, state.clashes);
  setBusy(false);

  const stats = message.stats;
  const count = state.clashes.length;
  $('tab-count').hidden = false;
  $('tab-count').textContent = number.format(count);
  $('legend').hidden = false;
  $('toolbar').hidden = false;
  showTab('clashes');
  renderList();

  const empty = $('empty');
  empty.hidden = count > 0;
  if (!count) {
    const heading = empty.querySelector('h2');
    const text = empty.querySelector('p');
    if (message.empty) {
      heading.textContent = `Set ${message.empty} is empty`;
      text.textContent = explainEmpty(message.empty.toLowerCase(), message.why);
    } else {
      heading.textContent = stats.cancelled ? 'Stopped early' : 'No clashes';
      text.textContent = `${number.format(stats.a)} elements in A and `
        + `${number.format(stats.b)} in B, ${number.format(stats.tested)} `
        + 'pairs close enough to test, none of them overlapping.';
    }
  }
  if (stats.cancelled) {
    toast(`Stopped after ${number.format(stats.tested)} of `
      + `${number.format(stats.pairs)} pairs. The list is incomplete.`, 'warn');
  }
  if (stats.contextCut) {
    toast(`${number.format(stats.contextCut)} elements that clash with nothing `
      + 'are not drawn, to keep the view responsive. Every clashing element is.', 'info');
  }
}

// What happened to a set's elements, from the tally the run keeps.
function explainEmpty(side, why) {
  const name = `Set ${side.toUpperCase()}`;
  if (!why || !why[side]) {
    return 'None of the ticked categories has elements in this scope. '
      + 'Tick more, or look at the whole model.';
  }
  const tally = why[side];
  const n = (value) => number.format(value);
  const inView = why.scope === 'view';
  if (tally.unknown && tally.unknown.length) {
    return `${name} names a model Revit no longer has. Click Refresh from Revit.`;
  }
  if (!tally.entries || !tally.categories) {
    return `Nothing is ticked in ${name}. Use a quick setup, or tick categories.`;
  }
  if (!tally.looked) {
    return inView
      ? `The active view shows none of the elements of the models in ${name}. `
        + 'Choose Whole model, or open a view that shows them.'
      : `The models in ${name} returned no elements.`;
  }
  if (!tally.matched) {
    return `${n(tally.looked)} elements looked at, none in the categories ticked in ${name}`
      + (inView ? ', among what the active view shows. Try Whole model.' : '.');
  }
  const parts = [`${n(tally.matched)} elements in the ticked categories`];
  if (tally.nobox) parts.push(`${n(tally.nobox)} with no 3D extent`);
  if (tally.clipped) parts.push(`${n(tally.clipped)} outside the active view's section box`);
  let text = `${name}: ${parts.join('; ')}.`;
  if (tally.clipped) text += ' Choose Whole model, or widen the section box.';
  return text;
}

// -- the list -------------------------------------------------------------------

function clashTitle(clash) {
  return [element(clash.a).cat, element(clash.b).cat];
}

function searchable(clash) {
  const a = element(clash.a);
  const b = element(clash.b);
  return `${a.cat} ${a.name} ${a.id} ${b.cat} ${b.name} ${b.id} ${clash.level}`.toLowerCase();
}

function groupKey(clash, by) {
  if (by === 'pair') return clashTitle(clash).join('  ×  ');
  if (by === 'level') return clash.level || 'No level';
  if (by === 'a' || by === 'b') {
    const one = element(clash[by]);
    return `${one.cat}: ${one.name || 'unnamed'}  [${one.id}]`;
  }
  return '';
}

function compute() {
  const tolerance = Number($('tolerance').value) / 1000;
  const words = $('search').value.toLowerCase().split(/\s+/).filter(Boolean);
  const hideReviewed = $('hide-reviewed').checked;
  let shallow = 0;
  let visible = state.clashes.filter((clash) => {
    if (clash.depth !== null && clash.depth !== undefined && clash.depth < tolerance) {
      shallow += 1;
      return false;
    }
    if (hideReviewed && state.reviewed.has(clash.n)) return false;
    if (words.length) {
      const text = searchable(clash);
      if (!words.every((word) => text.includes(word))) return false;
    }
    return true;
  });
  const sort = $('sort').value;
  const value = (v) => (v === null || v === undefined ? -1 : v);
  if (sort === 'depth') visible.sort((p, q) => value(q.depth) - value(p.depth) || p.n - q.n);
  else if (sort === 'volume') visible.sort((p, q) => value(q.vol) - value(p.vol) || p.n - q.n);
  else visible.sort((p, q) => p.n - q.n);

  const by = $('group').value;
  const groups = [];
  if (by === 'none') {
    groups.push({ key: '', clashes: visible });
  } else {
    const found = new Map();
    for (const clash of visible) {
      const key = groupKey(clash, by);
      if (!found.has(key)) found.set(key, []);
      found.get(key).push(clash);
    }
    for (const [key, clashes] of found) groups.push({ key, clashes });
    if (by === 'level') {
      const order = new Map(state.levels.map((level, n) => [level.name, n]));
      groups.sort((p, q) => (order.get(q.key) ?? -1) - (order.get(p.key) ?? -1));
    } else {
      groups.sort((p, q) => q.clashes.length - p.clashes.length || p.key.localeCompare(q.key));
    }
  }
  return { groups, shallow };
}

function renderList() {
  const list = $('list');
  const { groups, shallow } = compute();
  list.textContent = '';
  state.shown = [];
  let rows = 0;
  let held = 0;

  for (const group of groups) {
    const open = !state.collapsed.has(group.key);
    if (group.key) {
      const head = document.createElement('button');
      head.className = 'group-head';
      head.setAttribute('aria-expanded', String(open));
      const name = document.createElement('span');
      name.textContent = group.key;
      name.title = group.key;
      const count = document.createElement('span');
      count.className = 'count';
      count.textContent = number.format(group.clashes.length);
      head.append(name, count);
      head.onclick = () => {
        if (open) state.collapsed.add(group.key); else state.collapsed.delete(group.key);
        renderList();
      };
      list.append(head);
    }
    if (!open) continue;
    for (const clash of group.clashes) {
      state.shown.push(clash);
      if (rows >= state.rowLimit) {
        held += 1;
        continue;
      }
      rows += 1;
      list.append(clashRow(clash));
    }
  }

  if (held) {
    const more = document.createElement('button');
    more.className = 'small more';
    more.textContent = `Show ${number.format(Math.min(held, ROWS_AT_ONCE))} more`;
    more.onclick = () => {
      state.rowLimit += ROWS_AT_ONCE;
      renderList();
    };
    list.append(more);
  }
  if (!state.clashes.length) {
    list.innerHTML = '<p class="list-note">No clashes to list.</p>';
  } else if (!groups.some((group) => group.clashes.length)) {
    list.innerHTML = '<p class="list-note">Nothing matches. <b>Clear the search</b> or lower the tolerance.</p>';
  }

  const total = state.clashes.length;
  const listed = groups.reduce((sum, group) => sum + group.clashes.length, 0);
  const stats = state.stats || {};
  let summary = `<b>${number.format(listed)}</b> of <b>${number.format(total)}</b> clashes`;
  if (shallow) summary += `, ${number.format(shallow)} under the tolerance`;
  summary += `. ${number.format(stats.a || 0)} elements against `
    + `${number.format(stats.b || 0)}, ${number.format(stats.tested || 0)} pairs tested in `
    + `${stats.seconds || 0} s.`;
  const approx = state.clashes.filter((clash) => !clash.exact).length;
  if (approx) summary += ` ${number.format(approx)} approximate.`;
  $('summary').innerHTML = summary;

  viewer.setMarkers(state.shown.map((clash) => (
    { n: clash.n, c: clash.c, reviewed: state.reviewed.has(clash.n) })));
  updatePosition();
}

function clashRow(clash) {
  const row = document.createElement('button');
  row.className = 'clash' + (state.reviewed.has(clash.n) ? ' reviewed' : '');
  row.dataset.n = clash.n;
  if (state.selected && state.selected.n === clash.n) row.setAttribute('aria-current', 'true');
  const a = element(clash.a);
  const b = element(clash.b);
  const n = document.createElement('span');
  n.className = 'number';
  n.textContent = `#${clash.n + 1}`;
  const title = document.createElement('span');
  title.className = 'title';
  const cross = document.createElement('span');
  cross.className = 'x';
  cross.textContent = '×';
  title.append(a.cat, ' ', cross, ' ', b.cat);
  const measure = document.createElement('span');
  measure.className = 'measure';
  measure.textContent = clash.exact ? formatDepth(clash) : 'approx.';
  const names = document.createElement('span');
  names.className = 'names';
  names.textContent = `${a.name || a.id} · ${b.name || b.id}`;
  const sub = document.createElement('span');
  sub.className = 'sub';
  sub.textContent = clash.exact ? formatVolume(clash) : '';
  row.append(n, title, measure, names, sub);
  row.title = `${a.cat}: ${a.name}  [${a.id}]\n${b.cat}: ${b.name}  [${b.id}]`;
  row.onclick = () => select(clash);
  return row;
}

// -- selecting a clash --------------------------------------------------------------

function select(clash, options = {}) {
  state.selected = clash;
  for (const row of document.querySelectorAll('.clash[aria-current]')) {
    row.removeAttribute('aria-current');
  }
  stopPullAnimation();
  $('pull').value = 0;
  viewer.setPull(0);
  $('cut').value = 0;
  viewer.setCut(0, state.cutAxis);
  viewer.focus(clash, options.fly !== false);

  const card = $('detail-card');
  card.hidden = !clash;
  for (const id of ['pull', 'pull-play', 'cut', 'cut-axis', 'fit-clash', 'orbit']) {
    $(id).disabled = !clash;
  }
  if (!clash) {
    setOrbit(false);
    updatePosition();
    return;
  }
  const row = document.querySelector(`.clash[data-n="${clash.n}"]`);
  if (row) {
    row.setAttribute('aria-current', 'true');
    row.scrollIntoView({ block: 'nearest' });
  }
  const a = element(clash.a);
  const b = element(clash.b);
  $('card-number').textContent = `#${clash.n + 1}`;
  const kind = $('card-kind');
  kind.textContent = clash.exact ? 'Clash' : 'Approximate';
  kind.classList.toggle('approx', !clash.exact);
  $('card-depth').textContent = clash.exact ? formatDepth(clash) : 'n/a';
  $('card-volume').textContent = formatVolume(clash);
  $('card-level').textContent = clash.level || 'n/a';
  $('card-level').title = clash.level || '';
  for (const [side, one] of [['a', a], ['b', b]]) {
    $(`card-${side}-name`).textContent = `${one.cat}: ${one.name || 'unnamed'}`;
    $(`card-${side}-name`).title = `${one.cat}: ${one.name}`;
    $(`card-${side}-meta`).textContent = `ID ${one.id} · ${sourceLabel(one.src)}`;
  }
  const note = $('card-note');
  note.hidden = clash.exact;
  if (!clash.exact) {
    note.textContent = 'Found on the triangles, because Revit could not '
      + 'intersect these two as solids. There is no volume or depth'
      + (clash.partial ? ', and the search stopped at its limit.' : '.');
  }
  $('mark-reviewed').textContent = state.reviewed.has(clash.n) ? 'Reopen' : 'Mark reviewed';
  updatePosition();
  if ($('follow').checked && !options.quiet) {
    post({ type: 'select', index: clash.n, zoom: true });
  }
}

function updatePosition() {
  const at = state.selected ? state.shown.findIndex((clash) => clash.n === state.selected.n) : -1;
  $('position').textContent = state.shown.length
    ? `${at >= 0 ? at + 1 : '-'} / ${state.shown.length}` : '0';
  $('prev').disabled = $('next').disabled = $('tour').disabled = !state.shown.length;
}

function step(by) {
  if (!state.shown.length) return;
  let at = state.selected ? state.shown.findIndex((clash) => clash.n === state.selected.n) : -1;
  at = at < 0 ? (by > 0 ? 0 : state.shown.length - 1)
    : (at + by + state.shown.length) % state.shown.length;
  const clash = state.shown[at];
  // A clash held back by "Show more" has no row yet.
  if (!document.querySelector(`.clash[data-n="${clash.n}"]`) && at >= state.rowLimit) {
    state.rowLimit = at + ROWS_AT_ONCE;
    renderList();
  }
  select(clash);
}

function pickElement(key) {
  if (!key) return;
  const mine = state.shown.filter((clash) => clash.a === key || clash.b === key);
  if (!mine.length) {
    const one = element(key);
    toast(`${one.cat}: ${one.name || one.id} clashes with nothing in the list.`);
    return;
  }
  // Clicking the same element again moves on to its next clash.
  const at = state.selected ? mine.findIndex((clash) => clash.n === state.selected.n) : -1;
  select(mine[(at + 1) % mine.length]);
}

function showHover(n, x, y) {
  const tip = $('hover');
  if (n === null || n === undefined) {
    tip.hidden = true;
    return;
  }
  const clash = state.clashes[n];
  tip.textContent = `#${n + 1}  ${clashTitle(clash).join(' × ')}`;
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
  tip.hidden = false;
}

function toggleReviewed() {
  const clash = state.selected;
  if (!clash) return;
  if (state.reviewed.has(clash.n)) state.reviewed.delete(clash.n);
  else state.reviewed.add(clash.n);
  const hiding = $('hide-reviewed').checked && state.reviewed.has(clash.n);
  const at = state.shown.findIndex((one) => one.n === clash.n);
  renderList();
  if (hiding) {
    const next = state.shown[Math.min(at, state.shown.length - 1)];
    select(next || null);
  } else {
    $('mark-reviewed').textContent = state.reviewed.has(clash.n) ? 'Reopen' : 'Mark reviewed';
  }
}

// -- motion ---------------------------------------------------------------------------

function setOrbit(on) {
  $('orbit').setAttribute('aria-pressed', String(on));
  viewer.setOrbit(on);
}

function stopTour() {
  if (state.touring) clearInterval(state.touring);
  state.touring = null;
  $('tour').setAttribute('aria-pressed', 'false');
}

function toggleTour() {
  if (state.touring) return stopTour();
  if (!state.shown.length) return undefined;
  $('tour').setAttribute('aria-pressed', 'true');
  if (!state.selected) step(1);
  state.touring = setInterval(() => step(1), 3600);
  return undefined;
}

function stopPullAnimation() {
  if (state.pullAnimation) cancelAnimationFrame(state.pullAnimation);
  state.pullAnimation = null;
  $('pull-play').textContent = 'Play';
}

function playPull() {
  if (state.pullAnimation) return stopPullAnimation();
  if (!state.selected) return undefined;
  $('pull-play').textContent = 'Stop';
  const started = performance.now();
  const length = 2600;
  const frame = (now) => {
    const t = Math.min((now - started) / length, 1);
    // Out, hold, and back.
    const amount = t < 0.4 ? t / 0.4 : (t < 0.6 ? 1 : 1 - (t - 0.6) / 0.4);
    const eased = amount * amount * (3 - 2 * amount);
    $('pull').value = eased;
    viewer.setPull(eased);
    if (t < 1) state.pullAnimation = requestAnimationFrame(frame);
    else stopPullAnimation();
  };
  state.pullAnimation = requestAnimationFrame(frame);
  return undefined;
}

// -- wiring -------------------------------------------------------------------------------

function onMessage(message) {
  if (!message || typeof message !== 'object') return;
  switch (message.type) {
    case 'init':
      try {
        applyInit(message);
      } catch (error) {
        reportError(`The list of models could not be shown: ${error.message}`, error.stack);
      }
      break;
    case 'progress': {
      if (!state.busy) break;
      $('busy-stage').textContent = message.stage || 'Working';
      const meter = $('busy-bar').parentElement;
      if (message.total) {
        meter.classList.remove('unknown');
        $('busy-bar').style.width = `${Math.min(100, (message.done / message.total) * 100)}%`;
        $('busy-count').textContent =
          `${number.format(message.done)} of ${number.format(message.total)}`;
      } else {
        meter.classList.add('unknown');
        $('busy-bar').style.width = '';
        $('busy-count').textContent = message.done ? number.format(message.done) : '';
      }
      break;
    }
    case 'mesh': takeMesh(message); break;
    case 'result': takeResult(message); break;
    case 'aborted':
      setBusy(false);
      $('empty').hidden = false;
      break;
    case 'notice': toast(message.text, message.kind || 'info'); break;
    case 'error':
      $('refresh').disabled = false;
      toast(message.message, 'error', message.detail || '');
      break;
    default: break;
  }
}

function bind() {
  for (const tab of document.querySelectorAll('.tabs button')) {
    tab.onclick = () => showTab(tab.dataset.tab);
  }
  for (const side of ['a', 'b']) {
    $(`model-${side}`).onchange = () => {
      state.models[side] = $(`model-${side}`).value;
      renderCategories();
    };
    $(`all-${side}`).onclick = () => {
      if (state.init) toggleMany(side, visibleRows());
    };
  }
  $('cat-search').oninput = renderCategories;
  for (const button of document.querySelectorAll('.preset[data-preset]')) {
    button.onclick = () => {
      if (applyPreset(button.dataset.preset)) renderCategories();
    };
  }
  $('run').onclick = run;
  $('refresh').onclick = () => refresh(false);
  $('use-selection').onclick = () => refresh(true);
  $('run-tolerance').oninput = () => setTolerance($('run-tolerance').value, 'run');
  $('run-tolerance').onchange = () => setTolerance($('run-tolerance').value);
  $('cancel').onclick = () => {
    $('cancel').disabled = true;
    $('cancel').textContent = 'Stopping';
    post({ type: 'cancel' });
  };

  const relist = () => {
    state.rowLimit = ROWS_AT_ONCE;
    renderList();
  };
  $('search').oninput = relist;
  $('group').onchange = () => {
    state.collapsed.clear();
    relist();
  };
  $('sort').onchange = relist;
  $('hide-reviewed').onchange = relist;
  $('tolerance').oninput = () => {
    setTolerance($('tolerance').value, 'list');
    relist();
  };

  for (const button of document.querySelectorAll('#legend [data-layer]')) {
    button.onclick = () => {
      const on = button.getAttribute('aria-pressed') !== 'true';
      button.setAttribute('aria-pressed', String(on));
      viewer.setLayer(button.dataset.layer, on);
    };
  }
  const modes = [['ghost', 'faint'], ['solid', 'solid'], ['off', 'hidden']];
  $('context-mode').onclick = () => {
    const at = (modes.findIndex(([mode]) => mode === state.contextMode) + 1) % modes.length;
    state.contextMode = modes[at][0];
    $('context-mode').textContent = `Surroundings: ${modes[at][1]}`;
    viewer.setContextMode(state.contextMode);
  };

  $('card-close').onclick = () => select(null);
  $('select-revit').onclick = () => state.selected
    && post({ type: 'select', index: state.selected.n, zoom: true });
  $('section-revit').onclick = () => state.selected
    && post({ type: 'sectionbox', index: state.selected.n });
  $('mark-reviewed').onclick = toggleReviewed;

  $('prev').onclick = () => { stopTour(); step(-1); };
  $('next').onclick = () => { stopTour(); step(1); };
  $('tour').onclick = toggleTour;
  $('pull').oninput = () => {
    stopPullAnimation();
    viewer.setPull(Number($('pull').value));
  };
  $('pull-play').onclick = playPull;
  $('cut').oninput = () => viewer.setCut(Number($('cut').value), state.cutAxis);
  $('cut-axis').onclick = () => {
    state.cutAxis = (state.cutAxis + 1) % 3;
    $('cut-axis').textContent = 'XYZ'[state.cutAxis];
    viewer.setCut(Number($('cut').value), state.cutAxis);
  };
  $('orbit').onclick = () => setOrbit($('orbit').getAttribute('aria-pressed') !== 'true');
  $('fit-clash').onclick = () => viewer.fitClash();
  $('fit-all').onclick = () => viewer.fitAll();

  window.addEventListener('keydown', (event) => {
    const tag = event.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') {
      if (event.key === 'Escape') event.target.blur();
      return;
    }
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    const key = event.key;
    if (key === 'ArrowDown' || key === 'j') { stopTour(); step(1); }
    else if (key === 'ArrowUp' || key === 'k') { stopTour(); step(-1); }
    else if (key === 'r') toggleReviewed();
    else if (key === 'f') viewer.fitClash();
    else if (key === 'a') viewer.fitAll();
    else if (key === ' ') toggleTour();
    else if (key === 'Escape') { stopTour(); select(null); }
    else return;
    event.preventDefault();
  });

  window.matchMedia('(prefers-color-scheme: dark)')
    .addEventListener('change', () => viewer.setTheme(themeColors()));
}

async function start() {
  viewer.setTheme(themeColors());
  bind();
  select(null);
  post = await connect(onMessage);
  waitForInit();
  post({ type: 'ready' });
}

start();
