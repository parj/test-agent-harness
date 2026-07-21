/* FinAgent RUM — lightweight client-side telemetry: page-load timing,
   per-SPA-view duration, and click tracking. Batches events and POSTs them
   to /api/rum, which turns each into an OTel span + metric point on the
   finagent-web service (see src/observability.py). No external deps, no
   build step — this loads as a plain script alongside app.js. */
'use strict';

(function () {
  const SESSION_ID = (() => {
    let id = sessionStorage.getItem('finagent_rum_session');
    if (!id) {
      id = (window.crypto && crypto.randomUUID)
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      sessionStorage.setItem('finagent_rum_session', id);
    }
    return id;
  })();

  let queue = [];
  function push(event) {
    queue.push({ ...event, session_id: SESSION_ID, t: Date.now() });
    if (queue.length >= 20) flush(false);
  }

  function flush(useBeacon) {
    if (!queue.length) return;
    const events = queue;
    queue = [];
    const body = JSON.stringify({ events });
    if (useBeacon && navigator.sendBeacon) {
      navigator.sendBeacon('/api/rum', new Blob([body], { type: 'application/json' }));
    } else {
      fetch('/api/rum', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: true,
      }).catch(() => {});
    }
  }
  setInterval(() => flush(false), 5000);
  window.addEventListener('pagehide', () => flush(true));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush(true);
  });

  /* Page load timing, from the Navigation Timing API. */
  window.addEventListener('load', () => {
    setTimeout(() => {
      const nav = performance.getEntriesByType('navigation')[0];
      if (!nav) return;
      push({
        type: 'page_load',
        page: (window.S && window.S.view) || 'unknown',
        ttfb_ms: Math.round(nav.responseStart - nav.requestStart),
        dom_content_loaded_ms: Math.round(nav.domContentLoadedEventEnd - nav.startTime),
        load_ms: Math.round(nav.loadEventEnd - nav.startTime),
      });
    }, 0);
  });

  /* Click tracking — walks up from the click target to the nearest
     identifiable interactive element. */
  function describeTarget(el) {
    while (el && el !== document.body) {
      if (el.matches && el.matches('button, a, .nav-item, [onclick]')) {
        return {
          tag: el.tagName.toLowerCase(),
          label: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 60),
          id: el.id || null,
        };
      }
      el = el.parentElement;
    }
    return null;
  }
  document.addEventListener('click', (e) => {
    push({
      type: 'click',
      page: (window.S && window.S.view) || 'unknown',
      x: e.clientX,
      y: e.clientY,
      target: describeTarget(e.target),
    });
  }, true);

  /* SPA view-duration tracking. app.js exposes `S` (state) and `App`
     globally; navigation happens either via App.go() or a direct S.view
     assignment (e.g. App.goTask), so polling S.view is more robust than
     hooking individual App methods. */
  let currentView = null;
  let viewEnteredAt = performance.now();
  function checkView() {
    const view = (window.S && window.S.view) || null;
    if (view === currentView) return;
    if (currentView !== null) {
      push({
        type: 'page_view',
        page: currentView,
        duration_ms: Math.round(performance.now() - viewEnteredAt),
      });
    }
    currentView = view;
    viewEnteredAt = performance.now();
  }
  setInterval(checkView, 1000);
  window.addEventListener('pagehide', checkView);
  checkView();

  /* Core Web Vitals, via Google's web-vitals library (loaded as a plain
     <script> before this file — see index.html). Metric names/units here
     match what SigNoz's imported "Web Vitals Monitoring" dashboard expects
     (lcp/inp/ttfb/fcp in ms, cls unitless), see src/observability.py. */
  if (window.webVitals) {
    const report = (metric) => push({
      type: 'web_vital',
      page: (window.S && window.S.view) || 'unknown',
      name: metric.name,
      value: metric.value,
      rating: metric.rating,
    });
    webVitals.onLCP(report);
    webVitals.onINP(report);
    webVitals.onCLS(report);
    webVitals.onFCP(report);
    webVitals.onTTFB(report);
  }
})();
