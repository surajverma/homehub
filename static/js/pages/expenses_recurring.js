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

  // A rule needs a unit price and a quantity above zero; the server checks the same
  function amountProblem(form){
    const price = parseFloat(form.querySelector('input[name="unit_price"]').value);
    const quantity = parseFloat(form.querySelector('input[name="default_quantity"]').value);
    if (!(price > 0)) return t('Unit price must be greater than zero.');
    if (!(quantity > 0)) return t('Quantity must be greater than zero.');
    return '';
  }
  function showFormError(form, message){
    let note = form.querySelector('.form-error');
    if (!note){
      note = document.createElement('p');
      note.className = 'form-error text-sm text-red-600';
      note.setAttribute('role', 'alert');
      note.style.gridColumn = '1 / -1';
      form.querySelector('button[type="submit"]').parentElement.before(note);
    }
    note.textContent = message;
    note.classList.toggle('hidden', !message);
    return !!message;
  }
  const addRuleForm = document.getElementById('recurring-form');
  if (addRuleForm){
    addRuleForm.addEventListener('input', ()=> showFormError(addRuleForm, ''));
    addRuleForm.addEventListener('submit', (evt)=>{ if (showFormError(addRuleForm, amountProblem(addRuleForm))) evt.preventDefault(); });
  }

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
        // Today, which syncEffectiveBounds then keeps inside the rule's start/end
        const today = new Date();
        effective.value = today.getFullYear() + '-' + String(today.getMonth()+1).padStart(2, '0') + '-' + String(today.getDate()).padStart(2, '0');
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

    // Every save is confirmed first, with what the server says it would do to entries that already exist
    let confirmed = false;
    form.addEventListener('submit', async (evt) => {
      if (confirmed) { confirmed = false; return; }
      evt.preventDefault();
      if (showFormError(form, amountProblem(form))) return;
      if (!effective || !effective.disabled) ensureEffectiveDate();
      form.querySelector('input[name="user"]').value = currentUser();
      let preview = null;
      try {
        const resp = await fetch(form.action + '/preview', { method: 'POST', body: new FormData(form) });
        preview = await resp.json();
      } catch (e) { preview = null; }
      // A form the server would reject is submitted as it is, so its own error message shows
      if (!preview || preview.ok) {
        const selected = preview ? preview.strategy : (strategy ? strategy.value : 'apply_from');
        const destructive = selected === 'rewrite_all';
        const touchesPast = !preview || preview.past > 0 || preview.hand_edited > 0;
        const title = destructive ? t('Rewrite the whole history of this rule?')
          : (selected === 'split_rule' ? t('Split this rule?')
            : (touchesPast ? t('Change entries that already exist?') : t('Save changes to this rule?')));
        const ok = await confirmDialog({
          title: title,
          message: preview ? preview.summary : t('This can change entries that already exist.'),
          details: preview ? preview.details : [],
          confirmText: t('Save changes'),
          danger: destructive || !preview || preview.removed > 0 || preview.hand_edited > 0,
        });
        if (!ok) return;
      }
      confirmed = true;
      form.requestSubmit();
    });
  });

  // Deleting a rule: say how many generated entries go with it (or stay) before asking
  document.querySelectorAll('form.recurring-delete-form').forEach(form => {
    let confirmed = false;
    form.addEventListener('submit', async (evt) => {
      if (confirmed) { confirmed = false; return; }
      evt.preventDefault();
      form.querySelector('input[name="user"]').value = currentUser();
      let preview = null;
      try {
        const resp = await fetch(form.action + '/preview', { method: 'POST', body: new FormData(form) });
        preview = await resp.json();
      } catch (e) { preview = null; }
      const ok = await confirmDialog({
        title: t('Delete this recurring rule?'),
        message: preview && preview.ok ? preview.summary : '',
        details: preview && preview.ok ? preview.details : [],
        confirmText: t('Delete Rule'),
        danger: true,
      });
      if (!ok) return;
      confirmed = true;
      form.requestSubmit();
    });
  });

  // Initialize
  applyUserVisibility();
  document.addEventListener('user-switched', ()=>{ applyUserVisibility(); });
});
