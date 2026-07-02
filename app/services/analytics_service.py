"""
Analytics data aggregation service — SIM-PRD-ANALYTICS-001.
All queries accept start_dt / end_dt as datetime objects.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from sqlalchemy import func, text

logger = logging.getLogger(__name__)

# Cost per token (USD) by model family
_COST = {
    'haiku':  {'in': 0.80 / 1_000_000, 'out': 4.00 / 1_000_000},
    'sonnet': {'in': 3.00 / 1_000_000, 'out': 15.00 / 1_000_000},
    'opus':   {'in': 15.0 / 1_000_000, 'out': 75.00 / 1_000_000},
}

def _model_family(model_str: str) -> str:
    m = (model_str or '').lower()
    if 'haiku'  in m: return 'haiku'
    if 'opus'   in m: return 'opus'
    return 'sonnet'

def _token_cost(prompt_tokens, completion_tokens, model_str):
    fam   = _model_family(model_str)
    rates = _COST.get(fam, _COST['sonnet'])
    return (prompt_tokens or 0) * rates['in'] + (completion_tokens or 0) * rates['out']

def _prev_period(start_dt, end_dt):
    delta = end_dt - start_dt
    return start_dt - delta, start_dt

def _pct_change(current, previous):
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)

def _date_labels(start_dt, end_dt):
    labels, d = [], start_dt.date()
    while d <= end_dt.date():
        labels.append(d.isoformat())
        d += timedelta(days=1)
    return labels


# ---------------------------------------------------------------------------
# Summary bar
# ---------------------------------------------------------------------------

def get_summary(start_dt, end_dt) -> dict:
    from app.extensions import db
    from app.models.user import User
    from app.models.simulation import Simulation
    from app.models.page_view import PageView

    prev_start, prev_end = _prev_period(start_dt, end_dt)

    def _count(model, col, s, e):
        return db.session.query(func.count(col)).filter(col.between(s, e)).scalar() or 0

    users_now  = _count(User, User.created_at, start_dt, end_dt)
    users_prev = _count(User, User.created_at, prev_start, prev_end)
    total_users = User.query.count()

    sims_active = Simulation.query.filter_by(lifecycle_phase='active').count()
    sims_prev   = Simulation.query.filter(
        Simulation.created_at.between(prev_start, prev_end)).count()
    sims_now    = Simulation.query.filter(
        Simulation.created_at.between(start_dt, end_dt)).count()

    rev_now  = db.session.query(
        func.coalesce(func.sum(Simulation.amount_charged_cents), 0)
    ).filter(Simulation.status == 'complete',
             Simulation.created_at.between(start_dt, end_dt)).scalar() or 0
    rev_prev = db.session.query(
        func.coalesce(func.sum(Simulation.amount_charged_cents), 0)
    ).filter(Simulation.status == 'complete',
             Simulation.created_at.between(prev_start, prev_end)).scalar() or 0

    views_now  = db.session.query(func.count(PageView.id)).filter(
        PageView.created_at.between(start_dt, end_dt)).scalar() or 0
    views_prev = db.session.query(func.count(PageView.id)).filter(
        PageView.created_at.between(prev_start, prev_end)).scalar() or 0

    return {
        'total_users':      {'value': total_users, 'period_new': users_now,
                             'trend': _pct_change(users_now, users_prev)},
        'active_sims':      {'value': sims_active, 'period_new': sims_now,
                             'trend': _pct_change(sims_now, sims_prev)},
        'revenue_cents':    {'value': rev_now,
                             'trend': _pct_change(rev_now, rev_prev)},
        'page_views':       {'value': views_now,
                             'trend': _pct_change(views_now, views_prev)},
    }


# ---------------------------------------------------------------------------
# Card 1 — Traffic & Page Views
# ---------------------------------------------------------------------------

def get_traffic(start_dt, end_dt) -> dict:
    from app.extensions import db
    from app.models.page_view import PageView

    total = db.session.query(func.count(PageView.id)).filter(
        PageView.created_at.between(start_dt, end_dt)).scalar() or 0
    unique = db.session.query(func.count(func.distinct(PageView.visitor_id))).filter(
        PageView.created_at.between(start_dt, end_dt)).scalar() or 0

    # Daily series
    rows = db.session.query(
        func.date(PageView.created_at).label('d'),
        func.count(PageView.id).label('views'),
        func.count(func.distinct(PageView.visitor_id)).label('uniq'),
    ).filter(PageView.created_at.between(start_dt, end_dt)
    ).group_by(func.date(PageView.created_at)).order_by('d').all()

    daily_map = {str(r.d): (r.views, r.uniq) for r in rows}
    labels = _date_labels(start_dt, end_dt)
    daily_views = [daily_map.get(l, (0, 0))[0] for l in labels]
    daily_uniq  = [daily_map.get(l, (0, 0))[1] for l in labels]

    # Top 20 pages
    top = db.session.query(
        PageView.path,
        func.count(PageView.id).label('views'),
        func.count(func.distinct(PageView.visitor_id)).label('unique_visitors'),
    ).filter(PageView.created_at.between(start_dt, end_dt)
    ).group_by(PageView.path).order_by(text('views DESC')).limit(20).all()

    # Referrer breakdown (domain extraction done in Python)
    ref_rows = db.session.query(
        PageView.referrer,
        func.count(PageView.id).label('c'),
    ).filter(PageView.created_at.between(start_dt, end_dt),
             PageView.referrer.isnot(None)
    ).group_by(PageView.referrer).all()

    ref_map: dict = {}
    for r in ref_rows:
        src = _referrer_source(r.referrer or '')
        ref_map[src] = ref_map.get(src, 0) + r.c
    total_ref = sum(ref_map.values()) or 1
    referrers = [{'source': s, 'visits': v, 'pct': round(v/total_ref*100, 1)}
                 for s, v in sorted(ref_map.items(), key=lambda x: -x[1])]

    return {
        'summary': {'total_views': total, 'unique_visitors': unique},
        'chart': {'labels': labels, 'views': daily_views, 'unique': daily_uniq},
        'top_pages': [{'path': r.path, 'views': r.views,
                        'unique_visitors': r.unique_visitors,
                        'is_bio': r.path.startswith('/u/')} for r in top],
        'referrers': referrers,
    }

def _referrer_source(ref: str) -> str:
    if not ref: return 'Direct'
    for kw, label in [('google', 'Google'), ('linkedin', 'LinkedIn'),
                       ('twitter', 'Twitter'), ('facebook', 'Facebook'),
                       ('instagram', 'Instagram')]:
        if kw in ref.lower(): return label
    return 'Other'


# ---------------------------------------------------------------------------
# Card 2 — User Activity & Retention
# ---------------------------------------------------------------------------

def get_users(start_dt, end_dt) -> dict:
    from app.extensions import db
    from app.models.user import User
    from app.models.user_event import UserEvent

    new_signups = User.query.filter(User.created_at.between(start_dt, end_dt)).count()

    # Daily signups
    rows = db.session.query(
        func.date(User.created_at).label('d'),
        func.count(User.id).label('c'),
    ).filter(User.created_at.between(start_dt, end_dt)
    ).group_by(func.date(User.created_at)).order_by('d').all()
    signup_map = {str(r.d): r.c for r in rows}
    labels = _date_labels(start_dt, end_dt)
    daily_signups = [signup_map.get(l, 0) for l in labels]

    # DAU/MAU
    today = datetime.utcnow().date()
    dau = db.session.query(func.count(func.distinct(UserEvent.user_id))).filter(
        func.date(UserEvent.created_at) == today,
        UserEvent.event_type == UserEvent.LOGIN,
    ).scalar() or 0

    mau_start = datetime.utcnow() - timedelta(days=30)
    mau = db.session.query(func.count(func.distinct(UserEvent.user_id))).filter(
        UserEvent.created_at >= mau_start,
        UserEvent.event_type == UserEvent.LOGIN,
    ).scalar() or 0

    stickiness = round(dau / max(mau, 1) * 100, 1)

    # Onboarding funnel (use all-time counts for funnel shape)
    total_users = User.query.count()
    verified    = User.query.filter(User.email_verified == True).count()
    def _evt_count(etype):
        return db.session.query(func.count(func.distinct(UserEvent.user_id))).filter(
            UserEvent.event_type == etype).scalar() or 0

    funnel = [
        {'step': 'Account created',       'count': total_users},
        {'step': 'Email verified',         'count': verified},
        {'step': 'Resume uploaded',        'count': _evt_count(UserEvent.RESUME_UPLOADED)},
        {'step': 'Bio page published',     'count': _evt_count(UserEvent.BIO_PUBLISHED)},
        {'step': 'Simulation launched',    'count': _evt_count(UserEvent.SIMULATION_LAUNCHED)},
        {'step': 'First income captured',  'count': _evt_count(UserEvent.FIRST_INCOME)},
    ]
    for i, step in enumerate(funnel):
        prev = funnel[i-1]['count'] if i > 0 else step['count']
        step['pct'] = round(step['count'] / max(prev, 1) * 100, 1)

    # Login activity table — top 50 active users
    login_rows = db.session.query(
        UserEvent.user_id,
        func.max(UserEvent.created_at).label('last_login'),
        func.count(UserEvent.id).label('login_count'),
    ).filter(UserEvent.event_type == UserEvent.LOGIN,
             UserEvent.created_at.between(start_dt, end_dt)
    ).group_by(UserEvent.user_id
    ).order_by(text('login_count DESC')).limit(50).all()

    user_ids = [r.user_id for r in login_rows]
    users_map = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}

    login_table = [{
        'user_name':   getattr(users_map.get(r.user_id), 'name', r.user_id),
        'email':       getattr(users_map.get(r.user_id), 'email', ''),
        'last_login':  r.last_login.isoformat() if r.last_login else '',
        'login_count': r.login_count,
    } for r in login_rows]

    return {
        'summary': {'new_signups': new_signups, 'dau': dau, 'mau': mau, 'stickiness': stickiness},
        'chart':   {'labels': labels, 'signups': daily_signups},
        'funnel':  funnel,
        'login_table': login_table,
    }


# ---------------------------------------------------------------------------
# Card 3 — Simulation Metrics
# ---------------------------------------------------------------------------

def get_simulations(start_dt, end_dt) -> dict:
    from app.extensions import db
    from app.models.simulation import Simulation
    from app.models.agent_action import AgentAction

    total    = Simulation.query.count()
    active   = Simulation.query.filter_by(lifecycle_phase='active').count()
    maint    = Simulation.query.filter_by(lifecycle_phase='maintenance').count()
    dormant  = Simulation.query.filter_by(lifecycle_phase='dormant').count()

    # Lifecycle chart — daily new sims
    rows = db.session.query(
        func.date(Simulation.created_at).label('d'),
        Simulation.lifecycle_phase,
        func.count(Simulation.id).label('c'),
    ).filter(Simulation.created_at.between(start_dt, end_dt)
    ).group_by(func.date(Simulation.created_at), Simulation.lifecycle_phase
    ).order_by('d').all()

    labels = _date_labels(start_dt, end_dt)
    phase_map: dict = {l: {'active': 0, 'maintenance': 0, 'dormant': 0} for l in labels}
    for r in rows:
        d = str(r.d)
        if d in phase_map:
            phase_map[d][r.lifecycle_phase] = r.c

    # Agent popularity — count dispatches per agent type
    agent_rows = db.session.query(
        AgentAction.action_type,
        func.count(AgentAction.id).label('dispatches'),
    ).filter(AgentAction.status == AgentAction.STATUS_COMPLETE
    ).group_by(AgentAction.action_type
    ).order_by(text('dispatches DESC')).limit(20).all()

    # Completion rate (reached maintenance phase)
    completion_rate = round(maint / max(total, 1) * 100, 1)

    # Drill-down table
    sims = Simulation.query.order_by(Simulation.created_at.desc()).limit(50).all()
    from app.models.user import User as _U
    uid_set = {s.user_id for s in sims}
    umap = {u.id: u for u in _U.query.filter(_U.id.in_(uid_set)).all()} if uid_set else {}

    sim_table = [{
        'user':      getattr(umap.get(s.user_id), 'name', s.user_id),
        'sim_id':    s.id,
        'phase':     s.lifecycle_phase,
        'created':   s.created_at.date().isoformat(),
        'revenue':   (s.amount_charged_cents or 0) / 100,
    } for s in sims]

    return {
        'summary': {
            'total': total, 'active': active, 'maintenance': maint,
            'dormant': dormant, 'completion_rate': completion_rate,
        },
        'chart': {
            'labels':      labels,
            'active':      [phase_map[l]['active']      for l in labels],
            'maintenance': [phase_map[l]['maintenance'] for l in labels],
            'dormant':     [phase_map[l]['dormant']     for l in labels],
        },
        'agent_popularity': [{'agent': r.action_type, 'dispatches': r.dispatches}
                              for r in agent_rows],
        'sim_table': sim_table,
    }


# ---------------------------------------------------------------------------
# Card 4 — Revenue & Payments
# ---------------------------------------------------------------------------

def get_revenue(start_dt, end_dt) -> dict:
    from app.extensions import db
    from app.models.simulation import Simulation
    from app.models.payment_record import PaymentRecord

    # Revenue from completed simulations in the period
    rev_rows = db.session.query(
        func.date(Simulation.created_at).label('d'),
        func.coalesce(func.sum(Simulation.amount_charged_cents), 0).label('rev'),
    ).filter(Simulation.status == 'complete',
             Simulation.created_at.between(start_dt, end_dt)
    ).group_by(func.date(Simulation.created_at)).order_by('d').all()

    total_rev = db.session.query(
        func.coalesce(func.sum(Simulation.amount_charged_cents), 0)
    ).filter(Simulation.status == 'complete',
             Simulation.created_at.between(start_dt, end_dt)).scalar() or 0

    refunds = db.session.query(
        func.coalesce(func.sum(Simulation.amount_charged_cents), 0)
    ).filter(Simulation.status == 'refunded',
             Simulation.created_at.between(start_dt, end_dt)).scalar() or 0

    labels = _date_labels(start_dt, end_dt)
    rev_map = {str(r.d): r.rev for r in rev_rows}
    daily_rev = [rev_map.get(l, 0) / 100 for l in labels]

    # Payment records (voice, tier upgrades)
    pr_rows = db.session.query(
        PaymentRecord.payment_type,
        func.coalesce(func.sum(PaymentRecord.amount_cents), 0).label('rev'),
        func.count(PaymentRecord.id).label('count'),
    ).filter(PaymentRecord.status == 'succeeded',
             PaymentRecord.created_at.between(start_dt, end_dt)
    ).group_by(PaymentRecord.payment_type).all()

    by_type = {r.payment_type: {'rev': r.rev / 100, 'count': r.count} for r in pr_rows}
    by_type['simulation'] = by_type.get('simulation', {'rev': total_rev / 100, 'count':
        Simulation.query.filter(Simulation.status == 'complete',
                                Simulation.created_at.between(start_dt, end_dt)).count()})

    # Recent payments
    recent_sims = Simulation.query.filter(
        Simulation.status.in_(['complete', 'refunded']),
        Simulation.created_at.between(start_dt, end_dt),
    ).order_by(Simulation.created_at.desc()).limit(100).all()

    from app.models.user import User as _U
    uid_set = {s.user_id for s in recent_sims}
    umap = {u.id: u for u in _U.query.filter(_U.id.in_(uid_set)).all()} if uid_set else {}

    payment_table = [{
        'user':    getattr(umap.get(s.user_id), 'name', s.user_id),
        'amount':  (s.amount_charged_cents or 0) / 100,
        'type':    'simulation',
        'status':  s.status,
        'date':    s.created_at.date().isoformat(),
        'stripe':  s.stripe_charge_id or '',
    } for s in recent_sims]

    total_users = db.session.query(func.count(func.distinct(Simulation.user_id))).filter(
        Simulation.status == 'complete',
        Simulation.created_at.between(start_dt, end_dt)).scalar() or 0
    arpu = round((total_rev / 100) / max(total_users, 1), 2)

    return {
        'summary': {
            'total_revenue':  total_rev / 100,
            'refunds':        refunds / 100,
            'arpu':           arpu,
            'by_type':        by_type,
        },
        'chart':   {'labels': labels, 'simulation': daily_rev},
        'payment_table': payment_table,
    }


# ---------------------------------------------------------------------------
# Card 5 — Agent Costs
# ---------------------------------------------------------------------------

def get_costs(start_dt, end_dt) -> dict:
    from app.extensions import db
    from app.models.ai_interaction import AIInteraction
    from app.models.agent_action import AgentAction

    rows = db.session.query(
        AIInteraction.model,
        AIInteraction.interaction_type,
        func.coalesce(func.sum(AIInteraction.prompt_tokens), 0).label('inp'),
        func.coalesce(func.sum(AIInteraction.completion_tokens), 0).label('out'),
        func.count(AIInteraction.id).label('cnt'),
    ).filter(AIInteraction.created_at.between(start_dt, end_dt)
    ).group_by(AIInteraction.model, AIInteraction.interaction_type).all()

    total_cost  = 0.0
    total_inp   = 0
    total_out   = 0
    agent_cost: dict = {}
    simi_cost   = 0.0

    for r in rows:
        cost = _token_cost(r.inp, r.out, r.model)
        total_cost += cost
        total_inp  += r.inp
        total_out  += r.out
        if r.interaction_type == 'agent_action':
            agent_cost[r.model] = agent_cost.get(r.model, 0) + cost
        else:
            simi_cost += cost

    # Daily cost
    day_rows = db.session.query(
        func.date(AIInteraction.created_at).label('d'),
        AIInteraction.interaction_type,
        func.coalesce(func.sum(AIInteraction.prompt_tokens), 0).label('inp'),
        func.coalesce(func.sum(AIInteraction.completion_tokens), 0).label('out'),
        AIInteraction.model,
    ).filter(AIInteraction.created_at.between(start_dt, end_dt)
    ).group_by(func.date(AIInteraction.created_at),
               AIInteraction.interaction_type, AIInteraction.model).all()

    labels = _date_labels(start_dt, end_dt)
    day_agent: dict = {l: 0.0 for l in labels}
    day_simi:  dict = {l: 0.0 for l in labels}
    for r in day_rows:
        d = str(r.d)
        c = _token_cost(r.inp, r.out, r.model)
        if r.interaction_type == 'agent_action':
            day_agent[d] = day_agent.get(d, 0) + c
        else:
            day_simi[d]  = day_simi.get(d, 0) + c

    # Per-agent breakdown
    agent_rows = db.session.query(
        AIInteraction.interaction_type,
        AIInteraction.model,
        func.coalesce(func.sum(AIInteraction.prompt_tokens), 0).label('inp'),
        func.coalesce(func.sum(AIInteraction.completion_tokens), 0).label('out'),
        func.count(AIInteraction.id).label('dispatches'),
    ).filter(AIInteraction.interaction_type == 'agent_action',
             AIInteraction.created_at.between(start_dt, end_dt)
    ).group_by(AIInteraction.interaction_type, AIInteraction.model).all()

    agent_table = [{
        'model':      r.model,
        'dispatches': r.dispatches,
        'cost':       round(_token_cost(r.inp, r.out, r.model), 4),
        'inp_tokens': r.inp,
        'out_tokens': r.out,
    } for r in sorted(agent_rows, key=lambda x: -_token_cost(x.inp, x.out, x.model))]

    # Revenue for margin calc
    from app.models.simulation import Simulation
    rev = db.session.query(
        func.coalesce(func.sum(Simulation.amount_charged_cents), 0)
    ).filter(Simulation.status == 'complete',
             Simulation.created_at.between(start_dt, end_dt)).scalar() or 0
    rev_usd = rev / 100
    margin  = round((rev_usd - total_cost) / max(rev_usd, 0.01) * 100, 1) if rev_usd else None

    return {
        'summary': {
            'total_cost':   round(total_cost, 4),
            'simi_cost':    round(simi_cost, 4),
            'agent_cost':   round(total_cost - simi_cost, 4),
            'total_tokens': total_inp + total_out,
            'margin_pct':   margin,
        },
        'chart': {
            'labels':     labels,
            'agent_cost': [round(day_agent.get(l, 0), 4) for l in labels],
            'simi_cost':  [round(day_simi.get(l, 0), 4) for l in labels],
        },
        'agent_table': agent_table,
    }


# ---------------------------------------------------------------------------
# Card 6 — Bio Page Performance
# ---------------------------------------------------------------------------

def get_bio(start_dt, end_dt) -> dict:
    from app.extensions import db
    from app.models.bio_page import BioPage, BioChatSession, BioPageVisit

    published = BioPage.query.filter_by(status='published').count()

    views = db.session.query(func.count(BioPageVisit.id)).filter(
        BioPageVisit.visited_at.between(start_dt, end_dt)).scalar() or 0
    unique_visitors = db.session.query(
        func.count(func.distinct(BioPageVisit.visitor_id))
    ).filter(BioPageVisit.visited_at.between(start_dt, end_dt)).scalar() or 0

    sessions = db.session.query(func.count(BioChatSession.id)).filter(
        BioChatSession.created_at.between(start_dt, end_dt)).scalar() or 0

    # Daily chart
    day_rows = db.session.query(
        func.date(BioPageVisit.visited_at).label('d'),
        func.count(BioPageVisit.id).label('views'),
    ).filter(BioPageVisit.visited_at.between(start_dt, end_dt)
    ).group_by(func.date(BioPageVisit.visited_at)).order_by('d').all()

    labels = _date_labels(start_dt, end_dt)
    view_map = {str(r.d): r.views for r in day_rows}
    daily_views = [view_map.get(l, 0) for l in labels]

    # Top pages by views
    top_rows = db.session.query(
        BioPageVisit.bio_page_id,
        func.count(BioPageVisit.id).label('views'),
    ).filter(BioPageVisit.visited_at.between(start_dt, end_dt)
    ).group_by(BioPageVisit.bio_page_id
    ).order_by(text('views DESC')).limit(30).all()

    page_ids = [r.bio_page_id for r in top_rows]
    pages_map = {p.id: p for p in BioPage.query.filter(BioPage.id.in_(page_ids)).all()} if page_ids else {}
    from app.models.user import User as _U
    uid_set = {p.user_id for p in pages_map.values()}
    umap = {u.id: u for u in _U.query.filter(_U.id.in_(uid_set)).all()} if uid_set else {}

    top_pages = [{
        'user':  getattr(umap.get(pages_map[r.bio_page_id].user_id, None), 'name', ''),
        'slug':  getattr(pages_map.get(r.bio_page_id), 'slug', r.bio_page_id),
        'views': r.views,
    } for r in top_rows if r.bio_page_id in pages_map]

    return {
        'summary': {
            'published': published, 'views': views,
            'unique_visitors': unique_visitors, 'chat_sessions': sessions,
        },
        'chart':    {'labels': labels, 'views': daily_views},
        'top_pages': top_pages,
    }


# ---------------------------------------------------------------------------
# Card 7 — Email Delivery
# ---------------------------------------------------------------------------

def get_email(start_dt, end_dt) -> dict:
    from app.extensions import db
    from app.models.outreach_email import EmailLog, EmailSuppression

    sent = db.session.query(func.count(EmailLog.id)).filter(
        EmailLog.sent_at.between(start_dt, end_dt)).scalar() or 0
    opened = db.session.query(func.count(EmailLog.id)).filter(
        EmailLog.sent_at.between(start_dt, end_dt),
        EmailLog.opened_at.isnot(None)).scalar() or 0
    replied = db.session.query(func.count(EmailLog.id)).filter(
        EmailLog.sent_at.between(start_dt, end_dt),
        EmailLog.replied_at.isnot(None)).scalar() or 0
    bounced = db.session.query(func.count(EmailLog.id)).filter(
        EmailLog.sent_at.between(start_dt, end_dt),
        EmailLog.bounced_at.isnot(None)).scalar() or 0
    suppressed = EmailSuppression.query.count()

    open_rate   = round(opened  / max(sent, 1) * 100, 1)
    reply_rate  = round(replied / max(sent, 1) * 100, 1)
    bounce_rate = round(bounced / max(sent, 1) * 100, 1)

    # Daily chart
    day_rows = db.session.query(
        func.date(EmailLog.sent_at).label('d'),
        func.count(EmailLog.id).label('sent'),
        func.sum(func.IF(EmailLog.opened_at.isnot(None), 1, 0)).label('opened'),
        func.sum(func.IF(EmailLog.bounced_at.isnot(None), 1, 0)).label('bounced'),
    ).filter(EmailLog.sent_at.between(start_dt, end_dt)
    ).group_by(func.date(EmailLog.sent_at)).order_by('d').all()

    labels = _date_labels(start_dt, end_dt)
    day_map = {str(r.d): r for r in day_rows}
    daily = {
        'sent':   [getattr(day_map.get(l), 'sent',   0) or 0 for l in labels],
        'opened': [getattr(day_map.get(l), 'opened', 0) or 0 for l in labels],
        'bounced':[getattr(day_map.get(l), 'bounced',0) or 0 for l in labels],
    }

    # Bounce table
    bounce_rows = EmailLog.query.filter(
        EmailLog.sent_at.between(start_dt, end_dt),
        EmailLog.bounced_at.isnot(None),
    ).order_by(EmailLog.bounced_at.desc()).limit(50).all()

    bounce_table = [{
        'to_email':     r.to_email,
        'bounce_reason':r.bounce_reason or '',
        'action_id':    r.action_id or '',
        'date':         r.bounced_at.date().isoformat() if r.bounced_at else '',
    } for r in bounce_rows]

    return {
        'summary': {
            'sent': sent, 'opened': opened, 'replied': replied,
            'bounced': bounced, 'suppressed': suppressed,
            'open_rate': open_rate, 'reply_rate': reply_rate,
            'bounce_rate': bounce_rate,
        },
        'chart':        {'labels': labels, **daily},
        'bounce_table': bounce_table,
    }


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

def get_alerts(start_dt, end_dt) -> list:
    alerts = []
    try:
        email = get_email(start_dt, end_dt)
        if email['summary']['bounce_rate'] > 5:
            alerts.append({'severity': 'warning',
                'message': f"Bounce rate is {email['summary']['bounce_rate']}% — above 5% threshold. Review suppression list."})

        costs = get_costs(start_dt, end_dt)
        m = costs['summary'].get('margin_pct')
        if m is not None and m < 70:
            alerts.append({'severity': 'critical',
                'message': f'Gross margin is {m}% — below 70% target. Review API costs vs revenue.'})

        from app.models.user import User
        from app.extensions import db
        from datetime import timedelta as _td
        two_days_ago = datetime.utcnow() - _td(hours=48)
        recent_signup = User.query.filter(User.created_at >= two_days_ago).count()
        if recent_signup == 0:
            alerts.append({'severity': 'info',
                'message': 'No new signups in 48 hours. Check traffic sources.'})
    except Exception as exc:
        logger.warning('get_alerts failed: %s', exc)
    return alerts
