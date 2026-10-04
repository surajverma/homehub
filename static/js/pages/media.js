document.getElementById('creator').value = localStorage.getItem('username');
document.querySelectorAll('input[name="user"]').forEach(i=> i.value = localStorage.getItem('username'));
// Hide delete buttons for unauthorized users
(function(){
    const adminName = window.HomeHubAdmin.names[0];
    const current = localStorage.getItem('username');
    document.querySelectorAll('.delete-form').forEach(f => {
        const creator = f.getAttribute('data-creator');
        if (!(current === creator || current === adminName || current === 'Administrator' || current === 'admin')) {
            f.style.display = 'none';
        }
    });
})();
// Poll media progress
(function(){
    function poll(item){
        const id = item.getAttribute('data-id');
        fetch(`/media/status/${id}`).then(r=>r.json()).then(d=>{
            if (d.status === 'pending'){
                const p = item.querySelector('.progress-line');
                const chip = item.querySelector('.status-chip');
                if (p){
                    if (d.progress && /%$/.test(d.progress)){
                        chip.textContent = 'Downloading…';
                        p.textContent = d.progress;
                    } else {
                        chip.textContent = 'Starting…';
                        p.textContent = 'Will take a while…';
                    }
                }
                setTimeout(()=>poll(item), 2500);
            } else if (d.status === 'done' && d.filepath){
                // replace chip with Preview and Download links
                const chip = item.querySelector('.status-chip');
                if (chip){ 
                    chip.outerHTML = `
                        <a href="/media/preview/${d.filepath}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-sm" aria-label="Preview ${d.filepath} in new tab">
                            <i class="fa-solid fa-eye" aria-hidden="true"></i> Preview
                        </a>
                        <a href="/media/${d.filepath}" class="btn btn-secondary btn-sm">
                            <i class="fa-solid fa-download" aria-hidden="true"></i> Download
                        </a>
                    `; 
                }
                const p = item.querySelector('.progress-line');
                if (p) p.textContent = '';
            } else if (d.status === 'error'){
                const chip = item.querySelector('.status-chip');
                if (chip){ chip.textContent = 'Error'; chip.className = 'px-2.5 py-1 rounded-full text-xs font-medium bg-red-100 text-red-800 status-chip'; }
            }
        }).catch(()=> setTimeout(()=>poll(item), 2000));
    }
    // Start polling for each pending row
    document.querySelectorAll('li[data-id]').forEach(li=>{
        if (li.querySelector('.status-chip')) poll(li);
    });
})();
