// Tapo Studio front end. No external code: everything here runs locally.
// The access key arrives in the URL fragment (#k=...), which is never sent to
// any server. Keep it for this tab only, then tidy it out of the address bar.
const TOKEN = (() => {
  const m = location.hash.match(/(?:^#|&)k=([A-Za-z0-9_-]{20,64})/);
  let tok = m ? m[1] : '';
  try { if (tok) sessionStorage.setItem('studio-k', tok); else tok = sessionStorage.getItem('studio-k') || ''; } catch { /* storage off */ }
  if (m) { try { window.history.replaceState(null, '', location.pathname); } catch (e) { console.warn('Could not tidy the address bar', e); } }
  return tok;
})();
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

// ---------- tiny DOM builder (always uses textContent, never raw HTML) ----------
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'bg') e.style.background = v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}

// ---------- API ----------
async function api(path, data) {
  const opt = { headers: { 'X-Studio-Token': TOKEN } };
  if (data !== undefined) {
    opt.method = 'POST';
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(data);
  }
  const r = await fetch('/api/' + path, opt);
  let j = {};
  try { j = await r.json(); } catch { /* ignore */ }
  if (r.status === 403 && !j.error) throw new Error('Access refused - reopen Tapo Studio using the link in its window');
  if (r.status === 403 && j.error === 'Not authorised') { sessionStorage.removeItem('studio-k'); throw new Error('This link has expired (Tapo Studio was restarted). Use the new link shown in its window.'); }
  if (!r.ok) throw new Error(j.error || `Request failed (${r.status})`);
  return j;
}
let toastTimer;
function toast(msg, bad = false) {
  const t = $('#toast');
  t.textContent = msg; t.className = 'toast show' + (bad ? ' bad' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.className = 'toast', bad ? 6000 : 2200);
}
async function run(fn, okMsg) {
  try { const r = await fn(); if (r && r.warnings && r.warnings.length) toast(r.warnings.join('; '), true); else if (okMsg) toast(okMsg); return r; }
  catch (e) { toast(e.message, true); }
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const store = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
                set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } } };

// ---------- colour ----------
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function hsbToRgb([hh, s, v]) {
  s /= 100; v /= 100; const c = v * s, x = c * (1 - Math.abs(((hh / 60) % 2) - 1)), m = v - c;
  const [r, g, b] = hh < 60 ? [c, x, 0] : hh < 120 ? [x, c, 0] : hh < 180 ? [0, c, x] : hh < 240 ? [0, x, c] : hh < 300 ? [x, 0, c] : [c, 0, x];
  return [r, g, b].map(q => Math.round((q + m) * 255));
}
function rgbToHsb([r, g, b]) {
  r /= 255; g /= 255; b /= 255; const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  let hh = 0;
  if (d) hh = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [Math.round((hh * 60 + 360) % 360), Math.round(mx ? d / mx * 100 : 0), Math.round(mx * 100)];
}
const hexToRgb = x => [1, 3, 5].map(i => parseInt(x.slice(i, i + 2), 16));
const rgbToHex = c => '#' + c.map(v => clamp(Math.round(v), 0, 255).toString(16).padStart(2, '0')).join('');
const hsbCss = c => `rgb(${hsbToRgb(c).join(',')})`;
const hsbHex = c => rgbToHex(hsbToRgb(c));
const hexHsb = x => rgbToHsb(hexToRgb(x));
// Oklab blending
const lin = c => (c /= 255) <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
const delin = c => 255 * clamp(c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055, 0, 1);
function toLab(rgb) {
  const [r, g, b] = rgb.map(lin);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s];
}
function fromLab([L, a, b]) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3, m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3, s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3;
  return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s].map(delin);
}
function gradient(hexes, n, loop = false, repeat = 1) {
  const pts = hexes.map(x => toLab(hexToRgb(x)));
  if (loop) pts.push(pts[0]);
  const out = [];
  for (let i = 0; i < n; i++) {
    if (pts.length === 1) { out.push(rgbToHsb(fromLab(pts[0]))); continue; }
    let x = (loop ? i / n : i / Math.max(1, n - 1)) * repeat;
    if (loop) x %= 1; else { x %= 2; if (x > 1) x = 2 - x; }   // repeats ping-pong so there are no hard jumps
    const f = x * (pts.length - 1), j = Math.min(pts.length - 2, Math.floor(f)), t = f - j;
    out.push(rgbToHsb(fromLab(pts[j].map((v, k) => v * (1 - t) + pts[j + 1][k] * t))));
  }
  return out;
}
const SAFE_HEX = /^#[0-9a-f]{6}$/i;
function stripBar(el, cols) {
  if (!Array.isArray(cols)) cols = [];
  el.replaceChildren(...cols.slice(0, 120).map(c => h('i', { bg: Array.isArray(c) ? hsbCss(c.slice(0, 3).map(v => clamp(Number(v) || 0, 0, 360))) : (SAFE_HEX.test(c) ? c : '#000') })));
}

// ---------- state ----------
let S = { strips: [], live: {} }, CAT = null, SET = null;
let selected = store.get('targets', null);
// Which lights the controls apply to. A remembered selection can refer to lights that no longer exist
// (e.g. the demo lights from an earlier run), so only ids that still exist count; none left = all lights.
const targets = () => {
  const valid = new Set(S.strips.map(s => s.id));
  const sel = (selected || []).filter(id => valid.has(id));
  return sel.length ? sel : S.strips.map(s => s.id);
};
const capsOfTargets = () => {
  const set = new Set(['power', 'brightness']);
  for (const s of S.strips) if (targets().includes(s.id)) (s.caps || []).forEach(c => set.add(c));
  return set;
};
function applyCaps() {
  const caps = capsOfTargets();
  $$('[data-cap]').forEach(el => { el.hidden = !caps.has(el.dataset.cap); });
  const sel = $('[role=tab][aria-selected=true]');
  if (sel && sel.hidden) $('#tab-home').click();
  const onlySingle = S.strips.length && S.strips.filter(s => targets().includes(s.id)).every(s => !(s.caps || []).includes('zones'));
  $('#liveNote').hidden = !onlySingle;
}
const firstStrip = () => S.strips.find(s => targets().includes(s.id)) || S.strips[0];
const act = (kind, params, msg) => run(() => api('action', { targets: targets(), kind, params }), msg);

function renderTargets() {
  const box = $('#targets');
  $$('.chip', box).forEach(c => c.remove());
  for (const s of S.strips) {
    const cb = h('input', { type: 'checkbox', checked: targets().includes(s.id) || null });
    cb.addEventListener('change', () => {
      const ids = $$('.chip input', box).map((c, i) => c.checked ? S.strips[i].id : null).filter(Boolean);
      selected = ids.length ? ids : null; store.set('targets', selected); renderTargets(); syncZonesLength(); applyCaps(); renderStatus();
    });
    box.append(h('label', { class: 'chip', title: s.error || (s.connected ? 'Connected' : 'Not connected') },
      cb, h('span', { class: 'dot' + (s.connected ? ' on' : ''), 'aria-hidden': 'true' }), s.name,
      h('span', { class: 'sr-only', text: s.connected ? ' (connected)' : ' (not connected)' })));
  }
  if (!S.strips.length) box.append(h('span', { class: 'chip muted', text: 'No lights yet - add one in Settings' }));
  applyCaps();
}

function renderStatus() {
  $('#demoBadge').hidden = !S.mock;
  const s = firstStrip();
  const banner = $('#banner');
  const errs = S.strips.filter(x => x.error).map(x => `${x.name}: ${x.error}`);
  if (!S.strips.length) { banner.hidden = false; banner.textContent = 'Welcome! Go to Settings, enter your Tapo login and add your light.'; }
  else if (errs.length) { banner.hidden = false; banner.textContent = errs.join('  •  '); }
  else banner.hidden = true;
  if (s) {
    stripBar($('#stripPreview'), s.frame && s.frame.length ? s.frame : [[0, 0, 0]]);
    const label = s.active_effect ? `Effect: ${s.active_effect}` : ({ frame: 'Zone pattern', color: 'Colour', white: 'White', effect: 'Custom effect', preset: 'Effect', segment: 'Scene animation' }[s.mode] || '');
    $('#previewLabel').textContent = `${s.name}${label ? ' - ' + label : ''}${s.on ? '' : ' (off)'}`;
  }
  const cards = $('#stripCards');
  cards.replaceChildren(...S.strips.map(x => h('div', { class: 'tile' },
    h('span', { class: 'name', text: x.name }),
    h('span', { class: x.connected ? 'muted' : 'err', text: x.connected ? `Connected · ${x.model} · ${x.ip}` : (x.error ? 'Problem connecting' : 'Not connected') }),
    h('span', { class: 'muted', text: x.write_ms ? `Command time ≈ ${x.write_ms} ms` : '' }))));
  renderLive();
}

async function poll() {
  try {
    S = await api('state');
    if (!poll.once) { poll.once = true; renderTargets(); syncZonesLength(); }
    else if ($$('.chip', $('#targets')).length !== Math.max(1, S.strips.length)) renderTargets();
    renderStatus();
  } catch (e) { $('#banner').hidden = false; $('#banner').textContent = 'Lost contact with Tapo Studio. Is the app window still open?'; }
  const fast = S.live && S.live.mode === 'music' && !$('#panel-live').hidden;
  setTimeout(poll, fast ? 250 : 2000);
}

// ---------- tabs ----------
function initTabs() {
  const tabs = $$('[role=tab]');
  const show = t => {
    tabs.forEach(x => { const on = x === t; x.setAttribute('aria-selected', on); x.tabIndex = on ? 0 : -1; $('#' + x.getAttribute('aria-controls')).hidden = !on; });
    store.set('tab', t.id);
  };
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => show(t));
    t.addEventListener('keydown', e => {
      const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if (d) { const n = tabs[(i + d + tabs.length) % tabs.length]; n.focus(); show(n); }
    });
  });
  const saved = $('#' + store.get('tab', 'tab-home'));
  if (saved) show(saved);
}

// ---------- header controls ----------
function initHeader() {
  $('#btnOn').onclick = () => act('power', { on: true }, 'On');
  $('#btnOff').onclick = () => act('power', { on: false }, 'Off');
  const b = $('#brightness');
  const send = debounce(v => act('brightness', { value: v }), 250);
  b.addEventListener('input', () => { $('#brightnessOut').textContent = b.value + '%'; b.setAttribute('aria-valuetext', b.value + '%'); send(+b.value); });
}

// ---------- colour tab ----------
const QUICK = ['#ff0000', '#ff6a00', '#ffc400', '#9dff00', '#00ff6a', '#00e5ff', '#0066ff', '#6a00ff', '#ff00d4', '#ff4d88', '#ffffff'];
function initColour() {
  $('#quickColors').replaceChildren(...QUICK.map(c => h('button', { type: 'button', class: 'swatch', bg: c, 'aria-label': 'Colour ' + c,
    onclick: () => { $('#colorPick').value = c; applyColor(); } })));
  const applyColor = () => { const [hh, s, v] = hexHsb($('#colorPick').value); act('color', { h: hh, s: Math.max(1, s), b: Math.max(1, Math.round(v * $('#brightness').value / 100)) }); };
  $('#applyColor').onclick = applyColor;
  const k = $('#kelvin');
  k.oninput = () => $('#kelvinOut').textContent = k.value + ' K';
  $$('[data-k]').forEach(btn => btn.onclick = () => { k.value = btn.dataset.k; k.oninput(); applyWhite(); });
  const applyWhite = () => act('white', { k: +k.value, b: +$('#brightness').value });
  $('#applyWhite').onclick = applyWhite;
}

// ---------- paint ----------
let zones = [], undoStack = [];
const zoneCount = () => (firstStrip() || { segments: 50 }).segments;
function syncZonesLength() {
  const n = zoneCount();
  if (zones.length !== n) {
    const s = firstStrip();
    zones = (s && s.frame && s.frame.length === n) ? s.frame.map(c => [...c]) : Array.from({ length: n }, (_, i) => zones[i] || [0, 0, 0]);
    buildZones();
  }
}
function buildZones() {
  const g = $('#zones');
  g.style.gridTemplateColumns = `repeat(${Math.min(25, zones.length)}, 1fr)`;
  g.replaceChildren(...zones.map((c, i) => h('button', { type: 'button', class: 'zone', role: 'gridcell', 'data-i': i, tabindex: i ? -1 : 0 })));
  paintZones();
}
function paintZones() {
  $$('.zone').forEach((z, i) => { z.style.background = hsbCss(zones[i]); z.setAttribute('aria-label', `Zone ${i + 1}: ${zones[i][2] ? hsbHex(zones[i]) : 'off'}`); });
  stripBar($('#stripPreview'), zones);
}
const brushHsb = () => { const c = hexHsb($('#brush').value); c[2] = Math.round(c[2] * $('#brushBri').value / 100); return c; };
const tool = () => $('input[name=tool]:checked').value;
function pushHistory() { undoStack.push(zones.map(c => [...c])); if (undoStack.length > 50) undoStack.shift(); }
let sendingPaint = false, paintQueued = false;
async function sendPaint() {
  if (sendingPaint) { paintQueued = true; return; }
  sendingPaint = true;
  const st = $('#paintStatus');
  try {
    const r = await api('action', { targets: targets(), kind: 'paint', params: { colors: zones, transition: +$('#paintFade').value } });
    const when = new Date().toLocaleTimeString();
    if (r.warnings && r.warnings.length) { st.textContent = `${when}: ${r.warnings.join(' ')}`; st.classList.add('err'); toast(r.warnings.join('; '), true); }
    else { st.textContent = `${when}: sent, and the light confirms it is showing this pattern.`; st.classList.remove('err'); }
  } catch (e) { st.textContent = `Not sent: ${e.message}`; st.classList.add('err'); toast(e.message, true); }
  sendingPaint = false;
  if (paintQueued) { paintQueued = false; sendPaint(); }
}
const autoSend = debounce(() => { if ($('#livePaint').checked) sendPaint(); }, 300);
function applyZone(i) {
  const t = tool();
  if (t === 'pick') { $('#brush').value = hsbHex(zones[i][2] ? zones[i] : [0, 0, 100]); toast('Picked colour'); return; }
  zones[i] = t === 'off' ? [0, 0, 0] : brushHsb();
  paintZones(); autoSend();
}
function initPaint() {
  const g = $('#zones');
  let down = false;
  g.addEventListener('pointerdown', e => { const z = e.target.closest('.zone'); if (!z) return; down = true; pushHistory(); applyZone(+z.dataset.i); g.setPointerCapture(e.pointerId); });
  g.addEventListener('pointermove', e => { if (!down) return; const z = document.elementFromPoint(e.clientX, e.clientY)?.closest('.zone'); if (z) applyZone(+z.dataset.i); });
  g.addEventListener('pointerup', () => down = false);
  g.addEventListener('keydown', e => {
    const z = e.target.closest('.zone'); if (!z) return;
    const i = +z.dataset.i, cols = Math.min(25, zones.length);
    const d = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols, Home: -i, End: zones.length - 1 - i }[e.key];
    if (d !== undefined) { e.preventDefault(); const n = $$('.zone')[clamp(i + d, 0, zones.length - 1)]; z.tabIndex = -1; n.tabIndex = 0; n.focus(); }
    else if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); pushHistory(); applyZone(i); }
  });
  $('#brushBri').oninput = () => $('#brushBriOut').textContent = $('#brushBri').value + '%';
  const tools = {
    fill: () => zones = zones.map(() => brushHsb()),
    left: () => zones.push(zones.shift()),
    right: () => zones.unshift(zones.pop()),
    reverse: () => zones.reverse(),
    mirror: () => { const n = zones.length; for (let i = 0; i < n / 2; i++) zones[n - 1 - i] = [...zones[i]]; },
    clear: () => zones = zones.map(() => [0, 0, 0]),
    random: () => { const p = currentPalette(); zones = zones.map(() => hexHsb(p[Math.floor(Math.random() * p.length)])); },
    undo: () => { if (undoStack.length) zones = undoStack.pop(); },
  };
  $$('[data-tool]').forEach(b => b.onclick = () => { if (b.dataset.tool !== 'undo') pushHistory(); tools[b.dataset.tool](); paintZones(); autoSend(); });
  $('#sendPaint').onclick = () => sendPaint().then(() => toast('Sent'));
  $('#savePaint').onclick = () => saveScene('paint', { colors: zones, transition: +$('#paintFade').value });

  // gradient stops
  const stops = store.get('gradStops', ['#ff5e3a', '#a234ff', '#00c3ff']);
  setStops(stops);
  $('#addStop').onclick = () => { const s = getStops(); if (s.length < 16) setStops([...s, '#ffffff']); };
  $('#applyGrad').onclick = () => {
    pushHistory(); store.set('gradStops', getStops());
    zones = gradient(getStops(), zones.length, $('#gradLoop').checked, +$('#gradRepeat').value || 1);
    paintZones(); sendPaint();
  };
  $('#palGrad').onclick = () => { pushHistory(); zones = gradient(currentPalette(), zones.length, true); paintZones(); sendPaint(); };
  $('#palToStops').onclick = () => setStops(currentPalette());
  $('#paletteSel').onchange = () => stripBar($('#palettePreview'), gradient(currentPalette(), 40, true));
  $('#palImage').onchange = paletteFromImage;
  $('#savePalette').onclick = () => run(async () => { await api('collection/save', { collection: 'palettes', item: { name: $('#palName').value, colors: getStops() } }); await loadSettings(); fillPalettes(); }, 'Palette saved');
}
function setStops(list) {
  const box = $('#gradStops');
  box.replaceChildren(...list.map((c, i) => h('span', { class: 'stop' },
    h('input', { type: 'color', value: c, 'aria-label': `Gradient colour ${i + 1}` }),
    list.length > 1 ? h('button', { type: 'button', 'aria-label': `Remove colour ${i + 1}`, text: '×', onclick: () => setStops(getStops().filter((_, j) => j !== i)) }) : null)));
}
const getStops = () => $$('#gradStops input').map(i => i.value);
function allPalettes() {
  const out = { ...(CAT ? CAT.palettes : {}) };
  for (const p of (SET && Array.isArray(SET.palettes) ? SET.palettes : [])) out['★ ' + p.name] = p.colors;
  return out;
}
const currentPalette = () => allPalettes()[$('#paletteSel').value] || ['#ffffff'];
function fillPalettes() {
  const names = Object.keys(allPalettes());
  for (const sel of [$('#paletteSel'), ...$$('.palSelect')]) {
    const v = sel.value;
    sel.replaceChildren(...names.map(n => h('option', { value: n, text: n })));
    if (names.includes(v)) sel.value = v;
  }
  $('#paletteSel').onchange();
}
function paletteFromImage(e) {
  const f = e.target.files[0]; if (!f) return;
  const img = new Image(); const url = URL.createObjectURL(f);
  img.onload = () => {
    const c = document.createElement('canvas'); const W = 80, H = Math.max(1, Math.round(80 * img.height / img.width));
    c.width = W; c.height = H; const x = c.getContext('2d'); x.drawImage(img, 0, 0, W, H);
    const d = x.getImageData(0, 0, W, H).data; const px = [];
    for (let i = 0; i < d.length; i += 4) { const hsb = rgbToHsb([d[i], d[i + 1], d[i + 2]]); if (hsb[2] > 15) px.push([d[i], d[i + 1], d[i + 2]]); }
    URL.revokeObjectURL(url);
    if (!px.length) return toast('That image is too dark', true);
    // k-means (k=6) in Oklab
    const labs = px.map(toLab); let cent = Array.from({ length: 6 }, (_, k) => labs[Math.floor(k * labs.length / 6)]);
    for (let it = 0; it < 10; it++) {
      const sum = cent.map(() => [0, 0, 0, 0]);
      for (const p of labs) { let bi = 0, bd = 1e9; cent.forEach((c, k) => { const dd = (c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2 + (c[2] - p[2]) ** 2; if (dd < bd) { bd = dd; bi = k; } }); const s = sum[bi]; s[0] += p[0]; s[1] += p[1]; s[2] += p[2]; s[3]++; }
      cent = cent.map((c, k) => sum[k][3] ? [sum[k][0] / sum[k][3], sum[k][1] / sum[k][3], sum[k][2] / sum[k][3]] : c);
    }
    const cols = cent.map(fromLab).map(rgb => { const hsb = rgbToHsb(rgb); hsb[1] = Math.min(100, hsb[1] * 1.25); hsb[2] = Math.max(hsb[2], 70); return hsbHex(hsb); });
    $('#imgPalette').replaceChildren(...cols.map(c => h('span', { class: 'swatch', bg: c, role: 'img', 'aria-label': c })));
    setStops(cols); toast('Colours loaded into the gradient - press "Paint gradient"');
  };
  img.src = url;
}

// ---------- effects ----------
const FX_FIELDS = {
  common: [['transition', 'Fade between steps (ms)', 0, 65535, 1500], ['duration', 'Pause on each step (ms)', 0, 600000, 0]],
  sequence: [['spread', 'Spread: zones each colour blends over', 1, 50, 8], ['direction', 'Direction (1 forward, 2 backward, 3 and 4 other patterns)', 1, 4, 1], ['repeat_times', 'Repeat count (0 = forever)', 0, 255, 0]],
  pulse: [['spread', 'Spread', 1, 50, 1], ['direction', 'Direction', 1, 4, 1], ['repeat_times', 'Repeat count (0 = forever)', 0, 255, 0]],
  static: [['spread', 'Spread', 0, 50, 0]],
  random: [['fadeoff', 'Fade-out (ms)', 0, 65535, 1000], ['random_seed', 'Random seed', 1, 1000, 24]],
};
const RANGES = [['hue_range', 'Random hue range (0-360)', 360], ['saturation_range', 'Saturation range (0-100)', 100], ['brightness_range', 'Brightness range (0-100)', 100], ['transition_range', 'Random fade range (ms)', 65535]];
let fxEditing = null;
function fxColorsSet(list) {
  const box = $('#fxColors');
  box.replaceChildren(...list.map((c, i) => h('span', { class: 'stop' },
    h('input', { type: 'color', value: hsbHex(c), 'aria-label': `Colour ${i + 1}` }),
    h('input', { type: 'number', min: 0, max: 100, value: c[2], 'aria-label': `Colour ${i + 1} brightness`, title: 'Brightness (0 = off)', class: 'bri' }),
    h('button', { type: 'button', text: '×', 'aria-label': `Remove colour ${i + 1}`, onclick: () => fxColorsSet(fxColorsGet().filter((_, j) => j !== i)) }))));
}
const fxColorsGet = () => $$('#fxColors .stop').map(s => { const c = hexHsb($('input[type=color]', s).value); c[2] = clamp(+$('.bri', s).value, 0, 100); if (c[2] && !hexHsb($('input[type=color]', s).value)[2]) c[1] = 0; return c; });
function fxFieldsBuild(e = {}) {
  const t = $('#fxType').value, box = $('#fxFields');
  const num = ([k, label, mn, mx, d]) => h('label', {}, label, h('input', { type: 'number', 'data-f': k, min: mn, max: mx, value: e[k] ?? d }));
  const kids = [...FX_FIELDS.common, ...(FX_FIELDS[t] || [])].map(num);
  if (t === 'random') {
    for (const [k, label, mx] of RANGES) {
      const v = e[k] || (k === 'hue_range' ? [0, 360] : k === 'transition_range' ? [200, 1000] : [50, 100]);
      kids.push(h('fieldset', {}, h('legend', { text: label }),
        h('label', { class: 'row' }, 'from', h('input', { type: 'number', 'data-r': k, 'data-i': 0, min: 0, max: mx, value: v[0] }),
          'to', h('input', { type: 'number', 'data-r': k, 'data-i': 1, min: 0, max: mx, value: v[1] }))));
    }
    const init = (e.init_states || [[30, 80, 100]])[0];
    kids.push(h('label', { class: 'row' }, 'Twinkle starting colour', h('input', { type: 'color', id: 'fxInit', value: hsbHex(init) })));
    kids.push(h('p', { class: 'muted', text: 'For Random effects, the colour list above is the background the twinkles fade back to.' }));
  }
  kids.push(h('details', {}, h('summary', { text: 'Zone mapping (advanced)' }),
    h('label', {}, 'Zones list (comma separated, 0 = whole strip)', h('input', { type: 'text', 'data-list': 'segments', value: (e.segments || [0]).join(',') })),
    h('label', {}, 'Expansion strategy (1 = repeat pattern along strip, 2 = stretch)', h('input', { type: 'number', 'data-f': 'expansion_strategy', min: 0, max: 10, value: e.expansion_strategy ?? 1 }))));
  box.replaceChildren(...kids);
}
function fxLoad(e, asCopy = true) {
  fxEditing = asCopy ? null : (e.uid || null);
  $('#fxName').value = (e.name || 'My effect') + (asCopy && !e.custom ? ' (my copy)' : '');
  $('#fxType').value = e.type || 'sequence';
  $('#fxBri').value = e.brightness || 100;
  const cols = e.type === 'random' ? (e.backgrounds || e.display_colors) : (e.sequence || e.display_colors);
  fxColorsSet(cols && cols.length ? cols : [[0, 100, 100], [240, 100, 100]]);
  fxFieldsBuild(e);
  $('#fxForm').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function fxBuild() {
  const t = $('#fxType').value;
  const e = { name: $('#fxName').value, type: t, custom: 1, brightness: +$('#fxBri').value, uid: fxEditing || ('ed-' + $('#fxName').value) };
  $$('#fxFields [data-f]').forEach(i => { if (i.value !== '') e[i.dataset.f] = +i.value; });
  $$('#fxFields [data-r]').forEach(i => { (e[i.dataset.r] ||= [0, 0])[+i.dataset.i] = +i.value; });
  $$('#fxFields [data-list]').forEach(i => e[i.dataset.list] = i.value.split(',').map(x => parseInt(x, 10)).filter(x => !isNaN(x)));
  const cols = fxColorsGet();
  if (t === 'random') { e.backgrounds = cols; e.init_states = [$('#fxInit') ? hexHsb($('#fxInit').value) : cols[0]]; e.display_colors = cols.slice(0, 8); }
  else e.sequence = cols;
  return e;
}
function effectTile(e, onPlay, extra = []) {
  return h('div', { class: 'tile' }, h('span', { class: 'name', text: e.name }),
    (() => { const b = h('div', { class: 'strip' }); stripBar(b, gradientFromHsb(e.display_colors || e.sequence || [[0, 0, 50]])); return b; })(),
    h('div', { class: 'acts' }, h('button', { type: 'button', class: 'btn small primary', text: 'Play', 'aria-label': 'Play ' + e.name, onclick: onPlay }),
      h('button', { type: 'button', class: 'btn small', text: 'Edit a copy', 'aria-label': 'Edit a copy of ' + e.name, onclick: () => fxLoad(e) }), ...extra));
}
const gradientFromHsb = cols => cols.length > 1 ? gradient(cols.map(hsbHex), 24, true) : cols;
function renderEffects() {
  $('#fxPresets').replaceChildren(...CAT.lighting_presets.map(p => effectTile(p, () => act('lighting_preset', { name: p.name }, p.name))));
  $('#fxStudio').replaceChildren(...CAT.studio_effects.map(p => effectTile(p, () => act('effect', { effect: p }, p.name))));
  const mine = (SET.effects || []);
  $('#fxMine').replaceChildren(...(mine.length ? mine.map(p => effectTile(p, () => act('effect', { effect: p }, p.name), [
    h('button', { type: 'button', class: 'btn small', text: 'Edit', onclick: () => fxLoad(p, false) }),
    h('button', { type: 'button', class: 'btn small danger', text: 'Delete', 'aria-label': 'Delete ' + p.name,
      onclick: () => confirm(`Delete "${p.name}"?`) && run(async () => { await api('collection/delete', { collection: 'effects', id: p.id_key }); await loadSettings(); renderEffects(); }) })]))
    : [h('p', { class: 'muted', text: 'None yet. Make one in the editor.' })]));
}
function initEffects() {
  $('#fxType').onchange = () => fxFieldsBuild(fxBuild());
  $('#fxAddColor').onclick = () => fxColorsSet([...fxColorsGet(), [Math.floor(Math.random() * 360), 100, 100]]);
  $('#fxFromPalette').onclick = () => fxColorsSet(currentPalette().map(hexHsb));
  $('#fxForm').onsubmit = e => { e.preventDefault(); act('effect', { effect: fxBuild() }, 'Playing'); };
  $('#fxSave').onclick = () => run(async () => { const it = await api('collection/save', { collection: 'effects', item: { ...fxBuild(), id: fxEditing || undefined } }); fxEditing = it.id_key; await loadSettings(); renderEffects(); }, 'Saved to My effects');
  $('#fxScene').onclick = () => saveScene('effect', { effect: fxBuild() }, $('#fxName').value);
  $('#fxJsonShow').onclick = () => run(async () => { $('#fxJson').value = JSON.stringify(await api('effects/preview', { effect: fxBuild() }), null, 1); });
  $('#fxJsonLoad').onclick = () => { try { const e = JSON.parse($('#fxJson').value); if (typeof e !== 'object' || !e) throw 0; fxLoad(e); toast('Loaded'); } catch { toast('That is not valid effect JSON', true); } };
  fxLoad(CAT.lighting_presets.find(p => p.name === 'Aurora') || {});
}

// ---------- scenes ----------
async function saveScene(kind, data, defName) {
  const name = prompt('Name this scene:', defName || 'My scene');
  if (!name) return;
  await run(async () => { await api('scenes/save', { name, kind, data }); await loadSettings(); renderScenes(); }, 'Scene saved');
}
const KIND_LABEL = { paint: 'Zone pattern', effect: 'Effect', lighting_preset: 'TP-Link effect', segment_preset: 'Scene animation', segment_custom: 'Custom animation', color: 'Colour', white: 'White' };
function sceneSwatch(sc) {
  const d = sc.data || {}; const b = h('div', { class: 'strip' });
  const cols = d.colors || d.effect?.display_colors || d.effect?.states?.map(s => s.slice(0, 3)) || (sc.kind === 'segment_preset' ? CAT.segment_presets.find(p => p.enum === d.enum)?.colors.map(c => c.slice(0, 3)) : null)
    || (sc.kind === 'lighting_preset' ? CAT.lighting_presets.find(p => p.name === d.name)?.display_colors : null) || (d.h != null ? [[d.h, d.s, d.b]] : [[40, 20, 90]]);
  stripBar(b, cols); return b;
}
function renderScenes() {
  const scenes = SET.scenes || [];
  const applyScene = sc => run(() => api('scenes/apply', { id: sc.id, targets: targets() }), sc.name);
  $('#homeScenes').replaceChildren(...(scenes.length ? scenes.map(sc => h('button', { type: 'button', class: 'tile', onclick: () => applyScene(sc) },
    h('span', { class: 'name', text: sc.name }), sceneSwatch(sc), h('span', { class: 'muted', text: KIND_LABEL[sc.kind] || sc.kind })))
    : [h('p', { class: 'muted', text: 'No saved scenes yet.' })]));
  $('#sceneList').replaceChildren(...(scenes.length ? scenes.map(sc => h('div', { class: 'item' },
    h('span', { class: 'grow' }, h('strong', { text: sc.name }), ' ', h('span', { class: 'muted', text: KIND_LABEL[sc.kind] || '' })),
    h('button', { type: 'button', class: 'btn small primary', text: 'Play', 'aria-label': 'Play ' + sc.name, onclick: () => applyScene(sc) }),
    h('button', { type: 'button', class: 'btn small danger', text: 'Delete', 'aria-label': 'Delete ' + sc.name,
      onclick: () => confirm(`Delete scene "${sc.name}"?`) && run(async () => { await api('scenes/delete', { id: sc.id }); await loadSettings(); renderScenes(); }) })))
    : [h('p', { class: 'muted', text: 'Nothing saved yet.' })]));
  // playlists
  const pls = SET.playlists || [];
  $('#playlists').replaceChildren(...(pls.length ? pls.map(p => h('div', { class: 'item' },
    h('span', { class: 'grow' }, h('strong', { text: p.name }), ' ', h('span', { class: 'muted', text: `${p.steps.length} steps${p.shuffle ? ', shuffled' : ''}` })),
    h('button', { type: 'button', class: 'btn small primary', text: 'Play', onclick: () => run(() => api('live/start', { mode: 'playlist', params: { playlist_id: p.id }, targets: targets() }), 'Playlist started') }),
    h('button', { type: 'button', class: 'btn small danger', text: 'Delete', onclick: () => confirm(`Delete playlist "${p.name}"?`) && run(async () => { await api('collection/delete', { collection: 'playlists', id: p.id }); await loadSettings(); renderScenes(); }) })))
    : [h('p', { class: 'muted', text: 'No playlists yet.' })]));
  $$('#plSteps select').forEach(fillSceneSelect);
  fillScheduleSelects();
}
function fillSceneSelect(sel) {
  const v = sel.value;
  sel.replaceChildren(...(SET.scenes || []).map(s => h('option', { value: s.id, text: s.name })));
  if (v) sel.value = v;
}
function initScenes() {
  const filt = $('#segFilter');
  const draw = () => {
    const q = filt.value.toLowerCase();
    $('#segPresets').replaceChildren(...CAT.segment_presets.filter(p => p.label.toLowerCase().includes(q)).map(p => h('div', { class: 'tile' },
      h('span', { class: 'name', text: p.label }), (() => { const b = h('div', { class: 'strip' }); stripBar(b, p.colors.map(c => c.slice(0, 3))); return b; })(),
      h('div', { class: 'acts' }, h('button', { type: 'button', class: 'btn small primary', text: 'Play', 'aria-label': 'Play ' + p.label, onclick: () => act('segment_preset', { enum: p.enum }, p.label) }),
        h('button', { type: 'button', class: 'btn small', text: 'Save', 'aria-label': 'Save ' + p.label + ' as scene', onclick: () => saveScene('segment_preset', { enum: p.enum }, p.label) })))));
  };
  filt.oninput = draw; draw();
  $('#segType').replaceChildren(...CAT.segment_types.map(t => h('option', { value: t, text: t[0].toUpperCase() + t.slice(1) })));
  const setCols = list => $('#segColors').replaceChildren(...list.map((c, i) => h('span', { class: 'stop' },
    h('input', { type: 'color', value: c, 'aria-label': `Colour ${i + 1}` }),
    h('button', { type: 'button', text: '×', 'aria-label': `Remove colour ${i + 1}`, onclick: () => setCols(getCols().filter((_, j) => j !== i)) }))));
  const getCols = () => $$('#segColors input').map(i => i.value);
  setCols(['#ff2a68', '#00c3ff', '#ffc400']);
  $('#segAdd').onclick = () => setCols([...getCols(), '#ffffff']);
  const build = () => ({ name: $('#segName').value, type: $('#segType').value, brightness: +$('#segBri').value, states: getCols().map(c => [...hexHsb(c), 0]), uid: 'seg-' + $('#segName').value });
  $('#segForm').onsubmit = e => { e.preventDefault(); act('segment_custom', { effect: build() }, 'Playing'); };
  $('#segSave').onclick = () => saveScene('segment_custom', { effect: build() }, $('#segName').value);
  // playlists
  const addStep = () => { const sel = h('select', { 'aria-label': 'Scene' }); fillSceneSelect(sel);
    const row = h('div', { class: 'item' }, sel, h('label', { class: 'row' }, 'for', h('input', { type: 'number', min: 5, max: 86400, value: 120, 'aria-label': 'Seconds' }), 'seconds'),
      h('button', { type: 'button', class: 'btn small danger', text: 'Remove', onclick: () => row.remove() }));
    $('#plSteps').append(row); };
  $('#plAdd').onclick = () => { if (!(SET.scenes || []).length) return toast('Save some scenes first', true); addStep(); };
  $('#plForm').onsubmit = e => { e.preventDefault();
    const steps = $$('#plSteps .item').map(r => ({ scene_id: $('select', r).value, seconds: +$('input', r).value }));
    if (!steps.length) return toast('Add at least one step', true);
    run(async () => { await api('collection/save', { collection: 'playlists', item: { name: $('#plName').value, shuffle: $('#plShuffle').checked, steps } }); $('#plSteps').replaceChildren(); await loadSettings(); renderScenes(); }, 'Playlist saved'); };
}

// ---------- live ----------
const liveMode = () => $('input[name=liveMode]:checked').value;
function liveParams() {
  const card = $(`[data-mode="${liveMode()}"]`); const p = {};
  $$('[data-p]', card).forEach(i => { p[i.dataset.p] = i.type === 'checkbox' ? i.checked : (i.type === 'range' || i.type === 'number') ? +i.value : i.value; });
  if (p.palette && allPalettes()[p.palette]) p.palette_colors = allPalettes()[p.palette];
  return p;
}
function renderLive() {
  const L = S.live || {};
  const names = { music: 'Smart music', screen: 'Screen sync', animation: 'Animation', daylight: 'Daylight white', playlist: 'Playlist' };
  $('#liveStatus').textContent = L.error ? 'Problem: ' + L.error : L.mode ? `${names[L.mode]} is running${L.status && L.status !== 'running' ? ' - ' + L.status : ''}.` : 'Nothing running.';
  $('#liveStatus').classList.toggle('err', !!L.error);
  const m = L.music;
  if (m) {
    $('#mBass').style.width = m.levels.bass * 100 + '%'; $('#mSnare').style.width = m.levels.snare * 100 + '%'; $('#mHats').style.width = m.levels.hats * 100 + '%';
    $('#hBass').classList.toggle('on', m.hits.bass < 0.15); $('#hSnare').classList.toggle('on', m.hits.snare < 0.15); $('#hHats').classList.toggle('on', m.hits.hats < 0.15);
    $('#mBpm').textContent = m.bpm ? `${Math.round(m.bpm)} BPM ${m.locked ? '(locked)' : '(listening…)'}` : 'listening…';
  }
}
function initLive() {
  $('#animSel').replaceChildren(...CAT.animations.map(a => h('option', { value: a, text: a[0].toUpperCase() + a.slice(1) })));
  const showCard = () => $$('[data-mode]').forEach(c => c.hidden = c.dataset.mode !== liveMode());
  $$('input[name=liveMode]').forEach(r => r.onchange = showCard); showCard();
  const saved = store.get('liveParams', {});
  for (const [mode, p] of Object.entries(saved)) for (const [k, v] of Object.entries(p)) {
    const i = $(`[data-mode="${mode}"] [data-p="${k}"]`); if (!i) continue; if (i.type === 'checkbox') i.checked = v; else i.value = v;
  }
  $('#liveStart').onclick = () => { const p = liveParams(); const all = store.get('liveParams', {}); all[liveMode()] = p; store.set('liveParams', all);
    run(() => api('live/start', { mode: liveMode(), params: p, targets: targets() }), 'Started'); };
  $('#liveStop').onclick = () => run(() => api('live/stop', {}), 'Stopped');
  const upd = debounce(() => { if (S.live && S.live.mode === liveMode()) api('live/update', { params: liveParams() }).catch(e => toast(e.message, true)); }, 200);
  $$('[data-mode] [data-p]').forEach(i => i.addEventListener('input', upd));
}

// ---------- schedules ----------
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
function fillScheduleSelects() {
  fillSceneSelect($('#schScene'));
  $('#schPlaylist').replaceChildren(...(SET.playlists || []).map(p => h('option', { value: p.id, text: p.name })));
}
function renderSchedules() {
  const list = SET.schedules || [];
  const label = a => ({ scene: () => 'Scene: ' + ((SET.scenes || []).find(s => s.id === a.scene_id)?.name || '?'), on: () => 'Turn on', off: () => 'Turn off',
    preset: () => 'Effect: ' + a.name, playlist: () => 'Playlist: ' + ((SET.playlists || []).find(p => p.id === a.playlist_id)?.name || '?'), daylight: () => 'Start daylight white' }[a.type] || (() => a.type))();
  $('#schedList').replaceChildren(...(list.length ? list.map(s => {
    const en = h('input', { type: 'checkbox', checked: s.enabled || null, 'aria-label': 'Enabled' });
    en.onchange = () => run(async () => { await api('collection/save', { collection: 'schedules', item: { ...s, enabled: en.checked } }); await loadSettings(); });
    return h('div', { class: 'item' }, h('label', { class: 'row' }, en, h('strong', { text: s.time })),
      h('span', { class: 'grow' }, `${s.name} - ${label(s.action)} - ${s.days.length === 7 ? 'every day' : s.days.map(d => DAYS[d]).join(', ')}`),
      h('button', { type: 'button', class: 'btn small', text: 'Run now', onclick: () => run(() => api('schedules/run', { id: s.id }), 'Done') }),
      h('button', { type: 'button', class: 'btn small danger', text: 'Delete', 'aria-label': 'Delete schedule ' + s.name,
        onclick: () => run(async () => { await api('collection/delete', { collection: 'schedules', id: s.id }); await loadSettings(); renderSchedules(); }) }));
  }) : [h('p', { class: 'muted', text: 'No schedules yet.' })]));
}
function initSchedules() {
  $('#schDays').replaceChildren(...DAYS.map((d, i) => h('label', {}, h('input', { type: 'checkbox', value: i, checked: true }), d)));
  const acts = [['scene', 'Play a saved scene'], ['preset', 'Play a TP-Link effect'], ['playlist', 'Start a playlist'], ['on', 'Turn on'], ['off', 'Turn off'], ['daylight', 'Start daylight white']];
  $('#schAction').replaceChildren(...acts.map(([v, t]) => h('option', { value: v, text: t })));
  $('#schPreset').replaceChildren(...CAT.lighting_presets.map(p => h('option', { value: p.name, text: p.name })));
  $('#schPreset').value = 'Sunrise';
  const vis = () => { const a = $('#schAction').value; $('#schSceneWrap').hidden = a !== 'scene'; $('#schPresetWrap').hidden = a !== 'preset'; $('#schPlWrap').hidden = a !== 'playlist'; };
  $('#schAction').onchange = vis; vis();
  $('#schedForm').onsubmit = e => { e.preventDefault();
    const a = $('#schAction').value; const action = { type: a };
    if (a === 'scene') action.scene_id = $('#schScene').value;
    if (a === 'preset') action.name = $('#schPreset').value;
    if (a === 'playlist') action.playlist_id = $('#schPlaylist').value;
    if (a === 'daylight') action.params = { wake: $('[data-mode=daylight] [data-p=wake]').value, bed: $('[data-mode=daylight] [data-p=bed]').value };
    if ((a === 'scene' && !action.scene_id) || (a === 'playlist' && !action.playlist_id)) return toast('Nothing to choose from yet', true);
    const days = $$('#schDays input:checked').map(i => +i.value);
    run(async () => { await api('collection/save', { collection: 'schedules', item: { name: $('#schName').value, time: $('#schTime').value, days, action, targets: selected || [] } }); await loadSettings(); renderSchedules(); }, 'Schedule added'); };
}

// ---------- settings ----------
async function loadSettings() { SET = await api('settings'); applyTheme(); }
function applyTheme() { const t = SET?.prefs?.theme || 'auto'; if (t === 'auto') document.documentElement.removeAttribute('data-theme'); else document.documentElement.dataset.theme = t; }
const modelOptions = () => CAT.models.map(m => h('option', { value: m.model, text: `${m.model} - ${m.label}` }));
function renderDevices() {
  $('#devList').replaceChildren(...(SET.devices.length ? SET.devices.map(d => {
    const live = S.strips.find(s => s.id === d.id) || {};
    const model = h('select', { 'aria-label': 'Model' }, modelOptions()); model.value = d.model || 'L930';
    model.onchange = () => run(async () => { await api('devices/update', { id: d.id, patch: { model: model.value } }); await loadSettings(); S = await api('state'); renderDevices(); renderTargets(); }, 'Model changed');
    const upd = patch => run(async () => { await api('devices/update', { id: d.id, patch }); await loadSettings(); }, 'Saved');
    const name = h('input', { type: 'text', value: d.name, maxlength: 40, 'aria-label': 'Strip name' }); name.onchange = () => upd({ name: name.value });
    const seg = h('input', { type: 'number', min: 1, max: 100, value: d.segments, 'aria-label': 'Number of zones' }); seg.onchange = () => upd({ segments: +seg.value });
    const rev = h('input', { type: 'checkbox', checked: d.reverse || null }); rev.onchange = () => upd({ reverse: rev.checked });
    return h('div', { class: 'item' }, h('span', { class: 'dot' + (live.connected ? ' on' : ''), 'aria-hidden': 'true' }), name,
      model, h('span', { class: 'muted', text: d.ip + (live.fw ? ` · fw ${live.fw}` : '') }),
      (live.caps || []).includes('zones') ? h('label', { class: 'row' }, 'Zones', seg) : null,
      (live.caps || []).includes('zones') ? h('label', { class: 'row' }, rev, 'Reverse direction') : null,
      h('button', { type: 'button', class: 'btn small danger', text: 'Remove', 'aria-label': 'Remove ' + d.name,
        onclick: () => confirm(`Remove "${d.name}" from Tapo Studio?`) && run(async () => { await api('devices/remove', { id: d.id }); await loadSettings(); renderDevices(); S = await api('state'); renderTargets(); }) }),
      live.error ? h('span', { class: 'err grow', text: live.error }) : null);
  }) : [h('p', { class: 'muted', text: 'No lights added yet. Search your network or add one by IP address.' })]));
}
// Guided test: the strip itself says which painting request it accepts; the user confirms what they see.
function wizAsk(text, choices) {
  $('#wizText').textContent = text;
  const box = $('#wizButtons');
  return new Promise(resolve => {
    box.replaceChildren(...choices.map(([label, value, cls]) => h('button', { type: 'button', class: 'btn ' + (cls || ''), text: label,
      onclick: () => { box.replaceChildren(); resolve(value); } })));
    if (box.firstChild) box.firstChild.focus();
  });
}
async function fixPainting() {
  const strip = S.strips.find(s => (s.caps || []).includes('zones') && s.connected);
  if (!strip) return toast('Connect a light strip first', true);
  $('#fixBtn').disabled = true; $('#wizard').hidden = false;
  const lines = [`Paint test for ${strip.name} (${strip.model}, firmware ${strip.fw || '?'})`];
  const finish = text => { $('#wizText').textContent = text; $('#wizButtons').replaceChildren(); const out = $('#diagOut'); out.value = lines.join('\n'); out.hidden = false; $('#diagCopy').hidden = false; $('#fixBtn').disabled = false; };
  try {
    let total = 8;
    for (let i = 0; i < total; i++) {
      $('#wizText').textContent = `Testing method ${i + 1} of ${total}. Watch your strip...`;
      const r = await api('paint/test', { id: strip.id, index: i });
      total = r.total;
      lines.push(`${i + 1}. ${r.label}: ${r.error ? 'the light refused it (' + r.error + ')' : r.accepted ? 'the light says it is showing it' : 'the light did not apply it'}  [lighting: ${r.readback.lighting}; scene: ${r.readback.scene}]`);
      if (r.control || !r.accepted) continue;
      const ans = await wizAsk(`Method ${i} of ${total - 1} (${r.label}). The strip says it is showing this pattern. Is the LEFT half RED and the RIGHT half BLUE?`,
        [['Yes, that is right', 'yes', 'primary'], ['No', 'no'], ["Can't tell", 'unsure']]);
      lines.push(`   you answered: ${ans}`);
      if (ans === 'yes') {
        await api('paint/choose', { id: strip.id, key: r.key });
        lines.push(`CHOSEN: ${r.key}`);
        finish('Done. This strip will now paint using that method. Try the Paint zones tab.');
        toast('Painting method saved');
        return;
      }
    }
    finish('None of the methods showed a red and blue pattern. Please copy the report below and send it to me.');
  } catch (e) { lines.push('Error: ' + e.message); finish('The test stopped: ' + e.message); }
}

function initSettings() {
  $('#email').value = SET.email || '';
  $('#password').placeholder = SET.has_password ? '(saved - leave blank to keep)' : '';
  $('#credForm').onsubmit = e => { e.preventDefault(); run(async () => { await api('credentials', { email: $('#email').value, password: $('#password').value }); $('#password').value = ''; await loadSettings(); }, 'Saved - connecting…'); };
  $('#diagBtn').onclick = async () => {
    const out = $('#diagOut'), btn = $('#diagBtn');
    btn.disabled = true; btn.textContent = 'Checking… (watch your strip)';
    try { out.value = (await api('diagnose', { test: true })).report; out.hidden = false; $('#diagCopy').hidden = false; }
    catch (e) { out.value = 'Diagnostics failed: ' + e.message; out.hidden = false; }
    btn.disabled = false; btn.textContent = 'Run diagnostics';
  };
  $('#fixBtn').onclick = fixPainting;
  $('#diagCopy').onclick = async () => {
    const out = $('#diagOut'); out.select();
    try { await navigator.clipboard.writeText(out.value); toast('Copied'); } catch { document.execCommand('copy'); toast('Copied'); }
  };
  $('#reconnectBtn').onclick = () => run(() => api('reconnect', {}), 'Reconnecting…');
  $('#discoverBtn').onclick = async () => {
    const box = $('#discovered'); box.replaceChildren(h('p', { class: 'muted', text: 'Searching…' }));
    try {
      const r = await api('discover', {});
      box.replaceChildren(h('p', { class: 'muted', text: `Searched ${r.target}. Found ${r.found.length} device(s).` }), ...r.found.map(f => f.error ? h('div', { class: 'item err', text: f.error }) :
        h('div', { class: 'item' }, h('span', { class: 'grow' }, h('strong', { text: f.name || 'Unnamed' }), ' ', h('span', { class: 'muted', text: `${f.model} · ${f.ip}` })),
          f.added ? h('span', { class: 'muted', text: 'Added' }) : f.supported ? h('button', { type: 'button', class: 'btn small primary', text: 'Add', onclick: ev => run(async () => { await api('devices/add', { ip: f.ip, name: f.name, model: f.model }); ev.target.replaceWith(h('span', { class: 'muted', text: 'Added' })); await loadSettings(); renderDevices(); S = await api('state'); renderTargets(); }, 'Added') })
            : h('span', { class: 'muted', text: 'Not a supported light' }))));
    } catch (e) { box.replaceChildren(h('p', { class: 'err', text: e.message })); }
  };
  $('#devModel').replaceChildren(...modelOptions());
  $('#devModel').value = 'L930';
  const modelChanged = () => { const m = CAT.models.find(x => x.model === $('#devModel').value); $('#devSegWrap').hidden = !m.caps.includes('zones'); $('#devSeg').value = m.zones; };
  $('#devModel').onchange = modelChanged; modelChanged();
  $('#addDevForm').onsubmit = e => { e.preventDefault(); run(async () => { await api('devices/add', { ip: $('#devIp').value, name: $('#devName').value, model: $('#devModel').value, segments: +$('#devSeg').value }); await loadSettings(); renderDevices(); S = await api('state'); renderTargets(); }, 'Added'); };
  const p = SET.prefs;
  $('#pTheme').value = p.theme; $('#pFps').value = p.max_fps; $('#pTrans').checked = p.auto_transition; $('#pJoin').checked = p.join_strips; $('#pOffset').value = p.sync_offset_ms;
  $('#prefsForm').onsubmit = e => { e.preventDefault(); run(async () => { await api('prefs', { theme: $('#pTheme').value, max_fps: +$('#pFps').value, auto_transition: $('#pTrans').checked, join_strips: $('#pJoin').checked, sync_offset_ms: +$('#pOffset').value }); await loadSettings(); }, 'Saved'); };
  renderDevices();
}

// ---------- boot ----------
async function boot() {
  if (!TOKEN) {
    $('#main').replaceChildren(h('div', { class: 'card' }, h('h2', { text: 'Open Tapo Studio from its window' }),
      h('p', { text: 'For security, this page only works when opened with the private link shown in the Tapo Studio window (the black window that start.bat opened). Copy that link into your browser, or restart start.bat.' })));
    return;
  }
  initTabs(); initHeader(); initColour();
  try { [CAT] = await Promise.all([api('catalog'), loadSettings()]); S = await api('state'); }
  catch (e) { $('#banner').hidden = false; $('#banner').textContent = 'Could not reach Tapo Studio: ' + e.message; return; }
  renderTargets(); syncZonesLength();
  initPaint(); fillPalettes(); initEffects(); renderEffects(); initScenes(); renderScenes(); initLive(); initSchedules(); renderSchedules(); initSettings();
  renderStatus(); poll.once = true; poll();
}
boot();
