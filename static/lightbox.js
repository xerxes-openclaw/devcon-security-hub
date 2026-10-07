// Click a photo to see it large; click anywhere, the X or press Escape to close.
(function () {
  var links = document.querySelectorAll("a.zoom");
  if (!links.length) return;

  var box = document.createElement("div");
  box.className = "lightbox";
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-label", "Photo");
  var img = document.createElement("img");
  var close = document.createElement("button");
  close.type = "button";
  close.setAttribute("aria-label", "Close");
  close.textContent = "×";
  box.appendChild(img);
  box.appendChild(close);
  document.body.appendChild(box);

  var last = null;
  function open(a) {
    var thumb = a.querySelector("img");
    img.src = a.href;
    img.alt = thumb ? thumb.alt : "";
    last = a;
    box.classList.add("is-open");
    close.focus();
  }
  function shut() {
    box.classList.remove("is-open");
    img.removeAttribute("src");
    if (last) last.focus();
  }

  links.forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      open(a);
    });
  });
  box.addEventListener("click", shut);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && box.classList.contains("is-open")) shut();
  });
})();
