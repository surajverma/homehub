        
        (function(){
            if(window.globalToast) return;
            const hostId='toastHost';
            let toastEl = null;
            let hideTimer = null;
            let hideEndTimer = null;
            function ensure(){ let h=document.getElementById(hostId); if(!h){ h=document.createElement('div'); h.id=hostId; h.className='fixed top-4 right-4 left-4 sm:left-auto z-50 pointer-events-none flex justify-end'; h.setAttribute('role','status'); h.setAttribute('aria-live','polite'); document.body.appendChild(h);} return h; }
            function classFor(type){ return type==='error'?['bg-red-600']:(type==='success'?['bg-green-600']:(type==='info'?['bg-slate-800']:['bg-slate-800'])); }
            function hide(){
                if(!toastEl) return;
                toastEl.style.opacity='0';
                toastEl.style.pointerEvents='none';
                hideEndTimer=setTimeout(()=>{ toastEl && (toastEl.style.display='none'); }, 240);
            }
            window.globalToast=function(msg,type){ const host=ensure(); if(!toastEl){ toastEl=document.createElement('div'); host.appendChild(toastEl);} if(hideTimer){ clearTimeout(hideTimer); hideTimer=null; } if(hideEndTimer){ clearTimeout(hideEndTimer); hideEndTimer=null; }
                const isError = type==='error';
                toastEl.className='toast pointer-events-auto inline-flex items-start gap-3 max-w-md px-4 py-2.5 rounded-lg shadow-lg text-sm text-white transition-opacity duration-200 '+classFor(type).join(' ');
                toastEl.setAttribute('role', isError ? 'alert' : 'status');
                toastEl.style.display='';
                toastEl.style.opacity='1';
                toastEl.style.pointerEvents='auto';
                toastEl.textContent='';
                const text=document.createElement('span'); text.textContent=msg; toastEl.appendChild(text);
                const close=document.createElement('button'); close.type='button'; close.className='inline-flex items-center justify-center w-5 h-5 shrink-0 opacity-80 hover:opacity-100'; close.setAttribute('aria-label', t('Dismiss')); close.innerHTML='<i class="fa-solid fa-xmark" aria-hidden="true"></i>';
                close.addEventListener('click', hide); toastEl.appendChild(close);
                // Errors stay until dismissed so they can be read
                if(!isError) hideTimer=setTimeout(hide, 4000);
            };
        })();        
        (function(){
            const wrap = document.getElementById('globalFlashWrap');
            if(!wrap) return;
            const msgs = Array.from(wrap.querySelectorAll('.px-3, .flash-msg'));
            if(!msgs.length){ wrap.remove(); return; }
            const classify = (el)=>{
                const cls = el.className;
                if(/green/i.test(cls)) return 'success';
                if(/red/i.test(cls)) return 'error';
                if(/yellow|amber/i.test(cls)) return 'info';
                return 'info';
            };
            msgs.forEach((el,i)=>{
                const text = (el.textContent||'').trim();
                setTimeout(()=>{ if(window.globalToast && text) globalToast(text, classify(el)); }, i*60);
            });            
            setTimeout(()=>wrap.remove(), msgs.length*70 + 200);
        })();        
        // Forms with data-confirm ask before submitting (deletes). Capture phase runs before page handlers.
        document.addEventListener('submit', function(ev){
            const form = ev.target;
            const msg = form && form.getAttribute && form.getAttribute('data-confirm');
            if (msg && !window.confirm(msg)){
                ev.preventDefault();
                ev.stopImmediatePropagation();
            }
        }, true);
        // Ask for the admin password; resolves true once the server unlocked this session
        function requestAdminUnlock(){
            return new Promise(function(resolve){
                const modal = document.getElementById('adminPasswordModal');
                const input = document.getElementById('adminPasswordInput');
                const error = document.getElementById('adminPasswordError');
                const form = document.getElementById('adminPasswordForm');
                const cancel = document.getElementById('adminPasswordCancel');
                if (!modal || !input || !form){ resolve(false); return; }
                const close = function(ok){
                    modal.classList.add('hidden'); modal.classList.remove('flex');
                    form.onsubmit = null; cancel.onclick = null; input.value = '';
                    resolve(ok);
                };
                error.textContent = '';
                modal.classList.remove('hidden'); modal.classList.add('flex');
                input.focus();
                cancel.onclick = function(){ close(false); };
                form.onsubmit = async function(ev){
                    ev.preventDefault();
                    try{
                        const resp = await fetch('/admin/unlock', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ password: input.value })
                        });
                        const data = await resp.json().catch(function(){ return {}; });
                        if (resp.ok && data.ok){ window.HomeHubAdmin.unlocked = true; close(true); return; }
                        error.textContent = data.error || t('Could not unlock admin.');
                    }catch(e){
                        error.textContent = t('Could not reach the server.');
                    }
                    input.value = ''; input.focus();
                };
            });
        }
        function showUserSelectModal(users){
            const modal = document.getElementById('userSelectModal');
            const input = document.getElementById('userSelectInput');
            const confirmBtn = document.getElementById('userSelectConfirm');
            if (!modal || !input || !confirmBtn) return;
            input.innerHTML = '';
            users.forEach(u => {
                const opt = document.createElement('option');
                opt.value = u; opt.textContent = u; input.appendChild(opt);
            });
            modal.classList.remove('hidden');
            modal.classList.add('flex');
            confirmBtn.onclick = async function(){
                const selected = input.value;
                if (selected && window.HomeHubAdmin.needsPassword(selected)){
                    if (!(await requestAdminUnlock())) return;
                    localStorage.setItem('username', selected);
                    location.reload();
                    return;
                }
                if (selected){
                    localStorage.setItem('username', selected);
                    const switcher = document.getElementById('userSwitcher');
                    if (switcher){ switcher.value = selected; }
                    renderWelcome();
                    applyUserContext();
                }
                modal.classList.add('hidden');
                modal.classList.remove('flex');
            };
        }
        // Ensure a username is stored in localStorage; initialize after DOM is ready
        document.addEventListener('DOMContentLoaded', function(){
            const switcher = document.getElementById('userSwitcher');
            if (switcher){
                const familyEl = document.getElementById('familyData');
                let family = [];
                try { family = JSON.parse(familyEl?.textContent || '[]'); } catch(e) { family = []; }
                const adminName = window.HomeHubAdmin.names[0];
                const users = [adminName, ...family];
                switcher.innerHTML = '';
                users.forEach(u => {
                    const opt = document.createElement('option');
                    opt.value = u; opt.textContent = u;
                    switcher.appendChild(opt);
                });
                const existing = localStorage.getItem('username');
                if (existing){
                    switcher.value = existing;
                } else {
                    // Show first-run user selection modal now that DOM is ready
                    showUserSelectModal(users);
                }
                switcher.addEventListener('change', async function(){
                    const admin = window.HomeHubAdmin;
                    if (admin.needsPassword(this.value)){
                        const target = this.value;
                        const previous = localStorage.getItem('username') || '';
                        if (!(await requestAdminUnlock())){ this.value = previous; return; }
                        localStorage.setItem('username', target);
                        // Reload so every page script sees the unlocked admin
                        location.reload();
                        return;
                    }
                    if (admin.unlocked && !admin.isAdminName(this.value)){
                        admin.unlocked = false;
                        fetch('/admin/lock', { method: 'POST' }).catch(function(){});
                    }
                    localStorage.setItem('username', this.value);
                    renderWelcome();
                    applyUserContext();
                    // Notify page-level listeners to update UI without refresh
                    document.dispatchEvent(new CustomEvent('user-switched', { detail: { user: this.value }}));
                });
            }
            renderWelcome();
            applyUserContext();
            // Sidebar toggles
            const sidebar = document.getElementById('sidebar');
            const COLLAPSE_KEY = 'ui:sidebarCollapsed';
            const rootEl = document.documentElement;
            const openBtn = document.getElementById('openSidebar');
            const backdrop = document.getElementById('sidebarBackdrop');
            const isMobile = ()=> !window.matchMedia('(min-width: 768px)').matches;
            function setSidebarOpen(open){
                sidebar.classList.toggle('-translate-x-full', !open);
                backdrop?.classList.toggle('hidden', !open);
                openBtn?.setAttribute('aria-expanded', open ? 'true' : 'false');
                if (open){ document.getElementById('closeSidebar')?.focus(); }
                else if (isMobile() && sidebar.contains(document.activeElement)){ openBtn?.focus(); }
            }
            openBtn?.addEventListener('click', ()=> setSidebarOpen(true));
            document.getElementById('closeSidebar')?.addEventListener('click', ()=> setSidebarOpen(false));
            backdrop?.addEventListener('click', ()=> setSidebarOpen(false));
            document.addEventListener('keydown', (e)=>{
                if (e.key === 'Escape' && isMobile() && !sidebar.classList.contains('-translate-x-full')) setSidebarOpen(false);
            });
            document.querySelectorAll('#sidebarNav a').forEach(a=> a.addEventListener('click', ()=>{ if (isMobile()) setSidebarOpen(false); }));
            // Desktop collapse/expand
            const collapseIcon = document.getElementById('collapseIcon');
            function applyCollapsedState(collapsed){
                const isCollapsed = !!collapsed;
                if(window.matchMedia('(min-width: 768px)').matches){
                    sidebar.classList.toggle('collapsed', isCollapsed);
                }
                // Keep root hint in sync for next load (used to avoid flash)
                rootEl.classList.toggle('prefers-sidebar-collapsed', isCollapsed);
                if(collapseIcon){ collapseIcon.className = 'fa-solid '+(isCollapsed ? 'fa-angles-right' : 'fa-angles-left'); }
                const btn = document.getElementById('toggleCollapse');
                if(btn){ btn.title = isCollapsed ? t('Expand sidebar') : t('Collapse sidebar'); }
            }
            // Initialize from saved preference
            try{ const saved = localStorage.getItem(COLLAPSE_KEY); applyCollapsedState(saved==='1'||saved==='true'); }catch(e){}
            document.getElementById('toggleCollapse')?.addEventListener('click', ()=>{
                if(window.matchMedia('(min-width: 768px)').matches){
                    const willCollapse = !sidebar.classList.contains('collapsed');
                    applyCollapsedState(willCollapse);
                    try{ localStorage.setItem(COLLAPSE_KEY, willCollapse ? '1' : '0'); }catch(e){}
                }
            });
        });
        function renderWelcome(){
            const name = localStorage.getItem('username') || '';
            const d = new Date();
            const ds = d.toLocaleString(window.I18N.locale);
            const welcome = document.getElementById('welcome');
            if (welcome) welcome.textContent = name ? t('Welcome, {name}! — {date}', { name: name, date: ds }) : ds;
            const avatar = document.getElementById('userAvatar');
            if (avatar) avatar.textContent = (name || '?').trim().charAt(0).toUpperCase();
        }
        function applyUserContext(){
            const current = localStorage.getItem('username') || '';
            const adminName = window.HomeHubAdmin.names[0];
            const allowed = (creator) => (current === creator || current === adminName || current === 'Administrator' || current === 'admin');
            // hidden inputs
            document.querySelectorAll('input[name="creator"]').forEach(function(i){ i.value = current; });
            document.querySelectorAll('input[name="user"]').forEach(function(i){ i.value = current; });
            // delete button visibility where data-creator is available
            document.querySelectorAll('.delete-form').forEach(f => {
                const creator = f.getAttribute('data-creator');
                f.style.display = allowed(creator) ? '' : 'none';
            });
            // edit controls visibility where data-creator is available
            document.querySelectorAll('.edit-btn[data-creator], .editable-owner-only[data-creator]').forEach(el => {
                const creator = el.getAttribute('data-creator');
                el.style.display = allowed(creator) ? '' : 'none';
            });
            // Notice editor visibility if present
            const nf = document.getElementById('noticeForm');
            if (nf){
                const isAdmin = (current === adminName || current === 'Administrator' || current === 'admin');
                nf.style.display = isAdmin ? '' : 'none';
                const userField = nf.querySelector('input[name="user"]');
                if (userField) userField.value = current;
            }
        }
        // renderWelcome/applyUserContext are invoked from DOMContentLoaded
    
