"""The visitor navigation, defined once (NAV_AUDIENCE_PLAN.md §3).

Groups, columns, items, promo cards and the top-level links live here and
nowhere else. partials/_site_nav.html renders both the desktop panels and the
phone drawer from resolve(); the item and panel ids double as the allowlist for
the nav_open / nav_click events. No prices belong in this file.
"""
from __future__ import annotations

# An item's target is one of:
#   'endpoint' (+ optional 'anchor')  url_for at request time
#   'config'   (+ 'fallback' endpoint/anchor)  an app.config URL, e.g. the Amazon listing
#   'action': 'demo'                   the demo-video modal (a button, not a link)
# 'gate' names a condition in _gates(); the item is dropped when it is false.

GROUPS = [
    {
        'id': 'professionals',
        'label': 'For professionals',
        'nojs': {'endpoint': 'pages.index', 'anchor': 'how'},
        'columns': [
            {'label': 'Plan your path', 'items': [
                {'id': 'run_simulation', 'title': 'Run a simulation',
                 'desc': '24 months of income across five layers', 'endpoint': 'pages.register_page'},
                {'id': 'how_it_works', 'title': 'How it works',
                 'desc': 'From resume to plan in five steps', 'endpoint': 'pages.index', 'anchor': 'how'},
                {'id': 'sample_simulations', 'title': 'Sample simulations',
                 'desc': 'Three careers, three stacks', 'endpoint': 'pages.index', 'anchor': 'sims'},
                {'id': 'watch_demo', 'title': 'Watch the demo',
                 'desc': 'See SimiSI in action', 'action': 'demo'},
            ]},
            {'label': 'Act on it', 'items': [
                {'id': 'bio_page', 'title': 'Free bio page',
                 'desc': 'A page that captures leads for you', 'endpoint': 'pages.index', 'anchor': 'bio'},
                {'id': 'agents', 'title': 'Agents',
                 'desc': 'They draft; you approve every send', 'endpoint': 'pages.index', 'anchor': 'act'},
                # No waitlist route yet: the #more card holds the beta-access ask.
                {'id': 'sme_console', 'title': 'SME Console',
                 'desc': 'In development', 'endpoint': 'pages.index', 'anchor': 'more'},
            ]},
            {'label': 'Learn from others', 'items': [
                {'id': 'people', 'title': 'People on SimiSI',
                 'desc': 'Browse live bio pages', 'endpoint': 'public.explore'},
                {'id': 'journal', 'title': 'The Journal',
                 'desc': 'Notes on careers and income', 'endpoint': 'pages.blog_index', 'gate': 'blog'},
                {'id': 'questions', 'title': 'Questions',
                 'desc': 'What people ask before they start', 'endpoint': 'pages.index', 'anchor': 'faq'},
            ]},
        ],
        'promo': {
            'id': 'promo_playbook',
            'title': 'The Wealth Simulation Playbook',
            'desc': 'All 49 agents written up as case studies.',
            'link_label': 'Get it on Amazon',
            'config': 'PLAYBOOK_URL', 'fallback': {'endpoint': 'pages.index', 'anchor': 'playbook'},
        },
    },
    {
        'id': 'employers',
        'label': 'For employers',
        'nojs': {'endpoint': 'marketing.transition_package'},
        'columns': [
            {'label': 'The Transition Package', 'items': [
                {'id': 'tp_included', 'title': 'What people get',
                 'desc': 'A simulation, bio page and agents for every person',
                 'endpoint': 'marketing.transition_package', 'anchor': 'included'},
                # Coaching is a card inside "What's included"; it has no anchor of its own.
                {'id': 'tp_coaching', 'title': 'Human coaching',
                 'desc': 'An optional coach for each person',
                 'endpoint': 'marketing.transition_package', 'anchor': 'included'},
            ]},
            {'label': 'For HR teams', 'items': [
                {'id': 'tp_how', 'title': 'How it works for HR',
                 'desc': 'Seats, invites and an aggregate dashboard',
                 'endpoint': 'marketing.transition_package', 'anchor': 'how'},
                {'id': 'tp_privacy', 'title': 'Privacy',
                 'desc': 'You see progress, never private plans',
                 'endpoint': 'marketing.transition_package', 'anchor': 'privacy'},
            ]},
        ],
        # Follows the Transition Package page mode (Admin: transition_package_mode).
        'promo': {
            'id': 'promo_employers',
            'by_mode': {
                'design_partner': {'title': 'Now enrolling design partners',
                                   'desc': 'Priced per seat. Shape the package with us.',
                                   'link_label': 'Become a design partner'},
                'general': {'title': 'Request a quote',
                            'desc': 'Priced per seat. Tell us your headcount and timing.',
                            'link_label': 'Request a quote'},
            },
            'endpoint': 'marketing.transition_package', 'anchor': 'inquiry',
        },
    },
]

LINKS = [
    {'id': 'playbook', 'title': 'Playbook',
     'config': 'PLAYBOOK_URL', 'fallback': {'endpoint': 'pages.index', 'anchor': 'playbook'}},
    {'id': 'pricing', 'title': 'Pricing', 'endpoint': 'pages.index', 'anchor': 'pricing'},
]

ACTIONS = [
    {'id': 'log_in', 'title': 'Log in', 'endpoint': 'pages.login_page'},
    {'id': 'get_started', 'title': 'Get started free', 'endpoint': 'pages.register_page'},
]


def _ids(include_promos: bool = True):
    for g in GROUPS:
        for c in g['columns']:
            for it in c['items']:
                yield it['id']
        if include_promos:
            yield g['promo']['id']
    for it in LINKS + ACTIONS:
        yield it['id']


PANEL_IDS = frozenset(g['id'] for g in GROUPS)
ITEM_IDS = frozenset(_ids())


def _gates() -> dict:
    try:
        from app.services import blog_service
        blog = bool(blog_service.nav_visible())
    except Exception:
        blog = False
    return {'blog': blog}


def _mode() -> str:
    try:
        from app.services import enterprise_inquiry_service as eis
        return eis.page_mode()
    except Exception:
        return 'design_partner'


def _href(spec: dict) -> tuple:
    """(href, external) for an item spec."""
    from flask import current_app, url_for
    if spec.get('config'):
        url = (current_app.config.get(spec['config']) or '').strip()
        if url:
            return url, True
        spec = spec['fallback']
    url = url_for(spec['endpoint'])
    if spec.get('anchor'):
        url += '#' + spec['anchor']
    return url, False


def _item(spec: dict) -> dict:
    out = {'id': spec['id'], 'title': spec['title'], 'desc': spec.get('desc', '')}
    if spec.get('action'):
        out.update(action=spec['action'], href=None, external=False)
    else:
        out['href'], out['external'] = _href(spec)
    return out


def resolve() -> dict:
    """The nav for this request: URLs built, gates applied, promo variant chosen."""
    gates = _gates()
    mode = _mode()
    groups = []
    for g in GROUPS:
        columns = []
        for c in g['columns']:
            items = [_item(it) for it in c['items'] if gates.get(it.get('gate', ''), True)]
            if items:
                columns.append({'label': c['label'], 'items': items})
        p = g['promo']
        text = p['by_mode'].get(mode, p['by_mode']['design_partner']) if 'by_mode' in p else p
        href, external = _href(p)
        groups.append({
            'id': g['id'], 'label': g['label'], 'nojs_href': _href(g['nojs'])[0], 'columns': columns,
            'promo': {'id': p['id'], 'title': text['title'], 'desc': text['desc'],
                      'link_label': text['link_label'], 'href': href, 'external': external},
        })
    return {'groups': groups, 'links': [_item(x) for x in LINKS], 'actions': [_item(x) for x in ACTIONS]}
