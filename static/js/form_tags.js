(function(global){
  function parseTokens(text){
    return (text||'')
      .split(',')
      .map(t=>t.trim())
      .filter(Boolean);
  }
  function initTagInputForm(T, opts){
    const wrap = document.getElementById(opts.wrapId);
    const input = document.getElementById(opts.inputId);
    const hidden = document.getElementById(opts.hiddenId);
    const form = document.getElementById(opts.formId);
    const library = document.getElementById(opts.libraryId);
    let tags = [];
    function setHidden(){ if(hidden) hidden.value = JSON.stringify(tags); }
    function render(){ if(!wrap) return; T.renderPills(wrap, tags, t=>{ tags = tags.filter(x=>x!==t); render(); }); setHidden(); }
    function addTokens(tokens){
      (tokens||[]).forEach(v=>{ if(v && !tags.includes(v)){ tags.push(v); T.ensureColor(v); }});
    }
    if(input){
      input.addEventListener('keydown', (e)=>{
        if(e.key==='Enter' || e.key===','){
          e.preventDefault();
          const extra = parseTokens(input.value);
          if(extra.length){ addTokens(extra); input.value=''; render(); }
        }
        if(e.key==='Backspace' && !input.value && tags.length){ tags.pop(); render(); }
      });
    }
    if(wrap){ wrap.addEventListener('click', ()=> input && input.focus()); }
    if(library){ T.renderLibrary(library, (t)=>{ if(!tags.includes(t)){ tags.push(t); render(); }}); }
    if(form){
      form.addEventListener('submit', ()=>{
        if(!input) return;
        const extra = parseTokens(input.value);
        if(extra.length){
          // Harvest silently on submit: update data and hidden, but do not re-render pills to avoid brief flash
          addTokens(extra);
          input.value='';
          setHidden();
        }
      });
    }
    // initialize hidden on load so server sees []
    setHidden();
    return {
      getTags: ()=> tags.slice(),
      setTags: (arr)=>{ tags = (arr||[]).slice(); render(); },
      harvestPending: ()=>{ if(!input) return; const extra=parseTokens(input.value); if(extra.length){ addTokens(extra); input.value=''; render(); } }
    };
  }
  function harvestPendingInto(T, inputEl, tagsArray){
    if(!inputEl || !Array.isArray(tagsArray)) return;
    const extra = parseTokens(inputEl.value);
    extra.forEach(v=>{ if(v && !tagsArray.includes(v)){ tagsArray.push(v); T.ensureColor(v); }});
    inputEl.value='';
  }
  global.FormTags = { initTagInputForm, harvestPendingInto };
})(window);
