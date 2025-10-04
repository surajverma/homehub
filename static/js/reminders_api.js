// Phase 2 client: basic fetch helpers (non-invasive). Future enhancements will integrate UI.
window.remindersApi = (function(){
  async function list(scope, dateStr){
    const params = new URLSearchParams({scope: scope||'day', date: dateStr});
    const r = await fetch('/api/reminders?'+params.toString());
    return r.json();
  }
  async function create(data){
    const r = await fetch('/api/reminders', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)});
    return r.json();
  }
  async function update(id, data){
    const r = await fetch('/api/reminders/'+id, {method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)});
    return r.json();
  }
  async function removeMany(ids, creator){
    const r = await fetch('/api/reminders', {method:'DELETE', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ids, creator})});
    return r.json();
  }
  return {list, create, update, removeMany};
})();
