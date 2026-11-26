    function baseOptions() {
      return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: 'rgba(255,255,255,0.06)' } },
        },
      };
    }

    async function load() {
      const res = await fetch('/api/stats');
      if (res.status === 401) { location.href = '/login'; return; }
      const d = await res.json();

      const week = d.delivered.reduce((a, b) => a + b, 0);
      const today = d.delivered[d.delivered.length - 1];
      document.getElementById('k-week').textContent = week;
      document.getElementById('k-today').textContent = today;
      document.getElementById('k-time').textContent = d.avg_minutes ?? '–';
      document.getElementById('k-ontime').textContent = d.on_time_pct !== null ? d.on_time_pct + '%' : '–';
      const yuan = n => '¥' + (Number.isInteger(n) ? n : n.toFixed(2));
      document.getElementById('k-rev-today').textContent = yuan(d.revenue_today ?? 0);
      document.getElementById('k-rev-week').textContent = yuan(d.revenue_week ?? 0);
      const collect = document.getElementById('k-collect');
      collect.textContent = yuan(d.to_collect ?? 0);
      collect.style.color = d.to_collect > 0 ? 'var(--warn)' : '';

      Chart.defaults.color = '#8a8f98';
      Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;

      new Chart(document.getElementById('chart-days'), {
        type: 'bar',
        data: {
          labels: d.days,
          datasets: [{ data: d.delivered, backgroundColor: '#2d6cdf', borderRadius: 6, maxBarThickness: 34 }],
        },
        options: baseOptions(),
      });

      const busiest = Math.max(...d.by_hour);
      new Chart(document.getElementById('chart-hours'), {
        type: 'bar',
        data: {
          labels: d.by_hour.map((_, h) => h + 'h'),
          datasets: [{
            data: d.by_hour,
            backgroundColor: d.by_hour.map(v => v === busiest && v > 0 ? '#f0a020' : '#1f9d55'),
            borderRadius: 4,
          }],
        },
        options: baseOptions(),
      });

      renderDrivers(d.drivers);
      renderProducts(d.top_products);
    }

    load();

    // Best sellers: what to always keep in stock
    function renderProducts(products) {
      const box = document.getElementById('product-stats');
      box.textContent = '';
      if (!products || !products.length) { box.textContent = 'No products sold yet.'; return; }
      const table = document.createElement('table');
      table.className = 'driver-table product-table';
      const head = table.createTHead().insertRow();
      for (const h of ['Product', 'Sold', 'Orders', 'Revenue']) {
        const th = document.createElement('th');
        th.textContent = h;
        head.append(th);
      }
      const body = table.createTBody();
      for (const p of products) {
        const row = body.insertRow();
        const name = row.insertCell();
        const label = document.createElement('div');
        label.textContent = p.name;
        const bar = document.createElement('div');
        bar.className = 'share-bar';
        bar.style.width = Math.max(4, p.share) + '%';     // share of everything sold
        name.append(label, bar);
        row.insertCell().textContent = p.qty + (p.unit ? ' ' + p.unit : '');
        row.insertCell().textContent = p.orders;
        row.insertCell().textContent = '¥' + p.revenue.toFixed(2);
      }
      box.append(table);
    }

    function renderDrivers(drivers) {
      const box = document.getElementById('driver-stats');
      if (!drivers || !drivers.length) { box.textContent = 'No deliveries yet.'; return; }
      const table = document.createElement('table');
      table.className = 'driver-table';
      const head = table.createTHead().insertRow();
      for (const h of ['Driver', 'Today', '7 days', 'Median time', 'On time', 'Late']) {
        const th = document.createElement('th');
        th.textContent = h;
        head.append(th);
      }
      const body = table.createTBody();
      for (const d of drivers) {
        const row = body.insertRow();
        for (const v of [d.name, d.today, d.week,
                         d.median_minutes == null ? '--' : d.median_minutes + ' min',
                         d.on_time_pct == null ? '--' : d.on_time_pct + '%', d.late]) {
          row.insertCell().textContent = v;
        }
        if (d.late) row.lastChild.className = 'bad';
      }
      box.append(table);
    }


// ETA brain: what the learned ETA model knows (owner only)
(async function loadEtaBrain() {
  try {
    const res = await fetch('/api/eta-model');
    if (!res.ok) return;
    const m = await res.json();
    if (!m.legs) return;
    document.getElementById('eta-brain').hidden = false;
    document.getElementById('k-eta-legs').textContent = m.legs;
    if (m.mae == null) {
      document.getElementById('k-eta-err').textContent = 'learning';
      document.getElementById('k-eta-sub').textContent = 'needs more deliveries';
      return;
    }
    document.getElementById('k-eta-err').textContent = '±' + m.mae + ' min';
    document.getElementById('k-eta-sub').textContent = m.active
      ? `in use · simple formula was ±${m.baseline_mae} min`
      : `not better than the simple formula (±${m.baseline_mae} min) yet`;
    document.getElementById('k-eta-slow').textContent = m.slowest.hours;
    document.getElementById('k-eta-slow-sub').textContent = m.slowest.min_per_km + ' min per km';
  } catch (e) {}
})();
