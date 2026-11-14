// Landing page: 3D tilt that follows the pointer, fade-in on scroll, show/hide password.
(function () {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  // The phone leans toward the pointer (desktop only)
  const tilt = document.querySelector('.tilt');
  if (tilt && window.matchMedia('(pointer: fine)').matches) {
    window.addEventListener('pointermove', e => {
      const x = e.clientX / window.innerWidth - 0.5;
      const y = e.clientY / window.innerHeight - 0.5;
      tilt.style.setProperty('--ry', (-16 + x * 18) + 'deg');
      tilt.style.setProperty('--rx', (6 - y * 12) + 'deg');
    });
  }

  // Cards fade up when they scroll into view (hidden only once JS is sure it can show them)
  if (!('IntersectionObserver' in window)) return;
  const items = document.querySelectorAll('.feature, .how li');
  items.forEach(el => el.classList.add('reveal'));
  const io = new IntersectionObserver(entries => entries.forEach(en => {
    if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); }
  }), { threshold: 0.15 });
  items.forEach(el => io.observe(el));
})();

document.querySelectorAll('.eye').forEach(btn => btn.addEventListener('click', () => {
  const input = btn.parentElement.querySelector('input');
  input.type = input.type === 'password' ? 'text' : 'password';
  btn.classList.toggle('on', input.type === 'text');
}));

// Sign-up: live phone check and password strength, so mistakes show before submitting
(function () {
  let zh = true;
  try { zh = (localStorage.getItem('lang') || navigator.language || 'zh').startsWith('zh'); } catch (e) {}
  const phone = document.querySelector('.signup input[name="phone"]');
  const tip = document.querySelector('.signup .phone-tip');
  if (phone && tip) phone.addEventListener('input', () => {
    const v = phone.value.trim();
    const ok = /^1[3-9]\d{9}$/.test(v);
    tip.textContent = !v ? '' : ok ? '✓' : (zh ? '请输入11位手机号' : '11-digit China mobile number');
    tip.className = 'phone-tip' + (ok ? ' ok' : v ? ' bad' : '');
  });
  const pw = document.querySelector('.signup input[name="password"]');
  const meter = document.querySelector('.signup .meter');
  if (pw && meter) pw.addEventListener('input', () => {
    const v = pw.value;
    let score = 0;
    if (v.length >= 8) score++;
    if (v.length >= 12) score++;
    if (/[a-z]/.test(v) && /[A-Z]/.test(v)) score++;
    if (/\d/.test(v) && /[^A-Za-z0-9]/.test(v)) score++;
    const labels = zh ? ['太短', '弱', '一般', '好', '很强'] : ['Too short', 'Weak', 'OK', 'Good', 'Strong'];
    meter.dataset.score = v ? score : '';
    meter.querySelector('.meter-label').textContent = v ? labels[score] : '';
  });
})();
