// AJAX enhancement for Who is Home & Personal Status forms (no page reload)
(function(){
	// Skip if Who is Home feature disabled
	if(!document.getElementById('whoUnifiedForm') && !document.getElementById('whoStatusList')) return;
		// Family members injected from server
		const FAMILY_MEMBERS = JSON.parse(document.getElementById('familyMembersData').textContent || '[]');
		function fetchJson(url, opts){
		opts = opts || {}; opts.headers = Object.assign({'X-Requested-With':'fetch'}, opts.headers||{});
		return fetch(url, opts).then(r=>r.json().catch(()=>({error:'Bad JSON'})));
	}
	const whoForm = document.getElementById('whoUnifiedForm');
	const memberForm = document.getElementById('memberStatusForm');
	const memberDel = document.getElementById('memberStatusDelete');
	const whoList = document.getElementById('whoStatusList');
	const memberChips = document.getElementById('memberStatusChips');
	// Safe toast alias (previous inline function removed) to avoid ReferenceError breaking listeners
	const toast = (msg,type)=>{ if(window.globalToast) window.globalToast(msg,type); else console.log('[toast]', type||'', msg); };
	function currentUser(){ return localStorage.getItem('username')||''; }
	function updateWhoDOM(data){
		if(!data) return;
		let statuses={};
		if(Array.isArray(data.entries)) data.entries.forEach(e=>{ statuses[e.name]=e.status; });
		else if(data.who_statuses && typeof data.who_statuses==='object') statuses=data.who_statuses;
		// Use requestAnimationFrame to batch DOM updates and prevent layout thrashing
		requestAnimationFrame(()=>{
			whoList.querySelectorAll('[data-member]').forEach(container=>{
				const member=container.getAttribute('data-member');
				const st=statuses[member];
				const pill=container.querySelector('.status-pill');
				if(!pill) return;
				// Update text content only if changed
				const newText=st||'—';
				if(pill.textContent!==newText) pill.textContent=newText;
				// Compute new classes
				const baseClasses=['px-1.5','py-0.5','rounded','text-[10px]','leading-none','status-pill'];
				let colorClasses=[];
				if(st==='Home'){ colorClasses=['bg-green-100','text-green-700','border','border-green-300']; }
				else if(st==='Away'){ colorClasses=['bg-gray-100','text-gray-600','border','border-gray-300']; }
				else if(st==='Out'){ colorClasses=['bg-amber-100','text-amber-700','border','border-amber-300']; }
				else if(st==='Traveling'){ colorClasses=['bg-indigo-100','text-indigo-700','border','border-indigo-300']; }
				else if(st){ colorClasses=['bg-blue-100','text-blue-700','border','border-blue-300']; }
				else{ colorClasses=['bg-gray-100','text-gray-500','border','border-gray-200']; }
				// Replace className entirely to avoid classList manipulation overhead
				pill.className=baseClasses.concat(colorClasses).join(' ');
			});
		});
	}
	function recolorMemberChips(){
		const chips = memberChips.querySelectorAll('span[data-name]');
		const palette=['#DBEAFE','#FEF3C7','#DCFCE7','#FCE7F3','#E9D5FF','#FFEDD5','#E5E7EB'];
		const names=[...new Set(Array.from(chips).map(c=>c.dataset.name))];
		names.forEach((n,i)=>{ const bg=palette[i%palette.length]; chips.forEach(ch=>{ if(ch.dataset.name===n){ ch.style.backgroundColor=bg; ch.style.borderColor='#CBD5E1'; } }); });
	}
		function updateMemberStatusDOM(data){
			if(!data) return;
			let entries=[];
			if(Array.isArray(data.entries)) entries=data.entries;
			else if(data.member_statuses && typeof data.member_statuses==='object'){
				entries = Object.entries(data.member_statuses).map(([name,text])=>({name,text}));
			}
			memberChips.innerHTML='';
			entries.forEach(e=>{ if(!e.text) return; const span=document.createElement('span'); span.dataset.name=e.name; span.className='px-2 py-1 rounded-full border bg-white text-xs'; span.innerHTML=e.text+' <span class="text-gray-500 text-[10px]">— '+e.name+'</span>'; memberChips.appendChild(span); });
		recolorMemberChips();
		const user=currentUser();
		const has=Array.from(memberChips.querySelectorAll('span[data-name]')).some(el=>el.dataset.name===user);
		memberDel.classList.toggle('hidden', !has);
	}
		if(whoForm){ whoForm.addEventListener('submit', e=>{ e.preventDefault(); const fd=new FormData(whoForm); const payload=new URLSearchParams(fd); const actionField=document.getElementById('whoAction'); const actVal=actionField?.value; const targetUrl=whoForm.getAttribute('action'); fetchJson(targetUrl,{method:'POST',body:payload}).then(resp=>{ if(resp && resp.ok){ updateWhoDOM(resp); const resType = resp.result || (actVal==='clear'?'cleared':'updated'); if(resType==='cleared') toast('Status cleared','success'); else if(resType==='none') toast('No status to clear','info'); else toast('Status updated','success'); if(actionField) actionField.value='update'; } else toast(resp && resp.error || 'Update failed','error'); }); }); }
	const whoClear=document.getElementById('whoClearBtn');
	if(whoClear){
		whoClear.addEventListener('click', e=>{
			e.preventDefault();
			if(!whoForm) return;
			const nameVal = whoForm.querySelector('[name=name]')?.value || '';
			const payload = new URLSearchParams();
			payload.append('action','clear');
			payload.append('name', nameVal);
			fetchJson(whoForm.getAttribute('action'), { method:'POST', body: payload }).then(resp=>{
				if(resp && resp.ok){
					updateWhoDOM(resp);
					if(resp.result==='cleared') toast('Status cleared','success');
					else if(resp.result==='none') toast('No status to clear','info');
					else toast('Status updated','success');
				} else {
					toast((resp && resp.error) || 'Update failed','error');
				}
			});
		});
	}
		if(memberForm){ memberForm.addEventListener('submit', e=>{ e.preventDefault(); const input=memberForm.querySelector('[name=text]'); const val=(input?.value||'').trim(); if(!val){ toast('Cannot save empty status','info'); return; } const fd=new FormData(memberForm); const qs=new URLSearchParams(fd); const targetUrl=memberForm.getAttribute('action'); fetchJson(targetUrl,{method:'POST',body:qs}).then(resp=>{ if(resp && resp.ok){ updateMemberStatusDOM(resp); memberForm.reset(); memberForm.querySelector('[name=name]').value=currentUser(); toast('Status saved','success'); } else { if(resp && resp.error==='Empty status'){ toast('Cannot save empty status','info'); } else toast(resp && resp.error || 'Save failed','error'); } }); }); }
		if(memberDel){ memberDel.addEventListener('submit', e=>{ e.preventDefault(); const fd=new FormData(memberDel); const qs=new URLSearchParams(fd); const targetUrl=memberDel.getAttribute('action'); fetchJson(targetUrl,{method:'POST',body:qs}).then(resp=>{ if(resp && resp.ok){ updateMemberStatusDOM(resp); toast('Status removed','success'); } else toast(resp && resp.error || 'Remove failed','error'); }); }); }
	// Initialize name fields
	const cur=currentUser();
	document.getElementById('whoName')?.setAttribute('value', cur);
	document.getElementById('memberStatusName')?.setAttribute('value', cur);
	document.getElementById('memberStatusDeleteName')?.setAttribute('value', cur);
})();
