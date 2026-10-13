// Public page behaviour: accessible day tabs and reduced-motion media.
// Headlines remain native, selectable text without canvas or animation.
(function () {
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---- silent decorative videos; static posters under reduced motion
  Array.prototype.forEach.call(document.querySelectorAll('video'), function (video) {
    video.muted = true;
    video.defaultMuted = true;
    if (reduce) {
      video.removeAttribute('autoplay');
      try { video.pause(); } catch (e) {}
      video.preload = 'none';
    }
  });

  // ---- day tabs (without JS they are ?day=N links)
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.day-tab[data-day]'));
  function show(day, focus) {
    tabs.forEach(function (t) {
      var on = t.dataset.day === String(day);
      t.classList.toggle('is-active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      var panel = document.getElementById('day-' + t.dataset.day);
      if (panel) panel.hidden = !on;
      if (on && focus) t.focus();
    });
    try { history.replaceState(null, '', '?day=' + day + location.hash); } catch (e) {}
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
