document.getElementById('creator').value = localStorage.getItem('username');
document.querySelectorAll('input[name="user"]').forEach(i=> i.value = localStorage.getItem('username'));
// Hide delete for non-owners
(function(){
    const adminName=window.HomeHubAdmin.names[0];
    const current=localStorage.getItem('username');
    document.querySelectorAll('.delete-form').forEach(f=>{
        const c=f.getAttribute('data-creator');
        if(!(current===c||current===adminName||current==='Administrator'||current==='admin')) f.style.display='none';
    });
})();
