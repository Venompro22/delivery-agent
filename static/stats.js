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
    }

    load();

    (async () => {
      const res = await fetch('/api/stats');
      if (!res.ok) return;
      const {drivers} = await res.json();
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
    })();
