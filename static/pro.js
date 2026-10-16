(function () {
  // ✒️ Clean UI: remove emojis from buttons, titles and labels (customer data is never touched)
  const EMOJI = /(?:\p{Extended_Pictographic}|\p{Regional_Indicator})(?:\uFE0F|\u200D\p{Extended_Pictographic})*\uFE0F?\s?/gu;
  const SEL = 'button:not(.chip), .btn, .logout, .back, h1, h3, .label, .hint, .subtitle, .chips-label, ' +
              '.hist-head, .stat .label, .gps-status, .tab, .ai-who, .toast, .ai-intent, .ai-what, ' +
              '.ai-learn, .hist-row span:not(.m), .error, .empty, .meta, .badge, .note';
  function strip(el) {
    el.childNodes.forEach(n => {
      if (n.nodeType === 3) { const t = n.nodeValue.replace(EMOJI, ''); if (t !== n.nodeValue) n.nodeValue = t; }
    });
  }
  function clean(root) {
    if (root.matches && root.matches(SEL)) strip(root);
    if (root.querySelectorAll) root.querySelectorAll(SEL).forEach(strip);
    document.querySelectorAll('input[placeholder], textarea[placeholder]').forEach(el => {
      const t = el.placeholder.replace(EMOJI, ''); if (t !== el.placeholder) el.placeholder = t;
    });
  }
  function start() {
    clean(document.body);
    new MutationObserver(muts => muts.forEach(m => {
      if (m.type === 'characterData') {
        const p = m.target.parentElement;
        if (p && p.matches(SEL)) strip(p);
      } else m.addedNodes.forEach(n => {
        if (n.nodeType === 1) clean(n);
        else if (n.nodeType === 3 && n.parentElement && n.parentElement.matches(SEL)) strip(n.parentElement);
      });
    })).observe(document.body, {childList: true, subtree: true, characterData: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();


// 🚪 Logout is a POST (a GET could be triggered by another website, e.g. with an <img>)
document.addEventListener('click', (e) => {
  const link = e.target.closest('a[href="/logout"]');
  if (!link) return;
  e.preventDefault();
  const form = document.createElement('form');
  form.method = 'post';
  form.action = '/logout';
  document.body.appendChild(form);
  form.submit();
});
