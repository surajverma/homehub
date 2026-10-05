// Split picker behaviour: opts = { getTotal: () => number|string, fmt: n => string, precision: () => number }
window.ExpenseSplit = {
  init(box, opts){
    const modeSel = box.querySelector('.split-mode');
    const hint = box.querySelector('.split-hint');
    const rowsBox = box.querySelector('.split-rows');
    const rowOf = el => ({ el, cb: el.querySelector('.split-member'), w: el.querySelector('.split-weight'), out: el.querySelector('.split-share') });
    let rows = [...box.querySelectorAll('.split-row')].map(rowOf);
    const blankRow = rows.length ? rows[0].el.cloneNode(true) : null;
    const num = n => String(Number(n.toFixed(2)));
    const prec = () => (opts.precision ? opts.precision() : 2);
    const total = () => { const t = parseFloat(opts.getTotal ? opts.getTotal() : ''); return isNaN(t) ? null : t; };
    const ticked = () => rows.filter(r=>r.cb.checked);

    // Fill the ticked rows with equal values that add up to `sum`
    function spread(sum, digits){
      const rs = ticked(); if (!rs.length) return;
      const f = Math.pow(10, digits), base = Math.floor(sum * f / rs.length) / f;
      rs.forEach((r, i)=>{ r.w.value = String(Number((i === rs.length-1 ? sum - base*(rs.length-1) : base).toFixed(digits))); });
    }
    function fillDefaults(){
      const mode = modeSel.value;
      // Values from the previous mode mean something else, so start clean
      rows.forEach(r=>{ r.w.value = ''; });
      if (mode === 'shares') ticked().forEach(r=>{ r.w.value = '1'; });
      if (mode === 'percent') spread(100, 2);
      if (mode === 'amount' && total() != null) spread(total(), prec());
    }
    function refresh(){
      const mode = modeSel.value, uneven = mode !== 'equal', t = total();
      // Names alone fit two to a row; names with a value and a share need the full width
      rowsBox.classList.toggle('sm:grid-cols-2', !uneven);
      rows.forEach(r=>{ r.w.classList.toggle('hidden', !uneven); r.w.disabled = !uneven || !r.cb.checked; r.out.textContent = ''; });
      const rs = ticked();
      const weights = rs.map(r=> uneven ? (parseFloat(r.w.value) || 0) : 1);
      const sum = weights.reduce((a, b)=>a+b, 0);
      if (mode !== 'amount' && t != null && t > 0 && sum > 0) rs.forEach((r, i)=>{ r.out.textContent = opts.fmt(t * weights[i] / sum); });
      let msg = '', bad = false;
      if (!rs.length) msg = "Nobody ticked: a personal expense that isn't shared.";
      else if (uneven && sum <= 0){ bad = true; msg = 'Enter a value for at least one person.'; }
      else if (mode === 'shares') msg = 'Each person pays in proportion to their shares.';
      else if (mode === 'percent'){
        const left = 100 - sum; bad = Math.abs(left) > 0.01;
        msg = bad ? `Percentages add up to ${num(sum)}%: ${left > 0 ? num(left) + '% left to assign' : num(-left) + '% too much'}.` : 'Percentages add up to 100%.';
      } else if (mode === 'amount'){
        if (t == null){ bad = true; msg = 'Enter the total amount first.'; }
        else {
          const left = t - sum; bad = Math.abs(left) > 0.5 / Math.pow(10, prec());
          msg = bad ? `Amounts add up to ${opts.fmt(sum)} of ${opts.fmt(t)}: ${left > 0 ? opts.fmt(left) + ' left to assign' : opts.fmt(-left) + ' too much'}.` : `Amounts add up to ${opts.fmt(t)}.`;
        }
      }
      hint.textContent = msg;
      hint.classList.toggle('text-red-600', bad);
      hint.classList.toggle('text-gray-500', !bad);
      return !bad;
    }

    modeSel.addEventListener('change', ()=>{ fillDefaults(); refresh(); });
    function wire(r){
      r.cb.addEventListener('change', ()=>{
        if (!r.cb.checked) r.w.value = '';
        else if (modeSel.value === 'shares' && !r.w.value) r.w.value = '1';
        refresh();
      });
      r.w.addEventListener('input', refresh);
    }
    rows.forEach(wire);
    // A row for someone who is in a stored split but no longer a configured member
    function addRow(name){
      const el = blankRow.cloneNode(true);
      el.dataset.extra = '1';
      const r = rowOf(el);
      r.cb.value = name;
      r.w.name = `split_weight__${name}`;
      r.w.setAttribute('aria-label', `${name} split value`);
      el.querySelector('.block.truncate').textContent = name;
      rowsBox.appendChild(el);
      wire(r);
      rows.push(r);
    }
    const form = box.closest('form');
    if (form) form.addEventListener('submit', (evt)=>{
      if (!refresh()){ evt.preventDefault(); evt.stopImmediatePropagation(); hint.scrollIntoView({block: 'nearest'}); }
    });
    refresh();

    return {
      refresh,
      // members: array of names, or null to tick everyone
      set(members, mode, weights){
        modeSel.value = mode || 'equal';
        // Drop extra rows from a previous entry, then add any stored participant without a row
        rows = rows.filter(r=>{ if (r.el.dataset.extra){ r.el.remove(); return false; } return true; });
        if (members && blankRow) members.forEach(name=>{ if (!rows.some(r=>r.cb.value === name)) addRow(name); });
        rows.forEach(r=>{
          r.cb.checked = members === null ? true : members.includes(r.cb.value);
          r.w.value = (weights && weights[r.cb.value] != null) ? weights[r.cb.value] : '';
        });
        refresh();
      },
      tick(name){
        const r = rows.find(x=>x.cb.value === name);
        if (r && !r.cb.checked){ r.cb.checked = true; r.cb.dispatchEvent(new Event('change')); }
      },
    };
  },
};
