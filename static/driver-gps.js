// Driver phone GPS: drop bad fixes → Kalman smoothing → adaptive sampling → offline queue.
// Pure logic only (no DOM), so it also runs under node for a quick self-check.
(function (root) {
  const C = {
    MAX_ACC_M: 50,        // fixes worse than this are dropped
    MAX_KMH: 150,         // faster than this between two fixes = a GPS jump
    WEAK_ACC_M: 30,       // health chip: "weak" above this
    LOST_MS: 30000,       // health chip: "lost" after this long without a fix
    MOVE_MS: 5000,        // moving: a point every 5 s ...
    MOVE_M: 15,           // ... or every 15 m, whichever comes first
    STILL_MS: 30000,      // stopped: a point every 30 s
    MOVING_MS: 1,         // smoothed speed above 1 m/s (3.6 km/h) counts as moving
    QUEUE_KEY: 'gpsQueue',
    QUEUE_MAX: 600,       // memory cap (10 min at 1 point/s)
    QUEUE_AGE_MS: 9.5 * 60 * 1000,   // the server drops points older than 10 min
    BATCH: 100,           // the server takes at most 100 points per request
    RESET_AFTER: 3,       // 3 "jumps" in a row = we were wrong, not the GPS: start over
  };

  function distM(a, b) {
    const r = x => x * Math.PI / 180, dLat = r(b.lat - a.lat), dLng = r(b.lng - a.lng);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(r(a.lat)) * Math.cos(r(b.lat)) * Math.sin(dLng / 2) ** 2;
    return 6371000 * 2 * Math.asin(Math.sqrt(h));
  }

  // Is this fix usable after the last accepted one? Accuracy slack is subtracted from the
  // distance, so two noisy fixes 1 s apart are not mistaken for a 300 km/h jump.
  function plausible(prev, fix) {
    if (!(fix.acc <= C.MAX_ACC_M)) return false;              // also drops NaN / missing accuracy
    if (!prev) return true;
    const dt = (fix.ts - prev.ts) / 1000;
    if (dt <= 0) return false;                                // duplicate or out of order
    const d = Math.max(0, distM(prev, fix) - prev.acc - fix.acc);
    return d / dt * 3.6 <= C.MAX_KMH;
  }

  // Constant-velocity Kalman filter, one independent [position, velocity] per axis,
  // in metres around the first fix. q = acceleration noise (m/s²)², R = accuracy².
  function Kalman(q = 4) {
    let o = null, ax = null, ay = null, t = 0;
    const M_LAT = 110540;
    const axis = p => ({ p, v: 0, P: [[1e4, 0], [0, 100]] });
    function step(a, z, dt, R) {
      a.p += a.v * dt;                                        // predict
      const [[p00, p01], [p10, p11]] = a.P;
      const d2 = dt * dt, d3 = d2 * dt, d4 = d3 * dt;
      const n00 = p00 + dt * (p01 + p10) + d2 * p11 + q * d4 / 4;
      const n01 = p01 + dt * p11 + q * d3 / 2;
      const n10 = p10 + dt * p11 + q * d3 / 2;
      const n11 = p11 + q * d2;
      const S = n00 + R, k0 = n00 / S, k1 = n10 / S, y = z - a.p;   // update
      a.p += k0 * y; a.v += k1 * y;
      a.P = [[(1 - k0) * n00, (1 - k0) * n01], [n10 - k1 * n00, n11 - k1 * n01]];
    }
    return {
      update(fix) {
        if (!o) {
          o = { lat: fix.lat, lng: fix.lng, mLng: 111320 * Math.cos(fix.lat * Math.PI / 180) };
          ax = axis(0); ay = axis(0); t = fix.ts;
        } else {
          const dt = Math.max(0.001, (fix.ts - t) / 1000), R = Math.max(1, fix.acc) ** 2;
          step(ax, (fix.lng - o.lng) * o.mLng, dt, R);
          step(ay, (fix.lat - o.lat) * M_LAT, dt, R);
          t = fix.ts;
        }
        return { lat: o.lat + ay.p / M_LAT, lng: o.lng + ax.p / o.mLng, ts: fix.ts, acc: fix.acc,
                 speed: Math.hypot(ax.v, ay.v) };
      },
      reset() { o = null; },
    };
  }

  // Should this smoothed point be recorded for the server?
  function due(lastSent, pt) {
    if (!lastSent) return true;
    const dt = pt.ts - lastSent.ts;
    if (pt.speed > C.MOVING_MS) return dt >= C.MOVE_MS || distM(lastSent, pt) >= C.MOVE_M;
    return dt >= C.STILL_MS;
  }

  function health(lastFix, now) {
    if (!lastFix || now - lastFix.at > C.LOST_MS) return 'lost';
    return lastFix.acc > C.WEAK_ACC_M ? 'weak' : 'good';
  }

  // Unsent points, in memory and (when the browser allows) in localStorage, sent as batches.
  // Only one request is ever in flight.
  function Queue(storage, post, now = () => Date.now()) {
    let items = [], busy = false;
    const fresh = () => { const min = now() - C.QUEUE_AGE_MS; items = items.filter(p => p.ts >= min).slice(-C.QUEUE_MAX); };
    const save = () => { try { storage && storage.setItem(C.QUEUE_KEY, JSON.stringify(items)); } catch (e) {} };
    try {
      const saved = storage && JSON.parse(storage.getItem(C.QUEUE_KEY) || '[]');
      if (Array.isArray(saved)) items = saved.filter(p => p && isFinite(p.lat) && isFinite(p.lng) && isFinite(p.ts));
    } catch (e) {}
    fresh();
    return {
      get size() { return items.length; },
      get busy() { return busy; },
      push(p) { items.push({ lat: p.lat, lng: p.lng, accuracy: Math.round(p.acc), ts: Math.round(p.ts) }); fresh(); save(); },
      // Send the oldest batch. Resolves true when the queue moved forward.
      async flush() {
        fresh();
        if (busy || !items.length) return false;
        busy = true;
        const batch = items.slice(0, C.BATCH);
        try {
          const status = await post(batch);
          // 2xx: delivered. 4xx: the server will never take these points, so drop them
          // (except 401/403/429, which a later try may pass). 5xx / network: keep and retry.
          if ((status >= 200 && status < 300) || (status >= 400 && status < 500 && ![401, 403, 429].includes(status))) {
            const sent = new Set(batch);
            items = items.filter(p => !sent.has(p));
            save();
            return true;
          }
          return false;
        } catch (e) {
          return false;
        } finally {
          busy = false;
        }
      },
    };
  }

  const api = { C, distM, plausible, Kalman, due, health, Queue };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.DriverGPS = api;
})(this);
