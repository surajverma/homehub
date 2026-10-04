document.getElementById('creator').value = localStorage.getItem('username');
document.querySelectorAll('input[name="user"]').forEach(i=> i.value = localStorage.getItem('username'));
// Hide delete/edit for non-owners
(function(){
    const adminName=window.HomeHubAdmin.names[0];
    const current=localStorage.getItem('username');
    document.querySelectorAll('.delete-form').forEach(f=>{
        const c=f.getAttribute('data-creator');
        if(!(current===c||current===adminName||current==='Administrator'||current==='admin')) f.style.display='none';
    });
    document.querySelectorAll('.edit-btn').forEach(b=>{
        const c=b.getAttribute('data-creator');
        if(!(current===c||current===adminName||current==='Administrator'||current==='admin')) b.style.display='none';
    });
})();
// Edit flow
(function(){
    const form=document.getElementById('noteForm');
    const textarea=form.querySelector('textarea[name="content"]');
    const noteId=form.querySelector('input[name="note_id"]');
    const cancel=document.getElementById('cancelEdit');
    document.querySelectorAll('.edit-btn').forEach(btn=>{
        btn.addEventListener('click',()=>{
            textarea.value=btn.getAttribute('data-content');
            noteId.value=btn.getAttribute('data-id');
            cancel.classList.remove('hidden');
            textarea.focus();
        });
    });
    cancel.addEventListener('click',()=>{
        textarea.value='';
        noteId.value='';
        cancel.classList.add('hidden');
    });
})();
