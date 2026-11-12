/* Shared by the tool pages (Passport photos, Card scan): helpers, theme, page title, workspace zoom. */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
function toast(msg, opts = {}) {
  const t = document.createElement('div'); t.className = 'toast' + (opts.error ? ' err' : '');
  t.innerHTML = `<span>${esc(msg)}</span>`;
  $('#toasts').appendChild(t); setTimeout(() => t.remove(), opts.error ? 7000 : 4500);
}
const CHECK_SVG = '<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12l5 5 9-10"/></svg>';

/* embedded inside the main page (tile opens the tool in a panel) vs. opened as its own page */
const EMBED = new URLSearchParams(location.search).has('embed');
if (EMBED) try { document.body.classList.add('embed'); } catch {}
// finished (added / updated a job): tell the main page to close the panel and show it, or navigate when standalone
function toolFinish(job, updated) {
  if (EMBED && window.parent !== window) window.parent.postMessage({cps: 'tool-done', job, updated: !!updated}, location.origin);
  else location.href = (updated ? '/?updated=' : '/?added=') + encodeURIComponent(job);
}
// close without finishing (the "Back to cards" control)
function toolClose() {
  if (EMBED && window.parent !== window) window.parent.postMessage({cps: 'tool-close'}, location.origin);
  else location.href = '/';
}
if (EMBED) addEventListener('DOMContentLoaded', () => document.querySelectorAll('a.btn[href="/"]').forEach(a =>
  a.addEventListener('click', e => { e.preventDefault(); toolClose(); })));

/* theme (same switch and memory as the main page) */
(() => {
  const SUN = '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>';
  const MOON = '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>';
  const eff = () => { const t = document.documentElement.dataset.theme; return (t === 'light' || t === 'dark') ? t : (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); };
  const icon = () => { $('#themeIc').innerHTML = eff() === 'dark' ? SUN : MOON; $('#themeBtn').title = eff() === 'dark' ? 'Light mode' : 'Dark mode'; };
  $('#themeBtn').onclick = () => { const n = eff() === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = n; try { localStorage.setItem('cps-theme', n); } catch (e) {} icon(); };
  icon();
})();

/* the shop's own names (Rename types): page name in the top bar + tab title; returns the settings */
async function toolSettings(key) {
  let s = {};
  try { s = await (await fetch('/api/settings')).json(); } catch {}
  const own = (s.type_names || {})[key];
  if (own) { $('#toolName').textContent = own; document.title = own + ' – Card Print Studio'; }
  return s;
}
/* "Editing NAME · JOB-…" next to the page name, and the main button becomes "Save changes" */
function toolEditing(name, ref, btn, hint) {
  $('#toolCrumb').textContent = ' · Editing ' + name + (ref ? ' · ' + ref : '');
  if (btn) { btn.innerHTML = 'Save changes ' + CHECK_SVG; btn.title = 'Update this entry in the exports list'; }
  if (hint) hint.textContent = 'Saves over the same entry in the exports list (same job ref).';
}

/* Workspace zoom, like Photoshop: Alt + scroll (around the mouse), Ctrl + / Ctrl - / Ctrl 0, Space + drag or the
   middle mouse button to move around, and a − 100% + Fit bar. The page draws with layout(): its own "fit" scale
   times the zoom, centred, plus the pan. Runs before the page's own mouse handlers and stops them while busy. */
function viewZoom({stage, canvas, redraw, active = () => true}) {
  const v = {z: 1, px: 0, py: 0, space: false, L: null};
  const bar = document.createElement('div');
  bar.className = 'vbar'; bar.hidden = true;
  bar.title = 'View zoom: Alt + scroll, Ctrl + / Ctrl −, Ctrl 0 to fit. Space + drag moves around.';
  bar.innerHTML = '<button type="button" data-v="out" aria-label="Zoom out the view">&minus;</button><span>100%</span><button type="button" data-v="in" aria-label="Zoom in the view">+</button><button type="button" data-v="fit">Fit</button>';
  stage.appendChild(bar);
  const pct = bar.querySelector('span');
  v.layout = (D0, cw, ch, w, h, dy = 0) => {
    const D = D0 * v.z;
    v.L = {D0, cw, ch, w, h, dy, D, ox: (w - cw * D) / 2 + v.px, oy: (h - ch * D) / 2 + dy + v.py};
    pct.textContent = Math.round(v.z * 100) + '%';
    return v.L;
  };
  v.zoomAt = (X, Y, f) => {
    const L = v.L; if (!L) return;
    const ix = (X - L.ox) / L.D, iy = (Y - L.oy) / L.D, z = Math.min(16, Math.max(0.25, v.z * f)), D = L.D0 * z;
    v.z = z; v.px = X - ix * D - (L.w - L.cw * D) / 2; v.py = Y - iy * D - ((L.h - L.ch * D) / 2 + L.dy);
    redraw();
  };
  v.fit = () => { v.reset(); redraw(); };
  v.reset = () => { v.z = 1; v.px = 0; v.py = 0; };
  v.show = on => { bar.hidden = !on; };
  const centre = f => v.L && v.zoomAt(v.L.w / 2, v.L.h / 2, f);
  bar.addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (!b) return;
    if (b.dataset.v === 'fit') v.fit(); else centre(b.dataset.v === 'in' ? 1.25 : 1 / 1.25); });
  const at = e => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  canvas.addEventListener('wheel', e => {
    if (!e.altKey || !active()) return;
    e.preventDefault(); e.stopImmediatePropagation(); v.zoomAt(...at(e), e.deltaY < 0 ? 1.15 : 1 / 1.15);
  }, {passive: false, capture: true});
  let pan = null, over = false;
  canvas.addEventListener('pointerdown', e => {
    if (!active() || !(v.space || e.button === 1)) return;
    e.preventDefault(); e.stopImmediatePropagation();
    pan = {X: e.clientX, Y: e.clientY, px: v.px, py: v.py}; canvas.setPointerCapture(e.pointerId); canvas.style.cursor = 'grabbing';
  }, true);
  canvas.addEventListener('pointermove', e => {
    if (pan) { e.stopImmediatePropagation(); v.px = pan.px + e.clientX - pan.X; v.py = pan.py + e.clientY - pan.Y; redraw(); return; }
    if (v.space && active()) { e.stopImmediatePropagation(); canvas.style.cursor = 'grab'; }
  }, true);
  const endPan = e => { if (!pan) return; e.stopImmediatePropagation(); pan = null; canvas.style.cursor = v.space ? 'grab' : ''; };
  canvas.addEventListener('pointerup', endPan, true); canvas.addEventListener('pointercancel', endPan, true);
  stage.addEventListener('pointerenter', () => { over = true; }); stage.addEventListener('pointerleave', () => { over = false; });
  const typing = () => /input|textarea|select/i.test(document.activeElement?.tagName || '') && document.activeElement.type !== 'range';
  document.addEventListener('keydown', e => {
    if (!active()) return;
    if (e.key === ' ' && !typing()) { e.preventDefault(); if (!v.space) { v.space = true; if (!pan) canvas.style.cursor = 'grab'; } }
    if (e.key === 'Alt' && over) e.preventDefault();                 // Alt must not jump to the browser menu
    if ((e.ctrlKey || e.metaKey) && !typing()) {
      if (e.key === '0') { e.preventDefault(); v.fit(); }
      else if (e.key === '+' || e.key === '=') { e.preventDefault(); centre(1.25); }
      else if (e.key === '-') { e.preventDefault(); centre(1 / 1.25); }
    }
  });
  document.addEventListener('keyup', e => {
    if (e.key === ' ') { v.space = false; if (!pan) canvas.style.cursor = ''; }
    if (e.key === 'Alt' && over) e.preventDefault();
  });
  window.addEventListener('blur', () => { v.space = false; });
  return v;
}

/* drop a photo anywhere on the page */
function dropPhotos(stage, onFile) {
  document.addEventListener('dragover', e => { e.preventDefault(); stage.classList.add('dropping'); });
  document.addEventListener('dragleave', e => { if (!e.relatedTarget) stage.classList.remove('dropping'); });
  document.addEventListener('drop', e => { e.preventDefault(); stage.classList.remove('dropping'); if (e.dataTransfer.files[0]) onFile(e.dataTransfer.files[0]); });
}
