document.addEventListener('DOMContentLoaded', function(){
  function formatSize(bytes){
    let size = Number(bytes) || 0;
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (size >= 1024 && i < units.length - 1){ size /= 1024; i++; }
    return (i === 0 ? Math.round(size) : size.toFixed(1)) + ' ' + units[i];
  }

  // Running total of what the ticked parts add up to, with a warning for large backups
  const boxes = Array.from(document.querySelectorAll('.backup-part'));
  const totalEl = document.getElementById('backupTotal');
  const warning = document.getElementById('backupLargeWarning');
  function updateTotal(){
    const total = boxes.filter(b => b.checked).reduce((sum, b) => sum + (Number(b.dataset.bytes) || 0), 0);
    if (totalEl) totalEl.textContent = formatSize(total);
    if (warning) warning.classList.toggle('hidden', total < Number(warning.dataset.largeBytes || 0));
  }
  boxes.forEach(b => b.addEventListener('change', updateTotal));
  updateTotal();

  // While a backup or restore runs, show its progress and reload the page when it ends
  const job = document.getElementById('backupJob');
  if (!job || !job.dataset.running) return;
  const bar = document.getElementById('backupJobBar');
  const progress = document.getElementById('backupJobProgress');
  async function poll(){
    try {
      const res = await fetch(job.dataset.statusUrl, { headers: { 'Accept': 'application/json' } });
      const data = await res.json();
      if (!data.running){ window.location.reload(); return; }
      if (data.total){
        const pct = Math.min(100, Math.round(100 * data.done / data.total));
        bar.style.width = pct + '%';
        progress.textContent = t('{done} of {total}', { done: formatSize(data.done), total: formatSize(data.total) });
      }
    } catch (e) { /* try again on the next tick */ }
    setTimeout(poll, 2000);
  }
  poll();
});
