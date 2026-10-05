// --- Reminders Progressive Enhancement (clean re-integration) ---
(function(){
	// Wait until remindersApi is available (in case script ordering delays)
	if(!window.remindersApi){
		let tries=0; const timer=setInterval(()=>{
			if(window.remindersApi){ clearInterval(timer); init(); }
			else if(++tries>20){ clearInterval(timer); }
		},150);
		return;
	}
	init();
	function init(){
	const cal = document.getElementById('calendar');
	const calLabel = document.getElementById('calLabel');
	const prevBtn = document.getElementById('calPrev');
	const nextBtn = document.getElementById('calNext');
	const header = document.getElementById('remindersHeader');
	const listWrap = document.getElementById('remindersScroll');
	const bulkBar = document.getElementById('remindersBulkBar');
	const bulkCount = document.getElementById('bulkCount');
	const bulkIds = document.getElementById('bulkIds');
	const bulkUser = document.getElementById('bulkUser');
	const inlineWrap = document.getElementById('reminderInlineFormWrap');
	const inlineForm = document.getElementById('reminderInlineForm');
	const categoryRow = document.getElementById('categorySelectRow');
	const scopeBarWrap = document.getElementById('scopeBarWrap');
	const categorySelect = categoryRow.querySelector('select');
	const categoryFilter = null; // dropdown removed; using pills only
	const cancelBtn = document.getElementById('reminderCancelBtn');
	const selectedDateDisplay = document.getElementById('remindersSelectedDate');
	const searchInput = document.getElementById('calendarSearch');
	const upcomingTimeline = document.getElementById('upcomingTimeline');
	let editingId = null;
	let currentSearchTerm = '';
	let completedCollapsed = true;
	let highlightedReminderRange = null; // { start: 'YYYY-MM-DD', end: 'YYYY-MM-DD' } for card-click range highlight
	let currentScope = localStorage.getItem('remindersScope') || 'day';
	let display = new Date(); // current month
	let monthCache = {}; // YYYY-MM -> { reminders:[], counts:{}, categories_counts:{} }
	const catDataEl = document.getElementById('reminderCategoriesData');
	let live = document.getElementById('reminderLiveRegion');
	if(!live){ live=document.createElement('div'); live.id='reminderLiveRegion'; live.className='sr-only'; live.setAttribute('aria-live','polite'); header.appendChild(live);}  
	// Use global toast from base.html (no duplicate toast host)
	function toast(msg,type='info'){
		if(window.globalToast){ window.globalToast(msg,type); }
		if(live) live.textContent=msg;
	}
	let catPalette={};
	try{ const cats=JSON.parse(catDataEl?.textContent||'[]'); categorySelect.innerHTML='<option value="">(No category)</option>'+(Array.isArray(cats)?cats.map(c=>`<option value="${c.key}">${c.label||c.key}</option>`).join(''):''); (Array.isArray(cats)?cats:[]).forEach(c=>{ if(c && c.key){ catPalette[c.key]=c.color||'#2563eb'; } }); }catch(e){}
	function categoryColor(k){ return catPalette[k]; }
	function escapeHtml(str){ return (str||'').replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }
	// Unified bulk bar updater (delegated-safe)
	function updateBulkUI(){
		const checked = Array.from(listWrap.querySelectorAll('.reminderChk:checked'));
		bulkCount.textContent = String(checked.length);
		bulkIds.value = checked.map(cb=>cb.value).join(',');
		bulkBar.classList.toggle('hidden', checked.length===0);
	}
	// One-time delegated listeners for checkbox changes and single-delete
	if(!listWrap.dataset.delegated){
		listWrap.dataset.delegated = '1';
		listWrap.addEventListener('change', (e)=>{
			if(e.target && e.target.matches && e.target.matches('.reminderChk')){
				updateBulkUI();
			}
		});
		listWrap.addEventListener('click', (e)=>{
			const delBtn = e.target && e.target.closest ? e.target.closest('.deleteOne') : null;
			if(delBtn){
				const row = delBtn.closest('[data-reminder-row]');
				if(!row) return;
				const id = parseInt(row.getAttribute('data-id'));
				const dateStr = row.getAttribute('data-date')||'';
				if(!id) return;
				const creator = localStorage.getItem('username')||'';
				window.remindersApi.removeMany([id], creator).then(resp=>{
					if(resp && resp.ok){
						highlightedReminderRange = null;
						Object.values(monthCache).forEach(mc=>{ mc.reminders = (mc.reminders||[]).filter(x=>x.id!==id); });
						// If this id was selected, uncheck and update bulk bar
						const selCb = listWrap.querySelector(`.reminderChk[value="${id}"]`);
						if(selCb){ selCb.checked = false; }
						toast('Deleted','success');
						renderList();
						renderCalendar();
						updateBulkUI();
						updateCalendarBadges();
					}else{
						toast('Delete failed','error');
					}
				});
			}
		});
		const bulkForm = document.getElementById('bulkDeleteForm');
		const clearBtn = document.getElementById('bulkClearSel');
		if(clearBtn){
			clearBtn.addEventListener('click', ()=>{
				listWrap.querySelectorAll('.reminderChk').forEach(c=>{ c.checked=false; });
				updateBulkUI();
			});
		}
		if(bulkForm && !bulkForm._delegated){
			bulkForm._delegated = true;
			bulkForm.addEventListener('submit', (e)=>{
				e.preventDefault();
				const checked = Array.from(listWrap.querySelectorAll('.reminderChk:checked'));
				if(!checked.length){ return; }
				const ids = checked.map(cb=>parseInt(cb.value));
				const creator = localStorage.getItem('username')||'';
				window.remindersApi.removeMany(ids, creator).then(resp=>{
					if(!resp || !resp.ok){ toast('Delete failed','error'); return; }
					highlightedReminderRange = null;
					Object.values(monthCache).forEach(mc=>{ mc.reminders = (mc.reminders||[]).filter(r=>!ids.includes(r.id)); });
					toast('Deleted '+ids.length,'success');
					// Clear any selections and hide the bulk bar
					listWrap.querySelectorAll('.reminderChk').forEach(c=>{ c.checked=false; });
					updateBulkUI();
					renderList();
					renderCalendar();
					updateCalendarBadges();
				});
			});
		}
	}
	// Completed visibility is controlled only by the section-level Show/Hide toggle.
	function setSelectedDate(d){ header.setAttribute('data-selected-date', d); selectedDateDisplay.textContent=d; localStorage.setItem('remindersSelectedDate', d); }
	function getSelectedDate(){ return header.getAttribute('data-selected-date') || localStorage.getItem('remindersSelectedDate') || formatYmd(new Date()); }
	const scopeBar=document.createElement('div'); scopeBar.className='flex gap-1'; scopeBar.innerHTML=['day','week','month'].map(s=>`<button type="button" data-scope="${s}" class="px-2 py-0.5 text-xs rounded border scopeBtn ${s===currentScope?'border-blue-500 text-blue-600 bg-blue-50':'border-gray-300 bg-white text-gray-600 hover:bg-gray-100'}">${s[0].toUpperCase()+s.slice(1)}</button>`).join(''); scopeBarWrap.appendChild(scopeBar);
	scopeBar.addEventListener('click', e=>{ const b=e.target.closest('button[data-scope]'); if(!b)return; currentScope=b.getAttribute('data-scope'); localStorage.setItem('remindersScope', currentScope); scopeBar.querySelectorAll('.scopeBtn').forEach(btn=>{ const act=btn.getAttribute('data-scope')===currentScope; btn.className='px-2 py-0.5 text-xs rounded border scopeBtn '+(act?'border-blue-500 text-blue-600 bg-blue-50':'border-gray-300 bg-white text-gray-600 hover:bg-gray-100'); }); renderList(); });
	function openForm(mode,dateStr,data){ inlineForm.reset(); editingId=null; inlineForm.querySelector('[name=id]').value=''; inlineForm.querySelector('[name=date]').value=dateStr; if(mode==='edit'&&data){ editingId=data.id; inlineForm.querySelector('[name=id]').value=data.id; inlineForm.querySelector('[name=title]').value=data.title; inlineForm.querySelector('[name=description]').value=data.description||''; if(data.category && categorySelect) categorySelect.value=data.category; if(data.time) inlineForm.querySelector('[name=time]').value=data.time; if(data.start_date) inlineForm.querySelector('[name=date]').value=data.start_date; if(data.start_time) inlineForm.querySelector('[name=time]').value=data.start_time; if(data.end_date) inlineForm.querySelector('[name=end_date]').value=data.end_date; if(data.end_time) inlineForm.querySelector('[name=end_time]').value=data.end_time; }
		// reset recurring toggle state each time the form opens
		if(recChk){ recChk.checked = false; }
		clearGuardrail();
		toggleRecurringFields();
		inlineWrap.classList.remove('hidden'); setTimeout(()=>inlineForm.querySelector('[name=title]').focus(),30); }
	function closeForm(){ inlineWrap.classList.add('hidden'); editingId=null; if(typeof window.__editingRuleId!== 'undefined'){ window.__editingRuleId=null; } if(inlineForm){ inlineForm.reset(); } if(recChk){ recChk.checked=false; } clearGuardrail(); toggleRecurringFields(); }
	cancelBtn.addEventListener('click', closeForm);
	document.getElementById('openAdd').addEventListener('click', ()=> openForm('create', getSelectedDate(), null));
	// Recurring controls (enable/disable dependent fields)
	const recChk = inlineForm ? inlineForm.querySelector('[name=is_recurring]') : null;
	const recControls = document.getElementById('recurrenceControls');
	const recGuardrail = document.getElementById('recurrenceGuardrail');
	const recIntervalEl = inlineForm ? inlineForm.querySelector('[name=rec_interval]') : null;
	const recUnitEl = inlineForm ? inlineForm.querySelector('[name=rec_unit]') : null;
	const recEndDateEl = inlineForm ? inlineForm.querySelector('[name=rec_end_date]') : null;
	const endDateEl = inlineForm ? inlineForm.querySelector('[name=end_date]') : null;
	const endTimeEl = inlineForm ? inlineForm.querySelector('[name=end_time]') : null;
	const startDateEl = inlineForm ? inlineForm.querySelector('[name=date]') : null;
	function toggleRecurringFields(){
		const on = !!(recChk && recChk.checked);
		if(recControls){ recControls.classList.toggle('hidden', !on); }
		['rec_interval','rec_unit','rec_end_date'].forEach(n=>{
			const el = inlineForm ? inlineForm.querySelector(`[name=${n}]`) : null;
			if(el){ el.disabled = !on; }
		});
	}
	function clearGuardrail(){ if(recGuardrail){ recGuardrail.classList.add('hidden'); recGuardrail.innerHTML=''; } }
	function parseYmd(value){
		if(!value) return null;
		const d = new Date(value + 'T00:00:00');
		return Number.isNaN(d.getTime()) ? null : d;
	}
	function addMonthsClamped(base, months){
		const y = base.getFullYear();
		const m = base.getMonth() + months;
		const d = base.getDate();
		const first = new Date(y, m, 1);
		const lastDay = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
		return new Date(first.getFullYear(), first.getMonth(), Math.min(d, lastDay));
	}
	function addIntervalDate(base, interval, unit){
		if(unit === 'day') return new Date(base.getFullYear(), base.getMonth(), base.getDate() + interval);
		if(unit === 'week') return new Date(base.getFullYear(), base.getMonth(), base.getDate() + (7 * interval));
		if(unit === 'month') return addMonthsClamped(base, interval);
		if(unit === 'year') return addMonthsClamped(base, interval * 12);
		return new Date(base.getFullYear(), base.getMonth(), base.getDate() + interval);
	}
	function formatYmd(d){ return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
	function minNonOverlapInterval(startDate, endDate, unit){
		for(let n=1;n<=366;n++){
			const next = addIntervalDate(startDate, n, unit);
			if(next > endDate) return n;
		}
		return 1;
	}
	function recurrenceOverlapInfo(data){
		if(!(recChk && recChk.checked)) return { conflict:false };
		const start = parseYmd(data.date);
		const end = parseYmd(data.end_date);
		if(!start || !end || end <= start) return { conflict:false };
		let interval = parseInt(data.rec_interval || '1', 10);
		if(!Number.isFinite(interval) || interval < 1) interval = 1;
		const unit = data.rec_unit || 'day';
		const nextStart = addIntervalDate(start, interval, unit);
		if(nextStart > end) return { conflict:false };
		const minInterval = minNonOverlapInterval(start, end, unit);
		return { conflict:true, minInterval, unit };
	}
	function showGuardrail(info){
		if(!recGuardrail) return;
		recGuardrail.classList.remove('hidden');
		recGuardrail.innerHTML = `<div class="font-semibold mb-1">This recurrence overlaps with the reminder duration.</div>
			<div class="mb-2">Choose one fix to continue:</div>
			<div class="flex flex-wrap gap-2">
				<button type="button" data-guardrail="adjust" class="px-2 py-1 rounded border bg-white hover:bg-amber-100">Set repeat every ${info.minInterval} ${info.unit}${info.minInterval===1?'':'s'}</button>
				<button type="button" data-guardrail="remove-end" class="px-2 py-1 rounded border bg-white hover:bg-amber-100">Remove end date/time</button>
				<button type="button" data-guardrail="disable-rec" class="px-2 py-1 rounded border bg-white hover:bg-amber-100">Keep as non-recurring</button>
			</div>`;
	}
	if(recGuardrail && !recGuardrail.dataset.bound){
		recGuardrail.dataset.bound = '1';
		recGuardrail.addEventListener('click', (e)=>{
			const btn = e.target && e.target.closest ? e.target.closest('button[data-guardrail]') : null;
			if(!btn) return;
			const action = btn.getAttribute('data-guardrail');
			if(action === 'adjust'){
				const data = Object.fromEntries(new FormData(inlineForm).entries());
				const info = recurrenceOverlapInfo(data);
				if(info.conflict && recIntervalEl){ recIntervalEl.value = String(info.minInterval); }
			}
			if(action === 'remove-end'){
				if(endDateEl) endDateEl.value = '';
				if(endTimeEl) endTimeEl.value = '';
			}
			if(action === 'disable-rec'){
				if(recChk) recChk.checked = false;
				toggleRecurringFields();
			}
			clearGuardrail();
		});
	}
	[startDateEl, endDateEl, endTimeEl, recChk, recIntervalEl, recUnitEl, recEndDateEl].forEach(el=>{
		if(el && !el.dataset.guardrailBound){
			el.dataset.guardrailBound = '1';
			el.addEventListener('input', clearGuardrail);
			el.addEventListener('change', clearGuardrail);
		}
	});
	if(recChk){ recChk.addEventListener('change', toggleRecurringFields); toggleRecurringFields(); }

	// Recalculate counts & category aggregates for a month key (YYYY-M), including recurring rule dates
	function recalcMonth(key){
		const bucket = monthCache[key];
		if(!bucket) return;
		const counts = {};
		const catsCounts = {};
		const reminders = bucket.reminders || [];
		// Count actual reminders, spreading across date ranges for multi-day events
		function _localDateStr(d){ return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
		reminders.forEach(r => {
			if(r.completed_at || r.deleted_at) return; // skip completed/deleted from badge counts
			const sd = r.start_date || r.date;
			const ed = r.end_date || sd;
			if(!sd) return;
			let dcur = new Date(sd + 'T00:00:00');
			const dend = new Date(ed + 'T00:00:00');
			while(dcur <= dend){
				const k = _localDateStr(dcur);
				counts[k] = (counts[k] || 0) + 1;
				const c = r.category || '_uncategorized';
				if(!catsCounts[k]) catsCounts[k] = {};
				catsCounts[k][c] = (catsCounts[k][c] || 0) + 1;
				dcur = new Date(dcur.getTime() + 86400000);
			}
		});
		// For recurring rules, only add dates that are NOT already represented by a reminder for that rule/date
		(bucket.recurring_rules || []).forEach(rr => {
			const c = rr.category || '_uncategorized';
			(rr.dates || []).forEach(dstr => {
				const exists = reminders.some(r => (r.recurring_id == rr.id || String(r.recurring_id) === String(rr.id)) && r.date === dstr);
				if(!exists){
					counts[dstr] = (counts[dstr] || 0) + 1;
					if(!catsCounts[dstr]) catsCounts[dstr] = {};
					catsCounts[dstr][c] = (catsCounts[dstr][c] || 0) + 1;
				}
			});
		});
		bucket.counts = counts;
		bucket.categories_counts = catsCounts;
	}

	inlineForm.addEventListener('submit', async e=>{
		e.preventDefault();
		const fd=new FormData(inlineForm);
		const data=Object.fromEntries(fd.entries());
		const overlapInfo = recurrenceOverlapInfo(data);
		if(overlapInfo.conflict){
			showGuardrail(overlapInfo);
			toast('Recurring schedule overlaps selected date range','error');
			return;
		}
		const creator=localStorage.getItem('username')||'';
		const payload={title:data.title, date:data.date, time:(data.time||undefined), start_date:data.date, start_time:(data.time||undefined), end_date:data.end_date||undefined, end_time:(data.end_time||undefined), description:data.description, creator: creator, category:data.category||undefined};
		// include recurring payload when checkbox checked (interval+unit)
		if(recChk && recChk.checked){
			let interval = parseInt(data.rec_interval, 10);
			if(!Number.isFinite(interval) || interval < 1){ interval = 1; }
			const unit = (data.rec_unit||'day');
			payload.recurring = {
				interval,
				unit,
				end_date: data.rec_end_date || undefined
			};
		}
		// If editing a recurring rule, call rule PATCH instead of reminder create/update
		let res; const touchedMonths=new Set();
		if(window.__editingRuleId){
			const rid = window.__editingRuleId; const rulePayload = { creator: creator, title: data.title, description: data.description, time: (data.time||undefined), category: data.category||undefined };
			if(data.date) rulePayload.start_date = data.date;
			// If user unchecked recurring for a rule, convert rule -> single reminder
			if(recChk && !recChk.checked){
				// Create a single reminder using the current form date
				const createSingle = await window.remindersApi.create({ title: data.title, date: data.date, time: (data.time||undefined), start_date:data.date, start_time:(data.time||undefined), end_date:data.end_date||undefined, end_time:(data.end_time||undefined), description: data.description, creator: creator, category: data.category||undefined });
				if(createSingle && createSingle.ok){
					// Delete the recurring rule
					const del = await window.remindersApi.deleteRule(rid, creator);
					if(del && del.ok){
						res = { ok: true, converted: 'rule-to-single', reminder: createSingle.reminder };
					}else{
						res = { ok: false, error: (del && del.error) || 'Failed to delete rule after creating single' };
					}
				}else{
					res = { ok: false, error: (createSingle && createSingle.error) || 'Failed to create single reminder' };
				}
			}
			else {
				// Regular rule update path
				if(recChk && recChk.checked){ let interval=parseInt(data.rec_interval,10); if(!Number.isFinite(interval)||interval<1) interval=1; rulePayload.interval=interval; rulePayload.unit=(data.rec_unit||'day'); rulePayload.end_date=(data.rec_end_date||undefined); }
				res = await window.remindersApi.updateRule(rid, rulePayload);
			}
			if(res && res.ok){
				// Refresh the displayed month so calendar badges update correctly
				const dispKey = display.getFullYear()+'-'+String(display.getMonth()+1).padStart(2,'0');
				await fetchMonth(dispKey+'-01', true);
				touchedMonths.add(dispKey);
				// Also refresh the form date's month if different (rule anchor changed)
				if(data.date){ const d=new Date(data.date+'T00:00:00'); const key=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'); if(key!==dispKey){ await fetchMonth(key+'-01', true); touchedMonths.add(key); } }
			}
		}
		else if(data.id){ // update existing single reminder
			const id=parseInt(data.id);
			// If user checked recurring on a single reminder, convert single -> recurring rule
			if(recChk && recChk.checked && payload.recurring){
				const makeRule = await window.remindersApi.create(payload);
				if(makeRule && makeRule.ok){
					// Remove the original single reminder
					const del = await window.remindersApi.removeMany([id], creator);
					if(del && del.ok){
						res = { ok: true, converted: 'single-to-rule', recurring_id: makeRule.recurring_id };
						// Update local caches: remove old single, refresh month(s) for new rule
						let origMonthKey = null;
						Object.entries(monthCache).forEach(([k,mc])=>{
							const before = (mc.reminders||[]).length;
							mc.reminders = (mc.reminders||[]).filter(r=>r.id!==id);
							if(before !== (mc.reminders||[]).length){ origMonthKey = k; }
						});
						// Fetch the month for the selected/form date to pick up the new rule
						if(data.date){
							await fetchMonth(data.date, true);
						}
						// Also refresh current display month if different
						const dispKey = display.getFullYear()+'-'+String(display.getMonth()+1).padStart(2,'0');
						const formKey = (function(){ const d=new Date(data.date+'T00:00:00'); return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'); })();
						if(formKey !== dispKey){ await fetchMonth(dispKey+'-01', true); }
					}else{
						res = { ok: false, error: (del && del.error) || 'Failed to delete original reminder after creating recurring rule' };
					}
				}else{
					res = { ok: false, error: (makeRule && makeRule.error) || 'Failed to create recurring rule' };
				}
			} else {
				// Normal update of single reminder
				res=await window.remindersApi.update(id,payload);
				if(res.ok){ Object.entries(monthCache).forEach(([k,mc])=>{ const before=mc.reminders.length; mc.reminders=mc.reminders.filter(r=>r.id!==id); if(before!==mc.reminders.length) touchedMonths.add(k); }); if(res.reminder && res.reminder.date){ await fetchMonth(res.reminder.date, true); const d=new Date(res.reminder.date); const key=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'); touchedMonths.add(key); } }
			}
		} else { // create
			res=await window.remindersApi.create(payload);
			if(res.ok){
				if(res.reminder && res.reminder.date){
					await fetchMonth(res.reminder.date, true);
					const d=new Date(res.reminder.date); const key=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'); touchedMonths.add(key);
				} else if(res.recurring_id){
					// For recurring rules, just refresh the month of the selected date to synthesize occurrences
					await fetchMonth(data.date, true);
					const d=new Date(data.date+'T00:00:00'); const key=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'); touchedMonths.add(key);
				}
			}
		}
		if(res && res.ok){ touchedMonths.forEach(k=>recalcMonth(k)); updateCalendarBadges(); closeForm(); renderList(); let msg='Saved'; if(window.__editingRuleId) msg='Recurring rule updated'; else if(data.id) msg='Reminder updated'; else if(res.recurring_id) msg='Recurring rule saved'; else msg='Reminder added'; toast(msg,'success'); } else { toast((res&&res.error)||'Save failed','error'); }
	});
	async function fetchMonth(dateStr, force=false){ const d=new Date(dateStr+'T00:00:00'); const key=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'); if(monthCache[key] && !force) return monthCache[key]; let res; try{ res=await window.remindersApi.list('month', key+'-01'); if(res.ok) monthCache[key]=res; }catch(e){ toast('Network error','error'); } return monthCache[key]||{reminders:[],counts:{},categories_counts:{},recurring_rules:[]}; }
	function buildWeekdayHeader(){
		const row=document.getElementById('reminderWeekdayRow'); if(!row) return; const base=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
		// Convert config start day to index
		let startName = (window.REMINDERS_CAL_START||'sunday').toLowerCase();
		const idxMap = {sunday:0,monday:1,tuesday:2,wednesday:3,thursday:4,friday:5,saturday:6};
		let startIdx = idxMap[startName]; if(startIdx===undefined) startIdx=0;
		const ordered=[]; for(let i=0;i<7;i++){ ordered.push(base[(startIdx+i)%7]); }
		row.innerHTML=ordered.map(n=>'<div>'+n.slice(0,3)+'</div>').join('');
	}
	buildWeekdayHeader();
	function renderCalendar() {
	    const year = display.getFullYear();
	    const month = display.getMonth();
	    calLabel.textContent = display.toLocaleString(undefined, { month: 'long', year: 'numeric' });
	    cal.innerHTML = '';
	    const today = new Date();
	    const curKey = year + '-' + month;
	    const todayKey = today.getFullYear() + '-' + today.getMonth();
	    const selected = getSelectedDate();
	    const todayBtn = document.getElementById('backToToday');
	    if (todayBtn) {
	        todayBtn.classList.toggle('hidden', curKey === todayKey);
	        if (!todayBtn.dataset.bound) {
	            todayBtn.dataset.bound = '1';
	            todayBtn.addEventListener('click', () => {
	                const now = new Date();
	                display = new Date(now.getFullYear(), now.getMonth(), 1);
	                const todayStr = formatYmd(now);
	                setSelectedDate(todayStr);
	                buildAndEnsure();
	                renderList();
	            });
	        }
	    }
	    const firstDay = new Date(year, month, 1);
	    // Compute offset based on configured week start
	    const weekStartName = (window.REMINDERS_CAL_START||'sunday').toLowerCase();
	    const nameToIdx = {sunday:0,monday:1,tuesday:2,wednesday:3,thursday:4,friday:5,saturday:6};
	    let startIdx = nameToIdx[weekStartName]; if(startIdx===undefined) startIdx=0;
	    // firstDay.getDay() returns 0=Sunday..6=Saturday; we want number of blanks before first day in custom week
	    let gap = firstDay.getDay() - startIdx; if(gap < 0) gap += 7;
	    for (let i = 0; i < gap; i++) {
	        const empty = document.createElement('div');
	        empty.className = 'calendar-day-empty';
	        cal.appendChild(empty);
	    }
	    const days = new Date(year, month + 1, 0).getDate();
	    for (let d = 1; d <= days; d++) {
	        const dateStr = year + '-' + String(month + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0');
	        const btn = document.createElement('button');
	        btn.type = 'button';
	        const isToday = dateStr === formatYmd(today);
	        const isSel = dateStr === selected;
	        btn.className = 'calendar-day relative p-2 rounded border text-left focus:outline-none focus:ring-2 focus:ring-blue-400 hover:bg-blue-50 dark:hover:bg-slate-700 ' +
	            (isSel ? 'bg-blue-50 dark:bg-slate-700 border-blue-500' : '') +
	            (highlightedReminderRange && dateStr >= highlightedReminderRange.start && dateStr <= highlightedReminderRange.end ? ' ring-2 ring-orange-400' : '');
	        btn.setAttribute('aria-pressed', isSel ? 'true' : 'false');
	        btn.innerHTML = '<div class="font-semibold ' + (isToday ? 'text-blue-600' : '') + '">' + d + '</div>';
	        const monthKey = year + '-' + String(month + 1).padStart(2, '0');
	        const cache = monthCache[monthKey] || {};
	        const count = (cache.counts || {})[dateStr];
	        if (count) {
	            const badge = document.createElement('span');
	            badge.className = 'day-count-badge absolute -top-1 -right-1 inline-flex items-center justify-center w-4 h-4 text-[9px] rounded-full bg-blue-600 text-white';
	            badge.textContent = count;
	            btn.appendChild(badge);
	            const cats = (cache.categories_counts || {})[dateStr];
	            if (cats) {
	                const wrap = document.createElement('div');
	                wrap.className = 'absolute left-1 bottom-1 flex gap-0.5 cat-dots';
	                Object.entries(cats).slice(0, 5).forEach(([k, _v]) => {
	                    if (k === '_uncategorized') return;
	                    const dot = document.createElement('span');
	                    dot.className = 'w-1.5 h-1.5 rounded-full rem-cat-dot-' + k;
	                    wrap.appendChild(dot);
	                });
	                btn.appendChild(wrap);
	            }
	        }
	        btn.setAttribute('data-cal-date', dateStr);
	        btn.addEventListener('click', () => {
	            highlightedReminderRange = null; // clear reminder highlight when clicking a day
	            setSelectedDate(dateStr);
	            renderList();
	            renderCalendar();
	        });
	        if(window.CALENDAR_ONLY){
	            btn.addEventListener('dragover', (ev)=>{ ev.preventDefault(); });
	            btn.addEventListener('drop', async (ev)=>{
	                ev.preventDefault();
	                const idRaw = ev.dataTransfer ? ev.dataTransfer.getData('text/plain') : '';
	                if(!idRaw) return;
	                const id = parseInt(idRaw, 10);
	                if(!id) return;
	                const srcDate = ev.dataTransfer.getData('application/x-reminder-date') || '';
	                const srcEndDate = ev.dataTransfer.getData('application/x-reminder-end-date') || '';
	                const creator = localStorage.getItem('username')||'';
	                const payload = { creator, date: dateStr, start_date: dateStr };
	                if(srcDate && srcEndDate){
	                    try{
	                        const s = new Date(srcDate + 'T00:00:00');
	                        const e = new Date(srcEndDate + 'T00:00:00');
	                        const n = new Date(dateStr + 'T00:00:00');
	                        const diffDays = Math.round((n - s) / 86400000);
	                        const shifted = new Date(e.getTime() + diffDays * 86400000);
							// Use local date parts to avoid UTC offset shifting date by -1
	                        payload.end_date = shifted.getFullYear()+'-'+String(shifted.getMonth()+1).padStart(2,'0')+'-'+String(shifted.getDate()).padStart(2,'0');
	                    }catch(_e){ }
	                }
	                const res = await window.remindersApi.update(id, payload);
	                if(res && res.ok && res.reminder){
	                    Object.values(monthCache).forEach(mc=>{ mc.reminders = (mc.reminders||[]).filter(x=>x.id!==id); });
	                    await fetchMonth(res.reminder.date, true);
	                    setSelectedDate(dateStr);
	                    renderCalendar();
	                    renderList();
	                    updateCalendarBadges();
	                    toast('Reminder moved','success');
	                }else{
	                    toast((res&&res.error)||'Move failed','error');
	                }
	            });
	        }
	        cal.appendChild(btn);
	    }
	}
	function updateCalendarBadges(){ // ensure counts are in sync for currently loaded months
	Object.keys(monthCache).forEach(recalcMonth); renderCalendar(); }
	let activeCategory = 'ALL';
	function renderList(){ const dateStr=getSelectedDate(); const dObj=new Date(dateStr+'T00:00:00'); const mkey=dObj.getFullYear()+'-'+String(dObj.getMonth()+1).padStart(2,'0'); const cache=monthCache[mkey]; if(!cache){ listWrap.innerHTML='<div class="text-xs text-gray-400">Loading...</div>'; fetchMonth(dateStr).then(()=>{ renderCalendar(); renderList(); renderUpcomingTimeline(); }); return;} let baseItems=[]; // Unfiltered items for current scope
	// Filter baseItems to non-recurring generated occurrences (skip those with recurring_id)
	if(currentScope==='day'){
		baseItems=cache.reminders.filter(r=>{
			if(r.recurring_id) return false;
			const sd = r.start_date || r.date;
			const ed = r.end_date || sd;
			return dateStr >= sd && dateStr <= ed;
		});
	} else if(currentScope==='week'){
		const base=dObj; const mon=new Date(base.getFullYear(), base.getMonth(), base.getDate()-((base.getDay()+6)%7)); const end=new Date(mon.getFullYear(), mon.getMonth(), mon.getDate()+6);
		baseItems=cache.reminders.filter(r=>{
			if(r.recurring_id) return false;
			// show if reminder's range overlaps the week
			const sd = new Date((r.start_date || r.date) + 'T00:00:00');
			const ed = r.end_date ? new Date(r.end_date + 'T00:00:00') : sd;
			return sd <= end && ed >= mon;
		});
	} else {
		baseItems=cache.reminders.filter(r=>!r.recurring_id);
	}
	// If activeCategory no longer present in this scope, reset to ALL
	const rulesCats = (cache.recurring_rules||[]).map(rr=>rr.category).filter(Boolean);
	if(activeCategory!=='ALL' && !baseItems.some(r=>r.category===activeCategory) && !rulesCats.includes(activeCategory)) activeCategory='ALL';

	function formatRepeat(n, unit, endDate){ const label = (n||1)===1? unit : unit+'s'; return 'Repeats every '+(n||1)+' '+label+(endDate? (' till '+endDate):''); }
	function fmtTime(val){ if(!val) return ''; if(window.REMINDERS_TIME_FORMAT==='24h') return val; const [h,m]=val.split(':'); let hh=parseInt(h,10); const ap=hh>=12?'PM':'AM'; hh = (hh%12)||12; return hh+':'+m+' '+ap; }

	// Build compressed list = recurring rules intersecting scope + singles
	const out=[];
	const addRule = (rr)=>{ out.push({isRule:true, id: rr.id, title: rr.title, description: rr.description, creator: rr.creator, time: rr.time, category: rr.category, interval: rr.interval, unit: rr.unit, end_date: rr.end_date}); };
	if(cache.recurring_rules && cache.recurring_rules.length){
		const within = (ds)=>{ if(currentScope==='day') return ds===dateStr; if(currentScope==='week'){ const base=dObj; const mon=new Date(base.getFullYear(), base.getMonth(), base.getDate()-((base.getDay()+6)%7)); const end=new Date(mon.getFullYear(), mon.getMonth(), mon.getDate()+6); const rd=new Date(ds); return rd>=mon && rd<=end; } return true; };
		for(const rr of cache.recurring_rules){ if(activeCategory!=='ALL' && rr.category!==activeCategory) continue; if((rr.dates||[]).some(within)) addRule(rr); }
	}
	let singles = (activeCategory==='ALL'? baseItems : baseItems.filter(r=> r.category===activeCategory));
	if(window.CALENDAR_ONLY && currentSearchTerm){
		const q = currentSearchTerm.toLowerCase();
		singles = singles.filter(r => (r.title||'').toLowerCase().includes(q) || (r.description||'').toLowerCase().includes(q) || (r.creator||'').toLowerCase().includes(q));
	}
	// Separate active from completed items (completed rendered below with strikethrough)
	const activeSingles = singles.filter(r => !r.completed_at);
	const completedSingles = window.CALENDAR_ONLY ? singles.filter(r => r.completed_at) : [];
	out.push(...activeSingles);

	// Render compressed list
	listWrap.innerHTML='';
	if(!out.length && !completedSingles.length){
		listWrap.innerHTML='<div class="text-gray-500">No reminders</div>';
		updateBulkUI();
	} else {
		const currentUser=localStorage.getItem('username')||''; const adminName=window.HomeHubAdmin.names[0]; const isAdmin = [adminName,'Administrator','admin'].includes(currentUser);
		if(out.length){
		out.sort((a,b)=>{ if(a.isRule && !b.isRule) return -1; if(!a.isRule && b.isRule) return 1; if(a.isRule && b.isRule) return (a.title||'').localeCompare(b.title||''); return (a.date||'').localeCompare(b.date||'') || ((a.time||'~').localeCompare(b.time||'~')) || ((a.id||0)-(b.id||0)); });
		out.forEach(r=>{
			if(r.isRule){ const row=document.createElement('div'); row.className='group p-2 rounded border bg-white dark:bg-slate-800'; const meta = (r.time? ('at '+escapeHtml(fmtTime(r.time))+' · ') : '')+escapeHtml(r.creator||''); const canEdit = (function(){ const currentUser=localStorage.getItem('username')||''; const adminName=window.HomeHubAdmin.names[0]; const isAdmin = [adminName,'Administrator','admin'].includes(currentUser); return isAdmin || (r.creator && r.creator===currentUser); })(); const catClass = r.category?('rem-cat-dot-'+r.category):''; const dot = `<span class="inline-block w-2.5 h-2.5 rounded-full mr-1 flex-shrink-0 ${catClass||'bg-gray-400'}"></span>`; row.innerHTML = `<div class="font-semibold flex items-center">${dot}${escapeHtml(r.title)}<span class="ml-2 text-[11px] px-1 py-0.5 rounded border bg-white dark:bg-slate-800 text-gray-600">Recurring</span>${canEdit?`<button type="button" data-edit-rule="${r.id}" class="ml-2 text-[11px] px-1 py-0.5 rounded border bg-white dark:bg-slate-800 hover:bg-blue-50 dark:hover:bg-slate-600" aria-label="Edit recurring"><i class='fa-solid fa-pen' aria-hidden='true'></i></button>`:''}${canEdit?`<button type="button" data-delete-rule="${r.id}" class="ml-1 text-[11px] px-1 py-0.5 rounded border bg-white dark:bg-slate-800 hover:bg-red-50 dark:hover:bg-red-900/30" aria-label="Delete recurring"><i class='fa-solid fa-trash-can' aria-hidden='true'></i></button>`:''}</div><div class="text-xs text-gray-500">${escapeHtml(formatRepeat(r.interval, r.unit, r.end_date))}${meta? ' · '+meta:''}${r.category? ' · '+escapeHtml(r.category):''}</div>`; listWrap.appendChild(row); }
				else {
					const canEdit = isAdmin || (r.creator && r.creator===currentUser);
					const row=document.createElement('div');
					row.className='group flex items-start gap-2 p-2 rounded border hover:bg-gray-50 dark:hover:bg-slate-700';
					row.setAttribute('data-reminder-row','');
					row.setAttribute('data-id', r.id);
					row.setAttribute('data-date', r.date);
					const catClass = r.category?('rem-cat-dot-'+r.category):'';
					const dot = `<span class="inline-block w-2.5 h-2.5 rounded-full mr-1 flex-shrink-0 ${catClass||'bg-gray-400'}"></span>`;
					const timeFrag = r.time?` <span class=\"ml-1 text-[10px] text-blue-600 dark:text-blue-300\">${fmtTime(r.time)}</span>`:'';
					const endDateFrag = r.end_date ? ` → ${r.end_date}` : '';
					const endTimeFrag = r.end_time ? ` ${fmtTime(r.end_time)}` : '';
					const allDayFrag = r.all_day ? ' · all-day' : '';
					const meta = `${r.date}${timeFrag}${endDateFrag}${endTimeFrag}${allDayFrag} · ${escapeHtml(r.creator||'')}${r.category?' · '+escapeHtml(r.category):''}`;
					row.innerHTML=`${canEdit?`<label class=\"mt-1\"><input type=\"checkbox\" class=\"reminderChk\" value=\"${r.id}\" aria-label=\"Select reminder\"></label>`:''}
						<div class=\"flex-1 ${canEdit?'cursor-pointer':''}\" data-edit>
							<div class=\"font-semibold flex items-center\">${dot}${escapeHtml(r.title)}${canEdit?`<button type=\"button\" class=\"ml-2 text-[11px] px-1 py-0.5 rounded border bg-white dark:bg-slate-800 hover:bg-blue-50 dark:hover:bg-slate-600 editBtn hidden group-hover:inline\" aria-label=\"Edit\"><i class='fa-solid fa-pen' aria-hidden='true'></i></button>`:''}</div>
							<div class=\"text-xs text-gray-500 dark:text-gray-400\">${meta}</div>
							${r.description?`<div class=\"text-xs text-gray-600 dark:text-gray-300 whitespace-pre-wrap mt-1\">${escapeHtml(r.description)}</div>`:''}
						</div>
						${(window.CALENDAR_ONLY && canEdit)?`<div class=\"flex flex-col gap-1\"><button type=\"button\" data-done=\"${r.id}\" class=\"text-[11px] px-2 py-1 rounded border bg-white hover:bg-green-50\" title=\"Mark as done\" aria-label=\"Mark as done\"><i class='fa-solid fa-check' aria-hidden='true'></i></button></div>`:''}${canEdit?`<button type=\"button\" class=\"opacity-0 group-hover:opacity-100 transition text-xs px-2 py-1 rounded border bg-white dark:bg-slate-800 hover:bg-red-50 dark:hover:bg-red-900/30 deleteOne\" aria-label=\"Delete\" title=\"Move to Trash\"><i class='fa-solid fa-trash-can' aria-hidden='true'></i></button>`:''}`;

					if(window.CALENDAR_ONLY && canEdit){
						row.setAttribute('draggable', 'true');
						row.setAttribute('data-drag-reminder-id', String(r.id));
						row.setAttribute('data-drag-reminder-date', r.date || '');
						row.setAttribute('data-drag-reminder-end-date', r.end_date || '');
					}
					listWrap.appendChild(row);
					// Highlight date range on card click (calendar-only)
					if(window.CALENDAR_ONLY){
						row.addEventListener('click', (ev)=>{
							if(ev.target && ev.target.closest && ev.target.closest('button')) return;
							if(ev.target && ev.target.closest && ev.target.closest('label')) return;
							const newRange = { start: r.start_date || r.date, end: r.end_date || r.start_date || r.date };
							if(highlightedReminderRange && highlightedReminderRange.start === newRange.start && highlightedReminderRange.end === newRange.end){
								highlightedReminderRange = null;
							} else {
								highlightedReminderRange = newRange;
							}
							renderCalendar();
						});
					}
					// Bind single-item edit when allowed (delete handled by delegated listener)
					if(canEdit){
						const editTarget = row.querySelector('[data-edit]');
						const editBtn = row.querySelector('.editBtn');
						if(editTarget){ editTarget.addEventListener('dblclick',()=> openForm('edit', r.date, r)); }
						if(editBtn){ editBtn.addEventListener('click',()=> openForm('edit', r.date, r)); }
					}
				}
		});
		}
		// Completed items section at the bottom with strikethrough (calendar-only)
		if(completedSingles.length){
			const divider = document.createElement('div');
			divider.className = 'text-[10px] uppercase tracking-wide text-gray-500 mt-3 mb-1 px-1 flex items-center justify-between';
			divider.innerHTML = `<span>Completed (${completedSingles.length})</span><button type="button" data-toggle-completed class="normal-case px-2 py-0.5 rounded border bg-white text-[10px]">${(!completedCollapsed) ? 'Hide' : 'Show'}</button>`;
			listWrap.appendChild(divider);
			if(!completedCollapsed){
			completedSingles.forEach(r=>{
				const row2 = document.createElement('div');
				row2.className = 'flex items-start gap-2 p-2 rounded border opacity-50 text-sm bg-gray-50 dark:bg-slate-700/50';
				row2.setAttribute('data-reminder-row', '');
				row2.setAttribute('data-id', String(r.id));
				row2.setAttribute('data-date', r.date || '');
				const catClass2 = r.category?'rem-cat-dot-'+r.category:'';
				const dot2 = `<span class="inline-block w-2.5 h-2.5 rounded-full mr-1 flex-shrink-0 ${catClass2||'bg-gray-400'}"></span>`;
				row2.innerHTML = `<div class="flex-1"><div class="line-through text-gray-400 flex items-center">${dot2}${escapeHtml(r.title||'')} <span class="ml-2 text-[10px] px-1 py-0.5 rounded bg-green-100 text-green-700 font-medium"><i class='fa-solid fa-check' aria-hidden='true'></i> Done</span></div>${r.date?`<div class="text-xs text-gray-400">${r.date}${r.time?' '+r.time:''}</div>`:''}</div><div class="flex items-center gap-1"><button type="button" data-undo-done="${r.id}" class="text-[11px] px-2 py-1 rounded border bg-white dark:bg-slate-800 hover:bg-blue-50" title="Mark as active">Undo</button><button type="button" class="deleteOne text-[11px] px-2 py-1 rounded border bg-white dark:bg-slate-800 hover:bg-red-50 dark:hover:bg-red-900/30" title="Move to Trash" aria-label="Move to Trash"><i class='fa-solid fa-trash-can' aria-hidden='true'></i></button></div>`;
				listWrap.appendChild(row2);
			});
			}
		}
		// bind rule edit/delete
		bindRuleEdits();
		listWrap.querySelectorAll('[data-delete-rule]')?.forEach(btn=>{
			btn.addEventListener('click', async ()=>{
				const rid=parseInt(btn.getAttribute('data-delete-rule')); const creator=localStorage.getItem('username')||'';
				if(!confirm('Delete this recurring reminder and all its future dates?')) return;
				const res = await window.remindersApi.deleteRule(rid, creator);
				if(res && res.ok){ const dateStr=getSelectedDate(); await fetchMonth(dateStr, true); const d=new Date(dateStr+'T00:00:00'); const key=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'); recalcMonth(key); updateCalendarBadges(); renderList(); toast('Recurring rule deleted','success'); }
				else { toast((res&&res.error)||'Delete failed','error'); }
			});
		});
		if(window.CALENDAR_ONLY){
			listWrap.querySelectorAll('[data-toggle-completed]')?.forEach(btn=>{
				btn.addEventListener('click', ()=>{
					completedCollapsed = !completedCollapsed;
					renderList();
				});
			});
			listWrap.querySelectorAll('[data-done]')?.forEach(btn=>{
				btn.addEventListener('click', async ()=>{
					const id = parseInt(btn.getAttribute('data-done'));
					const creator = localStorage.getItem('username')||'';
					const res = await window.remindersApi.markDone(id, creator);
					if(res && res.ok){
						highlightedReminderRange = null;
						// Mark done locally so strikethrough shows immediately
						Object.values(monthCache).forEach(mc=>{
							(mc.reminders||[]).forEach(r=>{ if(r.id===id) r.completed_at = new Date().toISOString(); });
						});
						renderList();
						renderCalendar();
						updateCalendarBadges();
						toast('Marked done','success');
					}else{
						toast((res&&res.error)||'Action failed','error');
					}
				});
			});
			listWrap.querySelectorAll('[data-undo-done]')?.forEach(btn=>{
				btn.addEventListener('click', async ()=>{
					const id = parseInt(btn.getAttribute('data-undo-done'));
					const creator = localStorage.getItem('username')||'';
					const res = await window.remindersApi.undoDone(id, creator);
					if(res && res.ok){
						Object.values(monthCache).forEach(mc=>{
							(mc.reminders||[]).forEach(r=>{ if(r.id===id) r.completed_at = null; });
						});
						renderList();
						renderCalendar();
						updateCalendarBadges();
						toast('Marked active','success');
					}else{
						toast((res&&res.error)||'Action failed','error');
					}
				});
			});
			listWrap.querySelectorAll('[data-snooze]')?.forEach(btn=>{
				btn.addEventListener('click', async ()=>{
					const id = parseInt(btn.getAttribute('data-snooze'));
					const days = btn.getAttribute('data-days');
					const minutes = btn.getAttribute('data-minutes');
					const creator = localStorage.getItem('username')||'';
					const res = await window.remindersApi.snooze(id, creator, { days, minutes });
					if(res && res.ok && res.reminder){
						highlightedReminderRange = null;
						Object.values(monthCache).forEach(mc=>{ mc.reminders = (mc.reminders||[]).filter(x=>x.id!==id); });
						await fetchMonth(res.reminder.date, true);
						renderCalendar();
						renderList();
						updateCalendarBadges();
						toast('Snoozed','success');
					}else{
						toast((res&&res.error)||'Snooze failed','error');
					}
				});
			});
		}
			// Initialize bulk bar visibility on render
			updateBulkUI();
	}

	if(currentScope==='month'){ const prev=new Date(dObj.getFullYear(), dObj.getMonth()-1,1); const next=new Date(dObj.getFullYear(), dObj.getMonth()+1,1); fetchMonth(formatYmd(prev)); fetchMonth(formatYmd(next)); }
	updateCategorySummary(baseItems); renderUpcomingTimeline(); }
	// Category summary pills (buttons) click handler using data-cat attribute
	const catSummaryEl=document.getElementById('reminderCategorySummary');
	if(catSummaryEl && !catSummaryEl.dataset.clickReady){
		catSummaryEl.dataset.clickReady='1';
		catSummaryEl.addEventListener('click', e=>{ const b=e.target.closest('button[data-cat]'); if(!b) return; const val=b.getAttribute('data-cat'); activeCategory = (val===activeCategory)?'ALL':val; renderList(); });
	}
	function updateCategorySummary(scopeList){
		const wrap = document.getElementById('reminderCategorySummary');
		if (!scopeList.length) {
			// still consider recurring rules in scope
			const mkey=(new Date(getSelectedDate())).getFullYear()+'-'+String((new Date(getSelectedDate())).getMonth()+1).padStart(2,'0');
			const cache=monthCache[mkey]||{}; const rules=cache.recurring_rules||[];
			if(!rules.length){ wrap.innerHTML=''; return; }
		}
		const counts = {};
		scopeList.forEach(r => { if (r.category) counts[r.category] = (counts[r.category] || 0) + 1; });
		// include recurring rules in-scope
		const dateStr=getSelectedDate(); const dObj=new Date(dateStr+'T00:00:00');
		const within = (ds)=>{ if(currentScope==='day') return ds===dateStr; if(currentScope==='week'){ const base=dObj; const mon=new Date(base.getFullYear(), base.getMonth(), base.getDate()-((base.getDay()+6)%7)); const end=new Date(mon.getFullYear(), mon.getMonth(), mon.getDate()+6); const rd=new Date(ds); return rd>=mon && rd<=end; } return true; };
		const mkey=dObj.getFullYear()+'-'+String(dObj.getMonth()+1).padStart(2,'0'); const cache=monthCache[mkey]||{};
		(cache.recurring_rules||[]).forEach(rr=>{ if(!rr.category) return; const inScope = (rr.dates||[]).filter(within).length; if(inScope){ counts[rr.category]=(counts[rr.category]||0)+inScope; } });
		const ordered = Object.entries(counts).sort((a, b) => b[1] - a[1]);
		// Clear previous content
		wrap.innerHTML = '';
		// Create "All" button (count includes singles + recurring-in-scope)
		const allBtn = document.createElement('button');
		allBtn.type = "button";
		allBtn.setAttribute('data-cat', 'ALL');
		allBtn.className = 'rem-cat-pill inline-flex items-center gap-1 px-2 py-0.5 rounded border bg-white text-xs hover:bg-blue-50' +
			(activeCategory === 'ALL' ? ' ring-1 ring-blue-500 bg-blue-50' : '');
		const totalCount = Object.values(counts).reduce((a,b)=>a+b, 0);
		allBtn.textContent = `All: ${totalCount}`;
		wrap.appendChild(allBtn);
		// Build key→label lookup from reminderCategoriesData
		let catDataEl = document.getElementById('reminderCategoriesData');
		let catLabelMap = {};
		try {
			const cats = JSON.parse(catDataEl?.textContent || '[]');
			cats.forEach(c => { if(c && c.key) catLabelMap[c.key] = c.label || c.key; });
		} catch(e) {}
		// Create category buttons
		ordered.forEach(([k, v]) => {
			const act = activeCategory === k;
			const btn = document.createElement('button');
			btn.type = "button";
			btn.setAttribute('data-cat', k);
			btn.className = 'rem-cat-pill inline-flex items-center gap-1 px-2 py-0.5 rounded border bg-white text-xs hover:bg-blue-50' +
				(act ? ' ring-1 ring-blue-500 bg-blue-50' : '');
			// Create dot span
			const dot = document.createElement('span');
			dot.className = `w-2 h-2 rounded-full rem-cat-dot-${k}`;
			btn.appendChild(dot);
			// Add category label and count
			const label = document.createElement('span');
			label.innerHTML = `${escapeHtml(catLabelMap[k] || k)}: ${v}`;
			btn.appendChild(label);
			wrap.appendChild(btn);
		});
	}
	function paintItems(list){ const currentUser=localStorage.getItem('username')||''; const adminName=window.HomeHubAdmin.names[0]; const isAdmin = [adminName,'Administrator','admin'].includes(currentUser); const selections=new Set(); listWrap.innerHTML=''; bulkBar.classList.add('hidden'); bulkCount.textContent='0'; if(!list.length){ listWrap.innerHTML='<div class="text-gray-500">No reminders</div>'; return;} list.sort((a,b)=> (a.date.localeCompare(b.date)) || ((a.time||'~').localeCompare(b.time||'~')) || (a.id-b.id));
	function fmtTime(val){ if(!val) return ''; if(window.REMINDERS_TIME_FORMAT==='24h') return val; const [h,m]=val.split(':'); let hh=parseInt(h,10); const ap=hh>=12?'PM':'AM'; hh = (hh%12)||12; return hh+':'+m+' '+ap; }
	list.forEach(r=>{ const canEdit = isAdmin || (r.creator && r.creator===currentUser); const row=document.createElement('div'); row.className='group flex items-start gap-2 p-2 rounded border hover:bg-gray-50 dark:hover:bg-slate-700'; const catClass = r.category?('rem-cat-dot-'+r.category):''; const dot = `<span class="inline-block w-2.5 h-2.5 rounded-full mr-1 flex-shrink-0 ${catClass||'bg-gray-400'}"></span>`; const timeFrag = r.time?` <span class=\"ml-1 text-[10px] text-blue-600 dark:text-blue-300\">${fmtTime(r.time)}</span>`:''; const meta = `${r.date}${timeFrag} · ${escapeHtml(r.creator||'')}${r.category?' · '+escapeHtml(r.category):''}`; row.innerHTML=`${canEdit?`<label class=\"mt-1\"><input type=\"checkbox\" class=\"reminderChk\" value=\"${r.id}\" aria-label=\"Select reminder\"></label>`:''}<div class=\"flex-1 ${canEdit?'cursor-pointer':''}\" data-edit><div class=\"font-semibold flex items-center\">${dot}${escapeHtml(r.title)}${canEdit?`<button type=\"button\" class=\"ml-2 text-[11px] px-1 py-0.5 rounded border bg-white dark:bg-slate-800 hover:bg-blue-50 dark:hover:bg-slate-600 editBtn hidden group-hover:inline\" aria-label=\"Edit\"><i class='fa-solid fa-pen' aria-hidden='true'></i></button>`:''}</div><div class=\"text-xs text-gray-500 dark:text-gray-400\">${meta}</div>${r.description?`<div class=\"text-xs text-gray-600 dark:text-gray-300 whitespace-pre-wrap mt-1\">${escapeHtml(r.description)}</div>`:''}</div>${canEdit?`<button type=\"button\" class=\"opacity-0 group-hover:opacity-100 transition text-xs px-2 py-1 rounded border bg-white dark:bg-slate-800 hover:bg-red-50 dark:hover:bg-red-900/30 deleteOne\" aria-label=\"Delete\"><i class='fa-solid fa-trash-can' aria-hidden='true'></i></button>`:''}`; if(canEdit){ const editTarget=row.querySelector('[data-edit]'); editTarget.addEventListener('dblclick',()=> openForm('edit', r.date, r)); row.querySelector('.editBtn').addEventListener('click',()=> openForm('edit', r.date, r)); row.querySelector('.deleteOne').addEventListener('click',()=>{ const creator=currentUser; const snapshot=JSON.parse(JSON.stringify(r)); window.remindersApi.removeMany([r.id],creator).then(resp=>{ if(resp.ok){ Object.values(monthCache).forEach(mc=>{ mc.reminders=mc.reminders.filter(x=>x.id!==r.id); }); toast('Deleted (undo available)','success'); const host=document.getElementById('toastHost'); if(host){ const undo=document.createElement('div'); undo.className='pointer-events-auto px-3 py-2 rounded shadow text-sm bg-blue-600 text-white cursor-pointer'; undo.textContent='Undo delete'; undo.onclick=()=>{ const d=new Date(snapshot.date); const key=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'); if(!monthCache[key]) monthCache[key]={reminders:[],counts:{},categories_counts:{}}; monthCache[key].reminders.push(snapshot); renderList(); updateCalendarBadges(); undo.remove(); }; host.appendChild(undo); setTimeout(()=>undo.remove(),6000);} renderList(); updateCalendarBadges(); } else toast('Delete failed','error');}); }); }
	listWrap.appendChild(row); }); function updateBulk(){ bulkCount.textContent=selections.size; bulkIds.value=Array.from(selections).join(','); bulkBar.classList.toggle('hidden', !selections.size); } listWrap.querySelectorAll('.reminderChk').forEach(cb=>cb.addEventListener('change',()=>{ const id=parseInt(cb.value); if(cb.checked) selections.add(id); else selections.delete(id); updateBulk(); })); document.getElementById('bulkClearSel').onclick=()=>{ selections.clear(); listWrap.querySelectorAll('.reminderChk').forEach(c=>c.checked=false); updateBulk(); }; const bulkForm=document.getElementById('bulkDeleteForm'); if(bulkForm && !bulkForm.dataset.ajax){ bulkForm.dataset.ajax='1'; bulkForm.addEventListener('submit', e=>{ e.preventDefault(); const ids=Array.from(selections); if(!ids.length) return; if(!confirm('Move '+ids.length+' selected reminder'+(ids.length===1?'':'s')+' to the trash?')) return; const creator=localStorage.getItem('username')||''; window.remindersApi.removeMany(ids,creator).then(resp=>{ if(!resp.ok){ toast('Delete failed','error'); return;} Object.values(monthCache).forEach(mc=>{ mc.reminders=mc.reminders.filter(r=>!ids.includes(r.id)); }); toast('Deleted '+ids.length,'success'); selections.clear(); bulkCount.textContent='0'; bulkBar.classList.add('hidden'); renderList(); updateCalendarBadges(); }); }); }
	}

	// Add edit handling for recurring rule cards
	function bindRuleEdits(){
		listWrap.querySelectorAll('[data-edit-rule]')?.forEach(btn=>{
			btn.addEventListener('click', ()=>{
				const rid = parseInt(btn.getAttribute('data-edit-rule'));
				const mkey=display.getFullYear()+'-'+String(display.getMonth()+1).padStart(2,'0');
				const cache=monthCache[mkey]||{}; const rr=(cache.recurring_rules||[]).find(x=>x.id===rid);
				if(!rr) return;
				// Populate form for rule editing
				inlineForm.reset(); editingId=null; window.__editingRuleId = rid;
				inlineForm.querySelector('[name=title]').value = rr.title||'';
				inlineForm.querySelector('[name=description]').value = rr.description||'';
				if(rr.time) inlineForm.querySelector('[name=time]').value = rr.time;
				inlineForm.querySelector('[name=end_date]').value = '';
				inlineForm.querySelector('[name=end_time]').value = '';
				if(categorySelect && rr.category) categorySelect.value = rr.category;
				if(recChk){ recChk.checked = true; }
				inlineForm.querySelector('[name=rec_interval]').value = rr.interval||1;
				inlineForm.querySelector('[name=rec_unit]').value = rr.unit||'day';
				if(rr.end_date) inlineForm.querySelector('[name=rec_end_date]').value = rr.end_date;
				// set date field to rule start_date so user can change anchor
				inlineForm.querySelector('[name=date]').value = rr.start_date || getSelectedDate();
				toggleRecurringFields();
				inlineWrap.classList.remove('hidden');
			});
		});
	}
	function onMonthChange(delta){ 
		const oldMonth = display.getMonth(); 
		display = new Date(display.getFullYear(), display.getMonth()+delta, 1); 
		buildAndEnsure(); 
		// When month changes, show entire month and switch to first day of that month
		const firstDayOfMonth = display.getFullYear()+'-'+String(display.getMonth()+1).padStart(2,'0')+'-01';
		setSelectedDate(firstDayOfMonth);
		// Reset scope to month to show full month list
		currentScope='month'; 
		localStorage.setItem('remindersScope', currentScope);
		scopeBar.querySelectorAll('.scopeBtn').forEach(btn=>{ 
			const act=btn.getAttribute('data-scope')===currentScope; 
			btn.className='px-2 py-0.5 text-xs rounded border scopeBtn '+(act?'border-blue-500 text-blue-600 bg-blue-50':'border-gray-300 bg-white text-gray-600 hover:bg-gray-100'); 
		}); 
		renderList(); 
	}

	// Fix: when reloading page after viewing another month, ensure selected date's month matches display month cache and list uses current month not stale prior selection
	function normalizeAfterInitialLoad(){
		const selected = getSelectedDate();
		const selDate = new Date(selected+'T00:00:00');
		if(selDate.getMonth() !== display.getMonth() || selDate.getFullYear() !== display.getFullYear()){
			// Reset selected date to today's date within current display month
			const today=new Date();
			if(today.getMonth()===display.getMonth() && today.getFullYear()===display.getFullYear()){
				setSelectedDate(formatYmd(today));
			}else{
				// fallback to first day of display month
				const firstDayOfMonth = display.getFullYear()+'-'+String(display.getMonth()+1).padStart(2,'0')+'-01';
				setSelectedDate(firstDayOfMonth);
			}
		}
	}
	prevBtn.addEventListener('click', ()=> onMonthChange(-1));
	nextBtn.addEventListener('click', ()=> onMonthChange(1));
	function buildAndEnsure(){ const mk=display.getFullYear()+'-'+String(display.getMonth()+1).padStart(2,'0'); const have = monthCache[mk]; if(!have || !have.recurring_rules){ fetchMonth(mk+'-01', true).then(()=>{ renderCalendar(); renderList(); }); } renderCalendar(); }
	if(window.CALENDAR_ONLY && searchInput && !searchInput.dataset.bound){
		searchInput.dataset.bound = '1';
		let searchTimer = null;
		searchInput.addEventListener('input', ()=>{
			if(searchTimer) clearTimeout(searchTimer);
			searchTimer = setTimeout(()=>{
				currentSearchTerm = (searchInput.value || '').trim();
				renderList();
			}, 200);
		});
	}
	// Trash panel
	const trashToggleBtn = document.getElementById('trashToggleBtn');
	const trashPanel = document.getElementById('trashPanel');
	let trashVisible = false;
	async function renderTrashPanel(){
		if(!trashPanel) return;
		trashPanel.innerHTML = '<div class="text-xs text-gray-400 p-2">Loading trash…</div>';
		trashPanel.classList.remove('hidden');
		const res = await window.remindersApi.trash();
		if(!res || !res.ok){ trashPanel.innerHTML='<div class="text-xs text-red-500 p-2">Failed to load trash.</div>'; return; }
		const items = res.reminders || [];
		if(!items.length){ trashPanel.innerHTML='<div class="text-xs text-gray-400 p-2">Recycle bin is empty. Items auto-purge after 7 days.</div>'; return; }
		const creator = localStorage.getItem('username')||'';
		trashPanel.innerHTML = `<div class="text-xs font-semibold text-gray-500 mb-2">Recycle Bin — items auto-purge after 7 days</div>`;
		items.forEach(r=>{
			const row = document.createElement('div');
			row.className = 'flex items-center gap-2 p-1.5 border rounded mb-1 text-sm bg-gray-50 dark:bg-slate-700/50';
			row.innerHTML = `<div class="flex-1 line-through text-gray-400">${escapeHtml(r.title||'')} <span class="text-[10px] no-underline opacity-70">${r.date||''}</span></div>
				<button type="button" data-restore="${r.id}" class="text-[11px] px-2 py-0.5 rounded border bg-white dark:bg-slate-800 hover:bg-green-50">Restore</button>`;
			trashPanel.appendChild(row);
		});
		trashPanel.querySelectorAll('[data-restore]').forEach(btn=>{
			btn.addEventListener('click', async ()=>{
				const id = parseInt(btn.getAttribute('data-restore'));
				const res2 = await window.remindersApi.restore(id, creator);
				if(res2 && res2.ok){ toast('Restored','success'); Object.keys(monthCache).forEach(k=>delete monthCache[k]); renderTrashPanel(); buildAndEnsure(); renderList(); }
				else toast((res2&&res2.error)||'Failed','error');
			});
		});
	}
	if(trashToggleBtn && !trashToggleBtn.dataset.bound){
		trashToggleBtn.dataset.bound = '1';
		trashToggleBtn.addEventListener('click', ()=>{
			trashVisible = !trashVisible;
			if(trashVisible){ renderTrashPanel(); trashToggleBtn.classList.add('bg-red-50','border-red-300'); }
			else { trashPanel && trashPanel.classList.add('hidden'); trashToggleBtn.classList.remove('bg-red-50','border-red-300'); }
		});
	}
	if(window.CALENDAR_ONLY && !listWrap.dataset.dragBound){
		listWrap.dataset.dragBound = '1';
		listWrap.addEventListener('dragstart', (ev)=>{
			const row = ev.target && ev.target.closest ? ev.target.closest('[data-drag-reminder-id]') : null;
			if(!row || !ev.dataTransfer) return;
			ev.dataTransfer.setData('text/plain', row.getAttribute('data-drag-reminder-id') || '');
			ev.dataTransfer.setData('application/x-reminder-date', row.getAttribute('data-drag-reminder-date') || '');
			ev.dataTransfer.setData('application/x-reminder-end-date', row.getAttribute('data-drag-reminder-end-date') || '');
			ev.dataTransfer.effectAllowed = 'move';
		});
	}
	function reminderOverlapsWindow(r, windowStart, windowEnd){
		const startDate = r.start_date || r.date || '';
		const endDate = r.end_date || startDate;
		if(!startDate) return false;
		const reminderStart = new Date(startDate + 'T00:00:00');
		const reminderEnd = endDate ? new Date(endDate + 'T23:59:59') : reminderStart;
		if(Number.isNaN(reminderStart.getTime()) || Number.isNaN(reminderEnd.getTime())) return false;
		return reminderStart <= windowEnd && reminderEnd >= windowStart;
	}
	function renderUpcomingTimeline(){
		if(!window.CALENDAR_ONLY || !upcomingTimeline) return;
		const today = new Date();
		const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
		const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6);
		const windowEnd = new Date(end.getFullYear(), end.getMonth(), end.getDate(), 23, 59, 59, 999);
		const all = [];
		Object.values(monthCache).forEach(mc=>{
			(mc.reminders||[]).forEach(r=>{
				if(r.recurring_id) return;
				if(reminderOverlapsWindow(r, start, windowEnd)) all.push(r);
			});
		});
		all.sort((a,b)=> (a.date||'').localeCompare(b.date||'') || ((a.time||'~').localeCompare(b.time||'~')) || ((a.id||0)-(b.id||0)));
		upcomingTimeline.innerHTML = '<div class="text-xs font-semibold mb-1">Upcoming (next 7 days)</div>';
		if(!all.length){
			upcomingTimeline.innerHTML += '<div class="text-xs text-gray-500">No upcoming reminders.</div>';
			return;
		}
		upcomingTimeline.innerHTML += all.slice(0,30).map(r=>`<div class="text-xs py-1 border-b last:border-b-0"><span class="font-medium">${escapeHtml(r.title||'')}</span> <span class="text-gray-500">${escapeHtml(r.date||'')}${r.time?(' '+escapeHtml(r.time)):''}</span></div>`).join('');
	}
	const startDate = getSelectedDate(); setSelectedDate(startDate); buildAndEnsure(); normalizeAfterInitialLoad(); renderList(); renderUpcomingTimeline();
	// No dropdown filter now
	bulkUser.value = localStorage.getItem('username')||'';
	
	// Re-render list when user switches to update edit/delete visibility
	document.addEventListener('user-switched', function() {
		bulkUser.value = localStorage.getItem('username')||'';
		renderList(); // This will recalculate canEdit for all items
	});
	}
	})();
		
