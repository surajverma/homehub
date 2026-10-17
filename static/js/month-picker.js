// Quick month/year switching for the month calendars (home reminders and expenses).
//   MonthPicker.attach({ label, anchor, swipeArea, current, go, today })
//     label     the month label button; clicking it opens the picker under it
//     anchor    positioned element the popover is placed in (defaults to the label's parent)
//     swipeArea element that takes left/right swipes on touch screens (the calendar grid)
//     current() -> { year, month } shown now, month 0-11
//     go(year, month) moves the page to that month through its own navigation code
//     today()   optional; what "This month" does (defaults to go() with the current month)
// Also: PageUp/PageDown change the month, Shift+PageUp/PageDown the year.
(function(){
    if (window.MonthPicker) return;

    const SWIPE_MIN = 50;

    function el(tag, className, text){
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function monthName(month, style){
        try {
            return new Intl.DateTimeFormat(window.I18N.locale, { month: style }).format(new Date(2000, month, 1));
        } catch(e) {
            return String(month + 1);
        }
    }

    function isTyping(target){
        if (!target || !target.closest) return false;
        return !!target.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]');
    }

    function isVisible(node){ return !!(node && node.getClientRects().length); }

    // A modal is open: a native <dialog> or one of the page's fixed full-screen overlays
    function modalOpen(){
        if (document.querySelector('dialog[open]')) return true;
        return Array.prototype.some.call(document.querySelectorAll('.fixed.inset-0'), function(node){
            return !node.classList.contains('hidden') && isVisible(node);
        });
    }

    function attach(opts){
        const label = opts.label;
        if (!label) return null;
        const anchor = opts.anchor || label.parentElement;
        const step = function(delta){
            const cur = opts.current();
            const d = new Date(cur.year, cur.month + delta, 1);
            opts.go(d.getFullYear(), d.getMonth());
        };

        const popover = el('div', 'month-picker');
        popover.hidden = true;
        popover.setAttribute('role', 'dialog');
        popover.setAttribute('aria-label', t('Choose month and year'));
        popover.id = (label.id || 'month') + 'Picker';

        const yearRow = el('div', 'month-picker-year');
        const prevYear = el('button', 'month-picker-step');
        prevYear.type = 'button';
        prevYear.setAttribute('aria-label', t('Previous year'));
        prevYear.innerHTML = '<i class="fa-solid fa-chevron-left" aria-hidden="true"></i>';
        const yearText = el('span', 'month-picker-year-text');
        yearText.setAttribute('aria-live', 'polite');
        const nextYear = el('button', 'month-picker-step');
        nextYear.type = 'button';
        nextYear.setAttribute('aria-label', t('Next year'));
        nextYear.innerHTML = '<i class="fa-solid fa-chevron-right" aria-hidden="true"></i>';
        yearRow.append(prevYear, yearText, nextYear);

        const grid = el('div', 'month-picker-grid');
        const monthButtons = [];
        for (let m = 0; m < 12; m++){
            const b = el('button', 'month-picker-month', monthName(m, 'short'));
            b.type = 'button';
            b.dataset.month = String(m);
            monthButtons.push(b);
            grid.appendChild(b);
        }

        const thisMonth = el('button', 'month-picker-today', t('This month'));
        thisMonth.type = 'button';

        popover.append(yearRow, grid, thisMonth);
        anchor.appendChild(popover);

        label.setAttribute('aria-haspopup', 'dialog');
        label.setAttribute('aria-expanded', 'false');
        label.setAttribute('aria-controls', popover.id);

        let viewYear = 0;

        function paint(){
            const cur = opts.current();
            const now = new Date();
            yearText.textContent = String(viewYear);
            monthButtons.forEach(function(b, m){
                const shown = viewYear === cur.year && m === cur.month;
                const isNow = viewYear === now.getFullYear() && m === now.getMonth();
                b.classList.toggle('is-selected', shown);
                b.classList.toggle('is-today', isNow);
                b.setAttribute('aria-pressed', shown ? 'true' : 'false');
                if (isNow) b.setAttribute('aria-current', 'date'); else b.removeAttribute('aria-current');
                b.setAttribute('aria-label', monthName(m, 'long') + ' ' + viewYear);
                // One month button is in the tab order; arrow keys move between them
                b.tabIndex = (viewYear === cur.year ? shown : m === 0) ? 0 : -1;
            });
        }

        function isOpen(){ return !popover.hidden; }

        function open(){
            viewYear = opts.current().year;
            paint();
            popover.hidden = false;
            label.setAttribute('aria-expanded', 'true');
            const target = monthButtons.find(function(b){ return b.tabIndex === 0; }) || monthButtons[0];
            target.focus();
        }

        function close(returnFocus){
            if (!isOpen()) return;
            popover.hidden = true;
            label.setAttribute('aria-expanded', 'false');
            if (returnFocus) label.focus();
        }

        function choose(year, month){
            close(true);
            const cur = opts.current();
            if (cur.year !== year || cur.month !== month) opts.go(year, month);
        }

        label.addEventListener('click', function(){ if (isOpen()) close(true); else open(); });
        prevYear.addEventListener('click', function(){ viewYear -= 1; paint(); });
        nextYear.addEventListener('click', function(){ viewYear += 1; paint(); });
        grid.addEventListener('click', function(e){
            const b = e.target.closest('button[data-month]');
            if (b) choose(viewYear, Number(b.dataset.month));
        });
        thisMonth.addEventListener('click', function(){
            close(true);
            if (opts.today) { opts.today(); return; }
            const now = new Date();
            const cur = opts.current();
            if (cur.year !== now.getFullYear() || cur.month !== now.getMonth()) opts.go(now.getFullYear(), now.getMonth());
        });

        popover.addEventListener('keydown', function(e){
            if (e.key === 'Escape'){
                e.preventDefault();
                e.stopPropagation();
                close(true);
                return;
            }
            const b = e.target.closest('button[data-month]');
            if (!b) return;
            const moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -3, ArrowDown: 3 };
            let next = null;
            if (e.key in moves) next = (Number(b.dataset.month) + moves[e.key] + 12) % 12;
            else if (e.key === 'Home') next = 0;
            else if (e.key === 'End') next = 11;
            if (next === null) return;
            e.preventDefault();
            monthButtons.forEach(function(x, m){ x.tabIndex = m === next ? 0 : -1; });
            monthButtons[next].focus();
        });

        document.addEventListener('pointerdown', function(e){
            if (isOpen() && !popover.contains(e.target) && !label.contains(e.target)) close(false);
        });
        popover.addEventListener('focusout', function(e){
            const to = e.relatedTarget;
            if (to && !popover.contains(to) && to !== label) close(false);
        });

        document.addEventListener('keydown', function(e){
            if (e.key !== 'PageUp' && e.key !== 'PageDown') return;
            if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
            if (isOpen() || isTyping(e.target) || modalOpen() || !isVisible(label)) return;
            e.preventDefault();
            const dir = e.key === 'PageUp' ? -1 : 1;
            step(e.shiftKey ? dir * 12 : dir);
        });

        if (opts.swipeArea){
            let startX = null, startY = null;
            opts.swipeArea.addEventListener('touchstart', function(e){
                if (e.touches.length !== 1){ startX = null; return; }
                startX = e.touches[0].clientX;
                startY = e.touches[0].clientY;
            }, { passive: true });
            opts.swipeArea.addEventListener('touchend', function(e){
                if (startX === null || !e.changedTouches.length) return;
                const dx = e.changedTouches[0].clientX - startX;
                const dy = e.changedTouches[0].clientY - startY;
                startX = null;
                // Only a clearly sideways swipe; vertical scrolling stays as it is
                if (Math.abs(dx) < SWIPE_MIN || Math.abs(dx) < Math.abs(dy) * 2) return;
                step(dx < 0 ? 1 : -1);
            }, { passive: true });
            opts.swipeArea.addEventListener('touchcancel', function(){ startX = null; }, { passive: true });
        }

        return { open: open, close: close, step: step };
    }

    window.MonthPicker = { attach: attach };
})();
