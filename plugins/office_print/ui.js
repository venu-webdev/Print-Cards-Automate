/* A4 office printing - UI part (uses only window.CPS). */
(() => {
  const {api, toast, esc} = CPS;
  const BASE = '/api/plugins/office_print';
  const trayOpts = (list, sel) => list.length
    ? list.map(t => `<option ${t.name === sel ? 'selected' : ''}>${esc(t.name)}</option>`).join('')
    : '<option value="">Printer default</option>';
  // reload the tray list when another printer is picked
  const bindTrays = (pSel, tSel, after) => pSel.addEventListener('change', async () => {
    tSel.disabled = true;
    try { const r = await api(BASE + '/trays?printer=' + encodeURIComponent(pSel.value)); tSel.innerHTML = trayOpts(r.tray_list, r.tray); }
    catch (e) { tSel.innerHTML = trayOpts([], null); }
    tSel.disabled = false; if (after) after();
  });
  const ICON = '<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9V3h12v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M6 14h12v7H6z"/><path d="M17 12h.01"/></svg>';

  const ov = document.createElement('div');
  ov.className = 'overlay';
  ov.innerHTML = `<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="opTitle" style="width:min(500px,100%)">
    <div class="dhead"><h3 id="opTitle">Print A4 sheets</h3><button class="btn quiet icon" data-close aria-label="Close">${CPS.SVG.x}</button></div>
    <div class="dbody" id="opBody"></div><div class="dfoot" id="opFoot"></div></div>`;
  document.body.appendChild(ov);
  ov.addEventListener('click', e => { if (e.target === ov || e.target.closest('[data-close]')) ov.classList.remove('open'); });

  const $id = id => document.getElementById(id);
  const store = { get: k => { try { return localStorage.getItem(k); } catch { return null; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };
  const ROWS_ICON = '<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="2" width="14" height="20" rx="1.5"/><path d="M8 6h8M8 10h8M8 14h8" opacity=".9"/><path d="M8 18h3" opacity=".4"/></svg>';
  const sheetLine = sh => `${sh.printed} printed · ${sh.waiting} waiting · ${sh.free} free`;
  const preview = () => `<img src="${BASE}/small/preview.png?t=${Date.now()}" alt="Small sheet: which rows are used" style="width:132px;border:1px solid var(--line);border-radius:4px;background:#fff;flex:none">`;
  const refeedHint = sh => sh.printed
    ? `<p class="hint" style="margin:0">Put the <b>same sheet</b> back in the tray, the same way round as last time &ndash; only the new rows print.</p>` : '';

  // one card (row Print button) or several ticked cards (selection bar)
  async function open(arg) {
    const list = Array.isArray(arg) ? arg : [arg], j = list[0], many = list.length > 1;
    const sides = [...new Set(list.flatMap(x => x.sides))];          // Full, Both, Small
    const canSmall = list.every(x => x.sides.includes('Small'));
    let mode = canSmall && store.get('cps-a4-mode') === 'small' ? 'small' : 'sheets';
    ov.classList.add('open');
    const B = $id('opBody'), F = $id('opFoot');
    B.innerHTML = '<span class="status"><span class="spin"></span>Checking printers…</span>'; F.innerHTML = '';
    let s, sh;
    try { [s, sh] = await Promise.all([api(BASE + '/status'), api(BASE + '/small')]); }
    catch (e) { B.innerHTML = `<p class="plug-err">${esc(e.message)}</p>`; return; }
    if (!s.service.ok) { B.innerHTML = `<p class="plug-err">${esc(s.service.error)}</p>`; return; }
    const who = many
      ? `<div><b>${list.length} cards</b><div class="hint" style="margin-top:2px">${list.map(x => esc(x.details.name || x.source)).join(' · ')}</div></div>`
      : `<div><b>${esc(j.details.name || j.source)}</b> <span class="hint mono">${esc(j.ref || '')}</span></div>`;
    let keep = {printer: s.printer, tray: s.tray, color: s.color};
    // per person (several cards): which sheets + copies; starts with the first sheet (Full) for everyone
    const pick = Object.fromEntries(list.map(x => [x.job, {sheets: [x.sides[0]], copies: 1}]));
    const readPick = () => list.forEach((x, n) => {
      const p = pick[x.job];
      p.sheets = [...document.querySelectorAll(`.opPer[data-n="${n}"]:checked`)].map(c => c.value);
      const cp = document.querySelector(`.opPerCopies[data-n="${n}"]`); if (cp) p.copies = Math.max(1, Math.min(99, +cp.value || 1));
    });
    const perCardHtml = () => {
      const cell = 'padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:middle';
      const box = (n, x, sd) => x.sides.includes(sd)
        ? `<input type="checkbox" class="opPer" data-n="${n}" value="${esc(sd)}" ${pick[x.job].sheets.includes(sd) ? 'checked' : ''} aria-label="${esc(sd)} for ${esc(x.details.name || x.source)}">` : '–';
      return `<div><span class="t">Sheets for each person</span>
        <div style="max-height:260px;overflow:auto;border:1px solid var(--line);border-radius:8px">
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <tr style="background:var(--surface-2)"><th style="${cell};text-align:left">Person</th>${sides.map(sd => `<th style="${cell}">${esc(sd)}</th>`).join('')}<th style="${cell}">Copies</th></tr>
          <tr><td style="${cell}" class="hint">Same for all</td>${sides.map(sd => `<td style="${cell};text-align:center"><input type="checkbox" class="opAll" value="${esc(sd)}" aria-label="${esc(sd)} for everyone"
              ${list.every(x => !x.sides.includes(sd) || pick[x.job].sheets.includes(sd)) ? 'checked' : ''}></td>`).join('')}
            <td style="${cell}"><input class="text opAllCopies" type="number" min="1" max="99" placeholder="–" style="width:62px" aria-label="Copies for everyone"></td></tr>
          ${list.map((x, n) => `<tr><td style="${cell};max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(x.details.name || x.source)}"><b>${esc(x.details.name || x.source)}</b></td>
            ${sides.map(sd => `<td style="${cell};text-align:center">${box(n, x, sd)}</td>`).join('')}
            <td style="${cell}"><input class="text opPerCopies" data-n="${n}" type="number" min="1" max="99" value="${pick[x.job].copies}" style="width:62px" aria-label="Copies for ${esc(x.details.name || x.source)}"></td></tr>`).join('')}
        </table></div></div>`;
    };
    // "Share pages": a Small goes under each Big (Full); other Smalls share pages, 5 per A4 (same rule as the server)
    let share = store.get('cps-a4-share') !== '0';
    const countPages = sheets => {
      if (!share) return sheets.length;
      const n = k => sheets.filter(s => s === k).length, F = n('Full');
      return F + n('Both') + Math.ceil(Math.max(0, n('Small') - F) / 5);
    };
    const perCardSum = () => {
      let sheets, none = 0;
      if (many) {
        readPick();
        sheets = list.flatMap(x => Array(pick[x.job].copies).fill(pick[x.job].sheets).flat());
        none = list.filter(x => !pick[x.job].sheets.length).length;
      } else {
        const ck = [...document.querySelectorAll('.opSheet:checked')].map(c => c.value), cp = $id('opCopies');
        sheets = Array(Math.max(1, +(cp && cp.value) || 1)).fill(ck).flat();
      }
      const pages = countPages(sheets), saved = sheets.length - pages;
      const el = $id('opPerSum');
      if (el) el.textContent = `${pages} A4 sheet${pages === 1 ? '' : 's'} in one print job` + (saved > 0 ? ` (${saved} saved by sharing pages)` : '')
        + (none ? ` · ${none} person${none > 1 ? 's' : ''} with nothing ticked (skipped)` : '');
    };

    const draw = () => {
      const free = sh.rows.map((r, i) => r ? 0 : i + 1).filter(Boolean);
      const fits = list.length <= free.length;
      const rowsTxt = free.slice(0, list.length).join(', ');
      const common = `<div><label class="t" for="opPrinter">Printer</label>
          <select class="text" id="opPrinter">${s.service.printers.map(p => `<option ${p === keep.printer ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select></div>
        <div><label class="t" for="opTray">Paper tray</label>
          <select class="text" id="opTray">${trayOpts(s.tray_list, keep.tray)}</select></div>`;
      let body = `${who}
        <div class="seg" id="opMode" role="group" aria-label="What to print">
          <button data-v="sheets" aria-pressed="${mode === 'sheets'}">Whole sheets</button>
          <button data-v="small" aria-pressed="${mode === 'small'}" ${canSmall ? '' : 'disabled title="These cards have no Small sheet"'}>Small sheet &ndash; row by row</button></div>`;
      let foot = `<button class="btn" data-close>Cancel</button>`;
      if (mode === 'sheets') {
        body += `${common}
          ${many ? perCardHtml() : `<div><span class="t">Sheets</span><div style="display:flex;gap:14px;flex-wrap:wrap">${sides.map((x, i) =>
            `<label style="display:flex;gap:6px;align-items:center"><input type="checkbox" class="opSheet" value="${esc(x)}" ${i === 0 ? 'checked' : ''}>${esc(x)}</label>`).join('')}</div></div>`}
          <div class="rowf" style="align-items:end;gap:16px">
            ${many ? '' : `<div><label class="t" for="opCopies">Copies</label><input class="text" id="opCopies" type="number" min="1" max="99" value="1" style="width:90px"></div>`}
            <label style="display:flex;gap:6px;align-items:center;padding-bottom:8px"><input type="checkbox" id="opColor" ${keep.color ? 'checked' : ''}> Colour</label></div>
          <label style="display:flex;gap:8px;align-items:flex-start"><input type="checkbox" id="opShare" ${share ? 'checked' : ''} style="margin-top:3px">
            <span><b>Share pages</b> <span class="hint">&ndash; a Small goes under a Big on the same sheet; other Smalls share a sheet (5 per A4)</span></span></label>
          <div class="hint" id="opPerSum"></div>
          <p class="hint" style="margin:0">Printed at exactly 100% (A4, ${s.dpi} dpi) &ndash; no “fit to page”, so the cards keep their real size for lamination.</p>`;
        foot += `<button class="btn primary" id="opGo">${ICON}Print</button>`;
      } else {
        const now = sh.waiting + list.length;
        body += `<div style="display:flex;gap:14px;align-items:flex-start">${preview()}
            <div style="display:grid;gap:8px;min-width:0">
              <div><b>Small sheet</b> <span class="hint">(${sh.size} strips per A4)</span><div class="hint">${sheetLine(sh)}</div></div>
              ${fits ? `<div>${many ? 'These cards go' : 'This card goes'} in <b>row ${rowsTxt}</b>.</div>`
                     : `<div class="plug-err" style="margin:0">Only ${free.length} row${free.length === 1 ? '' : 's'} left on this sheet.</div>
                        <button class="btn" id="opNew" style="justify-self:start">Start a new sheet</button>`}
              ${sh.waiting ? `<div class="hint">${sh.waiting} card${sh.waiting > 1 ? 's are' : ' is'} already waiting on this sheet${fits ? ' – “Print now” prints them too' : ''}.</div>` : ''}
              ${fits ? refeedHint(sh) : ''}
            </div></div>
          ${common}
          <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="opColor" ${keep.color ? 'checked' : ''}> Colour</label>`;
        foot += `<button class="btn" id="opAdd" ${fits ? '' : 'disabled'} title="Keep ${many ? 'them' : 'it'} on the sheet and print later (Small sheet button at the top)">Add to sheet</button>
          <button class="btn primary" id="opNow" ${fits ? '' : 'disabled'}>${ICON}Print now${now > 1 ? ` (${now} rows)` : ` in row ${rowsTxt}`}</button>`;
      }
      B.innerHTML = body; F.innerHTML = foot;
      const remember = () => { keep = {printer: $id('opPrinter').value, tray: $id('opTray').value, color: $id('opColor').checked}; if (many && mode === 'sheets') readPick(); };
      bindTrays($id('opPrinter'), $id('opTray'));
      if (many && mode === 'sheets') {
        B.querySelectorAll('.opPer, .opPerCopies').forEach(i => i.addEventListener('input', () => {
          perCardSum();
          sides.forEach(sd => { const a = B.querySelector(`.opAll[value="${sd}"]`);   // "Same for all" follows the rows
            if (a) a.checked = list.every(x => !x.sides.includes(sd) || pick[x.job].sheets.includes(sd)); });
        }));
        B.querySelectorAll('.opAll').forEach(a => a.addEventListener('change', () => {
          B.querySelectorAll(`.opPer[value="${a.value}"]`).forEach(c => c.checked = a.checked); perCardSum();
        }));
        const ac = B.querySelector('.opAllCopies');
        ac.addEventListener('input', () => { if (+ac.value >= 1) { B.querySelectorAll('.opPerCopies').forEach(c => c.value = Math.min(99, +ac.value)); perCardSum(); } });
      }
      if (mode === 'sheets') {
        B.querySelectorAll('.opSheet, #opCopies').forEach(i => i.addEventListener('input', perCardSum));
        $id('opShare').addEventListener('change', e => { share = e.target.checked; store.set('cps-a4-share', share ? '1' : '0'); perCardSum(); });
        perCardSum();
      }
      document.querySelectorAll('#opMode button').forEach(b => b.onclick = () => { remember(); mode = b.dataset.v; store.set('cps-a4-mode', mode); draw(); });
      if ($id('opNew')) $id('opNew').onclick = async () => {
        if (!confirm('Start a new Small sheet?\n\nUse this when the current sheet is full or was used up. Cards still waiting on it are taken off.')) return;
        remember(); try { sh = await api(BASE + '/small/new', {}); refreshBadge(); draw(); } catch (e) { toast(e.message, {error: true}); }
      };
      const busy = (b, t) => { b.disabled = true; b.textContent = t; };
      const done = msg => { toast(msg); ov.classList.remove('open'); refreshBadge(); if (many && CPS.clearSelection) CPS.clearSelection(); };
      const fail = (b, label, e) => { toast(e.message, {error: true}); b.disabled = false; b.innerHTML = label; };
      if ($id('opGo')) $id('opGo').onclick = async () => {
        remember();
        const sheets = [...document.querySelectorAll('.opSheet:checked')].map(x => x.value);
        const items = many ? list.map(x => ({job: x.job, sheets: pick[x.job].sheets, copies: pick[x.job].copies})).filter(it => it.sheets.length) : null;
        if (many ? !items.length : !sheets.length) { toast('Choose at least one sheet', {error: true}); return; }
        const b = $id('opGo'); busy(b, 'Sending…');
        try {
          await api(BASE + '/config', keep);
          const r = await api(BASE + '/print', many
            ? {items, share, color: keep.color, tray: keep.tray, name: `${items.length} cards`}
            : {jobs: [j.job], sheets, share, copies: +$id('opCopies').value || 1, color: keep.color, tray: keep.tray, name: j.details.name || j.source});
          done(`Sent ${many ? `${r.sheets} sheets (${r.cards} cards)` : r.printed.join(', ')} to ${r.printer}${r.tray ? ' (' + r.tray + ')' : ''}`);
        } catch (e) { fail(b, ICON + 'Print', e); }
      };
      const add = async printNow => {
        remember(); const b = $id(printNow ? 'opNow' : 'opAdd'), label = b.innerHTML; busy(b, printNow ? 'Sending…' : 'Adding…');
        try {
          await api(BASE + '/config', keep);
          const r = await api(BASE + '/small/add', {jobs: list.map(x => x.job), print: printNow, printer: keep.printer, tray: keep.tray, color: keep.color});
          done(printNow ? `Printed row ${r.printed_rows.join(', ')} of the Small sheet on ${r.printer}`
                        : `Added to the Small sheet (row ${r.added_rows.join(', ')}) – print it from “Small sheet” at the top`);
        } catch (e) { fail(b, label, e); }
      };
      if ($id('opAdd')) $id('opAdd').onclick = () => add(false);
      if ($id('opNow')) $id('opNow').onclick = () => add(true);
    };
    draw();
  }

  /* ---------------- Small sheet manager (toolbar button) ---------------- */
  const sov = document.createElement('div');
  sov.className = 'overlay';
  sov.innerHTML = `<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="ssTitle" style="width:min(560px,100%)">
    <div class="dhead"><h3 id="ssTitle">Small sheet</h3><button class="btn quiet icon" data-close aria-label="Close">${CPS.SVG.x}</button></div>
    <div class="dbody" id="ssBody"></div><div class="dfoot" id="ssFoot"></div></div>`;
  document.body.appendChild(sov);
  sov.addEventListener('click', e => { if (e.target === sov || e.target.closest('[data-close]')) sov.classList.remove('open'); });

  const tb = document.createElement('button');
  tb.className = 'btn'; tb.id = 'smallSheetBtn'; tb.title = 'Aadhaar Small sheet: several customers on one A4, row by row';
  tb.onclick = () => openSheet();
  if (CPS.addToolbarButton) CPS.addToolbarButton(tb);
  async function refreshBadge() {
    try { const sh = await api(BASE + '/small'); tb.innerHTML = `${ROWS_ICON}<span class="lbl">Small sheet</span> <span class="hint mono">${sh.size - sh.free}/${sh.size}</span>${sh.waiting ? ` <span class="chip on" title="Waiting to print">${sh.waiting}</span>` : ''}`; }
    catch { tb.innerHTML = `${ROWS_ICON}<span class="lbl">Small sheet</span>`; }
  }
  refreshBadge();

  async function openSheet() {
    sov.classList.add('open');
    const B = $id('ssBody'), F = $id('ssFoot');
    B.innerHTML = '<span class="status"><span class="spin"></span>Loading…</span>'; F.innerHTML = '';
    let s, sh;
    try { [s, sh] = await Promise.all([api(BASE + '/status'), api(BASE + '/small')]); } catch (e) { B.innerHTML = `<p class="plug-err">${esc(e.message)}</p>`; return; }
    const when = t => t ? new Date(t).toLocaleString([], {day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit'}) : '';
    const rowHtml = (r, i) => `<div style="display:flex;gap:10px;align-items:center;min-height:30px;border-bottom:1px solid var(--line);padding:2px 0">
        <span class="mono hint" style="width:44px">Row ${i + 1}</span>
        ${r ? `<b style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.name)}</b>
               <span class="chip ${r.status === 'waiting' ? 'on' : ''}">${r.status === 'waiting' ? 'Waiting' : 'Printed ' + esc(when(r.at))}</span>
               ${r.status === 'waiting' ? `<button class="btn quiet icon" data-rm="${i + 1}" title="Take off the sheet" aria-label="Remove ${esc(r.name)}">${CPS.SVG.x}</button>` : ''}`
             : '<span class="hint" style="flex:1">Free</span>'}</div>`;
    const ok = s.service && s.service.ok;
    B.innerHTML = `<div style="display:flex;gap:16px;align-items:flex-start">${preview()}
        <div style="flex:1;min-width:0"><div class="hint" style="margin-bottom:6px">${sheetLine(sh)} &middot; started ${esc(when(sh.started))}</div>
          ${sh.rows.map(rowHtml).join('')}
          <p class="hint" style="margin:8px 0 0">Add a customer with <b>Print → Small sheet – row by row</b> on a laminated Aadhaar.</p></div></div>
      ${sh.waiting ? (ok ? `<div class="hint">Prints on <b>${esc(s.printer || '—')}</b>, tray <b>${esc(s.tray || 'printer default')}</b> (change in the A4 print dialog or Settings).</div>${refeedHint(sh)}`
                         : `<p class="plug-err">${esc(s.service.error)}</p>`) : ''}`;
    F.innerHTML = `<button class="btn" id="ssNew" title="The sheet is full or used up – start with a blank sheet">New sheet</button>
      <button class="btn primary" id="ssPrint" ${sh.waiting && ok ? '' : 'disabled'}>${ICON}Print ${sh.waiting || ''} waiting row${sh.waiting === 1 ? '' : 's'}</button>`;
    B.querySelectorAll('[data-rm]').forEach(b => b.onclick = async () => {
      try { await api(BASE + '/small/remove', {row: +b.dataset.rm}); refreshBadge(); openSheet(); } catch (e) { toast(e.message, {error: true}); }
    });
    $id('ssNew').onclick = async () => {
      if ((sh.waiting || sh.printed) && !confirm(`Start a new Small sheet?${sh.waiting ? `\n\n${sh.waiting} waiting card${sh.waiting > 1 ? 's' : ''} will be taken off (you can add them again).` : ''}`)) return;
      try { await api(BASE + '/small/new', {}); refreshBadge(); openSheet(); toast('New Small sheet started'); } catch (e) { toast(e.message, {error: true}); }
    };
    $id('ssPrint').onclick = async () => {
      const b = $id('ssPrint'); b.disabled = true; b.textContent = 'Sending…';
      try { const r = await api(BASE + '/small/print', {}); toast(`Printed row ${r.printed_rows.join(', ')} on ${r.printer}`); refreshBadge(); openSheet(); }
      catch (e) { toast(e.message, {error: true}); b.disabled = false; b.innerHTML = ICON + 'Print waiting rows'; }
    };
  }

  /* ---------------- PVC cards on A4: front + back side by side, printed straight away ---------------- */
  async function openPvc(arg) {
    const list = (Array.isArray(arg) ? arg : [arg]).filter(x => !x.pdf);
    if (!list.length) { toast('Laminated Aadhaar sheets are already A4 – use their Print button', {error: true}); return; }
    ov.classList.add('open');
    $id('opTitle').textContent = 'Print PVC cards on A4';
    const B = $id('opBody'), F = $id('opFoot');
    B.innerHTML = '<span class="status"><span class="spin"></span>Checking printers…</span>'; F.innerHTML = '';
    let s;
    try { s = await api(BASE + '/status'); } catch (e) { B.innerHTML = `<p class="plug-err">${esc(e.message)}</p>`; return; }
    if (!s.service.ok) { B.innerHTML = `<p class="plug-err">${esc(s.service.error)}</p>`; return; }
    const pages = n => Math.ceil(n / 4);
    B.innerHTML = `${list.length > 1
        ? `<div><b>${list.length} cards</b><div class="hint" style="margin-top:2px">${list.map(x => esc(x.details.name || x.source)).join(' · ')}</div></div>`
        : `<div><b>${esc(list[0].details.name || list[0].source)}</b> <span class="hint mono">${esc(list[0].ref || '')}</span></div>`}
      <div class="hint">Front and back side by side at real card size (85.6 × 54 mm) with cut marks &ndash; 4 cards per A4 page.</div>
      <div><label class="t" for="opPrinter">Printer</label>
        <select class="text" id="opPrinter">${s.service.printers.map(p => `<option ${p === s.printer ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select></div>
      <div><label class="t" for="opTray">Paper tray</label><select class="text" id="opTray">${trayOpts(s.tray_list, s.tray)}</select></div>
      <div class="rowf" style="align-items:end;gap:16px">
        <div><label class="t" for="opCopies">Copies</label><input class="text" id="opCopies" type="number" min="1" max="99" value="1" style="width:90px"></div>
        <label style="display:flex;gap:6px;align-items:center;padding-bottom:8px"><input type="checkbox" id="opColor" ${s.color ? 'checked' : ''}> Colour</label></div>
      <div class="hint" id="opPerSum"></div>
      <p class="hint" style="margin:0">Printed at exactly 100% (A4, ${s.dpi} dpi) &ndash; no “fit to page”, so the cards keep their real size.</p>`;
    F.innerHTML = `<a class="btn quiet" href="/api/a4?${list.map(x => 'job=' + x.job).join('&')}" download title="Save the same A4 layout as a PDF">A4 PDF</a>
      <span style="flex:1"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="opGo">${ICON}Print</button>`;
    const sum = () => { const c = Math.max(1, +$id('opCopies').value || 1), p = pages(list.length);
      $id('opPerSum').textContent = `${p} A4 sheet${p === 1 ? '' : 's'}${c > 1 ? ` × ${c} copies` : ''}`; };
    $id('opCopies').addEventListener('input', sum); sum();
    bindTrays($id('opPrinter'), $id('opTray'));
    $id('opGo').onclick = async () => {
      const b = $id('opGo'); b.disabled = true; b.textContent = 'Sending…';
      const keep = {printer: $id('opPrinter').value, tray: $id('opTray').value, color: $id('opColor').checked};
      try {
        await api(BASE + '/config', keep);
        const r = await api(BASE + '/pvc', {jobs: list.map(x => x.job), copies: +$id('opCopies').value || 1, ...keep,
          name: list.length > 1 ? `${list.length} cards` : (list[0].details.name || list[0].source)});
        toast(`Sent ${r.cards} card${r.cards > 1 ? 's' : ''} (${r.sheets} A4 sheet${r.sheets > 1 ? 's' : ''}) to ${r.printer}${r.tray ? ' (' + r.tray + ')' : ''}`);
        ov.classList.remove('open'); if (list.length > 1 && CPS.clearSelection) CPS.clearSelection();
      } catch (e) { toast(e.message, {error: true}); b.disabled = false; b.innerHTML = ICON + 'Print'; }
    };
  }

  CPS.registerRowAction({id: 'a4print', label: 'Print', title: 'Print A4 sheets (office printer)', icon: ICON,
    run: j => { $id('opTitle').textContent = 'Print A4 sheets'; open(j); }, when: j => j.pdf});
  // panel: shown in the card's ⋯ options next to "A4 PDF" (the row itself is already full for PVC cards)
  CPS.registerRowAction({id: 'pvca4', label: 'Print A4', title: 'Print on A4: front and back side by side at real size (office printer)',
    icon: ICON, run: openPvc, when: j => !j.pdf, panel: true});
  if (CPS.registerBulkAction) CPS.registerBulkAction({id: 'pvca4', label: 'Print on A4', icon: ICON,
    title: 'Print the selected PVC cards on A4 – front and back side by side, 4 cards per page',
    when: list => list.some(x => !x.pdf),
    blocked: list => list.every(x => !x.pdf) ? '' : 'Laminated Aadhaar sheets print with “Print A4” – untick them',
    run: list => openPvc(list)});

  // selection bar: tick laminated cards -> print all their sheets in one go
  if (CPS.registerBulkAction) CPS.registerBulkAction({id: 'a4', label: 'Print A4', icon: ICON, primary: true,
    title: 'Print the A4 sheets of the selected laminated cards (office printer)',
    when: list => list.some(x => x.pdf),
    blocked: list => list.every(x => x.pdf) ? '' : 'PVC cards print on the Epson – untick them to print the A4 sheets',
    run: list => { $id('opTitle').textContent = 'Print A4 sheets'; open(list); }});

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
        <header><h4>Paper tray</h4><p>Where the A4 sheets are taken from. Multi-purpose Tray is the default; the list comes from the printer's driver and is remembered per printer.</p></header>
        <div class="pbody"><select class="text" id="opsTray">${trayOpts(s.tray_list, s.tray)}</select></div>
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
    const save = async () => { try { await api(BASE + '/config', {printer: el.querySelector('#opsP').value, tray: el.querySelector('#opsTray').value, color: el.querySelector('#opsC').checked, dpi: dpi()}); toast('Saved'); } catch (e) { toast(e.message, {error: true}); } };
    el.querySelectorAll('#opsTray,input').forEach(x => x.onchange = save);
    bindTrays(el.querySelector('#opsP'), el.querySelector('#opsTray'), save);   // new printer -> its trays, then save
    el.querySelectorAll('#opsD button').forEach(b => b.onclick = () => { el.querySelectorAll('#opsD button').forEach(x => x.setAttribute('aria-pressed', x === b)); save(); });
    el.querySelector('#opsT').onclick = async ev => {
      const b = ev.currentTarget; b.disabled = true;
      try { const r = await api(BASE + '/test', {printer: el.querySelector('#opsP').value, tray: el.querySelector('#opsTray').value}); toast(`Scale test sent to ${r.printer}${r.tray ? ' (' + r.tray + ')' : ''}`); }
      catch (e) { toast(e.message, {error: true}); }
      b.disabled = false;
    };
  });
})();
