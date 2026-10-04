document.addEventListener('DOMContentLoaded', function(){
  const adminName = window.HomeHubAdmin.names[0];
  const currentUser = () => localStorage.getItem('username') || '';

  // HTML escaping to prevent XSS
  function escapeHtml(str){ return (str||'').replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }

  // Tab switching
  document.querySelectorAll('.tab-button').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.getAttribute('data-tab');
      // Remove active from all buttons and contents
      document.querySelectorAll('.tab-button').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      // Add active to clicked button and corresponding content
      btn.classList.add('active');
      const content = document.getElementById(tab);
      if (content) content.classList.add('active');
    });
  });

  // Load settings and populate UI
  let settings = {currency: '₹', categories: [], fraction_factor: 100, fraction_precision: 2};
  const serverSettings = JSON.parse(document.getElementById('expenseSettingsData').textContent);
  if (serverSettings && typeof serverSettings === 'object') {
    settings = { ...settings, ...serverSettings };
  }

  // Split pickers on the add and edit rule forms; shares preview uses unit price × quantity
  document.querySelectorAll('.split-box').forEach(box => {
    const form = box.closest('form');
    const field = name => form.querySelector(`input[name="${name}"]`);
    const picker = window.ExpenseSplit.init(box, {
      getTotal: () => (parseFloat(field('unit_price').value) || 0) * (parseFloat(field('default_quantity').value) || 1),
      fmt: n => (settings.currency || '₹') + (Number(n) || 0).toFixed(settings.fraction_precision ?? 2),
      precision: () => settings.fraction_precision ?? 2,
    });
    ['unit_price', 'default_quantity'].forEach(name => field(name).addEventListener('input', picker.refresh));
  });

  // Pre-fill settings form
  document.getElementById('currency-input').value = settings.currency || '₹';
  const fractionFactorInput = document.getElementById('fraction-factor-input');
  if (fractionFactorInput) fractionFactorInput.value = settings.fraction_factor || 100;
  document.getElementById('categories-input').value = (settings.categories||[]).join(', ');

  // Render category chips
  const chips = document.getElementById('category-chips');
  chips.innerHTML = (settings.categories||[]).map(c=> `<button type="button" class="px-2 py-1 text-xs rounded bg-gray-100 hover:bg-gray-200 add-cat" data-cat="${escapeHtml(c)}">${escapeHtml(c)}</button>`).join('');
  chips.addEventListener('click', (e)=>{
    const btn = e.target.closest('button.add-cat'); if(!btn) return;
    const cat = btn.getAttribute('data-cat') || '';
    document.getElementById('categories-input').value = cat;
  });

  // Recurring category chips for add form
  const recChips = document.getElementById('recurring-category-chips');
  if (recChips){
    recChips.innerHTML = (settings.categories||[]).map(c=> `<button type="button" class="px-2 py-1 text-xs rounded bg-gray-100 hover:bg-gray-200 add-cat" data-cat="${escapeHtml(c)}">${escapeHtml(c)}</button>`).join('');
    recChips.addEventListener('click', (e)=>{
      const btn = e.target.closest('button.add-cat'); if(!btn) return;
      const cat = btn.getAttribute('data-cat') || '';
      const field = document.querySelector('#recurring-form input[name="category"]');
      if (field) field.value = cat;
    });
  }

  // Edit category chips
  document.querySelectorAll('.edit-category-chips').forEach(container => {
    container.innerHTML = (settings.categories||[]).map(c=> `<button type="button" class="px-2 py-1 text-xs rounded bg-gray-100 hover:bg-gray-200 add-cat" data-cat="${escapeHtml(c)}">${escapeHtml(c)}</button>`).join('');
    container.addEventListener('click', (e)=>{
      const btn = e.target.closest('button.add-cat'); if(!btn) return;
      const cat = btn.getAttribute('data-cat') || '';
      const field = container.closest('form')?.querySelector('input[name="category"]');
      if (field) field.value = cat;
    });
  });

  // Set user on forms
  function applyUserVisibility(){
    document.querySelectorAll('form.delete-form input[name="user"]').forEach(i=> i.value = currentUser());
    document.querySelectorAll('form.recurring-edit-form input[name="user"]').forEach(i=> i.value = currentUser());
    document.getElementById('settings-user').value = currentUser();
    document.getElementById('recCreator').value = currentUser();
  }

  // Recurring edit safety: default effective date and destructive confirmations
  document.querySelectorAll('form.recurring-edit-form').forEach(form => {
    const strategy = form.querySelector('select[name="edit_strategy"]');
    const effective = form.querySelector('input[name="effective_from"]');
    const startDate = form.querySelector('input[name="start_date"]');
    const endDate = form.querySelector('input[name="end_date"]');
    const effectiveGroup = form.querySelector('.effective-from-group');

    const syncEffectiveBounds = () => {
      if (!effective) return;
      const minDate = startDate && startDate.value ? startDate.value : '';
      const maxDate = endDate && endDate.value ? endDate.value : '';
      if (minDate) effective.min = minDate;
      else effective.removeAttribute('min');
      if (maxDate) effective.max = maxDate;
      else effective.removeAttribute('max');

      if (effective.value && minDate && effective.value < minDate) effective.value = minDate;
      if (effective.value && maxDate && effective.value > maxDate) effective.value = maxDate;
    };

    const ensureEffectiveDate = () => {
      if (!effective) return;
      if (!effective.value) {
        if (startDate && startDate.value) effective.value = startDate.value;
        else {
          const today = new Date();
          effective.value = today.getFullYear() + '-' + String(today.getMonth()+1).padStart(2, '0') + '-' + String(today.getDate()).padStart(2, '0');
        }
      }
      syncEffectiveBounds();
    };

    const syncEffectiveVisibility = () => {
      if (!strategy || !effective || !effectiveGroup) return;
      const isDestructive = strategy.value === 'rewrite_all';
      effectiveGroup.style.display = isDestructive ? 'none' : '';
      effective.disabled = isDestructive;
      if (!isDestructive) ensureEffectiveDate();
    };

    if (strategy) {
      strategy.addEventListener('change', () => {
        syncEffectiveVisibility();
        syncEffectiveBounds();
      });
    }
    if (startDate) startDate.addEventListener('change', syncEffectiveBounds);
    if (endDate) endDate.addEventListener('change', syncEffectiveBounds);

    syncEffectiveBounds();
    syncEffectiveVisibility();

    form.addEventListener('submit', (evt) => {
      if (!effective || !effective.disabled) ensureEffectiveDate();
      const selected = strategy ? strategy.value : 'apply_from';
      if (selected === 'rewrite_all') {
        const ok = window.confirm('Rewrite entire history can delete or overwrite older generated entries. Continue?');
        if (!ok) {
          evt.preventDefault();
          return;
        }
      }
      if (selected === 'split_rule') {
        const ok = window.confirm('This will close the current rule and create a new one from Effective From date. Continue?');
        if (!ok) {
          evt.preventDefault();
        }
      }
    });
  });

  // Initialize
  applyUserVisibility();
  document.addEventListener('user-switched', ()=>{ applyUserVisibility(); });
});
