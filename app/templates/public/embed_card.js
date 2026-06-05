/* Simulacrum inline card embed — simulacrumai.io/embed/card.js */
(function () {
  var BASE = {{ base_url | tojson }};

  function buildCard(el, slug, data) {
    var ctx  = data.context || {};
    var page = data.bio_page || {};
    var name = ctx.full_name || slug;
    var title = ctx.professional_title || '';
    var avatar = ctx.hero_image_url || ctx.avatar_path || '';
    var avatarSrc = avatar ? (BASE + '/avatars/' + avatar) : '';
    var initials = name ? name.charAt(0).toUpperCase() : '?';
    var bioUrl = BASE + '/u/' + slug;

    el.style.cssText = 'display:inline-block;font-family:system-ui,-apple-system,sans-serif;';
    el.innerHTML =
      '<div style="display:flex;gap:.75rem;align-items:center;background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:.875rem 1rem;max-width:320px;box-shadow:0 1px 4px rgba(0,0,0,.06);">' +
        (avatarSrc
          ? '<img src="' + avatarSrc + '" style="width:48px;height:48px;border-radius:50%;object-fit:cover;flex-shrink:0;" alt="' + name + '">'
          : '<div style="width:48px;height:48px;border-radius:50%;background:linear-gradient(135deg,#0e7490,#059669);display:flex;align-items:center;justify-content:center;color:#fff;font-size:18px;font-weight:700;flex-shrink:0;">' + initials + '</div>') +
        '<div style="flex:1;min-width:0;">' +
          '<div style="font-weight:700;font-size:.9rem;color:#111827;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + name + '</div>' +
          (title ? '<div style="font-size:.8rem;color:#6b7280;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + title + '</div>' : '') +
        '</div>' +
        '<a href="' + bioUrl + '" target="_blank" rel="noopener" style="background:#0e7490;color:#fff;padding:.4rem .85rem;border-radius:7px;font-size:.8rem;font-weight:600;text-decoration:none;white-space:nowrap;flex-shrink:0;">Chat →</a>' +
      '</div>' +
      '<div style="text-align:right;font-size:10px;color:#9ca3af;margin-top:3px;">' +
        '<a href="https://simulacrumai.io?src=embed" target="_blank" rel="noopener" style="color:#9ca3af;text-decoration:none;">Powered by Simulacrum</a>' +
      '</div>';
  }

  var elements = document.querySelectorAll('[data-simulacrum-card]');
  elements.forEach(function (el) {
    var slug = el.getAttribute('data-simulacrum-card');
    if (!slug) return;
    fetch(BASE + '/u/' + slug + '/bio-page.json')
      .then(function (r) { return r.json(); })
      .then(function (data) { buildCard(el, slug, data); })
      .catch(function () { el.style.display = 'none'; });
  });
}());
