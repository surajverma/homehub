// Admin tools > API access: presets, the rules between the access table and the two options, and copying a new token
document.addEventListener('DOMContentLoaded', function(){
  const page = document.getElementById('apiAccessPage');
  if (!page) return;

  // The page answering a POST shows the new token once; a refresh must load the plain page instead
  if (page.dataset.afterPost && window.history && history.replaceState){
    history.replaceState(null, '', page.dataset.pageUrl);
  }

  // Copy buttons
  document.querySelectorAll('[data-copy-target]').forEach(function(btn){
    btn.addEventListener('click', async function(){
      const el = document.getElementById(btn.dataset.copyTarget);
      if (!el) return;
      const text = (el.value !== undefined && el.tagName === 'INPUT') ? el.value : el.textContent;
      let ok = false;
      try { await navigator.clipboard.writeText(text); ok = true; } catch(e) {
        // Plain http on a home network has no clipboard API: select the text instead
        if (el.select){ el.focus(); el.select(); }
        else { const range = document.createRange(); range.selectNodeContents(el); const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range); }
        try { ok = document.execCommand('copy'); } catch(e2) { ok = false; }
      }
      if (window.globalToast) globalToast(ok ? t('Copied') : t('Select the text and copy it.'), ok ? 'success' : 'info');
    });
  });

  const form = document.getElementById('createTokenForm');
  if (!form) return;
  const actAs = document.getElementById('actAs');
  const actAsAuto = document.getElementById('actAsAuto');
  const allowDelete = document.getElementById('allowDelete');
  const presetWrap = document.getElementById('presetButtons');
  let presets = {};
  try { presets = JSON.parse(presetWrap.dataset.presets || '{}'); } catch(e) { presets = {}; }
  const NEEDS_ACT_AS = ['presence', 'statuses'];

  function levelOf(tool){
    const checked = form.querySelector('input[name="perm_' + tool + '"]:checked');
    return checked ? checked.value : 'none';
  }
  function setLevel(tool, level){
    const radio = form.querySelector('input[name="perm_' + tool + '"][value="' + level + '"]');
    if (radio) radio.checked = true;
  }
  function tools(){
    return Array.from(form.querySelectorAll('.tool-row')).map(function(row){ return row.dataset.tool; });
  }

  // Allow deleting only covers reminders, so it needs Reminders with Read & write
  function syncDelete(){
    const possible = levelOf('reminders') === 'write';
    allowDelete.disabled = !possible;
    if (!possible) allowDelete.checked = false;
    allowDelete.closest('label').classList.toggle('opacity-60', !possible);
  }

  function highlight(key){
    presetWrap.querySelectorAll('.preset-chip').forEach(function(btn){
      const on = btn.dataset.preset === key;
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      btn.classList.toggle('btn-primary', on);
      btn.classList.toggle('btn-secondary', !on);
    });
  }

  presetWrap.querySelectorAll('.preset-chip').forEach(function(btn){
    btn.addEventListener('click', function(){
      const preset = presets[btn.dataset.preset];
      if (!preset) return;
      tools().forEach(function(tool){ setLevel(tool, preset.permissions[tool] || 'none'); });
      actAs.checked = !!preset.act_as;
      actAsAuto.classList.add('hidden');
      syncDelete();
      allowDelete.checked = !!preset.allow_delete && !allowDelete.disabled;
      highlight(btn.dataset.preset);
    });
  });

  form.addEventListener('change', function(ev){
    const target = ev.target;
    if (target.classList.contains('tool-level')){
      const tool = target.name.replace('perm_', '');
      // Setting who is home or a person's status is always for a family member
      if (NEEDS_ACT_AS.indexOf(tool) !== -1 && target.value === 'write' && !actAs.checked){
        actAs.checked = true;
        actAsAuto.classList.remove('hidden');
      }
      syncDelete();
      highlight(null);
    } else if (target === actAs){
      actAsAuto.classList.add('hidden');
      if (!actAs.checked){
        NEEDS_ACT_AS.forEach(function(tool){ if (levelOf(tool) === 'write') setLevel(tool, 'read'); });
        syncDelete();
      }
      highlight(null);
    } else if (target === allowDelete){
      highlight(null);
    }
  });

  syncDelete();
});
