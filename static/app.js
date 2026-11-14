// Inside pages: 3D tilt on stat cards, and numbers that count up when they change.
(function () {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // The card under the pointer leans toward it, like a real object
  if (!reduce && window.matchMedia('(pointer: fine)').matches) {
    document.addEventListener('pointermove', e => {
      const el = e.target.closest && e.target.closest('.stat, .kpi, .count, .me-card');
      document.querySelectorAll('.tilting').forEach(x => {
        if (x !== el) { x.classList.remove('tilting'); x.style.transform = ''; }
      });
      if (!el) return;
      const r = el.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
      el.classList.add('tilting');
      el.style.transform = `perspective(700px) rotateX(${(-y * 8).toFixed(2)}deg) rotateY(${(x * 10).toFixed(2)}deg) translateY(-3px)`;
    });
  }
  if (reduce) return;

  // Numbers count up from the old value to the new one (0 → 128) instead of jumping
  function countTo(el, from, to) {
    const start = performance.now(), dur = 700;
    el.dataset.counting = '1';
    function step(now) {
      const p = Math.min(1, (now - start) / dur), eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(from + (to - from) * eased);
      if (p < 1) requestAnimationFrame(step); else delete el.dataset.counting;
    }
    requestAnimationFrame(step);
  }
  document.querySelectorAll('.num, .n').forEach(el => {
    let last = parseInt(el.textContent, 10) || 0;
    new MutationObserver(() => {
      if (el.dataset.counting) return;               // our own animation, not new data
      const txt = el.textContent.trim();
      if (!/^\d+$/.test(txt)) return;               // "92%", "--": leave as is
      const to = parseInt(txt, 10);
      if (to !== last) { countTo(el, last, to); last = to; }
    }).observe(el, { childList: true, characterData: true, subtree: true });
  });
})();
