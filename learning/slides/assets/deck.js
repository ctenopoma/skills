// 紙芝居ナビゲーション(外部依存なし、file:// 直開きで動作)
document.addEventListener("DOMContentLoaded", function () {
  var slides = Array.prototype.slice.call(document.querySelectorAll(".slide"));
  if (slides.length === 0) return;

  var prevBtn = document.getElementById("prev");
  var nextBtn = document.getElementById("next");
  var counter = document.getElementById("counter");
  var bar = document.getElementById("bar");
  var toggle = document.getElementById("modeToggle");
  var current = 0;

  function show(n) {
    current = Math.max(0, Math.min(slides.length - 1, n));
    slides.forEach(function (s, i) {
      s.classList.toggle("active", i === current);
    });
    counter.textContent = (current + 1) + " / " + slides.length;
    bar.style.width = ((current + 1) / slides.length) * 100 + "%";
    prevBtn.disabled = current === 0;
    nextBtn.disabled = current === slides.length - 1;
    if (history.replaceState) {
      history.replaceState(null, "", "#" + (current + 1));
    }
    window.scrollTo(0, 0);
  }

  prevBtn.addEventListener("click", function () { show(current - 1); });
  nextBtn.addEventListener("click", function () { show(current + 1); });

  document.addEventListener("keydown", function (e) {
    if (document.body.classList.contains("list-mode")) return;
    if (e.key === "ArrowRight" || e.key === "PageDown" || e.key === " ") {
      e.preventDefault();
      show(current + 1);
    } else if (e.key === "ArrowLeft" || e.key === "PageUp") {
      e.preventDefault();
      show(current - 1);
    } else if (e.key === "Home") {
      show(0);
    } else if (e.key === "End") {
      show(slides.length - 1);
    }
  });

  if (toggle) {
    toggle.addEventListener("click", function () {
      var listed = document.body.classList.toggle("list-mode");
      toggle.textContent = listed ? "紙芝居に戻る" : "一覧表示";
      if (!listed) show(current);
    });
  }

  var start = parseInt((location.hash || "").replace("#", ""), 10);
  show(isNaN(start) ? 0 : start - 1);
});
