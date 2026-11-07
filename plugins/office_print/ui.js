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
    if (!s.service.ok) {
      el.innerHTML = `<div class="pnotice err">${esc(s.service.error)}</div>
        <section class="scard pcard"><header><h4>Scale test page</h4><p>You can still download the test PDF and print it from another program at 100%.</p></header>
          <div class="pbody"><a class="btn" href="${BASE}/test.pdf" download>Download test PDF</a></div></section>`;
      return;
    }
    el.innerHTML = `<section class="scard pcard">
        <header><h4>Printer</h4><p>Office printer for A4 sheets, e.g. the Canon imageRUNNER.</p></header>
        <div class="pbody"><select class="text" id="opsP">${s.service.printers.map(p => `<option ${p === s.printer ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select></div>
      </section>
      <section class="scard pcard">
        <header><h4>Defaults</h4><p>Used when you click Print on an A4 sheet. Changes save automatically.</p></header>
        <div class="pbody pstack">
          <div class="sw"><div><b>Colour</b><small>Off prints in black &amp; white.</small></div><input type="checkbox" id="opsC" ${s.color ? 'checked' : ''}></div>
          <div class="sw"><div><b>Resolution</b><small>600 dpi is sharper; 300 dpi prints faster.</small></div>
            <div class="seg" id="opsD"><button data-v="600" aria-pressed="${s.dpi === 600}">600 dpi</button><button data-v="300" aria-pressed="${s.dpi !== 600}">300 dpi</button></div></div>
        </div>
      </section>
      <section class="scard pcard">
        <header><h4>Scale test page</h4><p>Checks the printer keeps 100% size. Measure with a ruler: the square must be exactly 100&nbsp;mm and a real card must fit the card outline.</p></header>
        <div class="pbody"><div class="rowf" style="gap:8px;flex-wrap:wrap"><button class="btn" id="opsT">${ICON}Print scale test page</button>
          <a class="btn quiet" href="${BASE}/test.pdf" download>Download test PDF</a></div></div>
      </section>`;
    const dpi = () => el.querySelector('#opsD button[aria-pressed="true"]').dataset.v;
    const save = async () => { try { await api(BASE + '/config', {printer: el.querySelector('#opsP').value, color: el.querySelector('#opsC').checked, dpi: dpi()}); toast('Saved'); } catch (e) { toast(e.message, {error: true}); } };
    el.querySelectorAll('select,input').forEach(x => x.onchange = save);
    el.querySelectorAll('#opsD button').forEach(b => b.onclick = () => { el.querySelectorAll('#opsD button').forEach(x => x.setAttribute('aria-pressed', x === b)); save(); });
    el.querySelector('#opsT').onclick = async ev => {
      const b = ev.currentTarget; b.disabled = true;
      try { const r = await api(BASE + '/test', {printer: el.querySelector('#opsP').value}); toast(`Scale test sent to ${r.printer}`); }
      catch (e) { toast(e.message, {error: true}); }
      b.disabled = false;
    };
  });
})();
