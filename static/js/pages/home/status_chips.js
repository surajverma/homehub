// Enhance chips for member personal statuses with per-name coloring; default and hide admin for Who is Home quick controls
(function(){
	// Personal member statuses: give each member a stable color
	function colorPersonalChips(){
		const chips = document.querySelectorAll('#memberStatusChips span[data-name]');
		const palette = ['#DBEAFE','#FEF3C7','#DCFCE7','#FCE7F3','#E9D5FF','#FFEDD5','#E5E7EB'];
		const names = Array.from(new Set(Array.from(chips).map(el=>el.getAttribute('data-name'))));
		names.forEach((name, idx)=>{
			const els = Array.from(chips).filter(e=>e.getAttribute('data-name')===name);
			const bg = palette[idx % palette.length];
			els.forEach(el=>{ el.style.backgroundColor = bg; el.style.borderColor = '#CBD5E1'; });
		});
	}
	colorPersonalChips();
	// Helper to update Who is Home quick controls and member status forms for current user
	function updateHomeAndStatusUI(){
		const current = localStorage.getItem('username') || '';
		const adminName = window.HomeHubAdmin.names[0];
		// Hidden inputs
		document.getElementById('whoName')?.setAttribute('value', current);
		document.getElementById('whoNameClear')?.setAttribute('value', current);
		document.getElementById('memberStatusName')?.setAttribute('value', current);
		document.getElementById('memberStatusDeleteName')?.setAttribute('value', current);
		// Update user chip
		const chip = document.getElementById('memberStatusUserChip');
		if (chip){ chip.textContent = current ? current : ''; }
		// Admin: hide quick controls and personal status editor
		const isAdmin = (current === adminName || current === 'Administrator' || current === 'admin');
		const quick = document.getElementById('whoUnifiedForm');
		const msForm = document.getElementById('memberStatusForm');
		const msDel = document.getElementById('memberStatusDelete');
		const whoCard = document.getElementById('whoHomeCard');
		const personalCard = document.getElementById('personalStatusCard');
		if (isAdmin){
			whoCard?.classList.add('hidden');
			personalCard?.classList.add('hidden');
			return; // hide entire cards for admin
		}else{
			whoCard?.classList.remove('hidden');
			personalCard?.classList.remove('hidden');
			quick?.classList.remove('hidden');
			msForm?.classList.remove('hidden');
		}
		// Show delete button only if current user has a status chip
		const chips = document.querySelectorAll('#memberStatusChips span[data-name]');
		const hasStatus = Array.from(chips).some(el => el.getAttribute('data-name') === current);
		if (msDel){ msDel.classList.toggle('hidden', !hasStatus); }
	}
	// Initial paint
	updateHomeAndStatusUI();
	// Update on user switch without refresh (global event from base.html)
	document.addEventListener('user-switched', updateHomeAndStatusUI);

	// Unified Who is Home form clear button handling
	const whoClearBtn = document.getElementById('whoClearBtn');
	const whoUnifiedForm = document.getElementById('whoUnifiedForm');
	if (whoClearBtn && whoUnifiedForm){
		whoClearBtn.addEventListener('click', function(){
			const actionField = document.getElementById('whoAction');
			if(actionField){ actionField.value = 'clear'; }
			whoUnifiedForm.submit();
		});
		const updateBtn = document.getElementById('whoUpdateBtn');
		if(updateBtn){
			updateBtn.addEventListener('click', function(){
				const actionField = document.getElementById('whoAction');
				if(actionField){ actionField.value = 'update'; }
			});
		}
	}
})();
