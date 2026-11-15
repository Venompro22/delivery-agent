// Regular customers who went quiet: one tap copies a friendly WeChat message to bring them back.
(async function () {
  const box = document.getElementById('quiet');
  if (!box) return;
  const esc = t => String(t).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  let d = {};
  try { d = await (await fetch('/api/customers/quiet')).json(); } catch (e) {}
  if (!d.ok || !d.customers.length) {
    box.innerHTML = '<div class="q-empty">Everyone is ordering as usual.</div>';
    return;
  }
  box.innerHTML = d.customers.map(c => `
    <div class="q-row">
      <div><b>${esc(c.name)}</b>
        <div class="q-sub">usually every ${c.every_days} days · last order ${c.since_days} days ago · ${c.count} orders</div></div>
      <button type="button" data-name="${esc(c.name)}">Copy message</button>
    </div>`).join('');
  box.onclick = async e => {
    const b = e.target.closest('button[data-name]');
    if (!b) return;
    const msg = `您好${b.dataset.name}～好久没见啦，最近还需要送水吗？今天下单我们优先给您送～`;
    try { await navigator.clipboard.writeText(msg); b.textContent = 'Copied'; }
    catch (err) { window.prompt('Copy this message:', msg); }
  };
})();
