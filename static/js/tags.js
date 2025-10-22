(function(global){
  function makeStorageKeys(scope){
    const s = scope || 'global';
    return {
      COLORS: `${s}:homehub_tag_colors`,
      KNOWN: `${s}:homehub_known_tags`
    };
  }
  function getStore(key, fallback){
    try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; } catch(e){ return fallback; }
  }
  function setStore(key, value){
    try { localStorage.setItem(key, JSON.stringify(value)); } catch(e){}
  }
  function _randHue(){ return Math.floor(Math.random()*360); }
  function _colorFromHue(h){
    const sat = 65; // tighter range for visual consistency
    const light = 52; // balanced for dark/light text contrast
    return `hsl(${h} ${sat}% ${light}%)`;
  }
  function textColorFor(bg){
    // crude HSL parse to pick white/black for contrast
    if(/^hsl\(/i.test(bg)){
      try{
        const parts = bg.match(/hsl\(([^)]+)\)/i)[1].trim().split(/\s+/);
        const l = parseInt(parts[2]);
        return l < 60 ? '#fff' : '#111827';
      }catch(e){ return '#fff'; }
    }
    return '#fff';
  }
  function createScoped(scope){
    const KEYS = makeStorageKeys(scope);
    function emit(){
      try{ window.dispatchEvent(new CustomEvent('tags:changed', { detail: { scope, tags: getAllKnownTags() } })); }catch(e){}
    }
    function ensureColor(tag){
      const map = getStore(KEYS.COLORS, {});
      if(!map[tag]){
        // Pick a hue sufficiently distinct from existing hues in this scope
        const existingHues = Object.values(map)
          .map(c=>{ const m=/hsl\((\d+)\s/i.exec(c); return m?parseInt(m[1]):null; })
          .filter(v=> v!==null);
        const minDist = 28; // degrees separation
        let pick = _randHue();
        let tries = 0;
        function farEnough(h){ return existingHues.every(eh=> Math.min(Math.abs(eh-h), 360-Math.abs(eh-h)) >= minDist); }
        while(existingHues.length && !farEnough(pick) && tries < 32){ pick = _randHue(); tries++; }
        map[tag] = _colorFromHue(pick);
        setStore(KEYS.COLORS, map);
        recordTags([tag]);
      }
      return map[tag];
    }
    function colorFor(tag){
      const map = getStore(KEYS.COLORS, {});
      // Only return existing color; don't create new one
      return map[tag] || 'hsl(200 65% 52%)'; // default blue if no color exists
    }
    function recordTags(tags){
      const known = new Set(getStore(KEYS.KNOWN, []));
      (tags||[]).forEach(t=> known.add(t));
      setStore(KEYS.KNOWN, [...known]);
      emit();
    }
    function forgetTag(tag){
      const known = new Set(getStore(KEYS.KNOWN, []));
      if(known.delete(tag)) setStore(KEYS.KNOWN, [...known]);
      const map = getStore(KEYS.COLORS, {});
      if(tag in map){ delete map[tag]; setStore(KEYS.COLORS, map); }
      emit();
    }
    function getAllKnownTags(){
      return getStore(KEYS.KNOWN, []);
    }
    function makePill(tag, onClick, active){
      const bg = colorFor(tag);
      const fg = textColorFor(bg);
      const span = document.createElement('span');
      span.className = 'inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs shadow cursor-pointer';
      span.style.background = active ? bg : 'transparent';
      span.style.border = `1px solid ${bg}`;
      span.style.color = active ? fg : bg;
      span.textContent = tag;
      if(typeof onClick === 'function'){
        span.addEventListener('click', onClick);
      }
      return span;
    }
    function makeFilledPill(tag, onClick, active){
      const bg = colorFor(tag);
      const fg = textColorFor(bg);
      const span = document.createElement('span');
      span.className = 'inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs shadow cursor-pointer';
      span.style.background = bg;
      span.style.border = `1px solid ${bg}`;
      span.style.color = fg;
      if(active){ span.style.boxShadow = '0 0 0 2px rgba(0,0,0,0.08)'; }
      const label = document.createElement('span'); label.textContent = tag; span.appendChild(label);
      if(typeof onClick === 'function'){
        span.addEventListener('click', onClick);
      }
      return span;
    }
    function renderPills(host, tags, onRemove){
      host.querySelectorAll('.tag-pill').forEach(n=> n.remove());
      const input = host.querySelector('input');
      (tags||[]).forEach(t=>{
        const bg = colorFor(t);
        const fg = textColorFor(bg);
        const pill = document.createElement('span');
        pill.className = 'tag-pill inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs';
        pill.style.background = bg; pill.style.color = fg;
        const text = document.createElement('span');
        text.textContent = t;
        const btn = document.createElement('button');
        btn.type = 'button'; btn.className = 'ml-1'; btn.setAttribute('aria-label','remove');
        const icon = document.createElement('i'); icon.className = 'fa fa-times';
        btn.appendChild(icon);
        btn.addEventListener('click', ()=> { onRemove && onRemove(t); }); // Don't call forgetTag here - only remove from current input
        pill.appendChild(text);
        pill.appendChild(btn);
        host.insertBefore(pill, input);
      });
    }
    // Render a tag library with + (add) button and hover 'x' (delete) with confirm
    function renderLibrary(host, onAdd){
      const tags = getAllKnownTags().slice().sort((a,b)=> a.localeCompare(b));
      host.innerHTML = '';
      tags.forEach(t=>{
        const wrap = document.createElement('div');
        wrap.className = 'group inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs border cursor-pointer select-none';
        const bg = colorFor(t); const fg = textColorFor(bg);
        wrap.style.borderColor = bg; wrap.style.color = bg;
        const plus = document.createElement('span'); plus.title='Add'; plus.className='opacity-100'; plus.innerHTML='<i class="fa fa-plus"></i>';
        const text = document.createElement('span'); text.textContent = t;
        const del = document.createElement('button'); del.type='button'; del.title='Delete';
        del.className = 'opacity-0 group-hover:opacity-100 text-red-600 ml-1';
        del.innerHTML = '<i class="fa fa-times"></i>';
        del.addEventListener('click', (e)=>{
          e.stopPropagation();
          if(confirm(`Delete tag "${t}"? This removes it from suggestions (items keep their tags).`)){
            forgetTag(t);
            renderLibrary(host, onAdd);
          }
        });
        wrap.addEventListener('click', ()=> onAdd && onAdd(t));
        wrap.appendChild(plus);
        wrap.appendChild(text);
        wrap.appendChild(del);
        host.appendChild(wrap);
      });
    }
    function addTokenToInput(inputEl, token, separator=','){
      if(!inputEl) return;
      // For our token input, we push into pills renderer instead of string concat; this helper is generic fallback
      const v = inputEl.value.trim();
      inputEl.value = v ? (v + separator + ' ' + token) : token;
    }
    function onChange(cb){
      const handler = (e)=>{ if(e && e.detail && e.detail.scope === scope){ try{ cb(e.detail); }catch(_){} } };
      window.addEventListener('tags:changed', handler);
      return ()=> window.removeEventListener('tags:changed', handler);
    }
    return { ensureColor, colorFor, recordTags, getAllKnownTags, makePill, makeFilledPill, renderPills, renderLibrary, addTokenToInput, onChange };
  }

  // Backward-compatible global (uses 'global' scope), plus a factory for scoped usage
  const defaultScoped = createScoped('global');
  global.Tags = Object.assign({}, defaultScoped, { scoped: createScoped });
})(window);
