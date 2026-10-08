	// Live updating time and date
	(function updateDateTime() {
		const timeEl = document.getElementById('currentTime');
		const dateEl = document.getElementById('currentDate');
		if (!timeEl || !dateEl) return;
		
		function update() {
			const now = new Date();
			timeEl.textContent = now.toLocaleTimeString(window.I18N.locale);
			dateEl.textContent = now.toLocaleDateString(window.I18N.locale, { 
				weekday: 'long', 
				year: 'numeric', 
				month: 'long', 
				day: 'numeric' 
			});
		}
		
		update(); // Initial update
		setInterval(update, 1000); // Update every second
	})();
		// Toggle visibility of Notice form based on current user (admin)
		(function(){
			const nf = document.getElementById('noticeForm');
			if(!nf) return;
			const adminName = window.HomeHubAdmin.names[0];
			function update(){
				const current = localStorage.getItem('username');
				if (!(current === adminName || current === 'Administrator' || current === 'admin')) {
					nf.style.display = 'none';
				} else {
					nf.style.display = '';
				}
				const userField = nf.querySelector('input[name="user"]');
				if (userField) userField.value = current || '';
			}
			update();
			document.getElementById('userSwitcher')?.addEventListener('change', update);
		})();
