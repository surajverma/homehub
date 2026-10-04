document.addEventListener('DOMContentLoaded', function(){
  const adminName = window.HomeHubAdmin.names[0];
  const currentUser = () => localStorage.getItem('username') || '';
  const isAdmin = () => [adminName, 'Administrator', 'admin'].includes(currentUser());

  // HTML escaping to prevent XSS
  function escapeHtml(str){ return (str||'').replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }

  // Modal helpers
  const expenseModal = document.getElementById('expense-modal');
  function openModal(m){ m.classList.remove('hidden'); m.classList.add('flex'); setTimeout(()=> m.querySelector('.modal-content').classList.add('scale-100'), 10); }
  function closeModal(m){ const c=m.querySelector('.modal-content'); c.classList.remove('scale-100'); setTimeout(()=>{ m.classList.add('hidden'); m.classList.remove('flex'); }, 200); }

  // State
  let payload = {};
  try { payload = JSON.parse(document.getElementById('expensesData').textContent || '{}'); } catch(e){ payload = {}; }
  let year = payload.year || new Date().getFullYear();
  let month = payload.month || (new Date().getMonth()+1);
  let byDate = payload.by_date || {};
  let summary = payload.summary || { total_this_month: 0, per_payer: {}, per_share: {}, per_category: {}, top_category: null };
  let missingRecurring = payload.missing_recurring || {};
  let balances = payload.balances || { net: {}, settlements: [] };
  let selectedDate = null;
  // If server provided a selected date via query param embedding, try to pick it from location
  try {
    const url = new URL(window.location.href);
    const sel = url.searchParams.get('sel');
    if ((url.searchParams.get('open') || '') === 'recurring') {
      window.location.assign('/expenses/recurring');
      return;
    }
    if (sel) selectedDate = new Date(sel);
  } catch(err) { /* no-op */ }

  // Elements
  const grid = document.getElementById('calendar-grid');
  const calHeader = document.getElementById('calendar-header');
  const panelContent = document.getElementById('panel-content');
  const monthlyList = document.getElementById('monthly-entries-list');
  const bulkForm = document.getElementById('bulk-delete-form');
  const bulkBtn = document.getElementById('bulk-delete-btn');
  const selectAllLabel = document.getElementById('select-all-label');
  const monthlyTotalAmount = document.getElementById('monthly-total-amount');

  // Settings bootstrap
  let settings = (payload.settings||{currency:'₹', categories:[]});
  function fmt(amount){ return (settings.currency||'₹') + (Number(amount)||0).toFixed(settings.fraction_precision ?? 2); }

  function updateSummaryCards(){
    document.getElementById('total-this-month').textContent = fmt(summary.total_this_month||0);
    // my spending
    const me = currentUser();
    const p = summary.per_payer || {}; const mine = p[me] || 0;
    document.getElementById('my-spending').textContent = fmt(mine);
    // What I actually bear once shared expenses are split
    document.getElementById('my-share').textContent = `My share after splits: ${fmt((summary.per_share || {})[me] || 0)}`;
    document.getElementById('top-category').textContent = summary.top_category || '-';
    monthlyTotalAmount.textContent = fmt(summary.total_this_month||0);
  }

  function monthDays(y, m){ return new Date(y, m, 0).getDate(); }
  function firstWeekday(y, m){ return new Date(y, m-1, 1).getDay(); }
  function pad(n){ return String(n).padStart(2, '0'); }
  function isoLocalFromParts(y, m, d){ return `${y}-${pad(m)}-${pad(d)}`; }
  function isoLocalFromDate(d){ return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`; }
  function dateFromYmd(ymd){ const [y,m,d]=ymd.split('-').map(Number); return new Date(y, m-1, d); }

  function renderCalendar(){
    grid.innerHTML = '';
    const d = new Date(year, month-1, 1);
    calHeader.textContent = d.toLocaleString('default', { month: 'long' }) + ' ' + year;
    const pad = firstWeekday(year, month);
    for(let i=0;i<pad;i++){ grid.insertAdjacentHTML('beforeend', '<div></div>'); }
    const days = monthDays(year, month);
    const todayIso = isoLocalFromDate(new Date());
    for(let day=1; day<=days; day++){
      const cur = new Date(year, month-1, day);
      const ds = isoLocalFromDate(cur);
      const data = byDate[ds];
      const isToday = (ds===todayIso);
      const isSelected = selectedDate && (isoLocalFromDate(selectedDate)===ds);
      let cls = 'calendar-cell p-2 border rounded-md cursor-pointer flex flex-col text-left';
      if (isSelected) cls += ' bg-blue-500 text-white';
      else if (isToday) cls += ' bg-green-500';
      grid.insertAdjacentHTML('beforeend', `
        <button class="${cls}" data-date="${ds}">
          <div class="font-semibold">${day}</div>
          ${data && data.total ? `<div class="text-xs mt-1 ${isSelected? 'text-white' : 'text-gray-600'}">${fmt(data.total)}</div>` : ''}
        </button>
      `);
    }
  }

  function renderMonthlySidebar(){
    // Collect all entries from every date, sorted by date ascending
    const allEntries = [];
    Object.keys(byDate).sort().forEach(ds=>{
      if (!/^\d{4}-\d{2}-\d{2}$/.test(ds)) return;
      (byDate[ds]?.entries||[]).filter(e=>e&&e.id&&!e.is_settlement).forEach(e=>allEntries.push({...e, date:ds, counted: e.skipped ? 0 : e.amount}));
    });
    if(allEntries.length===0){
      monthlyList.innerHTML = '<div class="text-gray-500 p-2 text-sm">No entries this month.</div>';
      bulkBtn.style.display = 'none';
      if (selectAllLabel) selectAllLabel.style.display = 'none';
      return;
    }
    if (selectAllLabel) selectAllLabel.style.display = '';

    // Group by recurring_id (recurring) or by title+category+payer (manual)
    const groupMap = new Map();
    allEntries.forEach(e=>{
      const key = e.recurring_id != null ? `r:${e.recurring_id}` : `m:${e.title}:${e.category||''}:${e.payer||''}`;
      if (!groupMap.has(key)) groupMap.set(key, {title:e.title, category:e.category, payer:e.payer, isRecurring:e.recurring_id!=null, entries:[]});
      groupMap.get(key).entries.push(e);
    });

    // Sort: recurring first, then alphabetically
    const groups = [...groupMap.values()].sort((a,b)=>{
      if (a.isRecurring !== b.isRecurring) return a.isRecurring ? -1 : 1;
      return a.title.localeCompare(b.title);
    });

    const fmtShort = ds=>{ const d=dateFromYmd(ds); return `${d.getDate()}/${d.getMonth()+1}`; };
    const nextIsoDay = ds=>{
      const d = dateFromYmd(ds);
      d.setDate(d.getDate() + 1);
      return isoLocalFromDate(d);
    };

    monthlyList.innerHTML = groups.map(group=>{
      // Detect sequential runs: break when signature changes or date continuity breaks.
      const runs = [];
      let cur = null;
      group.entries.forEach(e=>{
        const sig = `${e.quantity}:${e.unit_price}:${e.skipped?1:0}`;
        const contiguous = !!cur && nextIsoDay(cur.endDate) === e.date;
        if (!cur || cur.sig !== sig || !contiguous) {
          cur = {sig, qty:e.quantity, unitPrice:e.unit_price, skipped:!!e.skipped, entries:[], startDate:e.date, endDate:e.date, subtotal:0};
          runs.push(cur);
        }
        cur.entries.push(e);
        cur.endDate = e.date;
        cur.subtotal += e.counted;
      });

      const groupTotal = group.entries.reduce((s,e)=>s+e.counted, 0);
      const badge = group.isRecurring
        ? `<span class="inline-block text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 leading-none">recurring</span>`
        : `<span class="inline-block text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 leading-none">manual</span>`;

      const runsHtml = runs.map(run=>{
        const count = run.entries.length;
        const dateRange = run.startDate===run.endDate ? fmtShort(run.startDate) : `${fmtShort(run.startDate)} – ${fmtShort(run.endDate)}`;
        const runDesc = run.skipped
          ? `skipped ${count} day${count>1?'s':''}`
          : (run.qty!=null && run.unitPrice!=null)
            ? `${run.qty} × ${count} day${count>1?'s':''} @ ${fmt(run.unitPrice)}`
            : `${count} entr${count>1?'ies':'y'}`;
        // Hidden checkboxes carry entry IDs for bulk-delete; toggled by the visible run-check
        const hiddenEntries = run.entries.map(e=>
          `<input type="checkbox" class="entry-select" name="ids" value="${e.id}" hidden tabindex="-1" aria-hidden="true">`
        ).join('');
        return `
          <div class="run-row flex items-center gap-2 px-3 py-2 border-b last:border-b-0 hover:bg-gray-50">
            <input type="checkbox" class="run-check rounded shrink-0 cursor-pointer" title="Select all entries in this period">
            <div class="flex-1 min-w-0">
              <span class="text-sm font-medium">${dateRange}</span>
              <span class="text-xs text-gray-500 ml-1.5">${escapeHtml(runDesc)}</span>
            </div>
            <span class="text-sm font-semibold shrink-0 ${run.skipped ? 'text-gray-400 line-through' : ''}">${run.skipped ? fmt(run.entries.reduce((s,e)=>s+e.amount,0)) : fmt(run.subtotal)}</span>
            ${hiddenEntries}
          </div>`;
      }).join('');

      const payerLine = !group.isRecurring && group.payer ? `<span class="text-xs text-gray-400">by ${escapeHtml(group.payer)}</span>` : '';
      return `
        <div class="mb-3 last:mb-0 border rounded overflow-hidden">
          <div class="flex items-center justify-between gap-2 px-3 py-2 bg-gray-50 border-b">
            <div class="flex items-center gap-1.5 min-w-0 flex-wrap">
              <span class="font-semibold text-sm truncate">${escapeHtml(group.title)}</span>
              ${badge}${payerLine}
            </div>
            <span class="text-sm font-bold shrink-0 ml-1">${fmt(groupTotal)}</span>
          </div>
          ${runsHtml}
        </div>`;
    }).join('');

    // Wire each run-check to toggle its hidden entry-select boxes
    bulkForm.querySelectorAll('.run-check').forEach(runCb=>{
      runCb.addEventListener('change', ()=>{
        const row = runCb.closest('.run-row');
        row.querySelectorAll('.entry-select').forEach(cb=>{ cb.checked = runCb.checked; });
        // Sync select-all indeterminate state
        const allBoxes = [...bulkForm.querySelectorAll('input.entry-select')];
        const checkedCount = allBoxes.filter(b=>b.checked).length;
        const selAllCb = document.getElementById('select-all-month');
        if (selAllCb) {
          selAllCb.indeterminate = checkedCount>0 && checkedCount<allBoxes.length;
          selAllCb.checked = checkedCount>0 && checkedCount===allBoxes.length;
        }
        bulkBtn.style.display = checkedCount>0 ? '' : 'none';
      });
    });
    const selAll = document.getElementById('select-all-month');
    if (selAll){ selAll.checked = false; selAll.indeterminate = false; }
  }

  function renderSidePanel(){
    if(!selectedDate){
      panelContent.innerHTML = `<div class="text-center text-gray-500 p-8 h-full flex items-center justify-center">
        <div>
          <div class="text-5xl mb-2">🧾</div>
          <div class="font-medium">Pick a day on the calendar to view or add expenses.</div>
        </div>
      </div>`; return;
    }
  const ds = isoLocalFromDate(selectedDate);
    const dayData = byDate[ds] || { entries: [], total: 0 };
    const entriesHtml = (dayData.entries||[]).map(e=>{
      const allowed = (currentUser()===e.payer) || isAdmin();
      const badges = [
        e.skipped ? '<span class="text-xs px-1.5 py-0.5 rounded bg-gray-200 text-gray-600">skipped</span>' : '',
        e.is_settlement ? '<span class="text-xs px-1.5 py-0.5 rounded bg-green-100 text-green-700">settlement</span>' : '',
      ].join(' ');
      const splitLine = (!e.is_settlement && e.split_with && e.split_with.length)
        ? `<div class="text-xs text-gray-500">Split: ${e.split_with.map(n=>`${escapeHtml(n)} ${fmt((e.shares||{})[n])}`).join(', ')}</div>` : '';
      const skipForm = e.recurring_id != null ? `
            <form method="POST" action="/expenses/skip/${e.id}" class="inline skip-form">
              <input type="hidden" name="user" value="${escapeHtml(currentUser())}">
              <button type="submit" class="text-gray-700 hover:underline">${e.skipped ? 'Restore' : 'Skip day'}</button>
            </form>` : '';
      return `
      <div class="flex items-start justify-between p-2 border rounded mb-2 ${e.skipped ? 'bg-gray-50' : ''}">
        <div>
          <div class="font-medium ${e.skipped ? 'line-through text-gray-400' : ''}">${escapeHtml(e.title)}${e.quantity? ` (${e.quantity})` : ''}</div>
          <div class="text-xs text-gray-500">${escapeHtml(e.category || '')} ${e.payer? `• by ${escapeHtml(e.payer)}`:''} ${badges}</div>
          ${splitLine}
        </div>
        <div class="text-right">
          <div class="font-semibold ${e.skipped ? 'line-through text-gray-400' : ''}">${fmt(e.amount)}</div>
          <div class="text-xs mt-1 space-x-2 ${allowed? '' : 'hidden'}">
            ${e.skipped || e.is_settlement ? '' : `<button class="text-blue-600 hover:underline edit-expense" data-id="${e.id}" data-date="${ds}">Edit</button>`}
            ${skipForm}
            <form method="POST" action="/expenses/delete/${e.id}" class="inline delete-form" data-creator="${escapeHtml(e.payer||'')}" data-confirm="Delete this expense?">
              <input type="hidden" name="user" value="${escapeHtml(currentUser())}">
              <button type="submit" class="text-red-600 hover:underline">Delete</button>
            </form>
          </div>
        </div>
      </div>`;
    }).join('');
    // Recurring days that were deleted earlier can be added back
    const missingHtml = (missingRecurring[ds]||[]).map(m=>{
      const allowed = (currentUser()===m.creator) || isAdmin();
      return `
      <div class="flex items-center justify-between p-2 border border-dashed rounded mb-2 text-gray-500">
        <div class="text-sm">${escapeHtml(m.title)} <span class="text-xs">(removed)</span></div>
        <form method="POST" action="/expenses/recurring/${m.rule_id}/restore" class="inline restore-form ${allowed ? '' : 'hidden'}">
          <input type="hidden" name="user" value="${escapeHtml(currentUser())}">
          <input type="hidden" name="date" value="${ds}">
          <button type="submit" class="text-xs text-blue-600 hover:underline">Add back</button>
        </form>
      </div>`;
    }).join('');
    panelContent.innerHTML = `
      <div class="flex items-center justify-between mb-2">
        <div>
          <div class="text-sm text-gray-500">Expenses for</div>
          <div class="text-lg font-semibold">${selectedDate.toLocaleDateString()}</div>
        </div>
        <div class="text-lg font-semibold">${fmt(dayData.total||0)}</div>
      </div>
      <div>${entriesHtml || (missingHtml ? '' : '<div class=\'text-gray-500\'>No expenses yet.</div>')}${missingHtml}</div>
      <button id="add-expense-btn" class="mt-3 w-full bg-blue-600 text-white py-2 rounded hover:bg-blue-700"><i class="fa-solid fa-plus mr-1"></i> Add Expense</button>
    `;
  }

  function renderBalances(){
    const net = balances.net || {};
    const names = Object.keys(net);
    document.getElementById('balances-net').innerHTML = names.length ? names.map(n=>{
      const v = net[n];
      const cls = v > 0 ? 'text-green-700' : 'text-red-600';
      const label = v > 0 ? 'gets back' : 'owes';
      return `<div class="flex justify-between text-sm py-1 border-b last:border-b-0"><span>${escapeHtml(n)}</span><span class="${cls}">${label} ${fmt(Math.abs(v))}</span></div>`;
    }).join('') : '<div class="text-sm text-gray-500">Everyone is settled up.</div>';
    const settlements = balances.settlements || [];
    document.getElementById('balances-settlements').innerHTML = settlements.length ? settlements.map((t, i)=>{
      const allowed = isAdmin() || [t.from, t.to].includes(currentUser());
      return `<div class="flex items-center justify-between text-sm py-1 border-b last:border-b-0">
        <span><strong>${escapeHtml(t.from)}</strong> owes <strong>${escapeHtml(t.to)}</strong> ${fmt(t.amount)}</span>
        <button type="button" class="text-xs text-blue-600 hover:underline settle-btn ${allowed ? '' : 'hidden'}" data-idx="${i}">Settle up</button>
      </div>`;
    }).join('') : '<div class="text-sm text-gray-500">Nothing to settle.</div>';
    // Per person this month: what they paid and what their share comes to
    const paid = summary.per_payer || {}, share = summary.per_share || {};
    const people = [...new Set([...Object.keys(paid), ...Object.keys(share)])].filter(Boolean).sort();
    document.getElementById('month-shares').innerHTML = people.length ? people.map(n=>
      `<div class="flex justify-between text-sm py-1 border-b last:border-b-0"><span>${escapeHtml(n)}</span><span class="text-gray-600">paid ${fmt(paid[n]||0)} · share ${fmt(share[n]||0)}</span></div>`
    ).join('') : '<div class="text-sm text-gray-500">No spending this month.</div>';
  }
  document.getElementById('balances-settlements').addEventListener('click', (e)=>{
    const btn = e.target.closest('.settle-btn'); if (!btn) return;
    const t = (balances.settlements||[])[Number(btn.getAttribute('data-idx'))]; if (!t) return;
    const input = window.prompt(`Record a payment from ${t.from} to ${t.to}. Amount:`, String(t.amount));
    if (input === null) return;
    const amount = parseFloat(input);
    if (!(amount > 0)) return;
    const sel = selectedDate ? isoLocalFromDate(selectedDate) : '';
    const f = document.getElementById('settle-form');
    f.action = `/expenses/settle?y=${year}&m=${month}&sel=${encodeURIComponent(sel)}`;
    document.getElementById('settle-user').value = currentUser();
    document.getElementById('settle-from').value = t.from;
    document.getElementById('settle-to').value = t.to;
    document.getElementById('settle-amount').value = String(amount);
    f.submit();
  });

  // Payer dropdown and split checkboxes in the add/edit modal
  function setPayer(name){
    const sel = document.getElementById('expense-payer');
    if (sel.tagName !== 'SELECT'){ sel.value = name || ''; return; }
    if (name && ![...sel.options].some(o=>o.value===name)){
      // Keep older free-text payers selectable when editing
      sel.insertAdjacentHTML('beforeend', `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`);
    }
    if (name) sel.value = name;
  }
  const splitBox = document.querySelector('#expense-split .split-box');
  const split = splitBox ? window.ExpenseSplit.init(splitBox, {
    getTotal: ()=> document.getElementById('expense-amount').value,
    fmt,
    precision: ()=> settings.fraction_precision ?? 2,
  }) : null;
  function setSplit(members, mode, weights){ if (split) split.set(members, mode, weights); }
  // Whoever pays normally shares the expense too; untick them for "I paid for others"
  document.getElementById('expense-payer').addEventListener('change', (e)=>{ if (split) split.tick(e.target.value); });
  document.getElementById('expense-amount').addEventListener('input', ()=>{ if (split) split.refresh(); });

  async function fetchMonth(y, m){
    const res = await fetch(`/api/expenses/month?year=${y}&month=${m}`);
    const data = await res.json();
    year = data.year; month = data.month; byDate = data.by_date || {}; summary = data.summary || summary; settings = data.settings || settings;
    missingRecurring = data.missing_recurring || {}; balances = data.balances || balances;
    updateSummaryCards(); renderCalendar(); renderMonthlySidebar(); renderSidePanel(); renderBalances();
  }

  // Event listeners
  document.getElementById('prev-month').addEventListener('click', ()=>{
    const d = new Date(year, month-2, 1); fetchMonth(d.getFullYear(), d.getMonth()+1); selectedDate = null;
  });
  document.getElementById('next-month').addEventListener('click', ()=>{
    const d = new Date(year, month, 1); fetchMonth(d.getFullYear(), d.getMonth()+1); selectedDate = null;
  });
  document.getElementById('calendar-grid').addEventListener('click', (e)=>{
    const btn = e.target.closest('button[data-date]');
    if(!btn) return;
    const ymd = btn.getAttribute('data-date');
    selectedDate = dateFromYmd(ymd);
    renderCalendar();
    renderSidePanel();
  });

  // Add/Edit modal
  document.addEventListener('click', (e)=>{
    if (e.target && e.target.id === 'add-expense-btn'){
  const ds = selectedDate ? isoLocalFromDate(selectedDate) : isoLocalFromDate(new Date());
      document.getElementById('modal-title').textContent = 'Add Expense';
  const form = document.getElementById('expense-form');
  // preserve current view in action
  const sel = selectedDate ? isoLocalFromDate(selectedDate) : '';
  form.action = `/expenses?y=${year}&m=${month}&sel=${encodeURIComponent(sel)}`;
      document.getElementById('expense-user').value = currentUser();
      document.getElementById('expense-date').value = ds;
      document.getElementById('expense-title').value = '';
      document.getElementById('expense-category').value = '';
      document.getElementById('expense-quantity').value = '';
      document.getElementById('expense-unit-price').value = '';
      document.getElementById('expense-amount').value = '';
      setPayer(currentUser());
      setSplit(null);
      openModal(expenseModal);
    }
    if (e.target && e.target.classList.contains('edit-expense')){
      const id = e.target.getAttribute('data-id');
  const ds = e.target.getAttribute('data-date');
      // Find entry in byDate
      const entry = (byDate[ds]?.entries||[]).find(x=> String(x.id)===String(id));
      if(!entry) return;
      document.getElementById('modal-title').textContent = 'Edit Expense';
  const form = document.getElementById('expense-form');
  // preserve current view in action
  const sel = selectedDate ? isoLocalFromDate(selectedDate) : ds;
  form.action = `/expenses/edit/${id}?y=${year}&m=${month}&sel=${encodeURIComponent(sel)}`;
      document.getElementById('expense-user').value = currentUser();
      document.getElementById('expense-date').value = ds;
      document.getElementById('expense-title').value = entry.title || '';
      document.getElementById('expense-category').value = entry.category || '';
      document.getElementById('expense-quantity').value = (entry.quantity!=null? entry.quantity : '');
      document.getElementById('expense-unit-price').value = (entry.unit_price!=null? entry.unit_price : '');
      document.getElementById('expense-amount').value = entry.amount || '';
      setPayer(entry.payer || currentUser());
      setSplit(entry.split_with || [], entry.split_mode, entry.split_weights);
      openModal(expenseModal);
    }
  });
  document.getElementById('cancel-expense').addEventListener('click', ()=> closeModal(expenseModal));
  expenseModal.addEventListener('click', (e)=>{ if (e.target===expenseModal) closeModal(expenseModal); });

  // On load and on user switch, refresh UI bits that depend on user
  function applyUserVisibility(){
    // Hide delete buttons not allowed (handled inline per-entry), ensure hidden inputs reflect current user
    document.querySelectorAll('form.delete-form input[name="user"]').forEach(i=> i.value = currentUser());
    document.getElementById('bulk-user').value = currentUser();
  }
  document.addEventListener('user-switched', ()=>{ updateSummaryCards(); renderSidePanel(); renderBalances(); applyUserVisibility(); });

  // Initialize
  updateSummaryCards();
  renderCalendar();
  renderMonthlySidebar();
  renderSidePanel();
  renderBalances();
  applyUserVisibility();

  // Auto-total calculation when editing/adding
  function recalcTotal(){
    // A blank quantity means one unit, so a unit price alone still gives a total
    const q = parseFloat(document.getElementById('expense-quantity').value || '1');
    const up = parseFloat(document.getElementById('expense-unit-price').value || '0');
    if (!isNaN(q) && !isNaN(up) && (q>0 || up>0)){
      document.getElementById('expense-amount').value = (q*up).toFixed(settings.fraction_precision ?? 2);
      if (split) split.refresh();
    }
  }
  ['expense-quantity','expense-unit-price'].forEach(id=>{
    document.getElementById(id).addEventListener('input', recalcTotal);
  });

  // Pre-fill settings UI for modal category chips usability
  const modalChips = document.getElementById('modal-category-chips');
  if (modalChips){
    modalChips.innerHTML = (settings.categories||[]).map(c=> `<button type="button" class="px-2 py-1 text-xs rounded bg-gray-100 hover:bg-gray-200 add-cat" data-cat="${escapeHtml(c)}">${escapeHtml(c)}</button>`).join('');
    modalChips.addEventListener('click', (e)=>{
      const btn = e.target.closest('button.add-cat'); if(!btn) return;
      const cat = btn.getAttribute('data-cat') || '';
      const field = document.getElementById('expense-category');
      if (field) field.value = cat;
    });
  }

  // Bulk delete submit behavior
  bulkBtn.addEventListener('click', (e)=>{
    e.preventDefault();
    // Attach query params to preserve view
  const sel = selectedDate ? isoLocalFromDate(selectedDate) : '';
    bulkForm.action = `/expenses/bulk-delete?y=${year}&m=${month}&sel=${encodeURIComponent(sel)}`;
    bulkForm.submit();
  });

  // Select-all behavior for monthly list
  const selAll = document.getElementById('select-all-month');
  if (selAll){
    selAll.addEventListener('change', ()=>{
      const boxes = bulkForm.querySelectorAll('input.entry-select');
      boxes.forEach(b=> b.checked = selAll.checked);
      // Also sync visible run-check boxes
      bulkForm.querySelectorAll('.run-check').forEach(cb=> { cb.checked = selAll.checked; });
      const any = [...boxes].some(x=> x.checked);
      bulkBtn.style.display = any ? '' : 'none';
    });
    // Keep indeterminate state in sync when run-check changes bubble up
    bulkForm.addEventListener('change', (e)=>{
      if (!e.target.classList.contains('run-check')) return;
      const boxes = [...bulkForm.querySelectorAll('input.entry-select')];
      const checked = boxes.filter(b=> b.checked).length;
      selAll.indeterminate = checked>0 && checked<boxes.length;
      selAll.checked = checked===boxes.length && boxes.length>0;
    });
  }

  // Preserve view state on recurring delete/edit and settings save by updating form actions at click time
  document.body.addEventListener('submit', (e)=>{
    const f = e.target;
    if (!(f instanceof HTMLFormElement)) return;
    const needsPreserve = f.action.includes('/expenses/recurring/delete/') || f.action.includes('/expenses/recurring/edit/') || f.action.includes('/expenses/delete/') || f.action.includes('/expenses/skip/') || f.action.includes('/restore') || f.action.endsWith('/expenses/settings');
    if (needsPreserve){
  const sel = selectedDate ? isoLocalFromDate(selectedDate) : '';
      try {
        const url = new URL(f.action, window.location.origin);
        url.searchParams.set('y', String(year));
        url.searchParams.set('m', String(month));
        if (sel) url.searchParams.set('sel', sel); else url.searchParams.delete('sel');
        f.action = url.pathname + url.search;
      } catch(err) {
        // Fallback string concat
        f.action = f.action + `?y=${year}&m=${month}&sel=${encodeURIComponent(sel)}`;
      }
    }
  });
});
