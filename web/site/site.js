/* Card Print Studio - marketing / account pages (no data leaves the page). */
(() => {
  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const store = { get: k => { try { return localStorage.getItem(k); } catch { return null; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };

  // theme (same key as the app)
  const eff = () => { const t = document.documentElement.dataset.theme;
    return (t === 'light' || t === 'dark') ? t : (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); };
  const SUN = '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>';
  const MOON = '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>';
  const paint = () => $$('[data-theme-icon]').forEach(s => s.innerHTML = eff() === 'dark' ? SUN : MOON);
  $$('[data-theme-toggle]').forEach(b => b.addEventListener('click', () => {
    const n = eff() === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = n; store.set('cps-theme', n); paint();
  }));
  paint();

  // sticky nav border
  const nav = $('.nav');
  if (nav) { const f = () => nav.classList.toggle('scrolled', scrollY > 8); addEventListener('scroll', f, {passive: true}); f(); }

  // reveal on scroll
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), {threshold: .12});
    $$('.rv').forEach(el => io.observe(el));
  } else $$('.rv').forEach(el => el.classList.add('in'));

  // show / hide password
  $$('[data-eye]').forEach(b => b.addEventListener('click', () => {
    const i = b.parentElement.querySelector('input'); const show = i.type === 'password';
    i.type = show ? 'text' : 'password'; b.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    b.innerHTML = show ? '<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.1A10 10 0 0 1 12 5c6 0 9.5 7 9.5 7a17 17 0 0 1-3.2 4.1M6.6 6.6C3.9 8.4 2.5 12 2.5 12S6 19 12 19a9.6 9.6 0 0 0 4.4-1"/></svg>'
                       : '<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2.5 12S6 5 12 5s9.5 7 9.5 7-3.5 7-9.5 7-9.5-7-9.5-7z"/><circle cx="12" cy="12" r="3"/></svg>';
  }));

  // password strength (register)
  const pw = $('#pw'), meter = $$('.meter i'), mt = $('.meter-txt');
  if (pw && meter.length) pw.addEventListener('input', () => {
    const v = pw.value; let s = 0;
    if (v.length >= 8) s++; if (/[A-Z]/.test(v) && /[a-z]/.test(v)) s++; if (/\d/.test(v)) s++; if (/[^A-Za-z0-9]/.test(v) || v.length >= 14) s++;
    const col = ['var(--err)', '#f59e0b', '#84cc16', 'var(--ok)'][Math.max(0, s - 1)];
    meter.forEach((m, i) => m.style.background = v && i < s ? col : '');
    mt.textContent = v ? ['Too weak', 'Weak', 'Good', 'Strong'][Math.max(0, s - 1)] + (v.length < 8 ? ' · use at least 8 characters' : '') : '';
  });

  // Accounts are not switched on yet: these pages are a design preview. Nothing is sent or stored.
  const banner = $('.banner');
  const notYet = e => { e.preventDefault(); if (banner) { banner.classList.add('show'); banner.scrollIntoView({block: 'nearest', behavior: 'smooth'}); } };
  $$('form[data-preview]').forEach(f => f.addEventListener('submit', e => {
    if (!f.reportValidity()) return; f.querySelectorAll('input[type=password]').forEach(i => i.value = ''); notYet(e);
  }));
  $$('[data-google]').forEach(b => b.addEventListener('click', notYet));

  // year
  $$('[data-year]').forEach(s => s.textContent = new Date().getFullYear());
})();
