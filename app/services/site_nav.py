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
    # Founder Track: shown only while the track is on for this visitor
    # (switch fully on for visitors; enabled for the signed-in member).
    {
        'id': 'founders',
        'label': 'Founder Track',
        'gate': 'founder_public',
        'nojs': {'endpoint': 'founder.intro'},
        'columns': [
            {'label': 'Build your startup', 'items': [
                {'id': 'fnd_how', 'title': 'How it works',
                 'desc': 'Pitch, evaluate, build the team, execute', 'endpoint': 'founder.intro', 'anchor': 'how'},
                {'id': 'fnd_report', 'title': 'Readiness report',
                 'desc': 'What your documents prove today', 'endpoint': 'founder.intro', 'anchor': 'report'},
            ]},
            {'label': 'Show investors', 'items': [
                {'id': 'fnd_startup_page', 'title': 'Startup page',
                 'desc': 'A page you publish when you are ready', 'endpoint': 'founder.intro', 'anchor': 'page'},
                {'id': 'fnd_start', 'title': 'Start your venture',
                 'desc': 'The evaluation preview is free', 'endpoint': 'founder.intro', 'anchor': 'start'},
            ]},
        ],
        'promo': {
            'id': 'promo_founders',
            'title': 'Free evaluation preview',
            'desc': 'See your rung on the evidence ladder before you pay anything.',
            'link_label': 'Start your venture',
            'endpoint': 'founder.intro', 'anchor': 'start',
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

# ── Signed-in members ────────────────────────────────────────────────────────
# Same panel style, compact (one column under its trigger). Role links show only
# to that role. The account menu is labelled with the member's name.
# 'target': 'bio_editor' resolves to the member's own editor (or Settings →
# Profile while they have no username yet). 'action': 'signout' is the sign-out
# button. Gates: 'blog', 'blog_post', 'guide', 'founder'.

MEMBER_GROUPS = [
    {
        'id': 'my_work',
        'label': 'My work',
        'nojs': {'endpoint': 'pages.dashboard'},
        'items': [
            {'id': 'm_simulations', 'title': 'Simulations',
             'desc': 'Your runs and what the agents are doing', 'endpoint': 'pages.dashboard'},
            {'id': 'm_bio_page', 'title': 'My bio page',
             'desc': 'Edit the page that captures leads for you', 'target': 'bio_editor'},
            {'id': 'm_resumes', 'title': 'Resumes',
             'desc': 'The source data behind your plans', 'endpoint': 'pages.resumes_view'},
            {'id': 'm_contacts', 'title': 'Contacts',
             'desc': 'Everyone your agents work with', 'endpoint': 'pages.contacts_list'},
            {'id': 'm_guide', 'title': 'SME guide',
             'desc': 'Ask an expert-trained guide (beta)', 'endpoint': 'guide.console_page', 'gate': 'guide'},
            {'id': 'm_founder', 'title': 'Founder Track',
             'desc': 'Evaluate your startup plan and work the next step', 'endpoint': 'founder.intro',
             'gate': 'founder'},
        ],
    },
    {
        'id': 'community',
        'label': 'Community',
        'nojs': {'endpoint': 'social.activity_feed'},
        'items': [
            {'id': 'm_feed', 'title': 'Feed',
             'desc': 'Updates from your connections', 'endpoint': 'social.activity_feed'},
            {'id': 'm_people', 'title': 'People on SimiSI',
             'desc': 'Browse live bio pages', 'endpoint': 'public.explore'},
            {'id': 'm_chats', 'title': 'My chats',
             'desc': 'Conversations with other members', 'endpoint': 'social.my_chats'},
            {'id': 'm_journal', 'title': 'The Journal',
             'desc': 'Notes on careers and income', 'endpoint': 'pages.blog_index', 'gate': 'blog'},
            {'id': 'm_write', 'title': 'Write a post',
             'desc': "Share what you're learning", 'endpoint': 'pages.blog_new', 'gate': 'blog_post'},
        ],
    },
]

# Members also get the public menu, as one 'Explore SimiSI' panel built from
# GROUPS and LINKS above (same items and ids; promo cards left out).
MEMBER_EXPLORE = {'id': 'explore', 'label': 'Explore SimiSI', 'nojs': {'endpoint': 'pages.index'}}

MEMBER_ROLE_LINKS = [
    {'id': 'm_partner', 'title': 'Partner dashboard', 'endpoint': 'pages.partner_dashboard', 'role': 'partner'},
    {'id': 'm_sme', 'title': 'SME Console', 'endpoint': 'pages.sme_console', 'role': 'sme'},
    {'id': 'm_admin', 'title': 'Admin', 'endpoint': 'pages.admin_view', 'role': 'admin'},
]

MEMBER_ACCOUNT = {
    'id': 'account',
    'nojs': {'endpoint': 'pages.settings_profile'},
    'items': [
        {'id': 'm_settings', 'title': 'Settings',
         'desc': 'Profile, visibility and security', 'endpoint': 'pages.settings_profile'},
        {'id': 'm_integrations', 'title': 'Integrations',
         'desc': 'Connect the tools your agents use', 'endpoint': 'pages.settings_integrations'},
        {'id': 'm_notifications', 'title': 'Notifications',
         'desc': 'Email, sending and alerts', 'endpoint': 'pages.settings_notifications'},
        {'id': 'm_demo', 'title': 'Watch the demo', 'desc': 'See SimiSI in action', 'action': 'demo'},
        {'id': 'm_help', 'title': 'About Simi', 'desc': 'How your assistant works', 'endpoint': 'public.simi_help'},
        {'id': 'm_signout', 'title': 'Sign out', 'action': 'signout'},
    ],
}


def _ids(include_promos: bool = True):
    for g in GROUPS:
        for c in g['columns']:
            for it in c['items']:
                yield it['id']
        if include_promos:
            yield g['promo']['id']
    for it in LINKS + ACTIONS:
        yield it['id']
    yield 'm_dashboard'
    for g in MEMBER_GROUPS + [MEMBER_ACCOUNT]:
        for it in g['items']:
            yield it['id']
    for it in MEMBER_ROLE_LINKS:
        yield it['id']


PANEL_IDS = frozenset([g['id'] for g in GROUPS] + [g['id'] for g in MEMBER_GROUPS]
                      + [MEMBER_ACCOUNT['id'], MEMBER_EXPLORE['id']])
ITEM_IDS = frozenset(_ids())


def _gates() -> dict:
    try:
        from app.services import blog_service
        blog = bool(blog_service.nav_visible())
    except Exception:
        blog = False
    return {'blog': blog, 'founder_public': _founder_public()}


def _founder_public() -> bool:
    """The Founder Track panel: a signed-in member sees it when the track is
    enabled for them; a visitor only when the switch is fully on."""
    try:
        from flask_login import current_user
        from app.services import founder_feature
        if getattr(current_user, 'is_authenticated', False):
            return bool(founder_feature.is_enabled(current_user))
        return founder_feature.mode() == 'on'
    except Exception:
        return False


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
        if g.get('gate') and not gates.get(g['gate'], False):
            continue
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


def _member_gates(user) -> dict:
    gates = _gates()
    try:
        from app.services import blog_service
        gates['blog_post'] = gates['blog'] and bool(blog_service.can_publish(user)[0])
    except Exception:
        gates['blog_post'] = False
    try:
        from app.services import guide_service
        gates['guide'] = bool(guide_service.is_enabled(user))
    except Exception:
        gates['guide'] = False
    # Founder Track (FOUNDER_PLAN.md WP7): opt-in, hidden while the switch is off.
    try:
        from app.services import founder_feature
        gates['founder'] = bool(founder_feature.is_enabled(user))
    except Exception:
        gates['founder'] = False
    return gates


def _member_href(spec: dict, user) -> tuple:
    from flask import url_for
    if spec.get('target') == 'bio_editor':
        try:
            from app.models.profile import UserProfile
            profile = UserProfile.query.filter_by(user_id=user.id).first()
            if profile and profile.username:
                return url_for('pages.bio_editor', slug=profile.username), False
        except Exception:
            pass
        return url_for('pages.settings_profile'), False
    return _href(spec)


def _member_item(spec: dict, user) -> dict:
    out = {'id': spec['id'], 'title': spec['title'], 'desc': spec.get('desc', '')}
    if spec.get('action'):
        out.update(action=spec['action'], href=None, external=False)
    else:
        out['href'], out['external'] = _member_href(spec, user)
    return out


def resolve_member(user, is_sme: bool = False) -> dict:
    """The signed-in nav for this member: gates and roles applied."""
    from flask import url_for
    gates = _member_gates(user)
    groups = []
    for g in MEMBER_GROUPS:
        items = [_member_item(it, user) for it in g['items'] if gates.get(it.get('gate', ''), True)]
        groups.append({'id': g['id'], 'label': g['label'], 'nojs_href': _href(g['nojs'])[0], 'items': items})
    roles = {'partner': bool(getattr(user, 'is_partner', False)), 'sme': bool(is_sme),
             'admin': bool(getattr(user, 'is_admin', False))}
    role_links = [_member_item(it, user) for it in MEMBER_ROLE_LINKS if roles[it['role']]]
    name = (getattr(user, 'full_name', None) or getattr(user, 'email', None) or 'Account').strip()
    account = {'id': MEMBER_ACCOUNT['id'], 'label': name, 'nojs_href': _href(MEMBER_ACCOUNT['nojs'])[0],
               'items': [_member_item(it, user) for it in MEMBER_ACCOUNT['items']]}
    public = resolve()
    explore = {'id': MEMBER_EXPLORE['id'], 'label': MEMBER_EXPLORE['label'],
               'nojs_href': _href(MEMBER_EXPLORE['nojs'])[0],
               'groups': [{'id': g['id'], 'label': g['label'], 'columns': g['columns']} for g in public['groups']],
               'links': public['links']}
    return {'dashboard': {'id': 'm_dashboard', 'title': 'Dashboard', 'href': url_for('pages.dashboard')},
            'groups': groups, 'explore': explore, 'role_links': role_links, 'account': account}
