// Inject category color classes immediately after data is available
(function(){
	try {
		var dataEl = document.getElementById('reminderCategoriesData');
		const cats = JSON.parse(dataEl ? dataEl.textContent || '[]' : '[]');
		let css = '';
		cats.forEach(c=>{ 
			if(!c || !c.key) return; 
			const key=(c.key+'').replace(/[^a-zA-Z0-9_-]/g,''); 
			const col=c.color||'#6b7280'; 
			css += '.rem-cat-dot-'+key+'{background:'+col+' !important;}'; 
		});
		css += '.rem-cat-pill{transition:background-color .15s,box-shadow .15s;}';
		css += '.rem-cat-pill:hover{box-shadow:0 0 0 1px rgba(var(--primary-rgb,37,99,235),0.35);}';
		const el=document.createElement('style'); 
		el.id='reminder-category-styles'; 
		el.textContent=css; 
		document.head.appendChild(el);
	}catch(e){ console.warn('Category style inject failed', e); }
})();
