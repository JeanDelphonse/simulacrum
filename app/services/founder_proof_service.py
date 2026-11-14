"""Founder Track proof-of-concept path (FOUNDER_PLAN.md WP5; PRD "Proof of concept path").

The evidence ladder moves one rung at a time, and only on evidence recorded here:

    rung 3  Agreed proof of concept: a proof_plan with a named customer and
            success criteria set in advance is marked agreed.
    rung 4  Shown benefit: a proof_result records MEASURED values for every
            criterion of an agreed plan.
    rung 5  Paid: a measured proof_result has a payment or signed commitment.

Rungs 1-2 come from the founder's documents (founder_evaluation_service) or an
admin. sync_rung() never skips a rung: it climbs one step per call to set_rung,
each with its own reason, and stops at the first rung whose evidence is missing.

The benefits case is written from measured results only. Estimates are listed
apart and labelled as estimates; with no measured result it refuses. A customer
quote appears only when the founders recorded the customer's permission.

Nothing here contacts a customer. Every write is audit-logged.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

TEXT_MAX = 4000
SHORT_MAX = 200
CRITERIA_MAX = 8
CRITERION_KINDS = ('time', 'cost', 'revenue', 'risk', 'other')
KIND_LABELS = {'time': 'Time', 'cost': 'Cost', 'revenue': 'Revenue', 'risk': 'Risk', 'other': 'Other'}
BASES = ('measured', 'estimate')
PAYMENT_KINDS = ('paid', 'commitment')
ID_MAX = 32

# Plan fields a customer has to see before they can say yes (PRD: the one-page plan).
REQUIRED_TO_AGREE = (('customer_name', 'the customer'), ('pain', 'the pain being tested'),
                     ('baseline', "the customer's current baseline"), ('smallest_build', 'the smallest build'),
                     ('duration', 'the duration'), ('on_success', 'what happens on success'))


class ProofError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(value, limit: int = TEXT_MAX) -> str:
    text = str(value or '').replace('\r\n', '\n').strip()
    text = re.sub(r'[ \t]+', ' ', text)
    if len(text) > limit:
        raise ProofError('One of the fields is too long (%d characters at most).' % limit)
    return text


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _log(venture_id, kind, note, user_id=None):
    from app.services import founder_ventures
    founder_ventures.log_event(venture_id, kind, note, user_id=user_id)


def _publish(venture_id, event, data):
    try:
        from app.services import founder_stream
        founder_stream.publish(venture_id, event, data, commit=False)
    except Exception as exc:        # the stream is a convenience; the audit log is the record
        logger.debug('founder stream publish skipped: %s', exc)


def _refresh_plan(venture):
    """Let the plan engine (WP4) recompute step states, when it exists."""
    try:
        from app.services import founder_orchestrator
        founder_orchestrator.refresh(venture)
    except (ImportError, AttributeError):
        pass
    except Exception as exc:
        logger.warning('founder plan refresh after proof change failed: %s', exc)


def _ladder():
    try:
        from app.services.founder_evaluation_service import LADDER
        return LADDER
    except (ImportError, AttributeError):
        return ((1, 'Claimed pain', '', None), (2, 'Confirmed pain', '', None),
                (3, 'Agreed proof of concept', '', None), (4, 'Shown benefit', '', None), (5, 'Paid', '', None))


def current_rung(venture) -> int:
    return int(getattr(venture, 'rung', None) or 1)


def _set_rung(venture, rung: int, reason: str, user_id=None) -> int:
    """founder_evaluation_service.set_rung, or an audited direct write when
    that service isn't importable. Doesn't commit."""
    try:
        from app.services import founder_evaluation_service as fes
        setter = fes.set_rung
    except (ImportError, AttributeError):
        setter = None
    if setter is not None:
        return setter(venture, rung, reason, user_id=user_id, commit=False)
    old = current_rung(venture)
    venture.rung = int(rung)
    venture.updated_at = datetime.utcnow()
    from app.models.audit_log import AuditLog
    AuditLog.log('founder_rung_changed', user_id=user_id, resource_id=venture.id,
                 metadata={'note': 'Rung %d -> %d: %s' % (old, rung, reason or '')})
    return int(rung)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def plans(venture) -> list:
    from app.models.founder import ProofPlan
    return ProofPlan.query.filter_by(venture_id=venture.id).order_by(ProofPlan.created_at.desc()).all()


def current_plan(venture):
    """The newest plan (draft, agreed or complete), or None."""
    rows = plans(venture)
    return rows[0] if rows else None


def get_plan(venture, plan_id: str):
    from app.extensions import db
    from app.models.founder import ProofPlan
    ok = isinstance(plan_id, str) and 0 < len(plan_id) <= ID_MAX
    p = db.session.get(ProofPlan, plan_id) if ok else None
    if p is None or p.venture_id != venture.id:
        raise ProofError('That proof-of-concept plan was not found.')
    return p


def latest_result(plan):
    from app.models.founder import ProofResult
    if plan is None:
        return None
    return (ProofResult.query.filter_by(proof_plan_id=plan.id)
            .order_by(ProofResult.created_at.desc()).first())


def latest_measured_result(plan):
    """The newest result with every criterion measured (rung 4 evidence)."""
    from app.models.founder import ProofResult
    if plan is None:
        return None
    return (ProofResult.query.filter_by(proof_plan_id=plan.id, measured=True)
            .order_by(ProofResult.created_at.desc()).first())


def _paid_result(plan):
    from app.models.founder import ProofResult
    if plan is None:
        return None
    return (ProofResult.query.filter(ProofResult.proof_plan_id == plan.id, ProofResult.paid_at.isnot(None))
            .order_by(ProofResult.created_at.desc()).first())


def best_result(plan):
    """The result the page and actions work from: the paid one, else the
    newest fully measured one, else the newest."""
    return _paid_result(plan) or latest_measured_result(plan) or latest_result(plan)


def is_open(plan) -> bool:
    """A plan still waiting on the founders: a draft, or an agreed plan with
    no result recorded yet. Only one may be open at a time."""
    return plan is not None and (plan.status == 'draft' or (plan.status == 'agreed' and latest_result(plan) is None))


def open_plan(venture):
    return next((p for p in plans(venture) if is_open(p)), None)


def quote_for_use(result):
    """The customer quote, only when the founders recorded permission to use it."""
    if result is None or not result.quote_permission:
        return None
    return (result.quote_text or '').strip() or None


# ---------------------------------------------------------------------------
# Evidence and rungs
# ---------------------------------------------------------------------------

def _agreed_plans(venture) -> list:
    return [p for p in plans(venture)
            if p.status in ('agreed', 'complete') and p.agreed_at is not None
            and (p.customer_name or '').strip() and (p.success_criteria or [])]


def _measured_results(venture) -> list:
    from app.models.founder import ProofResult
    agreed = {p.id for p in _agreed_plans(venture)}
    if not agreed:
        return []
    return [r for r in ProofResult.query.filter_by(venture_id=venture.id, measured=True).all()
            if r.proof_plan_id in agreed]


def has_evidence(venture, rung: int) -> bool:
    """Whether the proof path holds the evidence for this rung (3-5)."""
    if rung == 3:
        return bool(_agreed_plans(venture))
    if rung == 4:
        return bool(_measured_results(venture))
    if rung == 5:
        return any(r.paid_at is not None for r in _measured_results(venture))
    return False


def sync_rung(venture, user_id=None, commit: bool = True) -> list:
    """Climb the ladder one rung at a time while the next rung's evidence
    exists. Rungs 1-2 aren't decided here, so a venture below rung 2 doesn't
    move. Never lowers the rung. Returns the rungs reached, in order."""
    from app.extensions import db
    reasons = {3: 'A named customer agreed a proof-of-concept plan with success criteria set in advance.',
               4: 'Results measured against the agreed criteria were recorded.',
               5: 'The customer paid for a pilot or signed a commitment.'}
    reached = []
    while True:
        cur = current_rung(venture)
        nxt = cur + 1
        if cur < 2 or nxt > 5 or not has_evidence(venture, nxt):
            break
        _set_rung(venture, nxt, reasons[nxt], user_id=user_id)
        if current_rung(venture) != nxt:          # a setter that refused: stop rather than loop
            break
        reached.append(nxt)
    if commit:
        db.session.commit()
    if reached:
        _complete_proof_steps(venture, reached, user_id)
        _refresh_plan(venture)
    return reached


PROOF_STEP_CODES = {3: 'agree', 4: 'run', 5: 'paid'}


def _complete_proof_steps(venture, reached: list, user_id=None) -> None:
    """Mark the plan's matching proof steps done (WP4 founder_orchestrator),
    when the plan engine exists and the step is open. A step still locked
    behind its prerequisites is left for the founders."""
    try:
        from app.services import founder_orchestrator as orch
        find, complete, open_states = orch.proof_step, orch.complete, orch.OPEN
    except (ImportError, AttributeError):
        return
    from app.extensions import db
    from app.models.user import User
    user = db.session.get(User, user_id) if user_id else None
    for rung in reached:
        try:
            step = find(venture, PROOF_STEP_CODES[rung])
            if step is not None and step.state in open_states:
                complete(step, user, evidence_note='Evidence recorded on the proof page (rung %d).' % rung)
        except Exception as exc:
            db.session.rollback()
            logger.warning('founder proof step %s not completed: %s', rung, exc)


def next_rung_hint(venture) -> dict:
    """What moves the venture up next, in plain words."""
    cur = current_rung(venture)
    ladder = {r[0]: r for r in _ladder()}
    if cur >= 5:
        return {'rung': None, 'text': 'Top rung reached. A customer has paid or committed.'}
    nxt = cur + 1
    label = ladder.get(nxt, (nxt, '', '', None))[1]
    if cur < 2:
        text = ('Rung 2 comes first: target customers describe the pain and what it costs them today, in their '
                'own words. An agreed plan you record here counts as soon as you reach rung 2.')
    elif nxt == 3:
        text = 'Agree this plan with a named customer, with the success criteria written down before you start.'
    elif nxt == 4:
        text = 'Run the proof of concept, then record the measured result for every success criterion.'
    else:
        text = 'Record the payment for a pilot or the commitment the customer signed.'
    return {'rung': nxt, 'label': label, 'text': text}


# ---------------------------------------------------------------------------
# Proof-of-concept plan
# ---------------------------------------------------------------------------

def _criteria(raw) -> list:
    if raw in (None, ''):
        return []
    if not isinstance(raw, list):
        raise ProofError('Success criteria must be a list.')
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise ProofError('Each success criterion needs a measure and a target.')
        metric = _clean(item.get('metric'), SHORT_MAX)
        target = _clean(item.get('target'), SHORT_MAX)
        how = _clean(item.get('how_measured'), SHORT_MAX)
        baseline = _clean(item.get('baseline'), SHORT_MAX)
        kind = str(item.get('kind') or 'other').strip().lower()
        if not (metric or target or how or baseline):
            continue                           # an empty row in the form
        if not metric or not target or not how:
            raise ProofError('Each success criterion needs what is measured, the target and how it is measured.')
        if kind not in CRITERION_KINDS:
            kind = 'other'
        out.append({'id': 'c%d' % (len(out) + 1), 'metric': metric, 'target': target, 'how_measured': how,
                    'baseline': baseline, 'kind': kind})
    if len(out) > CRITERIA_MAX:
        raise ProofError('Keep it to %d success criteria at most.' % CRITERIA_MAX)
    return out


def _provides(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}

    def lines(v):
        if isinstance(v, list):
            v = '\n'.join(str(x) for x in v)
        return [s for s in (_clean(x, SHORT_MAX) for x in str(v or '').split('\n')) if s][:10]
    return {'founder': lines(raw.get('founder')), 'customer': lines(raw.get('customer'))}


def save_plan(venture, user, data: dict, plan_id: str = None):
    """Create a draft plan, or update a draft. An agreed plan is frozen: its
    success criteria were set in advance and can't change afterwards."""
    from app.extensions import db
    from app.models.founder import ProofPlan
    data = data or {}
    values = {'customer_name': _clean(data.get('customer_name'), SHORT_MAX) or None,
              'duration': _clean(data.get('duration'), 100) or None,
              'success_criteria': _criteria(data.get('success_criteria')),
              'provides': _provides(data.get('provides'))}
    for field in ('pain', 'baseline', 'smallest_build', 'on_success'):
        values[field] = _clean(data.get(field)) or None
    if plan_id:
        p = get_plan(venture, plan_id)
        if p.status != 'draft':
            raise ProofError('This plan was agreed with the customer, so its criteria are fixed. '
                             'Start a new plan to test something else.')
    else:
        _lock_venture(venture)                  # a double submit must not create two drafts
        cur = open_plan(venture)
        if cur is not None:
            raise ProofError('Record the results of the agreed plan with %s first.' % (cur.customer_name or 'the customer')
                             if cur.status == 'agreed' else 'You already have a draft plan. Edit that one.')
        p = ProofPlan(venture_id=venture.id, status='draft', created_at=datetime.utcnow())
        db.session.add(p)
    for field, value in values.items():
        setattr(p, field, value)
    db.session.flush()
    _log(venture.id, 'proof_plan_saved', 'Proof-of-concept plan %s saved' % p.id, user_id=getattr(user, 'id', None))
    db.session.commit()
    return p


def _lock_venture(venture) -> None:
    from app.models.founder import FounderVenture
    try:
        FounderVenture.query.filter_by(id=venture.id).with_for_update().first()
    except Exception as exc:
        logger.debug('venture row lock skipped: %s', exc)


def missing_to_agree(plan) -> list:
    missing = [label for field, label in REQUIRED_TO_AGREE if not (getattr(plan, field) or '').strip()]
    if not (plan.success_criteria or []):
        missing.append('at least one success criterion')
    return missing


def agree(venture, user, plan_id: str):
    """Record that the named customer accepted the written plan. Then climb
    the ladder if the venture is at rung 2."""
    from app.extensions import db
    p = get_plan(venture, plan_id)
    if p.status != 'draft':
        raise ProofError('This plan is already agreed.')
    missing = missing_to_agree(p)
    if missing:
        raise ProofError('Before the customer can agree, the plan needs %s.' % ', '.join(missing))
    p.status, p.agreed_at = 'agreed', datetime.utcnow()
    uid = getattr(user, 'id', None)
    _log(venture.id, 'proof_plan_agreed', 'Proof-of-concept plan agreed with %s' % p.customer_name, user_id=uid)
    _publish(venture.id, 'proof_plan_agreed', {'actor_user_id': uid, 'actor_kind': 'founder',
                                               'note': 'Plan agreed with %s' % p.customer_name})
    db.session.commit()
    sync_rung(venture, user_id=uid)
    return p


# ---------------------------------------------------------------------------
# Results, benefits case and payment
# ---------------------------------------------------------------------------

def record_result(venture, user, plan_id: str, results, quote_text: str = '', quote_permission=False):
    """Record the outcome against each agreed criterion. results is a list of
    {criterion_id, value, basis: measured|estimate, met: bool|None, note}.
    The result counts as measured (rung 4 evidence) only when every criterion
    has a measured value."""
    from app.extensions import db
    from app.models.founder import ProofResult
    p = get_plan(venture, plan_id)
    if p.status == 'draft':
        raise ProofError('Agree the plan with the customer before recording results, so the criteria are set in advance.')
    if _paid_result(p) is not None:
        raise ProofError('A payment is already recorded against these results, so they stay as recorded.')
    had_measured = latest_measured_result(p) is not None
    criteria = {c['id']: c for c in (p.success_criteria or [])}
    if not isinstance(results, list):
        raise ProofError('Results must be a list, one per success criterion.')
    by_id = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        cid = str(item.get('criterion_id') or '')
        if cid not in criteria:
            raise ProofError('A result refers to a criterion that is not in the agreed plan.')
        value = _clean(item.get('value'), SHORT_MAX)
        if not value:
            continue
        basis = str(item.get('basis') or 'measured').strip().lower()
        if basis not in BASES:
            raise ProofError('Say whether each result was measured or is an estimate.')
        met = item.get('met')
        met = None if met in (None, '') else _bool(met)
        by_id[cid] = {'criterion_id': cid, 'metric': criteria[cid]['metric'], 'kind': criteria[cid]['kind'],
                      'target': criteria[cid]['target'], 'baseline': criteria[cid].get('baseline') or '',
                      'value': value, 'basis': basis, 'met': met, 'note': _clean(item.get('note'), SHORT_MAX)}
    if not by_id:
        raise ProofError('Enter at least one result.')
    rows = [by_id[c] for c in criteria if c in by_id]
    measured = len(rows) == len(criteria) and all(r['basis'] == 'measured' for r in rows)
    if had_measured and not measured:
        raise ProofError('Every criterion already has a measured result. To update them, enter measured values '
                         'for every criterion.')
    quote_text = _clean(quote_text, 1000)
    permission = _bool(quote_permission) and bool(quote_text)
    r = ProofResult(venture_id=venture.id, proof_plan_id=p.id, results=rows, measured=measured,
                    quote_text=quote_text or None, quote_permission=permission, created_at=datetime.utcnow())
    db.session.add(r)
    if measured:
        p.status = 'complete'                  # complete = every criterion measured
    uid = getattr(user, 'id', None)
    db.session.flush()
    _log(venture.id, 'proof_result_recorded',
         'Results recorded for plan %s (%s)' % (p.id, 'all measured' if measured else 'not all measured'), user_id=uid)
    _publish(venture.id, 'proof_result_recorded', {'actor_user_id': uid, 'actor_kind': 'founder',
                                                   'note': 'Proof-of-concept results recorded'})
    db.session.commit()
    sync_rung(venture, user_id=uid)
    return r


def benefits_case(venture, user, plan_id: str) -> str:
    """Write the one-page benefits case from the latest result and store it.
    Uses measured results only; estimates are listed apart and labelled.
    Raises ProofError when nothing was measured."""
    from app.extensions import db
    p = get_plan(venture, plan_id)
    r = best_result(p)
    if r is None:
        raise ProofError('Record the results first.')
    rows = r.results or []
    measured = [x for x in rows if x.get('basis') == 'measured']
    estimates = [x for x in rows if x.get('basis') != 'measured']
    if not measured:
        raise ProofError('A benefits case needs at least one measured result. Estimates alone are not evidence.')
    agreed = p.agreed_at.strftime('%Y-%m-%d') if p.agreed_at else 'in advance'
    out = ['Benefits case: proof of concept with %s' % p.customer_name,
           'Success criteria agreed on %s, before the work started.' % agreed, '']
    if p.baseline:
        out += ['Before: %s' % p.baseline, '']
    out.append('Measured results')
    for x in measured:
        line = '- %s (%s): ' % (x['metric'], KIND_LABELS.get(x.get('kind'), 'Other').lower())
        line += ('before %s, after %s' % (x['baseline'], x['value'])) if x.get('baseline') else ('result %s' % x['value'])
        line += '. Target %s' % x['target']
        if x.get('met') is True:
            line += ', met'
        elif x.get('met') is False:
            line += ', not met'
        out.append(line + '.')
    if estimates:
        out += ['', 'Estimates (not measured)']
        for x in estimates:
            out.append('- %s: %s (estimate). Target %s.' % (x['metric'], x['value'], x['target']))
    missing = [c['metric'] for c in (p.success_criteria or []) if c['id'] not in {x['criterion_id'] for x in rows}]
    if missing:
        out += ['', 'Not reported: %s.' % ', '.join(missing)]
    quote = quote_for_use(r)
    if quote:
        out += ['', 'In the customer\'s words: "%s" (%s)' % (quote, p.customer_name)]
    text = '\n'.join(out)
    r.benefits_text = text
    _log(venture.id, 'benefits_case_written', 'Benefits case written for plan %s' % p.id,
         user_id=getattr(user, 'id', None))
    db.session.commit()
    return text


def record_payment(venture, user, plan_id: str, kind: str = 'paid', note: str = ''):
    """The customer paid for a pilot or signed a commitment. Needs a measured
    result first (rung 4 evidence). Records the founders' statement; SimiSI
    never takes or moves the money."""
    from app.extensions import db
    p = get_plan(venture, plan_id)
    kind = str(kind or 'paid').strip().lower()
    if kind not in PAYMENT_KINDS:
        raise ProofError('Choose a payment or a signed commitment.')
    if _paid_result(p) is not None:
        raise ProofError('A payment or commitment is already recorded for this proof of concept.')
    r = latest_measured_result(p)
    if r is None:
        raise ProofError('Record measured results for every criterion before recording a payment.')
    r.paid_at = datetime.utcnow()
    uid = getattr(user, 'id', None)
    note = _clean(note, 500)
    _log(venture.id, 'proof_' + kind, ('Customer paid for a pilot' if kind == 'paid' else 'Customer signed a commitment')
         + (': ' + note if note else ''), user_id=uid)
    _publish(venture.id, 'proof_paid', {'actor_user_id': uid, 'actor_kind': 'founder', 'kind': kind})
    db.session.commit()
    sync_rung(venture, user_id=uid)
    return r


# ---------------------------------------------------------------------------
# Page view
# ---------------------------------------------------------------------------

def _plan_dict(p) -> dict:
    if p is None:
        return None
    return {'id': p.id, 'status': p.status, 'customer_name': p.customer_name or '', 'pain': p.pain or '',
            'baseline': p.baseline or '', 'smallest_build': p.smallest_build or '',
            'success_criteria': p.success_criteria or [], 'duration': p.duration or '',
            'provides': p.provides or {'founder': [], 'customer': []}, 'on_success': p.on_success or '',
            'agreed_at': p.agreed_at.strftime('%Y-%m-%d') if p.agreed_at else None,
            'missing_to_agree': missing_to_agree(p) if p.status == 'draft' else []}


def _result_dict(r) -> dict:
    if r is None:
        return None
    return {'id': r.id, 'results': r.results or [], 'measured': bool(r.measured),
            'benefits_text': r.benefits_text or '', 'quote_text': r.quote_text or '',
            'quote_permission': bool(r.quote_permission), 'paid': r.paid_at is not None,
            'paid_at': r.paid_at.strftime('%Y-%m-%d') if r.paid_at else None,
            'has_measured': any(x.get('basis') == 'measured' for x in (r.results or []))}


def can_climb(venture) -> bool:
    """Whether recorded evidence would move the venture up a rung now (for
    example the evaluation reached rung 2 after a plan was agreed)."""
    cur = current_rung(venture)
    return 2 <= cur < 5 and has_evidence(venture, cur + 1)


def _plan_row(p) -> dict:
    r = best_result(p)
    if p.status == 'draft':
        state = 'Draft'
    elif r is not None and r.paid_at is not None:
        state = 'Paid'
    elif p.status == 'complete':
        state = 'Measured'
    elif r is not None:
        state = 'Results not all measured'
    else:
        state = 'Agreed, waiting for results'
    return {'id': p.id, 'customer_name': p.customer_name or 'No customer named yet', 'status': p.status,
            'state': state, 'created_at': p.created_at.strftime('%Y-%m-%d') if p.created_at else ''}


def view(venture, plan_id: str = None) -> dict:
    """The proof page. Shows plan_id when given (and it belongs to this
    venture), else the newest plan; every plan is listed so an earlier one can
    still be finished."""
    cur = current_rung(venture)
    rows = plans(venture)
    p = None
    if plan_id:
        p = next((x for x in rows if x.id == plan_id), None)
    if p is None:
        p = rows[0] if rows else None
    ladder = [{'rung': n, 'label': label, 'evidence': ev, 'reached': n <= cur, 'current': n == cur}
              for n, label, ev, _agent in _ladder()]
    blocking = next((x for x in rows if is_open(x)), None)
    return {'rung': cur, 'ladder': ladder, 'next': next_rung_hint(venture), 'plan': _plan_dict(p),
            'result': _result_dict(best_result(p)), 'past_plans': len(rows) - (1 if p else 0),
            'plans': [dict(_plan_row(x), selected=(p is not None and x.id == p.id)) for x in rows],
            'can_start_new': blocking is None, 'can_climb': can_climb(venture),
            'kinds': [(k, KIND_LABELS[k]) for k in CRITERION_KINDS], 'criteria_max': CRITERIA_MAX}
