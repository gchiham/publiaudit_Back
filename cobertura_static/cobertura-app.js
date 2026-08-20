'use strict';
// ─── Core: init · tabs · polling · theme ────────────────────────────────────
(function () {
  let activeTab      = 'streams';
  let lastRefresh    = Date.now();
  let pollTimer      = null;
  let liveTimer      = null;

  // ─── Boot ──────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    // ── Validación de token (solo en producción) ──
    if (!DEMO_MODE) {
      if (!getToken()) {
        showDenied('Token requerido — accede con <code>?token=TU_TOKEN</code>');
        return;
      }
    } else {
      // Banner visible solo en modo demo
      var demoBanner = document.getElementById('demo-banner');
      if (demoBanner) demoBanner.classList.remove('hidden');
    }

    // Listener para errores 401/403 del API
    document.addEventListener('cob:denied', function (e) {
      showDenied(e.detail || 'Token inválido o expirado.');
    });

    // Theme
    const theme = localStorage.getItem('cob-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', theme);
    setThemeBtn(theme);

    // Tab clicks
    document.querySelectorAll('.tab-item').forEach(function (btn) {
      btn.addEventListener('click', function () { switchTab(this.dataset.tab); });
    });

    // Theme toggle
    document.getElementById('theme-toggle').addEventListener('click', toggleTheme);

    // First render — restaurar tab activo si el usuario lo cambió antes
    switchTab(localStorage.getItem('cob-tab') || 'streams');

    // Poll every 30 s
    pollTimer = setInterval(function () { refreshActive(true); }, 30000);

    // Live counter every 1 s
    liveTimer = setInterval(updateLive, 1000);
  });

  // ─── Tab switch ────────────────────────────────────────────────────────────
  window.switchTab = function (tab) {
    activeTab = tab;
    localStorage.setItem('cob-tab', tab);

    document.querySelectorAll('.tab-item').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    document.querySelectorAll('.tab-panel').forEach(function (p) {
      p.classList.toggle('active', p.id === 'tab-' + tab);
    });

    // Costs mode indicator
    document.getElementById('live-wrap').classList.toggle('costs-mode', tab === 'costs');

    refreshActive(false);
  };

  // ─── Render dispatcher ─────────────────────────────────────────────────────
  function refreshActive(force) {
    var p;
    if (activeTab === 'streams')   p = renderStreams(force);
    if (activeTab === 'gateways')  p = renderGateways(force);
    if (activeTab === 'destroyer') p = renderDestroyer(force);
    if (activeTab === 'costs')     p = renderCosts(force);
    if (p && p.then) {
      p.then(function () {
        lastRefresh = Date.now();
        updateBadges();
        hideErr();
      }).catch(function (err) {
        showErr(err.message || 'Error de red');
      });
    }
  }

  // ─── Live indicator ────────────────────────────────────────────────────────
  function updateLive() {
    var el = document.getElementById('live-time');
    if (!el) return;
    if (activeTab === 'costs') {
      el.textContent = 'Datos con hasta 24 h de retraso';
    } else {
      var s = Math.round((Date.now() - lastRefresh) / 1000);
      el.textContent = 'En vivo · hace ' + fmtS(s);
    }
  }

  // ─── Header badges (datos reales, cacheados ~60 s) ──────────────────────────
  var _badgeCache = { ts: 0, html: '' };
  function updateBadges() {
    var el = document.getElementById('hdr-badges');
    if (!el) return;
    if (_badgeCache.html) el.innerHTML = _badgeCache.html;      // pinta lo último bueno
    if (Date.now() - _badgeCache.ts < 60000) return;            // throttle
    _badgeCache.ts = Date.now();
    Promise.all([
      apiFetch('/api/cobertura/coverage', { days: 1 }).catch(function () { return null; }),
      apiFetch('/api/cobertura/gateways').catch(function () { return null; }),
      apiFetch('/api/cobertura/costs/summary').catch(function () { return null; }),
    ]).then(function (res) {
      var cov = res[0], gw = res[1], costs = res[2];
      var html = '';
      if (cov && cov.summary) {
        html += badge(cov.summary.streams_active + ' streams', 'ok');
      }
      if (gw && gw.gateways) {
        var online = gw.gateways.filter(function (g) { return g.online; }).length;
        var total  = gw.gateways.length;
        html += badge(online + '/' + total + ' gateways', online === total ? 'ok' : 'warn');
      }
      if (costs && costs.available !== false && costs.summary) {
        html += badge('$' + Number(costs.summary.today || 0).toFixed(2) + ' hoy', 'neu');
      }
      _badgeCache.html = html;
      var elNow = document.getElementById('hdr-badges');
      if (elNow) elNow.innerHTML = html;
    });
  }

  function badge(txt, type) {
    return '<span class="hdr-badge ' + type + '">' + txt + '</span>';
  }

  // ─── Theme ─────────────────────────────────────────────────────────────────
  function toggleTheme() {
    var cur  = document.documentElement.getAttribute('data-theme');
    var next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('cob-theme', next);
    setThemeBtn(next);
    // Re-render to update Chart.js colors
    refreshActive(false);
  }

  function setThemeBtn(theme) {
    var btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = theme === 'dark' ? '☾' : '☀';
  }

  // ─── Error banner ──────────────────────────────────────────────────────────
  function showErr(msg) {
    var el = document.getElementById('err-banner');
    if (el) {
      el.textContent = '⚠ Error al actualizar — mostrando último dato · ' + msg;
      el.classList.remove('hidden');
    }
  }

  function hideErr() {
    var el = document.getElementById('err-banner');
    if (el) el.classList.add('hidden');
  }

  // ─── Acceso denegado ───────────────────────────────────────────────────────
  function showDenied(msg) {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
    var overlay = document.getElementById('denied-overlay');
    var msgEl   = document.getElementById('denied-msg');
    if (msgEl)   msgEl.innerHTML = msg || '';
    if (overlay) overlay.classList.remove('hidden');
  }

  // ─── Utils ─────────────────────────────────────────────────────────────────
  function fmtS(s) {
    if (s < 60)   return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm';
    return Math.floor(s / 3600) + 'h';
  }
}());
