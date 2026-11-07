/* A4 office printing - UI part (uses only window.CPS). */
(() => {
  const {api, toast, esc} = CPS;
  const BASE = '/api/plugins/office_print';
  const ICON = '<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9V3h12v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M6 14h12v7H6z"/><path d="M17 12h.01"/></svg>';

  const ov = document.createElement('div');
  ov.className = 'overlay';
  ov.innerHTML = `<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="opTitle" style="width:min(500px,100%)">
    <div class="dhead"><h3 id="opTitle">Print A4 sheets</h3><button class="btn quiet icon" data-close aria-label="Close">${CPS.SVG.x}</button></div>
    <div class="dbody" id="opBody"></div><div class="dfoot" id="opFoot"></div></div>`;
  document.body.appendChild(ov);
  ov.addEventListener('click', e => { if (e.target === ov || e.target.closest('[data-close]')) ov.classList.remove('open'); });

  async function open(j) {
    ov.classList.add('open');
    const B = document.getElementById('opBody'), F = document.getElementById('opFoot');
    B.innerHTML = '<span class="status"><span class="spin"></span>Checking printers…</span>'; F.innerHTML = '';
    let s; try { s = await api(BASE + '/status'); } catch (e) { B.innerHTML = `<p class="plug-err">${esc(e.message)}</p>`; return; }
    if (!s.service.ok) { B.innerHTML = `<p class="plug-err">${esc(s.service.error)}</p>`; return; }
    B.innerHTML = `<div><b>${esc(j.details.name || j.source)}</b> <span class="hint mono">${esc(j.ref || '')}</span></div>
      <div><label class="t" for="opPrinter">Printer</label>
        <select class="text" id="opPrinter">${s.service.printers.map(p => `<option ${p === s.printer ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select></div>
      <div><span class="t">Sheets</span><div style="display:flex;gap:14px;flex-wrap:wrap">${j.sides.map((x, i) =>
        `<label style="display:flex;gap:6px;align-items:center"><input type="checkbox" class="opSheet" value="${esc(x)}" ${i === 0 ? 'checked' : ''}>${esc(x)}</label>`).join('')}</div></div>
      <div class="rowf" style="align-items:end;gap:16px">
        <div><label class="t" for="opCopies">Copies</label><input class="text" id="opCopies" type="number" min="1" max="99" value="1" style="width:90px"></div>
        <label style="display:flex;gap:6px;align-items:center;padding-bottom:8px"><input type="checkbox" id="opColor" ${s.color ? 'checked' : ''}> Colour</label></div>
      <p class="hint" style="margin:0">Printed at exactly 100% (A4, ${s.dpi} dpi) &ndash; no “fit to page”, so the cards keep their real size for lamination.</p>`;
    F.innerHTML = `<button class="btn" data-close>Cancel</button><button class="btn primary" id="opGo">${ICON}Print</button>`;
    document.getElementById('opGo').onclick = async () => {
      const sheets = [...document.querySelectorAll('.opSheet:checked')].map(x => x.value);
      if (!sheets.length) { toast('Choose at least one sheet', {error: true}); return; }
      const printer = document.getElementById('opPrinter').value, color = document.getElementById('opColor').checked;
      const b = document.getElementById('opGo'); b.disabled = true; b.textContent = 'Sending…';
      try {
        await api(BASE + '/config', {printer, color});
        const r = await api(BASE + '/print', {job: j.job, sheets, copies: +document.getElementById('opCopies').value || 1, color, name: j.details.name || j.source});
        toast(`Sent ${r.printed.join(', ')} to ${r.printer}`); ov.classList.remove('open');
      } catch (e) { toast(e.message, {error: true}); b.disabled = false; b.innerHTML = ICON + 'Print'; }
    };
  }

  CPS.registerRowAction({id: 'a4print', label: 'Print', title: 'Print A4 sheets (office printer)', icon: ICON, run: open,
    when: j => j.pdf});

  CPS.registerSettingsPanel('office_print', async el => {
    el.innerHTML = '<span class="status"><span class="spin"></span>Checking printers…</span>';
    let s; try { s = await api(BASE + '/status'); } catch (e) { el.innerHTML = `<span class="plug-err">${esc(e.message)}</span>`; return; }
    if (!s.service.ok) { el.innerHTML = `<span class="plug-err">${esc(s.service.error)}</span>`; return; }
    el.innerHTML = `<div><label class="t" for="opsP" style="font-weight:500">Default printer</label>
        <select class="text" id="opsP">${s.service.printers.map(p => `<option ${p === s.printer ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select></div>
      <div class="rowf" style="gap:18px;align-items:center"><label class="hint" style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="opsC" ${s.color ? 'checked' : ''}> Colour by default</label>
        <label class="hint">Resolution <select class="text" id="opsD" style="width:auto;height:30px"><option ${s.dpi === 600 ? 'selected' : ''}>600</option><option ${s.dpi === 300 ? 'selected' : ''}>300</option></select> dpi</label></div>
      <div class="rowf" style="gap:8px;flex-wrap:wrap"><button class="btn" id="opsT">${ICON}Print scale test page</button>
        <a class="btn quiet" href="${BASE}/test.pdf" download>Download test PDF</a></div>
      <p class="hint" style="margin:0">Check with a ruler: the square must be exactly 100&nbsp;mm and a real card must fit the card outline.</p>`;
    const save = async () => { try { await api(BASE + '/config', {printer: el.querySelector('#opsP').value, color: el.querySelector('#opsC').checked, dpi: el.querySelector('#opsD').value}); toast('Saved'); } catch (e) { toast(e.message, {error: true}); } };
    el.querySelectorAll('select,input').forEach(x => x.onchange = save);
    el.querySelector('#opsT').onclick = async ev => {
      const b = ev.currentTarget; b.disabled = true;
      try { const r = await api(BASE + '/test', {printer: el.querySelector('#opsP').value}); toast(`Scale test sent to ${r.printer}`); }
      catch (e) { toast(e.message, {error: true}); }
      b.disabled = false;
    };
  });
})();
