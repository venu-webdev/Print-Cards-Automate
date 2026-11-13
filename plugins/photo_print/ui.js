/* Photo printing (4x6) - UI part (uses only window.CPS). */
(() => {
  const {api, toast, esc} = CPS;
  const BASE = '/api/plugins/photo_print';
  const ICON = '<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9V3h12v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M6 14h12v7H6z"/><path d="M17 12h.01"/></svg>';
  const $id = id => document.getElementById(id);
  const printerOpts = (list, sel) => (list.length ? list : ['']).map(n =>
    n ? `<option ${n === sel ? 'selected' : ''}>${esc(n)}</option>` : '<option value="">No printer found</option>').join('');
  const glossyNote = `Set <b>Glossy Photo Paper</b>, <b>Borderless</b> and the quality once in this printer&rsquo;s Windows
    <b>Printing preferences</b> &ndash; the app keeps them. Load 4&times;6 (10&times;15&nbsp;cm) glossy paper in the tray.`;

  const ov = document.createElement('div');
  ov.className = 'overlay';
  ov.innerHTML = `<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="phTitle" style="width:min(480px,100%)">
    <div class="dhead"><h3 id="phTitle">Print passport photos</h3><button class="btn quiet icon" data-close aria-label="Close">${CPS.SVG.x}</button></div>
    <div class="dbody" id="phBody"></div><div class="dfoot" id="phFoot"></div></div>`;
  document.body.appendChild(ov);
  ov.addEventListener('click', e => { if (e.target === ov || e.target.closest('[data-close]')) ov.classList.remove('open'); });

  async function open(j) {
    ov.classList.add('open');
    $id('phBody').innerHTML = '<span class="status"><span class="spin"></span>Finding photo printers…</span>';
    $id('phFoot').innerHTML = '';
    let s;
    try { s = await api(BASE + '/status'); } catch (e) { $id('phBody').innerHTML = `<div class="pnotice err">${esc(e.message)}</div>`; return; }
    if (!s.service.ok) { $id('phBody').innerHTML = `<div class="pnotice err">${esc(s.service.error)}</div>`; return; }
    const list = s.service.printers || [];
    $id('phBody').innerHTML = `
      <div><label class="t" style="display:block;font-size:13px;font-weight:600;margin-bottom:5px">Photo printer</label>
        <select class="text" id="phPrinter">${printerOpts(list, s.printer)}</select>
        <div id="phPaper" class="hint" style="margin-top:6px"></div></div>
      <div><label class="t" style="display:block;font-size:13px;font-weight:600;margin-bottom:5px">Copies of the sheet</label>
        <input class="text mono" id="phCopies" type="number" min="1" max="99" value="${+s.copies || 1}" style="max-width:120px"></div>
      <div class="pnotice"><b>${esc(j.details?.name || j.source || 'Passport photos')}</b> &ndash; 8 photos, 35&times;45&nbsp;mm, on one 4&times;6 sheet.</div>
      <div class="pnotice">${glossyNote}</div>`;
    $id('phFoot').innerHTML = `<button class="btn" data-close>Cancel</button><button class="btn primary" id="phGo">${ICON}Print</button>`;
    const sel = $id('phPrinter');
    async function refreshPaper() {
      $id('phPaper').textContent = 'Checking paper sizes…'; $id('phGo').disabled = true;
      try {
        const r = await api(BASE + '/config', {printer: sel.value});   // saving returns the resolved 4x6 paper for it
        $id('phPaper').innerHTML = r.ready ? `&#10003; prints on <b>${esc(r.paper)}</b>`
          : `<span style="color:var(--err)">This printer has no 4&times;6 / 10&times;15&nbsp;cm size &ndash; pick a photo printer, or load 4&times;6 paper and select it in the driver.</span>`;
        $id('phGo').disabled = !r.ready;
      } catch (e) { $id('phPaper').textContent = ''; }
    }
    sel.addEventListener('change', refreshPaper);
    refreshPaper();
    $id('phGo').onclick = async e => {
      const b = e.currentTarget; b.disabled = true; b.innerHTML = '<span class="spin"></span>Printing…';
      try {
        const r = await api(BASE + '/print', {job: j.job, printer: sel.value, copies: +$id('phCopies').value || 1});
        toast(`Sent ${r.copies > 1 ? r.copies + ' copies' : 'the sheet'} to ${r.printer}${r.paper ? ' (' + r.paper + ')' : ''}`);
        ov.classList.remove('open');
      } catch (err) { toast(err.message, {error: true}); b.disabled = false; b.innerHTML = ICON + 'Print'; }
    };
  }

  CPS.registerRowAction({id: 'photoprint', label: 'Print', title: 'Print the sheet on 4×6 photo paper (photo printer)',
    icon: ICON, run: open, when: j => j.type === 'passport'});

  CPS.registerSettingsPanel('photo_print', async el => {
    el.innerHTML = '<span class="status"><span class="spin"></span>Checking photo printers…</span>';
    let s; try { s = await api(BASE + '/status'); } catch (e) { el.innerHTML = `<span class="plug-err">${esc(e.message)}</span>`; return; }
    if (!s.service.ok) { el.innerHTML = `<div class="pnotice err">${esc(s.service.error)}</div>`; return; }
    const list = s.service.printers || [];
    el.innerHTML = `
      <section class="scard pcard"><header><h4>Photo printer</h4><p>Used for the passport-photo sheet, on 4×6 / 10×15 cm paper (e.g. Canon G2010).</p></header>
        <div class="pbody"><select class="text" id="phsP">${printerOpts(list, s.printer)}</select><div class="hint" id="phsPaper" style="margin-top:8px"></div></div></section>
      <section class="scard pcard"><header><h4>Printer settings (glossy, borderless, quality)</h4>
          <p>Set these on the printer once — the app records and reuses them for every passport sheet. (The app always picks the 4×6 size for you.)</p></header>
        <div class="pbody pstack">
          <div class="pnotice" id="phsProf"></div>
          <div class="rowf" style="gap:8px;flex-wrap:wrap">
            <button class="btn" id="phsSetup">${ICON}Set up on the printer…</button>
            <button class="btn quiet" id="phsClear" hidden>Use the printer default again</button>
          </div>
          <p class="hint" style="margin:0">A Windows settings window for this printer opens on this PC — set <b>Glossy Photo Paper</b>, <b>Borderless</b> and the quality, then click <b>OK</b>.</p>
        </div></section>
      <section class="scard pcard"><header><h4>Default copies</h4><p>Used when you click Print on a passport sheet. Saves automatically.</p></header>
        <div class="pbody"><input class="text mono" id="phsC" type="number" min="1" max="99" value="${+s.copies || 1}" style="max-width:120px"></div></section>`;
    const sel = $id('phsP');
    function showProfile(st) {
      const has = st.has_profile, p = st.profile || {};
      $id('phsProf').className = 'pnotice';
      $id('phsProf').innerHTML = has
        ? `&#10003; <b>Saved settings in use</b>${p.colour ? ' &middot; ' + esc(p.colour) : ''}${p.copies ? ' &middot; ' + p.copies + ' cop' + (p.copies > 1 ? 'ies' : 'y') : ''} <span class="hint">(from the printer&rsquo;s own dialog)</span>`
        : `Using the printer&rsquo;s <b>current default</b> settings. Click <b>Set up</b> to record glossy / borderless / quality from the driver.`;
      $id('phsClear').hidden = !has;
    }
    async function save() {
      try {
        const r = await api(BASE + '/config', {printer: sel.value, copies: +$id('phsC').value || 1});
        $id('phsPaper').innerHTML = r.ready ? `&#10003; 4×6 paper found: <b>${esc(r.paper)}</b>`
          : `<span style="color:var(--err)">No 4×6 / 10×15 cm paper size on this printer.</span>`;
        showProfile(r);
      } catch (e) { toast(e.message, {error: true}); }
    }
    sel.onchange = save; $id('phsC').onchange = save;
    $id('phsSetup').onclick = async e => {
      const b = e.currentTarget; b.disabled = true; b.innerHTML = '<span class="spin"></span>Opening the printer window…';
      try {
        const r = await api(BASE + '/setup', {printer: sel.value});
        if (r.cancelled) toast('Set-up cancelled – nothing changed');
        else { toast('Printer settings saved'); }
      } catch (err) { toast(err.message, {error: true}); }
      b.disabled = false; b.innerHTML = ICON + 'Set up on the printer…'; save();
    };
    $id('phsClear').onclick = async () => { try { await api(BASE + '/setup/clear', {printer: sel.value}); toast('Back to the printer default'); save(); } catch (e) { toast(e.message, {error: true}); } };
    save();
  });
})();
