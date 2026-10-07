// Day tabs: switch panels without a reload. Without JS the tabs are plain
// ?day=N links, so the agenda still works.
(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab[data-day]'));
  if (!tabs.length) return;
  function show(day, focus) {
    tabs.forEach(function (t) {
      var on = t.dataset.day === String(day);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      document.getElementById('day-' + t.dataset.day).hidden = !on;
      if (on && focus) t.focus();
    });
    try { history.replaceState(null, '', '?day=' + day + '#agenda'); } catch (e) {}
  }
  tabs.forEach(function (t, i) {
    t.addEventListener('click', function (e) { e.preventDefault(); show(t.dataset.day); });
    t.addEventListener('keydown', function (e) {
      var j = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : null;
      if (j === null) return;
      e.preventDefault();
      show(tabs[(j + tabs.length) % tabs.length].dataset.day, true);
    });
  });
})();
