// Initialize creator
document.getElementById('creator').value = localStorage.getItem('username') || '';
document.getElementById('settingsUser').value = localStorage.getItem('username') || '';
document.querySelectorAll('input[name="user"]').forEach(i=> i.value = localStorage.getItem('username') || '');

(function(){
    const adminName = window.HomeHubAdmin.names[0];
    const current = localStorage.getItem('username') || '';
    const wrap = document.getElementById('homepageToggleWrap');
    const btn = document.getElementById('homepageSaveBtn');
    const form = document.getElementById('homepageToggleForm');
    const allowed = current === adminName || current === 'Administrator' || current === 'admin';
    if (wrap) wrap.classList.toggle('hidden', !allowed);
    if (btn) btn.classList.toggle('hidden', !allowed);
    if (form) form.classList.toggle('hidden', !allowed);
})();

(function(){
    const recurringToggle = document.getElementById('isRecurringChore');
    const recurringControls = document.getElementById('recurringChoreControls');
    if (!recurringToggle || !recurringControls) return;
    const sync = ()=> recurringControls.classList.toggle('hidden', !recurringToggle.checked);
    recurringToggle.addEventListener('change', sync);
    sync();
})();

(function(){
    const form = document.getElementById('choreForm');
    const recurringToggle = document.getElementById('isRecurringChore');
    if (!form || !recurringToggle) return;
    form.addEventListener('submit', (e)=>{
        if (!recurringToggle.checked) return;
        const endInput = form.querySelector('input[name="rec_end_date"]');
        const endVal = endInput && endInput.value;
        if (!endVal) return;
        const today = new Date();
        today.setHours(0,0,0,0);
        const endDate = new Date(endVal + 'T00:00:00');
        if (endDate < today) {
            e.preventDefault();
            if (window.globalToast) {
                globalToast('Recurring chore ends in the past. Choose a future end date.', 'error');
            } else {
                alert('Recurring chore ends in the past. Choose a future end date.');
            }
        }
    });
})();

// Scoped Tags for chores
const T = Tags.scoped('chores');

// Tag input handling for create form (reusable)
(function(){
    FormTags.initTagInputForm(T, {
        formId: 'choreForm',
        wrapId: 'tagInputWrap',
        inputId: 'tagInput',
        hiddenId: 'tagsField',
        libraryId: 'tagLibrary'
    });
})();

// Build filter chips from all known tags
(function(){
    const filterHost = document.getElementById('tagFilters');
    const clearBtn = document.getElementById('clearTagFilters');
    const selected = new Set();
    function collectTags(){
        const set = new Set();
        document.querySelectorAll('#choreList li').forEach(li=>{
            try{ (JSON.parse(li.dataset.tags||'[]')||[]).forEach(t=> set.add(t)); }catch(e){}
        });
        T.getAllKnownTags().forEach(t=> set.add(t));
        return [...set].sort((a,b)=> a.localeCompare(b));
    }
    function apply(){
        const tags = [...selected];
        document.querySelectorAll('#choreList li').forEach(li=>{
            if(!tags.length){ li.classList.remove('hidden'); return; }
            let matched=false; try{ const tg=JSON.parse(li.dataset.tags||'[]')||[]; matched = tg.some(t=>tags.includes(t)); }catch(e){}
            li.classList.toggle('hidden', !matched);
        });
    }
    function render(){
        const all = collectTags();
        filterHost.innerHTML='';
        all.forEach(t=>{
            const pill = T.makePill(t, ()=>{ if(selected.has(t)) selected.delete(t); else selected.add(t); render(); apply(); }, selected.has(t));
            filterHost.appendChild(pill);
        });
        clearBtn.style.display = selected.size > 0 ? 'inline-block' : 'none';
    }
    clearBtn.addEventListener('click', ()=>{ selected.clear(); render(); apply(); });
    T.onChange(()=> render());
    render();
})();

// Per-item tags UI and update via API
(function(){
    const list = document.getElementById('choreList');
    function renderItemTags(li){
        const id = li.dataset.id;
        let tags=[]; try{ tags = JSON.parse(li.dataset.tags||'[]')||[]; }catch(e){}
        const hosts = li.querySelectorAll('.item-tags');
        hosts.forEach(host=>{ host.innerHTML = ''; });
        hosts.forEach(host=>{ tags.forEach(t=> host.appendChild(T.makeFilledPill(t))); });
        T.recordTags(tags);
    }
    list.querySelectorAll('li').forEach(li=>{
        renderItemTags(li);
    });
})();

// Hide delete for non-owners; re-apply on user switch
function applyChoreUserContext(){
    const adminName=window.HomeHubAdmin.names[0];
    const current=localStorage.getItem('username')||'';
    const allowed = (creator) => (current===creator || current===adminName || current==='Administrator' || current==='admin');
    document.querySelectorAll('.delete-form').forEach(f=>{
        f.style.display = allowed(f.getAttribute('data-creator')) ? '' : 'none';
    });
    document.querySelectorAll('.edit-btn[data-creator]').forEach(a=>{
        const canEdit = allowed(a.getAttribute('data-creator'));
        a.style.display = canEdit ? '' : 'none';
        if (canEdit) {
            const baseHref = a.getAttribute('data-base-href') || a.getAttribute('href') || '';
            if (baseHref) {
                a.setAttribute('data-base-href', baseHref.split('?')[0]);
                a.setAttribute('href', `${baseHref.split('?')[0]}?user=${encodeURIComponent(current)}`);
            }
        }
    });
}
applyChoreUserContext();
document.addEventListener('user-switched', applyChoreUserContext);
