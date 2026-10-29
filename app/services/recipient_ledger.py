"""
Recipient ledger — each person is targeted once per agent per simulation.

Rule: within one simulation, an agent may target a given person in only one of
its runs, across every cycle. Follow-up steps of that same run (Step 2/3 of a
sequence) still go to them; a later run of the agent never does. When an agent
has no unused recipients left, the orchestrator leaves it out of the cycle.

Before this, "already contacted" meant "an email was actually sent". Sends can
be held for channel approval, so the same CRM contacts stayed "uncontacted" and
were drafted again every cycle — and once the channel was approved, the steps
from every cycle went to the same person. Research results were never checked
against earlier runs at all, and scheduled steps were created even for
prospects the send hook had skipped.

Enforcement points:
  record_*          — claim recipients when the artifact is produced
  used_keys()       — filter CRM pools, research results, existing contacts
  check_send()      — last line of defence inside send_outreach_email
  has_unused_recipients() — orchestrator eligibility
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# An exhausted agent is re-probed after this long, so research sources that
# page forward (Apollo) get another chance to surface new people.
RECHECK_DAYS = 7

_backfilled: set[tuple[str, str]] = set()


def _ledger_unavailable(where: str, exc: Exception) -> None:
    """Log loudly and reset the session. Every ledger entry point degrades to the
    pre-ledger behaviour on failure (most likely: sql/054 not yet run) so an
    orchestrator cycle is never aborted by it."""
    logger.error('recipient ledger unavailable in %s (has sql/054_agent_recipients.sql '
                 'been run?): %s', where, exc)
    try:
        from app.extensions import db
        db.session.rollback()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Keys and scope
# ---------------------------------------------------------------------------

def recipient_key(email: str = '', first_name: str = '', last_name: str = '',
                  company_name: str = '') -> str | None:
    """Lower-cased email, else ``name:<first last>|<company>``; None if neither."""
    from app.services.contact_lookup import is_fallback_email
    email = (email or '').strip().lower()
    if email and not is_fallback_email(email):
        return email[:255]
    name = ' '.join(x for x in ((first_name or '').strip(), (last_name or '').strip()) if x).lower()
    if not name:
        return None
    return ('name:%s|%s' % (name, (company_name or '').strip().lower()))[:255]


def _key_of(item) -> str | None:
    get = item.get if isinstance(item, dict) else (lambda k, d='': getattr(item, k, d))
    return recipient_key(get('email', ''), get('first_name', ''), get('last_name', ''),
                         get('company_name', ''))


def tracked_agents() -> frozenset[str]:
    """Agents whose recipients are recorded and filtered."""
    from app.services.agent_registry import CONTACT_AGENTS
    return CONTACT_AGENTS | {'consulting_outreach', 'cold_email_campaign'}


def gated_agents() -> frozenset[str]:
    """Agents left out of a cycle when they have no unused recipients — the ones
    whose output *is* their recipient list. (affiliate_program and similar keep
    running: their contacts are a by-product of a program document.)"""
    from app.services.agent_registry import RESEARCH_AGENTS, UPDATE_ONLY_CONTACT_AGENTS
    return RESEARCH_AGENTS | UPDATE_ONLY_CONTACT_AGENTS | {'consulting_outreach', 'cold_email_campaign'}


def _agent_action_ids(simulation_id: str, action_type: str) -> list[str]:
    from app.models.agent_action import AgentAction
    from app.services.agent_registry import names_for
    return [r.id for r in AgentAction.query.with_entities(AgentAction.id).filter(
        AgentAction.simulation_id == simulation_id,
        AgentAction.action_type.in_(names_for(action_type)),
    ).all()]


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def used_keys(simulation_id: str, action_type: str) -> set[str]:
    """Every recipient this agent has already targeted in this simulation."""
    from app.models.agent_recipient import AgentRecipient
    from app.services.agent_registry import resolve_alias
    action_type = resolve_alias(action_type)
    if not simulation_id or action_type not in tracked_agents():
        return set()
    try:
        _ensure_backfilled(simulation_id, action_type)
        return {r.recipient_key for r in AgentRecipient.query.with_entities(AgentRecipient.recipient_key)
                .filter_by(simulation_id=simulation_id, action_type=action_type).all()}
    except Exception as exc:
        _ledger_unavailable('used_keys', exc)
        return set()


def filter_unused(simulation_id: str, action_type: str, items: list) -> list:
    """Drop items (dicts or Prospect objects) this agent has already targeted."""
    used = used_keys(simulation_id, action_type)
    if not used:
        return list(items)
    return [i for i in items if _key_of(i) not in used]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def record(simulation_id: str, action_type: str, action_id: str | None, items: list) -> int:
    """Claim recipients for this run. Already-claimed keys are left with their
    original run. Returns the number of new recipients."""
    from app.extensions import db
    from app.models.agent_recipient import AgentRecipient
    from app.services.agent_registry import resolve_alias
    action_type = resolve_alias(action_type)
    if not simulation_id or action_type not in tracked_agents() or not items:
        return 0
    try:
        existing = {r.recipient_key for r in AgentRecipient.query.with_entities(AgentRecipient.recipient_key)
                    .filter_by(simulation_id=simulation_id, action_type=action_type).all()}
    except Exception as exc:
        _ledger_unavailable('record', exc)
        return 0
    added = 0
    from app.services.contact_lookup import is_placeholder_item
    for item in items:
        key = _key_of(item)
        if not key or key in existing or is_placeholder_item(item):
            continue
        get = item.get if isinstance(item, dict) else (lambda k, d=None: getattr(item, k, d))
        db.session.add(AgentRecipient(
            simulation_id=simulation_id, action_type=action_type, recipient_key=key,
            contact_id=get('crm_contact_id') or get('contact_id') or None, action_id=action_id,
        ))
        existing.add(key)
        added += 1
    if added:
        try:
            db.session.commit()
        except Exception as exc:
            logger.warning('recipient ledger write failed sim=%s agent=%s: %s',
                           simulation_id, action_type, exc)
            db.session.rollback()
            return 0
    return added


def _artifact_prospects(text: str) -> list:
    if not text or not text.lstrip().startswith('{'):
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(text.strip())
    except Exception:
        return []
    prospects = data.get('prospects') if isinstance(data, dict) else None
    return [p for p in prospects if isinstance(p, dict)] if isinstance(prospects, list) else []


def record_from_artifact(simulation_id: str, action_type: str, action_id: str | None,
                         artifact: str) -> int:
    """Claim the prospects[] of a structured outreach artifact."""
    return record(simulation_id, action_type, action_id, _artifact_prospects(artifact))


# ---------------------------------------------------------------------------
# Send-time guard
# ---------------------------------------------------------------------------

def check_send(simulation_id: str, action_id: str | None, contact, subject: str) -> tuple[bool, str]:
    """(allowed, reason) for one outbound email. Called by send_outreach_email,
    the single choke point every agent send passes through."""
    if not simulation_id or not action_id or contact is None:
        return True, ''
    from app.models.agent_action import AgentAction
    from app.models.agent_recipient import AgentRecipient
    from app.models.outreach_email import EmailLog
    from app.services.agent_registry import resolve_alias

    action = AgentAction.query.get(action_id)
    if action is None:
        return True, ''
    agent = resolve_alias(action.action_type)
    if agent not in tracked_agents():
        return True, ''

    # The same email twice from one run — the cold-email hook and its Step 1
    # ActionStep both used to send.
    dup = EmailLog.query.filter_by(simulation_id=simulation_id, contact_id=contact.id,
                                   action_id=action_id, subject=subject).first()
    if dup is not None:
        return False, 'duplicate_send'

    key = recipient_key(contact.email, contact.first_name, contact.last_name, contact.company_name)
    if not key:
        return True, ''
    _ensure_backfilled(simulation_id, agent)
    owner = AgentRecipient.query.filter_by(simulation_id=simulation_id, action_type=agent,
                                           recipient_key=key).first()
    if owner is None:
        record(simulation_id, agent, action_id, [{'email': contact.email, 'contact_id': contact.id}])
        return True, ''
    # A NULL owner is a run an admin deleted after it emailed this person
    # (cycle_admin_service keeps those rows, unlinked) — still blocked.
    if owner.action_id != action_id:
        return False, 'already_targeted_by_agent'
    return True, ''


# ---------------------------------------------------------------------------
# Orchestrator eligibility
# ---------------------------------------------------------------------------

def _crm_pool_has_unused(user_id: str, used: set) -> bool:
    from app.models.contact import Contact
    from sqlalchemy import or_
    rows = Contact.query.with_entities(Contact.email, Contact.first_name, Contact.last_name,
                                       Contact.company_name).filter(
        Contact.user_id == user_id,
        Contact.is_archived.is_(False),
        Contact.do_not_contact.is_(False),
        Contact.email.isnot(None),
        Contact.pipeline_stage == 'prospect',
        Contact.last_contacted_at.is_(None),
        or_(Contact.outreach_count == 0, Contact.outreach_count.is_(None)),
    ).all()
    from app.services.contact_lookup import is_placeholder_contact
    return any(recipient_key(r.email) not in used for r in rows
               if recipient_key(r.email)
               and not is_placeholder_contact(r.first_name, r.last_name, r.company_name, r.email))


def _existing_contacts_unused(user_id: str, used: set) -> bool:
    from app.models.contact import Contact
    rows = Contact.query.filter(
        Contact.user_id == user_id,
        Contact.is_archived.is_(False),
        Contact.do_not_contact.is_(False),
        Contact.pipeline_stage.in_(('active', 'client')),
    ).all()
    from app.services.contact_lookup import is_placeholder_contact
    return any(_key_of({'email': c.email, 'first_name': c.first_name, 'last_name': c.last_name,
                        'company_name': c.company_name}) not in used
               for c in rows if not is_placeholder_contact(c.first_name, c.last_name,
                                                           c.company_name, c.email))


def has_unused_recipients(simulation_id: str, action_type: str, user_id: str) -> tuple[bool, str]:
    """(eligible, reason). False when the agent has no one new left to target.
    Fails open (eligible) if the ledger can't be read."""
    try:
        return _has_unused_recipients(simulation_id, action_type, user_id)
    except Exception as exc:
        _ledger_unavailable('has_unused_recipients', exc)
        return True, ''


def _has_unused_recipients(simulation_id: str, action_type: str, user_id: str) -> tuple[bool, str]:
    from app.models.agent_action import AgentAction
    from app.models.agent_recipient import AgentRecipient
    from app.services.agent_registry import resolve_alias, names_for, UPDATE_ONLY_CONTACT_AGENTS
    agent = resolve_alias(action_type)
    if agent not in gated_agents() or not simulation_id:
        return True, ''
    used = used_keys(simulation_id, agent)

    if agent in UPDATE_ONLY_CONTACT_AGENTS:
        if _existing_contacts_unused(user_id, used):
            return True, ''
        return False, 'every existing client/buyer has already been targeted by this agent'

    if user_id and _crm_pool_has_unused(user_id, used):
        return True, ''

    latest = (AgentAction.query
              .filter(AgentAction.simulation_id == simulation_id,
                      AgentAction.action_type.in_(names_for(agent)),
                      AgentAction.status == AgentAction.STATUS_COMPLETE)
              .order_by(AgentAction.completed_at.desc())
              .first())
    if latest is None:
        return True, ''          # never run — research may find people
    produced = AgentRecipient.query.filter_by(simulation_id=simulation_id, action_type=agent,
                                              action_id=latest.id).count()
    if produced:
        return True, ''          # last run still found new people
    if latest.completed_at and latest.completed_at < datetime.utcnow() - timedelta(days=RECHECK_DAYS):
        return True, ''          # periodic re-probe of research sources
    return False, 'no unused contacts: last run found no one new and the CRM pool is exhausted'


# ---------------------------------------------------------------------------
# Backfill — simulations that ran before the ledger existed
# ---------------------------------------------------------------------------

def _ensure_backfilled(simulation_id: str, action_type: str) -> None:
    """Seed the ledger once from history: sent emails, scheduled steps, prior
    structured artifacts and contacts the agent created. Idempotent."""
    cache_key = (simulation_id, action_type)
    if cache_key in _backfilled:
        return
    _backfilled.add(cache_key)
    try:
        from app.models.agent_recipient import AgentRecipient
        if AgentRecipient.query.filter_by(simulation_id=simulation_id,
                                          action_type=action_type).first() is not None:
            return
        action_ids = _agent_action_ids(simulation_id, action_type)
        if not action_ids:
            return
        from app.models.agent_action import AgentAction
        from app.models.action_step import ActionStep
        from app.models.contact import Contact
        from app.models.outreach_email import EmailLog

        by_action: dict[str, list] = {}

        def add(aid, contact=None, **item):
            if contact is not None:
                item = {'email': contact.email, 'first_name': contact.first_name,
                        'last_name': contact.last_name, 'company_name': contact.company_name,
                        'contact_id': contact.id}
            by_action.setdefault(aid, []).append(item)

        for log in EmailLog.query.filter(EmailLog.simulation_id == simulation_id,
                                         EmailLog.action_id.in_(action_ids)).all():
            c = Contact.query.get(log.contact_id) if log.contact_id else None
            if c is not None:
                add(log.action_id, c)
        for step in ActionStep.query.filter(ActionStep.simulation_id == simulation_id,
                                            ActionStep.agent_action_id.in_(action_ids)).all():
            cid = (step.payload or {}).get('contact_id') or step.condition_ref
            c = Contact.query.get(cid) if cid else None
            if c is not None:
                add(step.agent_action_id, c)
        for a in AgentAction.query.filter(AgentAction.id.in_(action_ids)).all():
            for p in _artifact_prospects(a.artifact) + _artifact_prospects(a.archived_artifact):
                add(a.id, **p)
        for c in Contact.query.filter(Contact.source_action_id.in_(action_ids)).all():
            add(c.source_action_id, c)

        # Oldest run first, so each person is attributed to the run that
        # targeted them first.
        order = {a.id: a.created_at or datetime.min for a in
                 AgentAction.query.filter(AgentAction.id.in_(list(by_action))).all()}
        total = 0
        for aid in sorted(by_action, key=lambda x: order.get(x, datetime.min)):
            total += record(simulation_id, action_type, aid, by_action[aid])
        if total:
            logger.info('recipient ledger backfilled %d for sim=%s agent=%s',
                        total, simulation_id, action_type)
    except Exception as exc:
        logger.warning('recipient ledger backfill failed sim=%s agent=%s: %s',
                       simulation_id, action_type, exc)
