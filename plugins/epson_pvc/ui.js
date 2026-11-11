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
  // layout: 'one' = one card, 'same' = this card in both slots, 'pair' = two selected cards (slot 1 + slot 2)
  let opt = {layout: 'one', pair: null, side: 'both', copies: 1};
  const isPvc = x => x.sides.includes('Front') && !x.pdf;           // not the A4 laminated sheets
  const nm = x => x.details.name || x.source;
  // cards for one tray pass of the given side ([] = nothing has that side)
  function passCards(side) {
    const one = +(st.slot || 1), list = [];
    const add = (slot, j) => { if (j && j.sides.includes(side)) list.push({slot, job: j.job, side}); };
    if (opt.layout === 'one') add(one, cur);
    else { add(1, cur); add(2, opt.layout === 'same' ? cur : opt.pair); }
    return list;
  }
  async function start() {
    step = 'choose';
    $('#epBody').innerHTML = '<span class="status"><span class="spin"></span>Checking printer…</span>'; $('#epFoot').innerHTML = '';
    ov.classList.add('open');
    try { await getStatus(); } catch (e) { $('#epBody').innerHTML = `<p class="plug-err">${esc(e.message)}</p>`; return; }
    draw();
  }
  // row "Print" button: one card (or the same card twice)
  function openPrint(j) {
    cur = j; opt = {layout: opt.layout === 'same' ? 'same' : 'one', pair: null, side: 'both', copies: 1};
    start();
  }
  // two ticked cards: first ticked -> slot 1, second -> slot 2
  function openPair(a, b) {
    cur = a; opt = {layout: 'pair', pair: b, side: 'both', copies: 1};
    start();
  }
  function draw() {
    const j = cur, s = st, err = problem(s);
    const second = opt.layout === 'same' ? j : opt.layout === 'pair' ? opt.pair : null;
    const hasBack = j.sides.includes('Back') || !!(second && second.sides.includes('Back'));
    if (!hasBack) opt.side = 'Front';
    const twoSlots = opt.layout !== 'one', slotOne = +(s.slot || 1);
    const pair = opt.layout === 'pair';
    const thumb = (x, n) => `<figure style="margin:0;text-align:center;min-width:0"><img src="/api/image/${x.job}/Front" alt="" style="width:120px;border-radius:6px;border:1px solid var(--line)">
      <figcaption class="hint" style="font-size:11.5px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(nm(x))}">Slot ${n}${pair ? ' · ' + esc(nm(x)) : ''}</figcaption></figure>`;
    const who = pair ? `<b>2 cards</b>` : `<b>${esc(nm(j))}</b>` + (j.ref ? ` <span class="hint mono">${esc(j.ref)}</span>` : '');
    let body = `<div class="holder" style="gap:12px;align-items:center;flex-wrap:wrap">${thumb(j, twoSlots ? 1 : slotOne)}${second ? thumb(second, 2) : ''}<div><div>${who}</div>
      ${pair && step === 'choose' ? `<button class="btn quiet" id="epSwap" style="height:28px;padding:0 8px;font-size:12.5px;margin-top:4px" title="Swap which card goes in slot 1 and slot 2">⇄ Swap slots</button>` : ''}
      <div class="hint">${err ? '' : `Printer: <b>${esc(s.printer)}</b>`}</div></div></div>`;
    let foot = `<a class="btn quiet" href="${BASE}/pdf/${j.job}?name=${encodeURIComponent(j.names.Front || j.source)}" title="Exact card-size PDF (front + back) – e.g. for Epson Photo+">Card PDF</a>`;
    const pressed = (k, v) => `aria-pressed="${opt[k] === v}"`;
    if (err) {
      body += `<p class="plug-err">${esc(err)}</p><p class="hint">You can still download the card-size PDF and print it from Epson Photo+.</p>`;
    } else if (step === 'choose') {
      const where = twoSlots ? '<b>both slots</b>' : `<b>slot ${slotOne}</b>`;
      body += `${pair ? '' : `<div><span class="t">Cards on the tray</span><div class="seg" id="epLay">
          <button data-v="one" ${pressed('layout', 'one')}>One card</button>
          <button data-v="same" ${pressed('layout', 'same')}>Same card ×2</button></div>
          <small class="hint" style="display:block;margin-top:4px">For two different people, tick both cards in the list and use <b>Print on PVC</b>.</small></div>`}
        <div><span class="t">Print</span><div class="seg" id="epSide">
          ${hasBack ? `<button data-v="both" ${pressed('side', 'both')}>Front, then back</button>` : ''}
          <button data-v="Front" ${pressed('side', 'Front')}>Front only</button>${hasBack ? `<button data-v="Back" ${pressed('side', 'Back')}>Back only</button>` : ''}</div></div>
        <div><label class="t" for="epCopies">${twoSlots ? 'Tray passes' : 'Copies'}</label><input class="text" id="epCopies" type="number" min="1" max="50" value="${opt.copies}" style="width:100px">
          ${twoSlots ? '<small class="hint" style="display:block">Each pass prints 2 cards.</small>' : ''}</div>
        <p class="hint">Click <b>Print</b> first. When the printer’s disc light blinks, put clean blank PVC card${twoSlots ? 's' : ''} in ${where} of the Disc/ID Card Tray (printable side up) and insert the tray. Keep the tray clean &ndash; borderless printing can leave ink on it.</p>`;
      foot += `<button class="btn" data-close>Cancel</button><button class="btn primary" id="epGo">${ICON}Print</button>`;
    } else if (step === 'flip') {
      const backs = passCards('Back').map(c => c.slot);
      const turn = twoSlots ? `turn ${backs.length > 1 ? 'both cards' : 'the card in slot ' + backs[0]} over (same top edge), each back in <b>its own slot</b>${backs.length < 2 ? ' (the other card has no back – leave it out)' : ''}`
                            : `turn the card over (same top edge), put it back in slot ${slotOne}`;
      body += `<div class="note" style="padding:0;display:block"><b>Front${twoSlots ? 's' : ''} printed.</b><p class="hint" style="margin:4px 0 0">Let the ink dry a moment. Click <b>Print back</b>, then ${turn} and insert the tray when the disc light blinks.</p></div>`;
      foot += `<button class="btn" data-close>Done for now</button><button class="btn primary" id="epBack">${ICON}Print back</button>`;
    } else if (step === 'done') {
      body += `<p><b>Sent to the printer.</b></p>`;
      foot += `<button class="btn primary" data-close>Close</button>`;
      if (pair && CPS.clearSelection) CPS.clearSelection();          // both cards printed -> untick them
    }
    $('#epBody').innerHTML = body; $('#epFoot').innerHTML = foot;
    const keepCopies = () => { const c = $('#epCopies'); if (c) opt.copies = Math.max(1, +c.value || 1); };
    document.querySelectorAll('#epLay button').forEach(b => b.onclick = () => { keepCopies(); opt.layout = b.dataset.v; draw(); });
    document.querySelectorAll('#epSide button').forEach(b => b.onclick = () => { keepCopies(); opt.side = b.dataset.v; draw(); });
    const sw = $('#epSwap'); if (sw) sw.onclick = () => { keepCopies(); [cur, opt.pair] = [opt.pair, cur]; draw(); };
    const go = $('#epGo'); if (go) go.onclick = async () => {
      keepCopies();
      const first = opt.side === 'Back' ? 'Back' : 'Front';
      if (await send(first, opt.copies)) { step = opt.side === 'both' && passCards('Back').length ? 'flip' : 'done'; draw(); }
    };
    const back = $('#epBack'); if (back) back.onclick = async () => { if (await send('Back', opt.copies)) { step = 'done'; draw(); } };
  }
  async function send(side, copies) {
    const cards = passCards(side);
    if (!cards.length) { toast(`No card has a ${side.toLowerCase()} side`, {error: true}); return false; }
    const btn = $('#epGo') || $('#epBack'); if (btn) { btn.disabled = true; btn.textContent = 'Printing…'; }
    const name = [...new Set(cards.map(c => nm(c.job === cur.job ? cur : opt.pair)))].join(' + ');
    try {
      const r = await api(BASE + '/print', {cards, copies, name});
      toast(`${side} sent to the printer` + (r.slots.length > 1 ? ` (both slots, ${r.cards} cards)` : ''));
      return true;
    } catch (e) { toast(e.message, {error: true}); if (btn) { btn.disabled = false; btn.innerHTML = ICON + 'Print'; } return false; }
  }

  CPS.registerRowAction({id: 'print', label: 'Print', title: 'Print on a PVC card (Epson)', icon: ICON, run: openPrint,
    when: j => j.sides.includes('Front')});   // PVC cards only (not the A4 laminated sheets)

  // selection bar: tick 1 or 2 cards -> print them straight onto the tray (1st ticked = slot 1)
  if (CPS.registerBulkAction) CPS.registerBulkAction({id: 'pvc', label: 'Print on PVC', icon: ICON, primary: true,
    title: 'Print the selected cards on PVC (Epson) – first ticked goes in slot 1',
    when: list => list.some(isPvc),
    blocked: list => !list.every(isPvc) ? 'A4 laminated sheets can’t go on the PVC tray – untick them'
                   : list.length > 2 ? 'The card tray holds 2 cards – select 1 or 2' : '',
    run: list => list.length === 2 ? openPair(list[0], list[1]) : openPrint(list[0])});

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
          ${num('epS2x', 'Slot 2: move right', s.slot2_dx_mm, 0.1, 'mm', 'Only if slot 2 is off while slot 1 is right')}${num('epS2y', 'Slot 2: move down', s.slot2_dy_mm, 0.1, 'mm')}
          <div class="pfield"><label for="epRotSel">Rotation</label>
            <select class="text" id="epRotSel">${[90, 270, 0, 180].map(r => `<option value="${r}" ${+s.rotation === r ? 'selected' : ''}>${r === 90 ? '90° (portrait, like Photo+)' : r === 270 ? '270° (portrait, other way)' : r + '°'}</option>`).join('')}</select></div>
        </div></div>
        <footer><button class="btn primary" id="epSave">Save printer settings</button></footer>
      </section>
      <section class="scard pcard">
        <header><h4>Alignment test</h4><p>Put a <b>blank card</b> in the tray. If the printed outline is off, adjust <i>Position &amp; size</i>; if the TOP EDGE arrow is upside down, choose rotation 270°.</p></header>
        <div class="pbody"><div class="rowf" style="flex-wrap:wrap">
          <button class="btn" id="epTest" ${ready ? '' : 'disabled'}>${ICON}Print alignment test</button>
          <button class="btn" id="epTest2" ${ready ? '' : 'disabled'} title="Blank cards in slot 1 and slot 2">${ICON}Test both slots</button>
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
        card_w_mm: $('#epW').value, card_h_mm: $('#epH').value, scale_pct: $('#epSc').value,
        slot2_dx_mm: $('#epS2x').value, slot2_dy_mm: $('#epS2y').value}, extra || {}));
      s = st;
    };
    document.querySelectorAll('#epMode button').forEach(b => b.onclick = async () => {
      document.querySelectorAll('#epMode button').forEach(x => x.setAttribute('aria-pressed', x === b)); await save(); drawMode();
    });
    body.addEventListener('change', async e => { if (e.target.id === 'epBl') { await save(); drawMode(); } });
    $('#epPrinter').onchange = async () => { await save(); CPS.settingsPanels.epson_pvc(el); };
    $('#epSave').onclick = async () => { try { await save(); toast('Printer settings saved'); } catch (e) { toast(e.message, {error: true}); } };
    $('#epTest').onclick = async () => { try { await save(); await api(BASE + '/test', {}); toast('Alignment test sent'); } catch (e) { toast(e.message, {error: true}); } };
    $('#epTest2').onclick = async () => { try { await save(); await api(BASE + '/test', {slots: [1, 2]}); toast('Alignment test sent (both slots)'); } catch (e) { toast(e.message, {error: true}); } };
    drawMode();
  });
})();
