// Chore suggestions (mirror shopping): use persisted history + hide list
(function(){
    const box = document.getElementById('choreSuggestions');
    const input = document.getElementById('choreInput');
    const HIDE_KEY = 'chores:hidden_suggestions';
    const BASE_KEY = 'chores:history_suggestions';
    function getHidden(){ try{return new Set(JSON.parse(localStorage.getItem(HIDE_KEY)||'[]'));}catch(e){return new Set();} }
    function setHidden(s){ try{ localStorage.setItem(HIDE_KEY, JSON.stringify([...s])); }catch(e){} }
    function getBase(){ try{return JSON.parse(localStorage.getItem(BASE_KEY)||'[]')||[];}catch(e){return [];} }
    function setBase(arr){ try{ localStorage.setItem(BASE_KEY, JSON.stringify(arr||[])); }catch(e){} }
    // Seed base with current chores once (if empty)
    (function seed(){ const base=getBase(); if(!base.length){ const seeds=[...document.querySelectorAll('#choreList li span.flex-1')].map(n=> n.textContent.trim()).filter(Boolean); if(seeds.length) setBase([...new Set(seeds)]);} })();
    function render(){
        const q = (input?.value||'').trim().toLowerCase();
        const hidden = getHidden();
        const base = getBase();
        const existing = new Set([...document.querySelectorAll('#choreList li span.flex-1')]
            .map(n=> n.textContent.trim().toLowerCase()));
        const list = base
            .filter(s=> !hidden.has(s)
                        && !existing.has(s.toLowerCase())
                        && (!q || s.toLowerCase().includes(q)))
            .slice(0,50);
        if(!list.length){ box.classList.add('hidden'); box.innerHTML=''; return; }
        box.innerHTML='';
        list.forEach(s=>{
            const row = document.createElement('div');
            row.className = 'flex items-center justify-between px-3 py-2 text-sm hover:bg-gray-100 cursor-pointer select-none';
            const text = document.createElement('div'); text.textContent = s; text.className='truncate pr-2 flex-1';
            const del = document.createElement('button'); del.type='button'; del.title='Hide'; del.setAttribute('aria-label','Hide suggestion '+s); del.className='text-gray-400 hover:text-red-600 w-6 h-6 inline-flex items-center justify-center'; del.innerHTML='<i class="fa-solid fa-xmark" aria-hidden="true"></i>';
            del.addEventListener('click', (e)=>{ e.stopPropagation(); const h=getHidden(); h.add(s); setHidden(h); render(); });
            row.appendChild(text); const actions=document.createElement('div'); actions.className='flex items-center gap-2'; actions.appendChild(del); row.appendChild(actions);
            row.addEventListener('click', ()=>{ input.value = s; box.classList.add('hidden'); input.focus(); });
            box.appendChild(row);
        });
        box.classList.remove('hidden');
    }
    if(input){ input.addEventListener('input', render); input.addEventListener('focus', render); }
    document.addEventListener('click', (e)=>{ if(!box.contains(e.target) && e.target!==input){ box.classList.add('hidden'); }});
    // After successful form submit, add chore text to history base
    const form = document.getElementById('choreForm');
    form?.addEventListener('submit', ()=>{
        const val = (input?.value||'').trim();
        if(val){ const base = new Set(getBase()); base.add(val); setBase([...base]); }
    });
})();
