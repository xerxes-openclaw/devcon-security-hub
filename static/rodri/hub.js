// Public page behaviour, ported from Rodri's React build (Figma Make):
// day tabs, the "fuzzy" gradient headline canvas, reduced-motion video.
(function () {
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---- hero video: poster only under reduced motion
  var video = document.querySelector('video.hero-video');
  if (video && reduce) {
    video.removeAttribute('autoplay');
    try { video.pause(); } catch (e) {}
    video.preload = 'none';
  }

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

  // ---- fuzzy text: magenta-to-violet gradient text drawn to a canvas, each
  // pixel row jittered sideways (stronger on hover). Same numbers as Rodri's
  // FuzzyText component: 30 fps, base 0.07, hover 0.34, spread 18px.
  function fuzzy(el) {
    var text = el.textContent.trim();
    if (!text) return;
    var canvas = document.createElement('canvas');
    canvas.setAttribute('aria-hidden', 'true');
    el.setAttribute('aria-label', text);
    el.textContent = '';
    el.appendChild(canvas);
    var raf = 0, hover = false, last = 0;

    function build() {
      cancelAnimationFrame(raf);
      var ctx = canvas.getContext('2d');
      if (!ctx) return;
      var cs = getComputedStyle(el);
      var size = parseFloat(cs.fontSize);
      var spacing = parseFloat(cs.letterSpacing) || 0;
      var font = cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
      var off = document.createElement('canvas');
      var o = off.getContext('2d');
      o.font = font;
      var w = 0;
      for (var k = 0; k < text.length; k++) w += o.measureText(text[k]).width + spacing;
      w = Math.max(1, w - spacing);
      var m = o.measureText(text);
      var asc = m.actualBoundingBoxAscent || size * 0.8;
      var desc = m.actualBoundingBoxDescent || size * 0.2;
      var h = Math.ceil(asc + desc);
      var W = Math.ceil(w) + 4;
      off.width = W; off.height = h;
      o.font = font;
      o.textBaseline = 'alphabetic';
      var g = o.createLinearGradient(0, 0, W, 0);
      g.addColorStop(0, '#e84ad2');
      g.addColorStop(1, '#b489ff');
      o.fillStyle = g;
      var x = 2;
      for (var c = 0; c < text.length; c++) { o.fillText(text[c], x, asc); x += o.measureText(text[c]).width + spacing; }
      canvas.width = W + 40; canvas.height = h;
      canvas.style.width = canvas.width + 'px';
      canvas.style.height = canvas.height + 'px';

      function frame(t) {
        if (!reduce && t - last < 1000 / 30) { raf = requestAnimationFrame(frame); return; }
        last = t;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        var amt = reduce ? 0 : hover ? 0.34 : 0.07;
        for (var y = 0; y < h; y++) {
          var dx = Math.round(amt * (Math.random() - 0.5) * 18);
          ctx.drawImage(off, 0, y, W, 1, 20 + dx, y, W, 1);
        }
        if (!reduce) raf = requestAnimationFrame(frame);
      }
      raf = requestAnimationFrame(frame);
    }
    canvas.addEventListener('pointerenter', function () { hover = true; });
    canvas.addEventListener('pointerleave', function () { hover = false; });
    if (window.ResizeObserver) new ResizeObserver(build).observe(el);
    if (document.fonts && document.fonts.load) {
      var cs0 = getComputedStyle(el);
      document.fonts.load(cs0.fontWeight + ' ' + cs0.fontSize + ' ' + cs0.fontFamily, text)
        .then(build, build);
      document.fonts.ready.then(build);
    } else build();
  }
  Array.prototype.forEach.call(document.querySelectorAll('.fuzzy-text'), fuzzy);
})();
