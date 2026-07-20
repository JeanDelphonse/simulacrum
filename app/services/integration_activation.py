"""
Integration activation — SIM-PRD-CONNECT-001 graceful degradation (FR-CON-04).

Two behaviors:

  evaluate_activation_state(action, user_id)
      Called right after an agent produces its artifact.  If the agent needs a
      one-tap (oauth) integration the user hasn't connected, the artifact is kept
      but marked 'pending_connection' — a "connect to activate" state — instead of
      hard-failing.  Otherwise it is 'active'.

  activate_pending_connections(user_id, provider)
      Called when the user connects an integration.  Every artifact that was
      waiting on it flips back to 'active' automatically (no re-run) and the user
      is notified.
"""
from __future__ import annotations
import logging

from app.services import integration_registry as registry

_log = logging.getLogger(__name__)


def evaluate_activation_state(action, user_id: str, commit: bool = False) -> str:
    """Set action.pending_connection / activation_state based on connection state.

    Returns the resulting activation_state. Does not raise — a failure here must
    never block artifact generation (the artifact is already produced).
    """
    try:
        pending = registry.pending_connection_for(action.action_type, user_id)
        if pending:
            action.pending_connection = pending
            action.activation_state = 'pending_connection'
        else:
            action.pending_connection = None
            action.activation_state = 'active'
        if commit:
            from app.extensions import db
            db.session.commit()
        return action.activation_state
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning('evaluate_activation_state failed for action %s: %s',
                     getattr(action, 'id', '?'), exc)
        return getattr(action, 'activation_state', 'active') or 'active'


def activate_pending_connections(user_id: str, provider_or_key: str) -> int:
    """Flip artifacts pending on `provider_or_key` to active + notify. Returns count.

    Accepts either a registry key ('cal_com') or a UserIntegration.provider key
    ('cal') and normalizes to the registry key.
    """
    try:
        from app.extensions import db
        from app.models.agent_action import AgentAction
        from app.models.simulation import Simulation

        key = registry.registry_key_for_provider(provider_or_key) or provider_or_key

        pending = (
            AgentAction.query
            .join(Simulation, AgentAction.simulation_id == Simulation.id)
            .filter(
                Simulation.user_id == user_id,
                AgentAction.pending_connection == key,
                AgentAction.activation_state == 'pending_connection',
            )
            .all()
        )
        if not pending:
            return 0

        for action in pending:
            action.activation_state = 'active'
            action.pending_connection = None
        db.session.commit()

        _notify_activated(user_id, key, pending)
        return len(pending)
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning('activate_pending_connections(%s, %s) failed: %s',
                     user_id, provider_or_key, exc)
        try:
            from app.extensions import db
            db.session.rollback()
        except Exception:
            pass
        return 0


def _notify_activated(user_id: str, key: str, actions: list) -> None:
    """One consolidated notification per connect event (FR-CON-04)."""
    try:
        from app.services.notification_service import send_notification
        from app.services.claude import AGENT_ACTION_TYPES
        from app.services.agent_registry import resolve_alias

        name = registry.display_name(key)
        # Label the (first) activated artifact for a concrete message.
        first = actions[0]
        layer_agents = AGENT_ACTION_TYPES.get(first.layer_number, {})
        defn = layer_agents.get(resolve_alias(first.action_type), {})
        artifact_label = defn.get('label', first.action_type.replace('_', ' ').title())

        if len(actions) == 1:
            body = f'{name} connected — your "{artifact_label}" is now live.'
        else:
            body = (f'{name} connected — {len(actions)} items including '
                    f'"{artifact_label}" are now live.')

        send_notification(
            user_id=user_id,
            notification_type='integration_activated',
            title=f'{name} connected',
            body=body,
            cta_url=f'/simulations/{first.simulation_id}/gcc',
            cta_label='View →',
            simulation_id=first.simulation_id,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning('_notify_activated failed: %s', exc)
