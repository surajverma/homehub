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
  function _hashString(value){
    let h = 0;
    const s = String(value || '');
    for(let i = 0; i < s.length; i++){
      h = ((h << 5) - h) + s.charCodeAt(i);
      h |= 0;
    }
    return Math.abs(h);
  }
  function _hueDistance(a, b){
    const d = Math.abs(a - b) % 360;
    return Math.min(d, 360 - d);
  }
  function _extractHues(map){
    return Object.values(map || {})
      .map(c=>{ const m = /hsl\((\d+)\s/i.exec(c); return m ? parseInt(m[1], 10) : null; })
      .filter(v=> Number.isInteger(v));
  }
  function _pickDistinctHue(existingHues, preferredHue, minDist){
    const target = Number.isFinite(preferredHue) ? ((preferredHue % 360) + 360) % 360 : 0;
    if(!existingHues.length) return target;
    const step = 137.508; // golden-angle spacing
    let bestHue = target;
    let bestScore = -1;
    for(let i = 0; i < 72; i++){
      const cand = Math.round((target + (i * step)) % 360);
      const nearest = existingHues.reduce((acc, h)=> Math.min(acc, _hueDistance(cand, h)), 360);
      if(nearest > bestScore){
        bestScore = nearest;
        bestHue = cand;
      }
      if(bestScore >= minDist) break;
    }
    return bestHue;
  }
  function _ensureColorInMap(tag, map){
    if(map[tag]) return map[tag];
    const existingHues = _extractHues(map);
    const preferredHue = _hashString(tag) % 360;
    const minDist = 36;
    const pick = _pickDistinctHue(existingHues, preferredHue, minDist);
    map[tag] = _colorFromHue(pick);
    return map[tag];
  }
  function _colorFromHue(h){
    const sat = 65; // tighter range for visual consistency
    const light = 52; // balanced for dark/light text contrast
    return `hsl(${h} ${sat}% ${light}%)`;
  }
  function _hslToRgb(h, s, l){
    const hh = ((h % 360) + 360) % 360;
    const ss = Math.max(0, Math.min(100, s)) / 100;
    const ll = Math.max(0, Math.min(100, l)) / 100;
    const c = (1 - Math.abs((2 * ll) - 1)) * ss;
    const x = c * (1 - Math.abs(((hh / 60) % 2) - 1));
    const m = ll - (c / 2);
    let r = 0, g = 0, b = 0;
    if(hh < 60){ r = c; g = x; b = 0; }
    else if(hh < 120){ r = x; g = c; b = 0; }
    else if(hh < 180){ r = 0; g = c; b = x; }
    else if(hh < 240){ r = 0; g = x; b = c; }
    else if(hh < 300){ r = x; g = 0; b = c; }
    else { r = c; g = 0; b = x; }
    return [
      Math.round((r + m) * 255),
      Math.round((g + m) * 255),
      Math.round((b + m) * 255)
    ];
  }
  function _relativeLuminance(rgb){
    const chan = (v)=>{
      const c = v / 255;
      return c <= 0.03928 ? (c / 12.92) : Math.pow((c + 0.055) / 1.055, 2.4);
    };
    const [r, g, b] = rgb.map(chan);
    return (0.2126 * r) + (0.7152 * g) + (0.0722 * b);
  }
  function _contrastRatio(rgbA, rgbB){
    const l1 = _relativeLuminance(rgbA);
    const l2 = _relativeLuminance(rgbB);
    const light = Math.max(l1, l2);
    const dark = Math.min(l1, l2);
    return (light + 0.05) / (dark + 0.05);
  }
  function textColorFor(bg){
    // Pick foreground by actual contrast against white and dark text.
    if(/^hsl\(/i.test(bg)){
      try{
        const parts = bg.match(/hsl\(([^)]+)\)/i)[1].trim().split(/\s+/);
        const h = parseFloat(parts[0]);
        const s = parseFloat(parts[1]);
        const l = parseFloat(parts[2]);
        const bgRgb = _hslToRgb(h, s, l);
        const white = [255, 255, 255];
        const dark = [17, 24, 39];
        const cWhite = _contrastRatio(bgRgb, white);
        const cDark = _contrastRatio(bgRgb, dark);
        return cWhite >= cDark ? '#fff' : '#111827';
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
        _ensureColorInMap(tag, map);
        setStore(KEYS.COLORS, map);
        recordTags([tag]);
      }
      return map[tag];
    }
    function colorFor(tag){
      const map = getStore(KEYS.COLORS, {});
      if(!map[tag]){
        _ensureColorInMap(tag, map);
        setStore(KEYS.COLORS, map);
      }
      return map[tag];
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
