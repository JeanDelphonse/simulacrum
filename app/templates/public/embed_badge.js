/* Simulacrum floating badge embed — simulacrumai.io/embed/{{ slug }}.js */
(function () {
  if (document.getElementById('sim-badge-{{ slug }}')) return;
  var NAME     = {{ name | tojson }};
  var AVATAR   = {{ avatar_url | tojson }};
  var BIO_URL  = {{ bio_url | tojson }};
  var INITIALS = NAME ? NAME.charAt(0).toUpperCase() : '?';

  var css = [
    '#sim-badge-{{ slug }}{position:fixed;bottom:24px;right:24px;z-index:2147483647;cursor:pointer;width:56px;height:56px;border-radius:50%;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,.2);transition:transform .15s}',
    '#sim-badge-{{ slug }}:hover{transform:scale(1.08)}',
    '#sim-badge-{{ slug }} img{width:100%;height:100%;object-fit:cover;border:2px solid #0e7490;border-radius:50%}',
    '#sim-badge-{{ slug }} .sim-initials{width:56px;height:56px;border-radius:50%;background:linear-gradient(135deg,#0e7490,#059669);display:flex;align-items:center;justify-content:center;color:#fff;font-size:22px;font-weight:700;font-family:system-ui,sans-serif}',
    '#sim-card-{{ slug }}{position:fixed;bottom:92px;right:24px;z-index:2147483647;width:280px;background:#fff;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,.18);padding:1.25rem;display:none;font-family:system-ui,-apple-system,sans-serif}',
    '#sim-card-{{ slug }} .sim-card-row{display:flex;gap:.75rem;align-items:center;margin-bottom:.875rem}',
    '#sim-card-{{ slug }} .sim-card-avatar{width:44px;height:44px;border-radius:50%;object-fit:cover;flex-shrink:0}',
    '#sim-card-{{ slug }} .sim-card-av-placeholder{width:44px;height:44px;border-radius:50%;background:linear-gradient(135deg,#0e7490,#059669);display:flex;align-items:center;justify-content:center;color:#fff;font-size:17px;font-weight:700;flex-shrink:0}',
    '#sim-card-{{ slug }} .sim-card-name{font-weight:700;font-size:.92rem;color:#111827}',
    '#sim-card-{{ slug }} .sim-card-cta{display:block;text-align:center;background:#0e7490;color:#fff;padding:.6rem;border-radius:8px;font-size:.88rem;font-weight:600;text-decoration:none;transition:background .15s}',
    '#sim-card-{{ slug }} .sim-card-cta:hover{background:#0c6480}',
    '#sim-card-{{ slug }} .sim-powered{text-align:center;margin-top:.5rem;font-size:10px;color:#9ca3af}',
    '#sim-card-{{ slug }} .sim-powered a{color:#9ca3af;text-decoration:none}',
  ].join('');

  var style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  var badge = document.createElement('div');
  badge.id = 'sim-badge-{{ slug }}';
  badge.innerHTML = AVATAR
    ? '<img src="' + AVATAR + '" alt="' + NAME + '">'
    : '<div class="sim-initials">' + INITIALS + '</div>';

  var card = document.createElement('div');
  card.id = 'sim-card-{{ slug }}';
  card.innerHTML =
    '<div class="sim-card-row">' +
      (AVATAR
        ? '<img class="sim-card-avatar" src="' + AVATAR + '" alt="' + NAME + '">'
        : '<div class="sim-card-av-placeholder">' + INITIALS + '</div>') +
      '<div class="sim-card-name">' + NAME + '</div>' +
    '</div>' +
    '<a class="sim-card-cta" href="' + BIO_URL + '" target="_blank" rel="noopener">Chat with me</a>' +
    '<div class="sim-powered"><a href="https://simulacrumai.io?src=embed" target="_blank" rel="noopener">Powered by Simulacrum</a></div>';

  document.body.appendChild(badge);
  document.body.appendChild(card);

  badge.addEventListener('click', function () {
    card.style.display = card.style.display === 'block' ? 'none' : 'block';
  });

  document.addEventListener('click', function (e) {
    if (!badge.contains(e.target) && !card.contains(e.target)) {
      card.style.display = 'none';
    }
  });
}());
