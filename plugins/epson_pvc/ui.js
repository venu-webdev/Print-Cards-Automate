/* Epson PVC card printing - UI part of the plugin (uses only window.CPS). */
(() => {
  const {api, toast, esc} = CPS;
  const BASE = '/api/plugins/epson_pvc';
  const ICON = '<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9V3h12v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M6 14h12v7H6z"/></svg>';
  let st = null;
  const getStatus = async () => (st = await api(BASE + '/status'));

  function problem(s) {
    if (!s.service.ok) return s.service.error;
    if (!s.service.printers.length) return 'No printers are installed on this PC. Install the Epson L8050 driver first.';
    if (!s.printer) return 'Choose the printer in Settings → Card printer · Epson.';
    return '';
  }

  /* ---------------- print dialog ---------------- */
  const ov = document.createElement('div');
  ov.className = 'overlay'; ov.id = 'epsonPrint';
  ov.innerHTML = `<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="epTitle" style="width:min(520px,100%)">
    <div class="dhead"><h3 id="epTitle">Print PVC card</h3><button class="btn quiet icon" data-close aria-label="Close">${CPS.SVG.x}</button></div>
    <div class="dbody" id="epBody"></div>
    <div class="dfoot" id="epFoot"></div></div>`;
  document.body.appendChild(ov);
  ov.addEventListener('click', e => { if (e.target === ov || e.target.closest('[data-close]')) ov.classList.remove('open'); });

  let cur = null, step = 'choose';
  async function openPrint(j) {
    cur = j; step = 'choose';
    $('#epBody').innerHTML = '<span class="status"><span class="spin"></span>Checking printer…</span>'; $('#epFoot').innerHTML = '';
    ov.classList.add('open');
    try { await getStatus(); } catch (e) { $('#epBody').innerHTML = `<p class="plug-err">${esc(e.message)}</p>`; return; }
    draw();
  }
  function draw() {
    const j = cur, s = st, err = problem(s), hasBack = j.sides.includes('Back');
    const who = esc(j.details.name || j.source) + (j.ref ? ` <span class="hint mono">${esc(j.ref)}</span>` : '');
    let body = `<div class="holder" style="gap:12px"><img src="/api/image/${j.job}/Front" alt="" style="width:120px;border-radius:6px;border:1px solid var(--line)"><div><b>${who}</b>
      <div class="hint">${err ? '' : `Printer: <b>${esc(s.printer)}</b>`}${!err && ''}</div></div></div>`;
    let foot = `<a class="btn quiet" href="${BASE}/pdf/${j.job}?name=${encodeURIComponent(j.names.Front || j.source)}" title="Exact card-size PDF (front + back) – e.g. for Epson Photo+">Card PDF</a>`;
    if (err) {
      body += `<p class="plug-err">${esc(err)}</p><p class="hint">You can still download the card-size PDF and print it from Epson Photo+.</p>`;
    } else if (step === 'choose') {
      body += `<div><span class="t">Print</span><div class="seg" id="epSide">
          ${hasBack ? '<button data-v="both" aria-pressed="true">Front, then back</button>' : ''}
          <button data-v="Front" aria-pressed="${!hasBack}">Front only</button>${hasBack ? '<button data-v="Back" aria-pressed="false">Back only</button>' : ''}</div></div>
        <div><label class="t" for="epCopies">Copies</label><input class="text" id="epCopies" type="number" min="1" max="50" value="1" style="width:100px"></div>
        <p class="hint">Click <b>Print</b> first. When the printer’s disc light blinks, put a clean blank PVC card in <b>slot 1</b> of the Disc/ID Card Tray (printable side up) and insert the tray. Keep the tray clean &ndash; borderless printing can leave ink on it.</p>`;
      foot += `<button class="btn" data-close>Cancel</button><button class="btn primary" id="epGo">${ICON}Print</button>`;
    } else if (step === 'flip') {
      body += `<div class="note" style="padding:0;display:block"><b>Front printed.</b><p class="hint" style="margin:4px 0 0">Let the ink dry a moment. Click <b>Print back</b>, then turn the card over (same top edge), put it back in slot 1 and insert the tray when the disc light blinks.</p></div>`;
      foot += `<button class="btn" data-close>Done for now</button><button class="btn primary" id="epBack">${ICON}Print back</button>`;
    } else if (step === 'done') {
      body += `<p><b>Sent to the printer.</b></p>`;
      foot += `<button class="btn primary" data-close>Close</button>`;
    }
    $('#epBody').innerHTML = body; $('#epFoot').innerHTML = foot;
    document.querySelectorAll('#epSide button').forEach(b => b.onclick = () => document.querySelectorAll('#epSide button').forEach(x => x.setAttribute('aria-pressed', x === b)));
    const go = $('#epGo'); if (go) go.onclick = async () => {
      const mode = document.querySelector('#epSide button[aria-pressed="true"]')?.dataset.v || 'Front';
      const copies = +$('#epCopies').value || 1;
      if (await send(mode === 'Back' ? 'Back' : 'Front', copies)) { step = mode === 'both' ? 'flip' : 'done'; cur.copies = copies; draw(); }
    };
    const back = $('#epBack'); if (back) back.onclick = async () => { if (await send('Back', cur.copies || 1)) { step = 'done'; draw(); } };
  }
  async function send(side, copies) {
    const btn = $('#epGo') || $('#epBack'); if (btn) { btn.disabled = true; btn.textContent = 'Printing…'; }
    try { await api(BASE + '/print', {job: cur.job, side, copies, name: cur.details.name || cur.source}); toast(`${side} sent to the printer`); return true; }
    catch (e) { toast(e.message, {error: true}); if (btn) { btn.disabled = false; btn.innerHTML = ICON + 'Print'; } return false; }
  }

  CPS.registerRowAction({id: 'print', label: 'Print', title: 'Print on a PVC card (Epson)', icon: ICON, run: openPrint,
    when: j => j.sides.includes('Front')});   // PVC cards only (not the A4 laminated sheets)

  /* ---------------- settings panel ---------------- */
  CPS.registerSettingsPanel('epson_pvc', async el => {
    el.innerHTML = '<span class="status"><span class="spin"></span>Checking printer…</span>';
    let s; try { s = await getStatus(); } catch (e) { el.innerHTML = `<span class="plug-err">${esc(e.message)}</span>`; return; }
    const err = s.service.ok ? '' : s.service.error;
    const printers = s.service.printers || [];
    const auto = s.mode !== 'custom';
    const num = (id, label, v, step, unit, help) => `<div class="pfield"><label for="${id}">${label}</label>
        <div class="punit"><input class="text mono" id="${id}" type="number" step="${step}" value="${v}"><span>${unit}</span></div>${help ? `<small>${help}</small>` : ''}</div>`;
    const ready = s.printer && !err;
    el.innerHTML = `${err ? `<div class="pnotice err">${esc(err)}</div>` : ''}
      <section class="scard pcard">
        <header><h4>Printer</h4><p>The Epson L8050 (or another Epson with a Disc/ID Card Tray).</p>
          <span class="chip ${ready ? 'on' : ''}">${ready ? 'Ready' : 'Not connected'}</span></header>
        <div class="pbody"><select class="text" id="epPrinter" ${printers.length ? '' : 'disabled'}>${printers.map(p => `<option ${p === s.printer ? 'selected' : ''}>${esc(p)}</option>`).join('') || '<option>No printers found</option>'}</select></div>
      </section>
      <section class="scard pcard">
        <header><h4>Card settings</h4><p>Tray, paper type and quality sent to the Epson driver.</p></header>
        <div class="pbody">
          <div class="seg" id="epMode"><button data-v="auto" aria-pressed="${auto}">Automatic (Photo+ compatible)</button><button data-v="custom" aria-pressed="${!auto}">Custom</button></div>
          <div id="epModeBody" class="pstack"></div>
        </div>
      </section>
      <section class="scard pcard">
        <header><h4>Position &amp; size</h4><p>Fine-tune after an alignment test. Negative values move left / up.</p></header>
        <div class="pbody"><div class="pgrid">
          ${num('epOx', 'Move right', s.offset_x_mm, 0.1, 'mm')}${num('epOy', 'Move down', s.offset_y_mm, 0.1, 'mm')}
          ${num('epW', 'Card width', s.card_w_mm, 0.1, 'mm', 'Standard card: 85.6')}${num('epH', 'Card height', s.card_h_mm, 0.1, 'mm', 'Standard card: 54')}
          ${num('epSc', 'Scale', s.scale_pct, 0.1, '%')}
          <div class="pfield"><label for="epRotSel">Rotation</label>
            <select class="text" id="epRotSel">${[90, 270, 0, 180].map(r => `<option value="${r}" ${+s.rotation === r ? 'selected' : ''}>${r === 90 ? '90° (portrait, like Photo+)' : r === 270 ? '270° (portrait, other way)' : r + '°'}</option>`).join('')}</select></div>
        </div></div>
        <footer><button class="btn primary" id="epSave">Save printer settings</button></footer>
      </section>
      <section class="scard pcard">
        <header><h4>Alignment test</h4><p>Put a <b>blank card</b> in the tray. If the printed outline is off, adjust <i>Position &amp; size</i>; if the TOP EDGE arrow is upside down, choose rotation 270°.</p></header>
        <div class="pbody"><div class="rowf" style="flex-wrap:wrap">
          <button class="btn" id="epTest" ${ready ? '' : 'disabled'}>${ICON}Print alignment test</button>
          <a class="btn quiet" href="${BASE}/test.png">Download test image</a></div></div>
      </section>`;

    const body = $('#epModeBody');
    const drawMode = async () => {
      const isAuto = document.querySelector('#epMode button[aria-pressed="true"]').dataset.v === 'auto';
      if (isAuto) {
        body.innerHTML = `<div class="sw"><div><b style="font-size:13px">Quality &amp; colour (Epson)</b><p class="hint" style="margin:0">${s.card_setup
              ? 'Saved – your Epson quality and colour choices are used for every card.'
              : 'Optional. Cards print with the driver’s defaults for the card tray (Standard quality, automatic colour). Click Set up… only if you want different Epson quality or colour settings.'}</p></div>
            <button class="btn" id="epQc" ${s.printer && !err ? '' : 'disabled'}>${s.card_setup ? 'Change…' : 'Set up…'}</button></div>
          <div class="sw"><div><b style="font-size:13px">Borderless</b><p class="hint" style="margin:0">Print edge to edge (Photo+ “Plastic ID card – borderless”). Off = with a small border.</p></div>
            <input type="checkbox" id="epBl" ${s.borderless ? 'checked' : ''}></div>
          <div class="hint" id="epDetect">${s.printer && !err ? '<span class="status"><span class="spin"></span>Reading the driver…</span>' : 'Install the Epson driver and choose the printer to see the detected card settings.'}</div>`;
        if ($('#epQc')) $('#epQc').onclick = async () => {
          const b = $('#epQc'); b.disabled = true; b.textContent = 'Opened Epson window…';
          try { await save(); const r = await api(BASE + '/setup', {}); toast(r.saved ? 'Quality & colour saved' : 'No changes saved'); }
          catch (e) { toast(e.message, {error: true}); }
          CPS.settingsPanels.epson_pvc(el);
        };
        if (s.printer && !err) {
          try {
            const d = await api(BASE + '/detect');
            $('#epDetect').innerHTML = `Uses the same settings as Epson Photo+:<br>Tray <b>${esc(d.tray || '—')}</b> &ensp; Paper type <b>${esc(d.media || '—')}</b> &ensp; Size <b>${esc(d.paper || '—')}</b>` +
              (d.missing.length ? `<div class="plug-err">The driver did not report: ${esc(d.missing.join(', '))}. Try “Custom”.</div>` : '');
          } catch (e) { $('#epDetect').innerHTML = `<span class="plug-err">${esc(e.message)}</span>`; }
        }
      } else {
        body.innerHTML = `<div class="sw"><div><b style="font-size:13px">Epson printing preferences</b><p class="hint" style="margin:0">${s.card_setup ? 'Saved. Used only for card printing.' : 'Opens Epson’s own Printing Preferences – choose Paper Source <b>Disc/ID Card Tray</b> and the PVC ID card paper type.'}</p></div>
            <button class="btn" id="epSetup" ${s.printer ? '' : 'disabled'}>${s.card_setup ? 'Change…' : 'Set up…'}</button></div>`;
        $('#epSetup').onclick = async () => {
          const b = $('#epSetup'); b.disabled = true; b.textContent = 'Opened Epson window…';
          try { await save(); const r = await api(BASE + '/setup', {}); toast(r.saved ? 'Card settings saved' : 'No changes saved'); }
          catch (e) { toast(e.message, {error: true}); }
          CPS.settingsPanels.epson_pvc(el);
        };
      }
    };
    const save = async extra => {
      const mode = document.querySelector('#epMode button[aria-pressed="true"]').dataset.v;
      st = await api(BASE + '/config', Object.assign({printer: $('#epPrinter').value, mode, borderless: $('#epBl') ? $('#epBl').checked : s.borderless,
        rotation: $('#epRotSel').value, offset_x_mm: $('#epOx').value, offset_y_mm: $('#epOy').value,
        card_w_mm: $('#epW').value, card_h_mm: $('#epH').value, scale_pct: $('#epSc').value}, extra || {}));
      s = st;
    };
    document.querySelectorAll('#epMode button').forEach(b => b.onclick = async () => {
      document.querySelectorAll('#epMode button').forEach(x => x.setAttribute('aria-pressed', x === b)); await save(); drawMode();
    });
    body.addEventListener('change', async e => { if (e.target.id === 'epBl') { await save(); drawMode(); } });
    $('#epPrinter').onchange = async () => { await save(); CPS.settingsPanels.epson_pvc(el); };
    $('#epSave').onclick = async () => { try { await save(); toast('Printer settings saved'); } catch (e) { toast(e.message, {error: true}); } };
    $('#epTest').onclick = async () => { try { await save(); await api(BASE + '/test', {}); toast('Alignment test sent'); } catch (e) { toast(e.message, {error: true}); } };
    drawMode();
  });
})();
