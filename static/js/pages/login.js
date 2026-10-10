// Login form while the server is refusing attempts: count the wait down, then let the form be used again
(function(){
    const note = document.getElementById('loginLockout');
    const form = document.getElementById('loginForm');
    if (!note || !form) return;
    const ends = Date.now() + (parseInt(note.dataset.seconds, 10) || 0) * 1000;
    const text = note.dataset.text || '';
    const timer = setInterval(tick, 500);
    function tick(){
        const left = Math.ceil((ends - Date.now()) / 1000);
        if (left > 0){ note.textContent = text.replace('__N__', left); return; }
        clearInterval(timer);
        note.remove();
        form.querySelectorAll('[disabled]').forEach(function(el){ el.disabled = false; });
        form.querySelector('input[name="password"]').focus();
    }
    tick();
})();
