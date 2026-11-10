// Trash page: send the current user with each form, and show Restore and Delete forever only on
// entries this person may change (the server checks the same rule: the owner or the admin).
// Switching user in the top bar updates the page at once, as on the other pages.
(function(){
  const page = document.getElementById('trashPage');
  if (!page) return;

  function apply(){
    const current = localStorage.getItem('username') || '';
    const isAdmin = !!(window.HomeHubAdmin && window.HomeHubAdmin.isAdminName(current));
    page.querySelectorAll('input[name="user"]').forEach(function(input){ input.value = current; });
    page.querySelectorAll('.trash-item').forEach(function(item){
      const actions = item.querySelector('.trash-actions');
      if (!actions) return;
      const owner = actions.getAttribute('data-owner') || '';
      const allowed = isAdmin || (!!current && current === owner);
      actions.classList.toggle('hidden', !allowed);
      const note = item.querySelector('.trash-not-yours');
      if (note) note.classList.toggle('hidden', allowed);
    });
  }

  apply();
  document.addEventListener('user-switched', apply);
})();
