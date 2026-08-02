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
  async function updateRule(id, data){
    const r = await fetch('/api/recurring_rules/'+id, {method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)});
    return r.json();
  }
  async function deleteRule(id, creator){
    const r = await fetch('/api/recurring_rules/'+id, {method:'DELETE', headers:{'Content-Type':'application/json'}, body: JSON.stringify({creator})});
    return r.json();
  }
  async function markDone(id, creator){
    const r = await fetch('/api/reminders/'+id+'/done', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({creator})});
    return r.json();
  }
  async function undoDone(id, creator){
    const r = await fetch('/api/reminders/'+id+'/undo', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({creator})});
    return r.json();
  }
  async function snooze(id, creator, opts){
    const payload = Object.assign({creator}, opts || {});
    const r = await fetch('/api/reminders/'+id+'/snooze', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
    return r.json();
  }
  async function trash(){
    const r = await fetch('/api/reminders/trash');
    return r.json();
  }
  async function restore(id, creator){
    const r = await fetch('/api/reminders/'+id+'/restore', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({creator})});
    return r.json();
  }
  async function purge(id, creator){
    const r = await fetch('/api/reminders/'+id+'/purge', {method:'DELETE', headers:{'Content-Type':'application/json'}, body: JSON.stringify({creator})});
    return r.json();
  }
  return {list, create, update, removeMany, updateRule, deleteRule, markDone, undoDone, snooze, trash, restore, purge};
})();
