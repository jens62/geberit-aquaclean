// Generic, metadata-driven settings-control primitives for the BLE mock webuis
// (MeraMock, AlbaMock). Mirrors the visual style of
// aquaclean_console_app/static/index.html's stepper/toggle/swatch controls, but
// driven entirely by a metadata list instead of per-ID hardcoded markup/JS —
// see docs/developer/mock-service-requirements.md §6 "DRY: shared frontend
// assets with the real bridge webui". Deliberately NOT shared at runtime with
// index.html (mock-only for now) — see docs/roadmap.md for the future refactor
// that would let index.html consume this module too.
//
// Usage: mcRenderSettingsTable(document.getElementById('mc-root'), data)
// where data = {sections: [{title, rows: [{id, name, kind, value, min, max,
// options, writeUrl}]}]}. kind is one of: "stepper", "toggle", "select",
// "swatch", "text", or anything else (rendered read-only).
//
// Write contract: every control POSTs writeUrl with JSON body {value: <v>} and
// expects any 2xx response. The row's own writeUrl fully identifies the target
// (setting id baked into the URL) — this module has no knowledge of Mera's
// common/profile split or Alba's DpId space, only rows and URLs.
//
// mcConnectSSE(url, onState) opens an EventSource against url (mirrors
// aquaclean_console_app/static/index.html's connectSSE()/onmessage pattern)
// and calls onState(data) for every {"type": "state", ...} message — each
// mock's own page decides what to do with the payload (re-render the
// settings table, update a badge, etc.), same division of responsibility as
// the real bridge's onStateReceived().

function mcConnectSSE(url, onState) {
  const es = new EventSource(url);
  es.onmessage = function (e) {
    try {
      const data = JSON.parse(e.data);
      if (data.type === 'state') onState(data);
    } catch (err) {
      console.error('[SSE] parse error', err, 'raw:', e.data);
    }
  };
  return es;
}

function mcRenderSettingsTable(container, data) {
  container.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'mc-sections';
  (data.sections || []).forEach(function (section) {
    if (!section.rows || !section.rows.length) return;
    const sec = document.createElement('div');
    sec.className = 'mc-section';
    const title = document.createElement('div');
    title.className = 'mc-section-title';
    title.textContent = section.title;
    sec.appendChild(title);
    section.rows.forEach(function (row) { sec.appendChild(mcRenderRow(row)); });
    wrap.appendChild(sec);
  });
  container.appendChild(wrap);
}

function mcRenderRow(row) {
  const rowEl = document.createElement('div');
  rowEl.className = 'mc-row';
  rowEl.id = 'mc-row-' + row.id;
  const label = document.createElement('span');
  label.className = 'mc-row-label';
  label.textContent = row.name;
  rowEl.appendChild(label);
  rowEl.appendChild(mcBuildControl(row));
  return rowEl;
}

function mcBuildControl(row) {
  switch (row.kind) {
    case 'stepper': return mcBuildStepper(row);
    case 'toggle':  return mcBuildToggle(row);
    case 'select':  return mcBuildSelect(row);
    case 'swatch':  return mcBuildSwatch(row);
    case 'text':    return mcBuildText(row);
    default:        return mcBuildReadonly(row);
  }
}

function mcBuildReadonly(row) {
  const span = document.createElement('span');
  span.className = 'mc-readonly';
  span.textContent = row.value;
  return span;
}

function mcBuildStepper(row) {
  const st = document.createElement('div');
  st.className = 'mc-stepper';
  st.dataset.min = row.min;
  st.dataset.max = row.max;
  st.dataset.writeUrl = row.writeUrl;

  const dec = document.createElement('button');
  dec.type = 'button';
  dec.className = 'mc-step-btn';
  dec.textContent = '−';
  dec.onclick = function () { mcStep(st, -1); };

  const val = document.createElement('span');
  val.className = 'mc-step-val';
  val.textContent = row.value;

  const inc = document.createElement('button');
  inc.type = 'button';
  inc.className = 'mc-step-btn';
  inc.textContent = '+';
  inc.onclick = function () { mcStep(st, 1); };

  st.appendChild(dec);
  st.appendChild(val);
  st.appendChild(inc);
  return st;
}

function mcStep(stepperEl, delta) {
  const valEl = stepperEl.querySelector('.mc-step-val');
  const cur = parseInt(valEl.textContent, 10);
  const min = parseInt(stepperEl.dataset.min, 10);
  const max = parseInt(stepperEl.dataset.max, 10);
  if (isNaN(cur)) return;
  const next = cur + delta;
  if (next < min || next > max) return;
  const btn = delta < 0
    ? stepperEl.querySelector('.mc-step-btn:first-child')
    : stepperEl.querySelector('.mc-step-btn:last-child');
  mcWrite(stepperEl.dataset.writeUrl, next, btn).then(function (ok) {
    if (ok) valEl.textContent = next;
  });
}

function mcBuildToggle(row) {
  const label = document.createElement('label');
  label.className = 'mc-toggle';
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.checked = row.value === 1;
  input.onchange = function () {
    const next = input.checked ? 1 : 0;
    mcWrite(row.writeUrl, next, null).then(function (ok) {
      if (!ok) input.checked = !input.checked; // revert on failure
    });
  };
  const track = document.createElement('span');
  track.className = 'mc-toggle-track';
  label.appendChild(input);
  label.appendChild(track);
  return label;
}

function mcBuildSelect(row) {
  const sel = document.createElement('select');
  sel.className = 'mc-sel';
  (row.options || []).forEach(function (opt) {
    const o = document.createElement('option');
    o.value = opt.value;
    o.textContent = opt.label;
    if (opt.value === row.value) o.selected = true;
    sel.appendChild(o);
  });
  const prevValue = row.value;
  sel.onchange = function () {
    const next = parseInt(sel.value, 10);
    mcWrite(row.writeUrl, next, null).then(function (ok) {
      if (!ok) sel.value = prevValue;
    });
  };
  return sel;
}

function mcBuildSwatch(row) {
  const wrap = document.createElement('span');
  wrap.className = 'mc-swatch-group';
  (row.options || []).forEach(function (opt) {
    const sw = document.createElement('span');
    sw.className = 'mc-swatch' + (opt.value === row.value ? ' active' : '');
    sw.style.background = opt.color;
    sw.title = opt.label;
    sw.onclick = function () {
      mcWrite(row.writeUrl, opt.value, null).then(function (ok) {
        if (!ok) return;
        wrap.querySelectorAll('.mc-swatch').forEach(function (s) { s.classList.remove('active'); });
        sw.classList.add('active');
      });
    };
    wrap.appendChild(sw);
  });
  return wrap;
}

function mcBuildText(row) {
  const wrap = document.createElement('span');
  wrap.className = 'mc-text-group';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'mc-text';
  input.value = row.value || '';
  if (row.max) input.maxLength = row.max;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'mc-text-save';
  btn.textContent = 'Save';
  btn.onclick = function () { mcWrite(row.writeUrl, input.value, btn); };
  wrap.appendChild(input);
  wrap.appendChild(btn);
  return wrap;
}

// POSTs {value} to writeUrl, toggling loading/success/error feedback on btn (if
// given). Returns a Promise<boolean> — resolves true on a 2xx response, false
// otherwise — so callers can decide whether to keep or revert their optimistic
// UI update instead of this module guessing.
async function mcWrite(writeUrl, value, btn) {
  if (!writeUrl) return false;
  if (btn) {
    btn.disabled = true;
    btn.classList.add('loading');
    btn.classList.remove('success', 'error');
  }
  let ok = false;
  try {
    const r = await fetch(writeUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: value }),
    });
    ok = r.ok;
    if (btn) btn.classList.add(ok ? 'success' : 'error');
  } catch (_) {
    if (btn) btn.classList.add('error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove('loading');
      setTimeout(function () { btn.classList.remove('success', 'error'); }, 2000);
    }
  }
  return ok;
}
