/* The visitor nav (NAV_AUDIENCE_PLAN.md): audience mega panels and the phone
   drawer. Markup comes from partials/_site_nav.html; without this file the two
   triggers stay plain links. No framework. */
(function () {
  'use strict';
  var nav = document.getElementById('ssNav');
  if (!nav || !nav.classList.contains('ss-nav-aud')) return;
  var scrim = document.getElementById('ssScrim');
  var drawer = document.getElementById('ssDrawer');
  var menuBtn = document.getElementById('ssMenuBtn');
  var phone = window.matchMedia('(max-width: 899px)');
  var root = window.ROOT || '';

  // nav_open / nav_click: panel name or item id only, never personal data.
  function track(event, key, value) {
    try {
      var data = {event: event};
      data[key] = value;
      var body = JSON.stringify(data);
      if (navigator.sendBeacon && navigator.sendBeacon(root + '/api/nav-event', new Blob([body], {type: 'application/json'}))) return;
      fetch(root + '/api/nav-event', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: body, keepalive: true})
        .catch(function () {});
    } catch (e) { /* analytics never breaks the nav */ }
  }

  // ── Desktop panels ─────────────────────────────────────────
  // Progressive enhancement: each link trigger becomes a real button.
  var triggers = [];
  Array.prototype.forEach.call(nav.querySelectorAll('a.ss-trigger'), function (a) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = a.className;
    b.id = a.id;
    b.innerHTML = a.innerHTML;
    b.setAttribute('data-panel', a.getAttribute('data-panel'));
    b.setAttribute('aria-expanded', 'false');
    b.setAttribute('aria-controls', 'ssPanel-' + a.getAttribute('data-panel'));
    a.parentNode.replaceChild(b, a);
    triggers.push(b);
  });

  var open = null;
  function panelOf(t) { return document.getElementById(t.getAttribute('aria-controls')); }

  function closePanel(returnFocus) {
    if (!open) return;
    var t = open;
    open = null;
    t.setAttribute('aria-expanded', 'false');
    panelOf(t).hidden = true;
    if (scrim) scrim.hidden = true;
    if (returnFocus) t.focus();
  }

  function togglePanel(t) {
    if (open === t) { closePanel(false); return; }
    closePanel(false);
    open = t;
    t.setAttribute('aria-expanded', 'true');
    panelOf(t).hidden = false;
    if (scrim) scrim.hidden = false;
    track('nav_open', 'panel', t.getAttribute('data-panel'));
  }

  triggers.forEach(function (t) {
    t.addEventListener('click', function () { togglePanel(t); });   // Enter and Space click a button natively
  });

  if (scrim) scrim.addEventListener('click', function () { closePanel(false); });

  document.addEventListener('click', function (e) {
    var item = e.target.closest ? e.target.closest('[data-nav-item]') : null;
    if (item && (nav.contains(item) || (drawer && drawer.contains(item)))) {
      track('nav_click', 'item', item.getAttribute('data-nav-item'));
    }
    if (!open) return;
    var li = open.parentNode;
    if (!li.contains(e.target)) { closePanel(false); return; }        // anywhere outside the open trigger and panel
    if (item && panelOf(open).contains(item)) closePanel(false);      // a link (or the demo) was chosen
  });

  // ── Phone drawer ──────────────────────────────────────────
  function drawerOpen() { return drawer && drawer.classList.contains('open'); }

  function setDrawer(on, returnFocus) {
    if (!drawer || !menuBtn) return;
    if (on) {
      var top = Math.max(0, Math.round(nav.getBoundingClientRect().bottom));
      drawer.style.top = top + 'px';
      drawer.style.maxHeight = 'calc(100vh - ' + top + 'px)';
    }
    drawer.classList.toggle('open', on);
    menuBtn.setAttribute('aria-expanded', on ? 'true' : 'false');
    menuBtn.textContent = on ? 'Close' : 'Menu';
    document.documentElement.classList.toggle('ss-lock', on);
    if (on) {
      var first = drawer.querySelector('button, a[href]');
      if (first) setTimeout(function () { first.focus(); }, 0);   // after visibility flips
    } else if (returnFocus) {
      menuBtn.focus();
    }
  }

  if (menuBtn && drawer) {
    menuBtn.addEventListener('click', function () { setDrawer(!drawerOpen(), false); });
    Array.prototype.forEach.call(drawer.querySelectorAll('.ss-dsec-btn'), function (h) {
      h.addEventListener('click', function () {
        var list = document.getElementById(h.getAttribute('aria-controls'));
        var expand = h.getAttribute('aria-expanded') !== 'true';
        h.setAttribute('aria-expanded', expand ? 'true' : 'false');
        list.hidden = !expand;
      });
    });
    Array.prototype.forEach.call(drawer.querySelectorAll('a[href], [data-demo]'), function (a) {
      a.addEventListener('click', function () { setDrawer(false, false); });
    });
  }

  // ── Escape and resizing ───────────────────────────────────
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' && e.key !== 'Esc') return;
    if (open) closePanel(true);
    else if (drawerOpen()) setDrawer(false, true);
  });

  function onBreakpoint(e) {
    if (e.matches) closePanel(false);       // to phone: no desktop panel
    else if (drawerOpen()) setDrawer(false, false);
  }
  if (phone.addEventListener) phone.addEventListener('change', onBreakpoint);
  else if (phone.addListener) phone.addListener(onBreakpoint);
})();
