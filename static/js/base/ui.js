// Shared dialogs and toasts for every page. Loaded in <head>, so the elements are created on first use.
//   confirmDialog({ title, message, details, confirmText, cancelText, danger }) -> Promise<boolean>
//   promptDialog({ title, message, label, value, type, min, step, confirmText, validate }) -> Promise<string|null>
//   globalToast(message, type, { action: { label, onClick }, duration }) -> { dismiss }
(function(){
    if (window.confirmDialog) return;

    function el(tag, className, text){
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    // Builds a modal <dialog>, resolves with done(value) and removes it. Esc and a backdrop click cancel.
    function openDialog(opts, build){
        return new Promise(function(resolve){
            const dialog = el('dialog', 'hh-dialog');
            const form = el('form', 'hh-dialog-body');
            form.method = 'dialog';
            form.noValidate = true;
            let settled = false;
            const done = function(value){
                if (settled) return;
                settled = true;
                if (dialog.open) dialog.close();
                dialog.remove();
                resolve(value);
            };

            if (opts.title){
                const title = el('h2', 'hh-dialog-title', opts.title);
                title.id = 'hhDialogTitle' + Date.now();
                dialog.setAttribute('aria-labelledby', title.id);
                form.appendChild(title);
            }
            if (opts.message) form.appendChild(el('p', 'hh-dialog-message', opts.message));
            if (Array.isArray(opts.details) && opts.details.length){
                const list = el('ul', 'hh-dialog-details');
                opts.details.forEach(function(line){ list.appendChild(el('li', '', line)); });
                form.appendChild(list);
            }
            const focusFirst = build(form, done);

            const actions = el('div', 'hh-dialog-actions');
            const cancel = el('button', 'btn btn-secondary', opts.cancelText || t('Cancel'));
            cancel.type = 'button';
            cancel.addEventListener('click', function(){ done(opts.cancelValue); });
            const confirm = el('button', 'btn ' + (opts.danger ? 'btn-danger-solid' : 'btn-primary'), opts.confirmText || t('OK'));
            confirm.type = 'submit';
            actions.appendChild(cancel);
            actions.appendChild(confirm);
            form.appendChild(actions);
            dialog.appendChild(form);

            dialog.addEventListener('click', function(ev){ if (ev.target === dialog) done(opts.cancelValue); });
            // Esc closes the dialog natively; treat that as Cancel
            dialog.addEventListener('close', function(){ done(opts.cancelValue); });
            document.body.appendChild(dialog);
            if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', '');
            // A destructive dialog starts on Cancel so a stray Enter does nothing
            (focusFirst || (opts.danger ? cancel : confirm)).focus();
        });
    }

    window.confirmDialog = function(options){
        const opts = Object.assign({ cancelValue: false }, typeof options === 'string' ? { message: options } : options);
        return openDialog(opts, function(form, done){
            form.addEventListener('submit', function(ev){ ev.preventDefault(); done(true); });
            return null;
        });
    };

    window.promptDialog = function(options){
        const opts = Object.assign({ cancelValue: null, confirmText: t('Save') }, options);
        return openDialog(opts, function(form, done){
            const field = el('div', 'hh-dialog-field');
            const input = el('input', 'w-full');
            input.type = opts.type || 'text';
            input.id = 'hhDialogInput' + Date.now();
            ['min', 'max', 'step', 'placeholder', 'inputMode'].forEach(function(name){
                if (opts[name] !== undefined && opts[name] !== null) input[name] = opts[name];
            });
            input.value = opts.value === undefined || opts.value === null ? '' : String(opts.value);
            if (opts.label){
                const label = el('label', 'hh-dialog-label', opts.label);
                label.htmlFor = input.id;
                field.appendChild(label);
            }
            const error = el('p', 'hh-dialog-error');
            error.setAttribute('role', 'alert');
            field.appendChild(input);
            field.appendChild(error);
            form.appendChild(field);
            form.addEventListener('submit', function(ev){
                ev.preventDefault();
                const problem = opts.validate ? opts.validate(input.value) : '';
                if (problem){ error.textContent = problem; input.focus(); return; }
                done(input.value);
            });
            setTimeout(function(){ try{ input.select(); }catch(e){} }, 0);
            return input;
        });
    };

    // Toasts stack, newest at the bottom; each one has its own timer
    const MAX_TOASTS = 4;
    const TOAST_COLORS = { error: 'bg-red-600', success: 'bg-green-600', info: 'bg-slate-800' };
    function toastHost(){
        let host = document.getElementById('toastHost');
        if (!host){
            host = el('div', 'fixed top-4 right-4 left-4 sm:left-auto z-50 pointer-events-none flex flex-col items-end gap-2');
            host.id = 'toastHost';
            host.setAttribute('role', 'status');
            host.setAttribute('aria-live', 'polite');
            document.body.appendChild(host);
        }
        return host;
    }

    window.globalToast = function(msg, type, options){
        const opts = options || {};
        const host = toastHost();
        const isError = type === 'error';
        const toast = el('div', 'toast pointer-events-auto inline-flex items-start gap-3 max-w-md px-4 py-2.5 rounded-lg shadow-lg text-sm text-white transition-opacity duration-200 ' + (TOAST_COLORS[type] || TOAST_COLORS.info));
        toast.setAttribute('role', isError ? 'alert' : 'status');
        let timer = null;
        const dismiss = function(){
            if (timer){ clearTimeout(timer); timer = null; }
            toast.style.opacity = '0';
            toast.style.pointerEvents = 'none';
            setTimeout(function(){ toast.remove(); }, 240);
        };
        toast.appendChild(el('span', 'flex-1', msg));
        if (opts.action && opts.action.label){
            const action = el('button', 'toast-action shrink-0 font-semibold underline underline-offset-2 hover:no-underline', opts.action.label);
            action.type = 'button';
            action.addEventListener('click', function(){
                dismiss();
                if (typeof opts.action.onClick === 'function') opts.action.onClick();
            });
            toast.appendChild(action);
        }
        const close = el('button', 'inline-flex items-center justify-center w-5 h-5 shrink-0 opacity-80 hover:opacity-100');
        close.type = 'button';
        close.setAttribute('aria-label', t('Dismiss'));
        close.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>';
        close.addEventListener('click', dismiss);
        toast.appendChild(close);
        host.appendChild(toast);
        while (host.children.length > MAX_TOASTS) host.firstElementChild.remove();
        // Errors get longer to be read. The clock stops while the toast is hovered or focused.
        const duration = opts.duration !== undefined ? opts.duration : (isError ? 8000 : 4000);
        const start = function(){ if (duration > 0 && !timer) timer = setTimeout(dismiss, duration); };
        const pause = function(){ if (timer){ clearTimeout(timer); timer = null; } };
        toast.addEventListener('mouseenter', pause);
        toast.addEventListener('mouseleave', start);
        toast.addEventListener('focusin', pause);
        toast.addEventListener('focusout', start);
        start();
        return { dismiss: dismiss };
    };
})();
