// Budgets and charts for the expenses page, drawn as HTML bars and inline SVG (no chart library, works offline).
// expenses.js calls HomeHubExpenseCharts.render() whenever the month on screen changes.
(function(){
  const NS = 'http://www.w3.org/2000/svg';
  const WIDTH = 300;
  // Rows past this fold into "Other", so a long category list stays readable
  const MAX_ROWS = 8;

  function esc(str){ return String(str ?? '').replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }

  function svg(height, body, label){
    return `<svg xmlns="${NS}" viewBox="0 0 ${WIDTH} ${height}" width="100%" role="img" aria-label="${esc(label)}" style="max-width:100%;overflow:visible">${body}</svg>`;
  }

  // A table with the same numbers, for screen readers and anyone who prefers figures
  function table(rows, headers){
    return `<details class="mt-2 text-xs text-gray-500"><summary class="cursor-pointer">${th('Show as table')}</summary>
      <table class="mt-1 w-full"><thead><tr>${headers.map((h, i)=>`<th class="text-left font-semibold py-0.5 ${i ? 'text-right' : ''}">${esc(h)}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(r=>`<tr>${r.map((c, i)=>`<td class="py-0.5 ${i ? 'text-right tabular-nums' : ''}">${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></details>`;
  }

  function empty(el){ el.innerHTML = `<p class="text-sm text-gray-500">${th('No spending this month.')}</p>`; }

  function foldRows(rows){
    rows = rows.filter(r => r.value > 0).sort((a, b) => b.value - a.value);
    if (rows.length <= MAX_ROWS) return rows;
    const rest = rows.slice(MAX_ROWS - 1).reduce((sum, r) => sum + r.value, 0);
    return rows.slice(0, MAX_ROWS - 1).concat([{ label: t('Other'), value: rest, other: true }]);
  }

  // Each category and each person keeps one colour: the configured order picks the slot,
  // and names not in the list get one from their spelling, so it never depends on this month's ranking.
  const SLOTS = 8;
  function hashName(name){
    let h = 0;
    for (const ch of String(name)) h = (h * 31 + ch.codePointAt(0)) >>> 0;
    return h;
  }
  // Colours for the names in one chart. Configured names take their own slot; other names start
  // from their spelling and move to the next free slot when that one is taken (alphabetically, so it stays stable).
  function colorsFor(names, ordered){
    ordered = ordered || [];
    const slots = {}, used = new Set();
    names.forEach(name => {
      const index = ordered.indexOf(name);
      if (index >= 0){ slots[name] = index % SLOTS; used.add(slots[name]); }
    });
    names.filter(name => !(name in slots)).sort().forEach(name => {
      let slot = hashName(name) % SLOTS;
      for (let tries = 0; used.has(slot) && tries < SLOTS; tries++) slot = (slot + 1) % SLOTS;
      slots[name] = slot;
      used.add(slot);
    });
    const colors = {};
    Object.keys(slots).forEach(name => { colors[name] = `var(--chart-${slots[name] + 1})`; });
    return colors;
  }
  function people(){
    let names = [];
    try { names = JSON.parse((document.getElementById('familyData') || {}).textContent || '[]') || []; } catch (e) { names = []; }
    const adminName = window.HomeHubAdmin && window.HomeHubAdmin.names && window.HomeHubAdmin.names[0];
    if (adminName && names.indexOf(adminName) === -1) names.push(adminName);
    return names;
  }
  // Colours picked in config.yml (base.html sets one CSS variable per person, with its dark-mode shade)
  function memberColors(){
    try { return JSON.parse((document.getElementById('memberColors') || {}).textContent || '{}') || {}; } catch (e) { return {}; }
  }
  // Palette colours by position, then the picked ones on top, so nobody else's colour moves
  function personColors(names){
    const colors = colorsFor(names, people());
    const picked = memberColors();
    names.forEach(name => { if (picked[name] && picked[name].var) colors[name] = `var(${picked[name].var})`; });
    return colors;
  }

  function bar(value, max, fill){
    const pct = value > 0 ? Math.max(1, (value / max) * 100) : 0;
    return `<div class="chart-track"><div class="chart-bar" style="width:${pct.toFixed(2)}%;background:${fill}"></div></div>`;
  }
  // Their share: the person's colour hatched over a pale tint of it
  function hatch(color){
    return `repeating-linear-gradient(135deg, ${color} 0 2px, transparent 2px 5px), color-mix(in srgb, ${color} 18%, transparent)`;
  }

  // One row per category: name, bar, amount
  function categoryChart(el, perCategory, categories, fmt){
    const rows = foldRows(Object.entries(perCategory || {}).map(([label, value]) => ({ label, value: Number(value) || 0 })));
    if (!rows.length){ empty(el); return; }
    // Colours come from every category of the month, so folding into "Other" does not shift them
    const colors = colorsFor(Object.keys(perCategory || {}), categories);
    const max = Math.max(...rows.map(r => r.value));
    const list = rows.map(r => `<li class="chart-row" title="${esc(r.label)}: ${esc(fmt(r.value))}">
        <span class="chart-name">${esc(r.label)}</span>${bar(r.value, max, r.other ? 'var(--chart-other)' : colors[r.label])}<span class="chart-amount">${esc(fmt(r.value))}</span></li>`).join('');
    el.innerHTML = `<ul class="chart-rows" aria-label="${esc(t('Spending by category'))}">${list}</ul>` +
      table(rows.map(r => [r.label, fmt(r.value)]), [t('Category'), t('Amount')]);
  }

  // One row per person with two thin bars: what they paid (solid) and their share after splits (hatched).
  // People with nothing paid and no share this month are left out.
  function peopleChart(el, perPayer, perShare, fmt){
    const names = Array.from(new Set(Object.keys(perPayer || {}).concat(Object.keys(perShare || {})))).filter(Boolean);
    const rows = names.map(name => ({ label: name, paid: Number((perPayer || {})[name]) || 0, share: Number((perShare || {})[name]) || 0 }))
      .filter(r => r.paid > 0 || r.share > 0).sort((a, b) => Math.max(b.paid, b.share) - Math.max(a.paid, a.share));
    if (!rows.length){ empty(el); return; }
    const max = Math.max(...rows.map(r => Math.max(r.paid, r.share)));
    const colors = personColors(rows.map(r => r.label));
    const legend = `<div class="chart-legend" aria-hidden="true">
        <span><i class="chart-swatch" style="background:var(--muted-text)"></i>${th('Paid')}</span>
        <span><i class="chart-swatch" style="background:${hatch('var(--muted-text)')}"></i>${th('Share after splits')}</span></div>`;
    const list = rows.map(r => `<li class="chart-row chart-row-pair" title="${esc(t('{name}: paid {paid}, share {share}', { name: r.label, paid: fmt(r.paid), share: fmt(r.share) }))}">
        <span class="chart-name">${esc(r.label)}</span>
        ${bar(r.paid, max, colors[r.label])}<span class="chart-amount">${esc(fmt(r.paid))}</span>
        ${bar(r.share, max, hatch(colors[r.label]))}<span class="chart-amount chart-amount-muted">${esc(fmt(r.share))}</span></li>`).join('');
    el.innerHTML = legend + `<ul class="chart-rows" aria-label="${esc(t('Paid and share by person'))}">${list}</ul>` +
      table(rows.map(r => [r.label, fmt(r.paid), fmt(r.share)]), [t('Person'), t('Paid'), t('Share after splits')]);
  }

  // One column per day of the month. Today and the highest day are drawn full strength, the rest
  // a little lighter; empty days get a faint stub, and a dashed line marks the daily share of the overall budget.
  function dayChart(el, byDate, year, month, budgets, fmt){
    const days = new Date(year, month, 0).getDate();
    const values = [];
    for (let d = 1; d <= days; d++){
      const key = `${year}-${String(month).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      values.push({ day: d, key, value: Number((byDate[key] || {}).total) || 0 });
    }
    const max = Math.max(...values.map(v => v.value));
    if (!max){ empty(el); return; }
    const now = new Date();
    const todayDay = (now.getFullYear() === year && now.getMonth() + 1 === month) ? now.getDate() : null;
    const overall = (budgets || []).find(b => b.category == null);
    const dailyBudget = overall ? overall.amount / days : null;
    const scaleMax = Math.max(max, dailyBudget || 0);
    const plotH = 110, top = 14, slot = WIDTH / days, barW = Math.max(2, slot - 2);
    const bars = values.map(v => {
      const x = (v.day - 1) * slot + (slot - barW) / 2;
      const strong = v.day === todayDay || v.value === max;
      const note = v.day === todayDay ? ` (${t('today')})` : '';
      let mark;
      if (v.value) {
        const h = Math.max(2, (v.value / scaleMax) * plotH);
        mark = `<rect x="${x}" y="${top + plotH - h}" width="${barW}" height="${h}" rx="${Math.min(2, barW / 2)}" style="fill:var(--primary-color)" fill-opacity="${strong ? 1 : 0.55}"></rect>`;
      } else {
        mark = `<rect x="${x}" y="${top + plotH - 2}" width="${barW}" height="2" rx="1" style="fill:var(--subtle-text)" fill-opacity="0.35"></rect>`;
      }
      return `<g><title>${esc(v.key)}${esc(note)}: ${esc(fmt(v.value))}</title>
        <rect x="${(v.day - 1) * slot}" y="${top}" width="${slot}" height="${plotH}" fill="transparent"></rect>${mark}</g>`;
    }).join('');
    let budgetLine = '';
    if (dailyBudget) {
      const y = top + plotH - (dailyBudget / scaleMax) * plotH;
      const label = t('Daily budget: {amount}', { amount: fmt(dailyBudget) });
      budgetLine = `<g><title>${esc(label)}</title>
        <line x1="0" y1="${y}" x2="${WIDTH}" y2="${y}" stroke-width="1" stroke-dasharray="4 3" style="stroke:var(--muted-text)"></line>
        <text x="${WIDTH}" y="${y - 3}" font-size="10" text-anchor="end" style="fill:var(--muted-text)">${esc(label)}</text></g>`;
    }
    const ticks = [1, 5, 10, 15, 20, 25, days].filter((d, i, a) => a.indexOf(d) === i && !(d !== days && days - d < 3)).map(d =>
      `<text x="${(d - 0.5) * slot}" y="${top + plotH + 13}" font-size="10" text-anchor="middle" style="fill:var(--muted-text)">${d}</text>`).join('');
    const todayMark = todayDay ? `<path d="M ${(todayDay - 0.5) * slot - 3} ${top + plotH + 21} l 3 -4 l 3 4 z" style="fill:var(--primary-color)"><title>${esc(t('today'))}</title></path>` : '';
    const axis = `<line x1="0" y1="${top + plotH}" x2="${WIDTH}" y2="${top + plotH}" stroke-width="1" style="stroke:var(--border-color)"></line>`;
    const peak = `<text x="0" y="9" font-size="10" style="fill:var(--muted-text)">${esc(t('Highest day: {amount}', { amount: fmt(max) }))}</text>`;
    el.innerHTML = svg(top + plotH + 24, peak + bars + budgetLine + axis + ticks + todayMark, t('Spending by day')) +
      table(values.filter(v => v.value).map(v => [v.key, fmt(v.value)]), [t('Date'), t('Amount')]);
  }

  // Budget progress: the status is said in words and an icon, not by colour alone
  function budgets(card, list, items, fmt){
    if (!items || !items.length){ card.classList.add('hidden'); list.innerHTML = ''; return; }
    card.classList.remove('hidden');
    const colors = { ok: 'var(--primary-color)', near: '#d97706', over: '#dc2626' };
    list.innerHTML = items.map(b => {
      const name = b.category == null ? t('Overall') : b.category;
      const pct = Math.min(100, b.percent);
      let note;
      if (b.level === 'over') note = `<span class="text-red-600"><i class="fas fa-circle-exclamation" aria-hidden="true"></i> ${th('{amount} over budget', { amount: fmt(b.spent - b.amount) })}</span>`;
      else if (b.level === 'near') note = `<span class="text-amber-700"><i class="fas fa-triangle-exclamation" aria-hidden="true"></i> ${th('{amount} left', { amount: fmt(b.amount - b.spent) })}</span>`;
      else note = `<span class="text-gray-500">${th('{amount} left', { amount: fmt(b.amount - b.spent) })}</span>`;
      return `<div>
        <div class="flex justify-between text-sm"><span class="font-semibold">${esc(name)}</span><span class="tabular-nums text-gray-600">${th('{spent} of {budget}', { spent: fmt(b.spent), budget: fmt(b.amount) })}</span></div>
        <div class="w-full h-2 rounded mt-1 overflow-hidden" style="background: var(--hover-bg);" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${b.percent}" aria-label="${esc(name)}">
          <div class="h-2 rounded" style="width:${pct}%; background:${colors[b.level] || colors.ok};"></div>
        </div>
        <div class="text-xs mt-1 flex justify-between"><span>${note}</span><span class="tabular-nums text-gray-500">${b.percent}%</span></div>
      </div>`;
    }).join('');
  }

  // On phones the charts share one spot, chosen with tabs; wide screens show all three (lg:block)
  const TAB_KEY = 'expenses:chartTab';
  function setupTabs(){
    const tabs = Array.from(document.querySelectorAll('.chart-tab'));
    const panels = Array.from(document.querySelectorAll('.chart-panel'));
    if (!tabs.length) return;
    function show(name){
      tabs.forEach(tab => {
        const on = tab.dataset.chart === name;
        tab.setAttribute('aria-selected', on ? 'true' : 'false');
        tab.classList.toggle('btn-primary', on);
        tab.classList.toggle('btn-secondary', !on);
      });
      panels.forEach(panel => {
        const on = panel.dataset.chart === name;
        panel.classList.toggle('hidden', !on);
        panel.classList.toggle('lg:block', !on);
      });
      try { localStorage.setItem(TAB_KEY, name); } catch (e) { /* private mode: just not remembered */ }
    }
    let saved = null;
    try { saved = localStorage.getItem(TAB_KEY); } catch (e) { saved = null; }
    tabs.forEach(tab => tab.addEventListener('click', () => show(tab.dataset.chart)));
    show(tabs.some(tab => tab.dataset.chart === saved) ? saved : tabs[0].dataset.chart);
  }
  // The charts card folds away from its header; open unless this browser folded it last time
  const COLLAPSED_KEY = 'expenses:chartsCollapsed';
  function setupCollapse(){
    const toggle = document.getElementById('charts-toggle');
    const body = document.getElementById('charts-body');
    if (!toggle || !body) return;
    function set(open, remember){
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      body.classList.toggle('hidden', !open);
      if (remember) {
        try { if (open) localStorage.removeItem(COLLAPSED_KEY); else localStorage.setItem(COLLAPSED_KEY, '1'); } catch (e) { /* private mode: just not remembered */ }
      }
    }
    let collapsed = false;
    try { collapsed = localStorage.getItem(COLLAPSED_KEY) === '1'; } catch (e) { collapsed = false; }
    set(!collapsed, false);
    toggle.addEventListener('click', () => set(toggle.getAttribute('aria-expanded') !== 'true', true));
  }
  function setup(){ setupTabs(); setupCollapse(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup);
  else setup();

  window.HomeHubExpenseCharts = {
    render(state, fmt){
      const summary = state.summary || {};
      const card = document.getElementById('budgets-card');
      if (card) budgets(card, document.getElementById('budgets-list'), state.budgets, fmt);
      const cats = document.getElementById('chart-categories');
      if (cats) categoryChart(cats, summary.per_category, (state.settings || {}).categories, fmt);
      const people = document.getElementById('chart-people');
      if (people) peopleChart(people, summary.per_payer, summary.per_share, fmt);
      const days = document.getElementById('chart-days');
      if (days) dayChart(days, state.byDate || {}, state.year, state.month, state.budgets, fmt);
    },
  };
})();
