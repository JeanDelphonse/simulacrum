from datetime import datetime, timedelta
from flask import request, jsonify, current_app
from flask_login import login_required, current_user
from app.blueprints.simulations import simulations_bp
from app.blueprints.simulations.sse import trigger_recovery
from app.extensions import db
from app.models.simulation import Simulation, SimulationLayer, IncomeStream
from app.models.agent_action import AgentAction
from app.models.agent_context import AgentContext
from app.models.resume import Resume
from app.models.audit_log import AuditLog
from app.services.stripe_service import create_payment_intent, confirm_payment_intent
from app.models.platform_settings import PlatformSetting
from utils.id_gen import generate_id


def _agent_action_types():
    from app.services.claude import AGENT_ACTION_TYPES
    return AGENT_ACTION_TYPES


# Layer display metadata (colors match PRD design tokens)
_LAYER_META: dict = {
    1: {'label': 'Active Income',       'subtitle': '1:1 Time-for-Money',          'color': '#0F7B72'},
    2: {'label': 'Leveraged Delivery',  'subtitle': '1:Many Teaching & Speaking',  'color': '#7c3aed'},
    3: {'label': 'Digital Products',    'subtitle': 'Earn While You Sleep',        'color': '#1d4ed8'},
    4: {'label': 'Automation & IP',     'subtitle': 'Systems That Scale',          'color': '#b45309'},
    5: {'label': 'Wealth Deployment',   'subtitle': 'Money That Makes Money',      'color': '#15803d'},
}


def _get_action_layer(action_type: str):
    """Return (layer_number, agent_def) for a given action_type, or (None, {})."""
    from app.services.claude import AGENT_ACTION_TYPES
    for ln, agents in AGENT_ACTION_TYPES.items():
        if action_type in agents:
            return ln, agents[action_type]
    return None, {}


@simulations_bp.route('/price', methods=['GET'])
def get_simulation_price():
    """Public endpoint — returns current simulation pricing including any active discount."""
    from app.services.pricing_service import get_current_price, format_price_usd
    pricing = get_current_price()
    return jsonify({
        'amount_cents': pricing['discounted_price_cents'],
        'amount_usd': format_price_usd(pricing['discounted_price_cents']),
        'base_price_cents': pricing['base_price_cents'],
        'base_price_usd': format_price_usd(pricing['base_price_cents']),
        'is_discounted': pricing['is_discounted'],
        'discount_percentage': pricing['discount_percentage'],
        'label': pricing['label'],
        'expires_at': pricing['expires_at'],
    }), 200


@simulations_bp.route('', methods=['GET'])
@login_required
def list_simulations():
    from app.models.collaboration import Collaboration

    owned = Simulation.query.filter_by(user_id=current_user.id).order_by(
        Simulation.created_at.desc()
    ).all()

    # Include simulations shared with the user as an accepted collaborator
    shared_collabs = Collaboration.query.filter_by(
        invitee_email=current_user.email,
    ).filter(
        Collaboration.accepted_at.isnot(None),
        Collaboration.revoked_at.is_(None),
    ).all()
    shared_sim_ids = {c.simulation_id for c in shared_collabs}
    owned_ids = {s.id for s in owned}
    shared_sim_ids -= owned_ids  # Don't double-count

    shared = Simulation.query.filter(
        Simulation.id.in_(shared_sim_ids)
    ).order_by(Simulation.created_at.desc()).all() if shared_sim_ids else []

    def _row(s, is_shared=False):
        return {
            'id': s.id,
            'name': s.name,
            'expertise_zone': s.expertise_zone,
            'status': s.status,
            'layer_count': len(s.layers),
            'created_at': s.created_at.isoformat(),
            'shared': is_shared,
            'unlock_all_layers': bool(s.unlock_all_layers),
        }

    return jsonify(
        [_row(s) for s in owned] + [_row(s, is_shared=True) for s in shared]
    ), 200


@simulations_bp.route('', methods=['POST'])
@login_required
def create_simulation():
    """Create a simulation — triggers Stripe PaymentIntent."""
    data = request.get_json()
    required = ['resume_id', 'expertise_zone', 'name']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Missing fields: {", ".join(missing)}'}), 400

    resume = Resume.query.filter_by(id=data['resume_id'], user_id=current_user.id).first()
    if not resume:
        return jsonify({'error': 'Resume not found'}), 404

    sim_id = generate_id()
    sim = Simulation(
        id=sim_id,
        user_id=current_user.id,
        resume_id=data['resume_id'],
        name=data['name'],
        focus_hint=data.get('focus_hint', ''),
        expertise_zone=data['expertise_zone'],
        status=Simulation.STATUS_PENDING,
    )
    db.session.add(sim)
    AuditLog.log('simulation_created', user_id=current_user.id, resource_id=sim_id)
    db.session.commit()

    # Resolve current pricing (applies any active discount — FR-DISC-10)
    from app.services.pricing_service import get_current_price
    pricing = get_current_price()
    charge_cents = pricing['discounted_price_cents']

    sim.base_price_at_purchase_cents = pricing['base_price_cents']
    sim.discount_applied_percentage = pricing['discount_percentage']
    sim.amount_charged_cents = charge_cents

    # 100% discount: activate immediately without Stripe (FR-DISC-06)
    if charge_cents == 0:
        sim.status = Simulation.STATUS_PROCESSING
        db.session.commit()
        import threading
        from flask import current_app as _app
        _app_obj = _app._get_current_object()
        _sim_id = sim_id
        def _run():
            with _app_obj.app_context():
                from app.tasks.simulation import generate_simulation_task
                generate_simulation_task.apply(args=[_sim_id])
        threading.Thread(target=_run, daemon=True).start()
        return jsonify({
            'simulation_id': sim_id,
            'is_free': True,
            'amount_cents': 0,
            'message': 'Free simulation activated. Generation started.',
        }), 201

    # Create Stripe PaymentIntent with discount metadata
    try:
        payment = create_payment_intent(
            user_id=current_user.id,
            simulation_id=sim_id,
            amount_cents=charge_cents,
            base_price_cents=pricing['base_price_cents'],
            discount_percentage=pricing['discount_percentage'],
        )
        sim.stripe_payment_intent_id = payment['payment_intent_id']
        db.session.commit()

        return jsonify({
            'simulation_id': sim_id,
            'payment_intent_id': payment['payment_intent_id'],
            'client_secret': payment['client_secret'],
            'amount_cents': payment['amount'],
            'is_free': False,
            'message': 'Simulation created. Complete payment to begin generation.',
        }), 201
    except Exception as e:
        db.session.delete(sim)
        db.session.commit()
        return jsonify({'error': f'Payment setup failed: {str(e)}'}), 500


@simulations_bp.route('/<sim_id>/confirm-payment', methods=['POST'])
@login_required
def confirm_simulation_payment(sim_id):
    """Confirm Stripe payment and queue the generation task."""
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()

    if sim.status != Simulation.STATUS_PENDING:
        return jsonify({'error': 'Simulation is not in pending state'}), 400

    try:
        payment = confirm_payment_intent(sim.stripe_payment_intent_id)
        if payment['status'] != 'succeeded':
            return jsonify({'error': 'Payment not confirmed'}), 402

        sim.stripe_charge_id = payment.get('charge_id')
        sim.status = Simulation.STATUS_PROCESSING
        db.session.commit()

        # Kick off generation immediately in a background thread.
        # Cron remains as a fallback in case this thread dies.
        import threading
        from flask import current_app as _app
        _app_obj = _app._get_current_object()
        _sim_id  = sim_id

        def _run_generation():
            with _app_obj.app_context():
                from app.tasks.simulation import generate_simulation_task
                generate_simulation_task.apply(args=[_sim_id])

        threading.Thread(target=_run_generation, daemon=True).start()

        return jsonify({
            'simulation_id': sim_id,
            'status': 'processing',
            'message': 'Payment confirmed. Simulation generation started.',
        }), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@simulations_bp.route('/<sim_id>', methods=['GET'])
@login_required
def get_simulation(sim_id):
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first()
    if not sim:
        # Check if collaborator
        from app.models.collaboration import Collaboration
        collab = Collaboration.query.filter_by(
            simulation_id=sim_id,
            invitee_email=current_user.email,
        ).filter(Collaboration.accepted_at.isnot(None)).first()
        if not collab:
            return jsonify({'error': 'Not found'}), 404
        sim = Simulation.query.get(sim_id)

    # Heal: if all 5 layers are present but status never flipped, fix it now.
    if sim.status == Simulation.STATUS_STREAMING:
        layer_count = SimulationLayer.query.filter_by(simulation_id=sim_id).count()
        if layer_count >= 5:
            try:
                sim.status = Simulation.STATUS_COMPLETE
                from app.models.user import User
                user = User.query.get(sim.user_id)
                charged = sim.amount_charged_cents or current_app.config.get('SIMULATION_PRICE_CENTS', 69500)
                if user:
                    user.simulation_count = (user.simulation_count or 0) + 1
                    user.total_spend = (user.total_spend or 0) + charged
                db.session.commit()
                try:
                    from app.services.email_service import send_invoice_email
                    send_invoice_email(user.email, user.full_name, sim.name, sim.id, charged)
                except Exception:
                    pass
            except Exception as _heal_err:
                db.session.rollback()
                import logging as _log
                _log.getLogger(__name__).error('Heal failed for %s: %s', sim_id, _heal_err)

    return jsonify(sim.to_dict()), 200


@simulations_bp.route('/<sim_id>/recover', methods=['POST'])
@login_required
def recover_simulation(sim_id):
    """Fire-and-forget recovery trigger — restarts generation if stuck in PROCESSING or STREAMING."""
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first()
    if not sim:
        from app.models.collaboration import Collaboration
        collab = Collaboration.query.filter_by(
            simulation_id=sim_id,
            invitee_email=current_user.email,
        ).filter(Collaboration.accepted_at.isnot(None)).first()
        if not collab:
            return jsonify({'error': 'Not found'}), 404
        sim = Simulation.query.get(sim_id)

    if sim and sim.status in (Simulation.STATUS_PROCESSING, Simulation.STATUS_STREAMING):
        trigger_recovery(sim_id)

    return jsonify({'status': sim.status if sim else 'unknown'}), 200


@simulations_bp.route('/<sim_id>/name', methods=['PUT'])
@login_required
def rename_simulation(sim_id):
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    data = request.get_json()
    if not data or not data.get('name'):
        return jsonify({'error': 'name is required'}), 400
    sim.name = data['name']
    db.session.commit()
    return jsonify({'message': 'Renamed', 'name': sim.name}), 200


@simulations_bp.route('/<sim_id>/layers/<int:layer_num>/refine', methods=['POST'])
@login_required
def refine_layer(sim_id, layer_num):
    """Free layer regeneration with a new constraint."""
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first()
    if not sim:
        # Editors can also refine
        from app.models.collaboration import Collaboration
        collab = Collaboration.query.filter_by(
            simulation_id=sim_id,
            invitee_email=current_user.email,
            permission_level='editor',
        ).filter(Collaboration.accepted_at.isnot(None)).first()
        if not collab:
            return jsonify({'error': 'Not found or insufficient permissions'}), 403
        sim = Simulation.query.get(sim_id)

    data = request.get_json()
    if not data or not data.get('constraint'):
        return jsonify({'error': 'constraint is required'}), 400

    layer = SimulationLayer.query.filter_by(
        simulation_id=sim_id, layer_number=layer_num
    ).first_or_404()

    resume = Resume.query.get(sim.resume_id) if sim.resume_id else None
    parsed_text = resume.parsed_text if resume else ''

    try:
        from app.services.claude import refine_simulation_layer
        new_data = refine_simulation_layer(
            layer_number=layer_num,
            expertise_zone=sim.expertise_zone,
            parsed_text=parsed_text,
            constraint=data['constraint'],
            existing_layer=layer.to_dict(),
            user_id=current_user.id,
            simulation_id=sim_id,
        )

        # Update layer record
        layer.ai_narrative = new_data.get('ai_narrative', layer.ai_narrative)
        layer.priority_score = new_data.get('priority_score', layer.priority_score)

        # Replace income streams
        for stream in layer.income_streams:
            db.session.delete(stream)
        db.session.flush()

        for stream_data in new_data.get('income_streams', []):
            stream = IncomeStream(
                layer_id=layer.id,
                name=stream_data.get('name', ''),
                description=stream_data.get('description', ''),
                platform=stream_data.get('platform', ''),
                est_monthly_low=stream_data.get('est_monthly_low'),
                est_monthly_high=stream_data.get('est_monthly_high'),
                ai_reasoning=stream_data.get('ai_reasoning', ''),
                automation_level=stream_data.get('automation_level', ''),
                launch_timeline_weeks=stream_data.get('launch_timeline_weeks'),
            )
            stream.deliverable_refs = stream_data.get('deliverable_refs', [])
            db.session.add(stream)

        AuditLog.log('layer_refined', user_id=current_user.id, resource_id=sim_id,
                     metadata={'layer_number': layer_num, 'constraint': data['constraint']})
        db.session.commit()

        return jsonify(layer.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Refinement failed: {str(e)}'}), 500


@simulations_bp.route('/<sim_id>/export', methods=['POST'])
@login_required
def export_simulation(sim_id):
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    if sim.status != Simulation.STATUS_COMPLETE:
        return jsonify({'error': 'Simulation must be complete before export'}), 400

    try:
        from flask import render_template, make_response
        import weasyprint

        html = render_template('simulations/export.html', simulation=sim,
                               layers=sorted(sim.layers, key=lambda l: l.layer_number))
        pdf = weasyprint.HTML(string=html).write_pdf()

        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=simulacrum_{sim_id}.pdf'
        return response
    except Exception as e:
        return jsonify({'error': f'Export failed: {str(e)}'}), 500


@simulations_bp.route('/<sim_id>', methods=['DELETE'])
@login_required
def delete_simulation(sim_id):
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    AuditLog.log('simulation_deleted', user_id=current_user.id, resource_id=sim_id)
    db.session.delete(sim)
    db.session.commit()
    return jsonify({'message': 'Simulation deleted'}), 200


# ---------------------------------------------------------------------------
# Advisor Access — client grants a partner read-only view of their Simulation
# ---------------------------------------------------------------------------

@simulations_bp.route('/<sim_id>/advisor-access', methods=['GET'])
@login_required
def list_advisor_access(sim_id):
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    from app.models.partner import AdvisorAccess, ReferralPartner
    accesses = AdvisorAccess.query.filter_by(
        simulation_id=sim_id, granted_by=current_user.id,
    ).filter(AdvisorAccess.revoked_at.is_(None)).all()
    result = []
    for a in accesses:
        partner = ReferralPartner.query.get(a.partner_id) if a.partner_id else None
        result.append({
            **a.to_dict(),
            'advisor_name': partner.full_name if partner else None,
            'advisor_email': partner.email if partner else a.pending_email,
        })
    return jsonify(result), 200


@simulations_bp.route('/<sim_id>/advisor-access', methods=['POST'])
@login_required
def grant_advisor_access(sim_id):
    """Owner grants a partner (or any email) read-only advisor access."""
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()

    data = request.get_json()
    advisor_email = (data.get('advisor_email') or '').strip().lower()
    if not advisor_email:
        return jsonify({'error': 'advisor_email is required'}), 400

    from app.models.partner import ReferralPartner, AdvisorAccess
    # Check if already granted
    existing = AdvisorAccess.query.filter_by(
        simulation_id=sim_id, granted_by=current_user.id,
    ).filter(AdvisorAccess.revoked_at.is_(None)).filter(
        db.or_(
            AdvisorAccess.pending_email == advisor_email,
            AdvisorAccess.partner_id.in_(
                db.session.query(ReferralPartner.id).filter_by(email=advisor_email)
            ),
        )
    ).first()
    if existing:
        return jsonify({'error': 'Advisor access already granted to this email'}), 409

    partner = ReferralPartner.query.filter_by(
        email=advisor_email, status=ReferralPartner.STATUS_ACTIVE,
    ).first()

    # FR-CTP-11: prevent self-grant
    if partner and partner.user_id == current_user.id:
        return jsonify({'error': 'You cannot grant yourself advisor access to your own Simulation'}), 400

    access = AdvisorAccess(
        id=generate_id(),
        simulation_id=sim_id,
        partner_id=partner.id if partner else None,
        pending_email=None if partner else advisor_email,
        granted_by=current_user.id,
        access_level=AdvisorAccess.ACCESS_LEVEL_FULL_READ,
    )
    db.session.add(access)
    AuditLog.log('advisor_access_granted', user_id=current_user.id, resource_id=sim_id,
                 metadata={'advisor_email': advisor_email})
    db.session.commit()

    try:
        from app.services.email_service import _send
        advisor_name = partner.full_name if partner else advisor_email
        _send(
            subject=f'{current_user.full_name} shared a Simulacrum Simulation with you',
            recipients=[advisor_email],
            body=(
                f'Hi {advisor_name},\n\n'
                f'{current_user.full_name} has granted you advisor read-only access to their '
                f'Simulation "{sim.name}" on Simulacrum.\n\n'
                f'Log in to your Partner Dashboard to view it.\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception:
        pass

    return jsonify({
        **access.to_dict(),
        'advisor_name': partner.full_name if partner else None,
        'advisor_email': advisor_email,
        'status': 'granted' if partner else 'invited',
    }), 201


@simulations_bp.route('/<sim_id>/advisor-access/<access_id>', methods=['DELETE'])
@login_required
def revoke_advisor_access(sim_id, access_id):
    """Owner revokes an advisor's access."""
    from app.models.partner import AdvisorAccess
    access = AdvisorAccess.query.filter_by(
        id=access_id, simulation_id=sim_id, granted_by=current_user.id,
    ).first_or_404()
    access.revoked_at = datetime.utcnow()
    AuditLog.log('advisor_access_revoked', user_id=current_user.id, resource_id=sim_id,
                 metadata={'access_id': access_id})
    db.session.commit()
    return jsonify({'message': 'Advisor access revoked'}), 200


# ---------------------------------------------------------------------------
# Collaboration seeding — fork an existing Simulation as a starting point
# ---------------------------------------------------------------------------

@simulations_bp.route('/<sim_id>/seed', methods=['POST'])
@login_required
def seed_simulation(sim_id):
    """Seed a new Simulation from an existing one (FR-COL-02).

    Creates a new Simulation with the same expertise_zone, resume_id, and
    focus_hint as the source, then immediately invites the listed collaborators.
    Returns the new simulation_id + PaymentIntent so the owner can pay and
    generate fresh layers with their collaborators able to view/edit.
    """
    source = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first_or_404()
    data = request.get_json() or {}

    new_name = data.get('name') or f'{source.name} (fork)'
    from app.services.pricing_service import get_current_price
    pricing = get_current_price()

    new_sim_id = generate_id()
    new_sim = Simulation(
        id=new_sim_id,
        user_id=current_user.id,
        resume_id=source.resume_id,
        name=new_name,
        focus_hint=source.focus_hint,
        expertise_zone=source.expertise_zone,
        status=Simulation.STATUS_PENDING,
    )
    db.session.add(new_sim)
    AuditLog.log('simulation_seeded', user_id=current_user.id, resource_id=new_sim_id,
                 metadata={'source_sim_id': sim_id})
    db.session.commit()

    # Invite any collaborators listed in the request body
    from datetime import timedelta
    from app.models.collaboration import Collaboration
    invited = []
    for invite in data.get('collaborators', []):
        email = invite.get('email', '').lower()
        perm = invite.get('permission_level', Collaboration.PERM_VIEWER)
        if not email or perm not in (Collaboration.PERM_VIEWER, Collaboration.PERM_COMMENTER, Collaboration.PERM_EDITOR):
            continue
        existing = Collaboration.query.filter_by(
            simulation_id=new_sim_id, invitee_email=email, revoked_at=None,
        ).first()
        if not existing:
            collab = Collaboration(
                id=generate_id(),
                simulation_id=new_sim_id,
                invitee_email=email,
                permission_level=perm,
                expires_at=datetime.utcnow() + timedelta(days=30),
                created_by=current_user.id,
            )
            db.session.add(collab)
            db.session.flush()
            invited.append({'email': email, 'share_token': collab.share_token})
            try:
                from app.services.email_service import send_collab_invite_email
                send_collab_invite_email(email, current_user.full_name, new_name, collab.share_token)
            except Exception:
                pass
    db.session.commit()

    charge_cents = pricing['discounted_price_cents']
    new_sim.base_price_at_purchase_cents = pricing['base_price_cents']
    new_sim.discount_applied_percentage = pricing['discount_percentage']
    new_sim.amount_charged_cents = charge_cents

    if charge_cents == 0:
        new_sim.status = Simulation.STATUS_PROCESSING
        db.session.commit()
        import threading
        from flask import current_app as _app
        _app_obj = _app._get_current_object()
        _nsid = new_sim_id
        def _run():
            with _app_obj.app_context():
                from app.tasks.simulation import generate_simulation_task
                generate_simulation_task.apply(args=[_nsid])
        threading.Thread(target=_run, daemon=True).start()
        return jsonify({
            'simulation_id': new_sim_id,
            'name': new_name,
            'source_simulation_id': sim_id,
            'is_free': True,
            'amount_cents': 0,
            'invited_collaborators': invited,
        }), 201

    try:
        payment = create_payment_intent(
            user_id=current_user.id,
            simulation_id=new_sim_id,
            amount_cents=charge_cents,
            base_price_cents=pricing['base_price_cents'],
            discount_percentage=pricing['discount_percentage'],
        )
        new_sim.stripe_payment_intent_id = payment['payment_intent_id']
        db.session.commit()
    except Exception as e:
        db.session.delete(new_sim)
        db.session.commit()
        return jsonify({'error': f'Payment setup failed: {str(e)}'}), 500

    return jsonify({
        'simulation_id': new_sim_id,
        'name': new_name,
        'source_simulation_id': sim_id,
        'payment_intent_id': payment['payment_intent_id'],
        'client_secret': payment['client_secret'],
        'amount_cents': charge_cents,
        'is_free': False,
        'invited_collaborators': invited,
    }), 201


# ---------------------------------------------------------------------------
# Agent action types catalogue
# ---------------------------------------------------------------------------

@simulations_bp.route('/agent-action-types', methods=['GET'])
@login_required
def get_agent_action_types():
    """Return available agent action types per layer (prompt_form schemas included)."""
    return jsonify(_agent_action_types()), 200


# ---------------------------------------------------------------------------
# Agent action CRUD + execution
# ---------------------------------------------------------------------------

def _check_sim_access(sim_id):
    """Return (sim, is_editor) or raise 403/404."""
    sim = Simulation.query.get(sim_id)
    if not sim:
        return None, None
    if sim.user_id == current_user.id:
        return sim, True
    from app.models.collaboration import Collaboration
    collab = Collaboration.query.filter_by(
        simulation_id=sim_id,
        invitee_email=current_user.email,
    ).filter(
        Collaboration.accepted_at.isnot(None),
        Collaboration.revoked_at.is_(None),
    ).first()
    if collab:
        return sim, collab.permission_level == 'editor'
    return None, None


@simulations_bp.route('/<sim_id>/layers/<int:layer_num>/actions', methods=['GET'])
@login_required
def list_agent_actions(sim_id, layer_num):
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    actions = AgentAction.query.filter_by(
        simulation_id=sim_id, layer_number=layer_num,
    ).order_by(AgentAction.created_at.desc()).all()

    # Return stored context so the frontend can pre-fill forms
    stored_context = AgentContext.get_for_layer(sim_id, layer_num)

    layer_action_types = _agent_action_types().get(layer_num, {})
    return jsonify({
        'available_action_types': [
            {
                'action_type': k,
                'label': v['label'],
                'description': v['description'],
                'prompt_form': v['prompt_form'],
                'disclaimer': v.get('disclaimer', False),
            }
            for k, v in layer_action_types.items()
        ],
        'actions': [a.to_dict() for a in actions],
        'stored_context': stored_context,
    }), 200


@simulations_bp.route('/<sim_id>/layers/<int:layer_num>/actions', methods=['POST'])
@login_required
def create_agent_action(sim_id, layer_num):
    """Create and immediately queue an agent action for a simulation layer."""
    sim, is_editor = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404
    if not is_editor:
        return jsonify({'error': 'Editor permission required'}), 403
    if sim.status != Simulation.STATUS_COMPLETE:
        return jsonify({'error': 'Simulation must be complete before running agent actions'}), 400
    if layer_num not in range(1, 6):
        return jsonify({'error': 'layer_num must be 1-5'}), 400

    data = request.get_json()
    action_type = data.get('action_type') if data else None
    if not action_type:
        return jsonify({'error': 'action_type is required'}), 400
    if action_type not in _agent_action_types().get(layer_num, {}):
        return jsonify({'error': f'Invalid action_type for layer {layer_num}'}), 400

    user_inputs = data.get('user_inputs', {}) or {}

    # Validate required fields from prompt_form schema
    action_schema = _agent_action_types()[layer_num][action_type]
    missing_required = [
        f['key'] for f in action_schema.get('prompt_form', [])
        if f.get('required') and not user_inputs.get(f['key'], '').strip()
    ]
    if missing_required:
        return jsonify({'error': f'Missing required fields: {", ".join(missing_required)}'}), 400

    action = AgentAction(
        id=generate_id(),
        simulation_id=sim_id,
        layer_number=layer_num,
        action_type=action_type,
        status=AgentAction.STATUS_PENDING,
        created_by=current_user.id,
    )
    action.user_inputs = user_inputs
    db.session.add(action)

    # Persist user inputs to agent_context for cross-action reuse
    AgentContext.save_inputs(sim_id, layer_num, user_inputs)

    AuditLog.log('agent_action_created', user_id=current_user.id, resource_id=sim_id,
                 metadata={'action_type': action_type, 'layer': layer_num})
    db.session.commit()

    # Queue async execution
    import threading
    from app.tasks.agent import execute_agent_action_task
    app = current_app._get_current_object()
    action_id = action.id

    def _run():
        with app.app_context():
            execute_agent_action_task.apply(args=[action_id])

    threading.Thread(target=_run, daemon=True).start()

    return jsonify(action.to_dict()), 202


@simulations_bp.route('/<sim_id>/layers/<int:layer_num>/actions/<action_id>', methods=['GET'])
@login_required
def get_agent_action(sim_id, layer_num, action_id):
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    action = AgentAction.query.filter_by(
        id=action_id, simulation_id=sim_id, layer_number=layer_num,
    ).first_or_404()
    return jsonify(action.to_dict()), 200


@simulations_bp.route('/<sim_id>/layers/<int:layer_num>/actions/<action_id>/execute', methods=['POST'])
@login_required
def re_execute_agent_action(sim_id, layer_num, action_id):
    """Re-run an existing action; archives the previous artifact before overwriting."""
    sim, is_editor = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404
    if not is_editor:
        return jsonify({'error': 'Editor permission required'}), 403

    action = AgentAction.query.filter_by(
        id=action_id, simulation_id=sim_id, layer_number=layer_num,
    ).first_or_404()

    if action.status == AgentAction.STATUS_IN_PROGRESS:
        # Auto-recover stale in_progress actions (daemon thread killed by process recycle).
        # consulting_outreach can take 10+ minutes; Passenger workers are recycled sooner.
        from datetime import datetime as _dt
        stale_cutoff = 15 * 60  # 15 minutes
        age = (_dt.utcnow() - action.created_at).total_seconds()
        if age < stale_cutoff:
            return jsonify({'error': 'Action is already running'}), 409
        action.status = AgentAction.STATUS_FAILED
        action.error_message = 'Execution timed out — worker process was recycled'
        action.completed_at = _dt.utcnow()
        db.session.commit()

    # Archive prior artifact
    if action.artifact:
        action.archived_artifact = action.artifact
        action.archived_at = datetime.utcnow()

    # Optionally accept new user_inputs in body
    data = request.get_json(force=True, silent=True) or {}
    if data.get('user_inputs'):
        action.user_inputs = data['user_inputs']

    action.artifact = None
    action.status = AgentAction.STATUS_PENDING
    action.error_message = None
    action.completed_at = None
    db.session.commit()

    import threading
    from app.tasks.agent import execute_agent_action_task
    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            execute_agent_action_task.apply(args=[action_id])

    threading.Thread(target=_run, daemon=True).start()

    return jsonify(action.to_dict()), 202


@simulations_bp.route('/<sim_id>/layers/<int:layer_num>/actions/<action_id>/download', methods=['GET'])
@login_required
def download_agent_artifact(sim_id, layer_num, action_id):
    """Download the action artifact as a plain-text file."""
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    action = AgentAction.query.filter_by(
        id=action_id, simulation_id=sim_id, layer_number=layer_num,
    ).first_or_404()

    if not action.artifact:
        return jsonify({'error': 'No artifact available'}), 404

    from flask import make_response
    filename = f'simulacrum_{sim_id}_L{layer_num}_{action.action_type}.txt'
    response = make_response(action.artifact)
    response.headers['Content-Type'] = 'text/plain; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@simulations_bp.route('/<sim_id>/layers/<int:layer_num>/actions/<action_id>/send', methods=['POST'])
@login_required
def send_agent_artifact(sim_id, layer_num, action_id):
    """Email the action artifact to the simulation owner."""
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    action = AgentAction.query.filter_by(
        id=action_id, simulation_id=sim_id, layer_number=layer_num,
    ).first_or_404()

    if not action.artifact:
        return jsonify({'error': 'No artifact available'}), 404

    try:
        from app.services.email_service import _send
        from app.models.user import User
        owner = User.query.get(sim.user_id)
        _send(
            subject=f'Your Simulacrum Artifact — {action.action_type.replace("_", " ").title()} (Layer {layer_num})',
            recipients=[owner.email],
            body=(
                f'Hi {owner.full_name},\n\n'
                f'Here is your requested artifact from Simulation "{sim.name}", '
                f'Layer {layer_num} — {action.action_type.replace("_", " ").title()}:\n\n'
                f'{"—" * 40}\n\n'
                f'{action.artifact}\n\n'
                f'{"—" * 40}\n\n'
                f'— Simulacrum'
            ),
        )
    except Exception as e:
        return jsonify({'error': f'Failed to send: {str(e)}'}), 500

    return jsonify({'message': 'Artifact sent to your email'}), 200


@simulations_bp.route('/<sim_id>/layers/<int:layer_num>/actions/<action_id>/archive', methods=['GET'])
@login_required
def get_archived_artifact(sim_id, layer_num, action_id):
    """Retrieve the archived (pre-re-execution) artifact for an action."""
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    action = AgentAction.query.filter_by(
        id=action_id, simulation_id=sim_id, layer_number=layer_num,
    ).first_or_404()

    if not action.archived_artifact:
        return jsonify({'error': 'No archived artifact for this action'}), 404

    return jsonify({
        'action_id': action.id,
        'archived_artifact': action.archived_artifact,
        'archived_at': action.archived_at.isoformat() if action.archived_at else None,
    }), 200


# ---------------------------------------------------------------------------
# Wealth Pyramid Launchpad — pyramid data + agent grid
# ---------------------------------------------------------------------------

@simulations_bp.route('/<sim_id>/pyramid', methods=['GET'])
@login_required
def get_pyramid(sim_id):
    """Return wealth pyramid layer data + per-agent statuses for the Launchpad."""
    from app.services.claude import AGENT_ACTION_TYPES
    from app.services.layer6 import ACTION_PREREQUISITES

    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    # Snapshot completed and running action types for this simulation
    all_actions = AgentAction.query.filter_by(simulation_id=sim_id).all()
    completed_types: set = {a.action_type for a in all_actions if a.status == AgentAction.STATUS_COMPLETE}
    running_types:   set = {a.action_type for a in all_actions
                            if a.status in (AgentAction.STATUS_PENDING, AgentAction.STATUS_IN_PROGRESS)}

    layers_out = []
    for layer in sorted(sim.layers, key=lambda l: l.layer_number):
        n = layer.layer_number
        meta = _LAYER_META.get(n, {})
        layer_agent_defs = AGENT_ACTION_TYPES.get(n, {})

        # Build opportunities list from income streams
        opportunities = []
        for s in layer.income_streams:
            rev = None
            if s.est_monthly_low and s.est_monthly_high:
                rev = f'${s.est_monthly_low:,}–${s.est_monthly_high:,}/mo'
            opportunities.append({
                'name': s.name,
                'revenue_range': rev,
                'description': s.description,
                'channels': [s.platform] if s.platform else [],
                'reasoning': s.ai_reasoning,
                'evidence': s.deliverable_refs or [],
                'automation_level': s.automation_level,
                'launch_timeline': f'{s.launch_timeline_weeks}w' if s.launch_timeline_weeks else None,
            })

        # Build agent list with statuses
        agents_list = []
        for at, defn in layer_agent_defs.items():
            if at in running_types:
                status = 'running'
            elif at in completed_types:
                status = 'complete'
            else:
                prereqs = ACTION_PREREQUISITES.get(at, [])
                missing = [p for p in prereqs if p not in completed_types]
                status = 'locked' if missing else 'ready'

            agents_list.append({
                'action_type': at,
                'label': defn.get('label', at.replace('_', ' ').title()),
                'description': defn.get('description', ''),
                'status': status,
                'prompt_form': defn.get('prompt_form', []),
                'missing_prereqs': (
                    [p for p in ACTION_PREREQUISITES.get(at, []) if p not in completed_types]
                    if status == 'locked' else []
                ),
            })

        priority = layer.priority_score or 0
        layers_out.append({
            'layer_number': n,
            'label': meta.get('label', layer.layer_name),
            'subtitle': meta.get('subtitle', ''),
            'color': meta.get('color', '#0F7B72'),
            'priority': 'high' if priority >= 0.7 else ('medium' if priority >= 0.4 else 'low'),
            'ai_narrative': layer.ai_narrative,
            'opportunities': opportunities,
            'agents': agents_list,
            'counts': {
                'complete': sum(1 for a in agents_list if a['status'] == 'complete'),
                'running':  sum(1 for a in agents_list if a['status'] == 'running'),
                'ready':    sum(1 for a in agents_list if a['status'] == 'ready'),
                'locked':   sum(1 for a in agents_list if a['status'] == 'locked'),
            },
        })

    from app.services.pricing_service import get_prospect_tier_config, format_price_usd
    _ptcfg = get_prospect_tier_config()
    _tier = sim.prospect_tier or 1
    _tier_counts = {1: _ptcfg['tier1_count'], 2: _ptcfg['tier2_count'], 3: _ptcfg['tier3_count']}
    _tier_prices = {1: 0, 2: _ptcfg['tier2_price_cents'], 3: _ptcfg['tier3_price_cents']}

    def _tier_option(t):
        count = _tier_counts[t]
        price = _tier_prices[t]
        already_paid = _tier_prices.get(_tier, 0)
        delta = max(0, price - already_paid) if t > _tier else 0
        return {
            'tier': t,
            'count': count,
            'cumulative_price_cents': price,
            'delta_cents': delta,
            'delta_usd': format_price_usd(delta) if delta else None,
            'is_current': t == _tier,
        }

    return jsonify({
        'simulation': {
            'id': sim.id,
            'name': sim.name,
            'status': sim.status,
            'expertise_zone': sim.expertise_zone,
            'created_at': sim.created_at.isoformat(),
            'prospect_tier': _tier,
            'prospect_count': _tier_counts.get(_tier, _ptcfg['tier1_count']),
            'prospect_tier_options': [_tier_option(t) for t in (1, 2, 3)],
        },
        'layers': layers_out,
    }), 200


@simulations_bp.route('/<sim_id>/agents/<action_type>/run', methods=['POST'])
@login_required
def run_agent(sim_id, action_type):
    """Manually dispatch a single agent for this simulation (FR-PYR-09)."""
    from app.services.layer6 import ACTION_PREREQUISITES

    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404
    if sim.user_id != current_user.id:
        return jsonify({'error': 'Only the simulation owner can run agents'}), 403

    layer_number, defn = _get_action_layer(action_type)
    if layer_number is None:
        return jsonify({'error': 'Unknown agent type'}), 400

    # Validate DAG prerequisites
    completed_types = {
        a.action_type for a in
        AgentAction.query.filter_by(simulation_id=sim_id, status=AgentAction.STATUS_COMPLETE).all()
    }
    missing_prereqs = [p for p in ACTION_PREREQUISITES.get(action_type, []) if p not in completed_types]
    if missing_prereqs:
        readable = [p.replace('_', ' ') for p in missing_prereqs]
        return jsonify({
            'error': 'prerequisite_missing',
            'missing': missing_prereqs,
            'message': f'Cannot run — complete {", ".join(readable)} first.',
        }), 422

    # Create action record
    params = (request.get_json(silent=True) or {}).get('params', {})
    action = AgentAction(
        id=generate_id(),
        simulation_id=sim_id,
        layer_number=layer_number,
        action_type=action_type,
        user_inputs=params,
        status=AgentAction.STATUS_PENDING,
        created_by=current_user.id,
    )
    db.session.add(action)
    db.session.commit()

    # Dispatch Celery task
    try:
        from app.tasks.agent import execute_agent_action_task
        execute_agent_action_task.delay(action.id)
    except Exception:
        pass  # Worker picks it up via periodic sweep if Celery unavailable

    return jsonify({
        'action_id': action.id,
        'action_type': action_type,
        'layer_number': layer_number,
        'status': 'running',
        'label': defn.get('label', action_type.replace('_', ' ').title()),
    }), 200


# ---------------------------------------------------------------------------
# GCC Journey v3 (SIM-PRD-GCC-003)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Prospect Tier (SIM-REQ-PROSPECT-001)
# ---------------------------------------------------------------------------

@simulations_bp.route('/<sim_id>/prospect-tier', methods=['GET'])
@login_required
def get_prospect_tier(sim_id):
    """Return current prospect tier, count, and upgrade options for this simulation."""
    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    from app.services.pricing_service import get_prospect_tier_config, format_price_usd
    cfg = get_prospect_tier_config()
    tier = sim.prospect_tier or 1
    tier_counts = {1: cfg['tier1_count'], 2: cfg['tier2_count'], 3: cfg['tier3_count']}
    tier_prices = {1: 0, 2: cfg['tier2_price_cents'], 3: cfg['tier3_price_cents']}
    already_paid = tier_prices.get(tier, 0)

    options = []
    for t in (1, 2, 3):
        count = tier_counts[t]
        price = tier_prices[t]
        delta = max(0, price - already_paid) if t > tier else 0
        options.append({
            'tier': t,
            'count': count,
            'cumulative_price_cents': price,
            'delta_cents': delta,
            'delta_usd': format_price_usd(delta) if delta else None,
            'is_current': t == tier,
        })

    return jsonify({
        'simulation_id': sim_id,
        'prospect_tier': tier,
        'prospect_count': tier_counts.get(tier, cfg['tier1_count']),
        'prospect_tier_paid_cents': sim.prospect_tier_paid_cents or 0,
        'options': options,
    }), 200


@simulations_bp.route('/<sim_id>/prospect-tier/upgrade', methods=['POST'])
@login_required
def upgrade_prospect_tier(sim_id):
    """Create a Stripe Checkout session to upgrade this simulation's prospect tier."""
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first()
    if not sim:
        return jsonify({'error': 'Not found'}), 404
    if sim.status != Simulation.STATUS_COMPLETE:
        return jsonify({'error': 'Simulation must be complete before upgrading'}), 400

    data = request.get_json() or {}
    target_tier = data.get('target_tier')
    action_type = data.get('action_type', '')
    layer_number = data.get('layer_number', '')

    if target_tier not in (2, 3):
        return jsonify({'error': 'target_tier must be 2 or 3'}), 400

    current_tier = sim.prospect_tier or 1
    if target_tier <= current_tier:
        return jsonify({'error': 'Already at this tier or higher'}), 400

    from app.services.pricing_service import get_prospect_tier_config, format_price_usd
    cfg = get_prospect_tier_config()
    tier_prices = {2: cfg['tier2_price_cents'], 3: cfg['tier3_price_cents']}
    tier_counts = {1: cfg['tier1_count'], 2: cfg['tier2_count'], 3: cfg['tier3_count']}
    already_paid = tier_prices.get(current_tier, 0)
    target_price = tier_prices[target_tier]
    delta = max(0, target_price - already_paid)

    if delta == 0:
        # Nothing to charge (admin set prices so delta == 0); upgrade for free
        sim.prospect_tier = target_tier
        db.session.commit()
        return jsonify({'upgraded': True, 'prospect_tier': target_tier}), 200

    from flask import url_for as _url_for
    from app.services.stripe_service import create_prospect_tier_checkout_session

    # Build success URL back to the simulation, with params for auto-dispatch
    base_url = current_app.config.get('BASE_URL', request.host_url.rstrip('/'))
    success_url = (
        f'{base_url}/simulations/{sim_id}'
        f'?tier_upgraded={target_tier}'
        f'&run_agent={action_type}'
        f'&run_layer={layer_number}'
    )
    cancel_url = f'{base_url}/simulations/{sim_id}'

    try:
        result = create_prospect_tier_checkout_session(
            user_id=current_user.id,
            simulation_id=sim_id,
            upgrade_to_tier=target_tier,
            delta_cents=delta,
            tier_count=tier_counts[target_tier],
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except Exception as exc:
        return jsonify({'error': f'Stripe error: {exc}'}), 500

    return jsonify({
        'checkout_url': result['checkout_url'],
        'session_id': result['session_id'],
        'delta_cents': delta,
        'delta_usd': format_price_usd(delta),
        'target_tier': target_tier,
    }), 200


@simulations_bp.route('/<sim_id>/journey', methods=['GET'])
@login_required
def get_journey(sim_id):
    """Return journey data for GCC Journey tab v3."""
    from app.services.claude import AGENT_ACTION_TYPES
    from app.models.layer6 import Layer6Outcome, Layer6Cycle, Layer6ActionQueue, Layer6Config
    from sqlalchemy import func, distinct as sa_distinct

    sim, _ = _check_sim_access(sim_id)
    if not sim:
        return jsonify({'error': 'Not found'}), 404

    status_filter = request.args.get('status', 'all').strip()
    valid_statuses = {'pending', 'in_progress', 'complete', 'failed'}

    # Latest cycle — scopes action rows to the current run
    latest_cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).first()

    # All agent tasks, ordered newest-first, then deduplicated by (layer, action_type)
    # so each agent type shows once with its most recent status.
    _raw_actions = AgentAction.query.filter(
        AgentAction.simulation_id == sim_id,
        AgentAction.status.in_(list(valid_statuses)),
    ).order_by(AgentAction.created_at.desc()).all()
    _seen: set = set()
    all_actions = []
    for _a in _raw_actions:
        _key = (_a.layer_number, _a.action_type)
        if _key not in _seen:
            _seen.add(_key)
            all_actions.append(_a)

    # Scope to the latest cycle — ALL queue items (including escalated/queued ones
    # with no AgentAction yet) so every dispatched agent appears on the Journey tab.
    _qstatus_map = {
        'complete': 'complete', 'dispatched': 'in_progress',
        'queued': 'pending', 'escalated': 'escalated',
        'failed': 'failed', 'rejected': 'rejected',
    }
    if latest_cycle:
        _cycle_queue = Layer6ActionQueue.query.filter_by(
            cycle_id=latest_cycle.id
        ).order_by(Layer6ActionQueue.created_at).all()
        _dispatched_ids = [q.agent_action_id for q in _cycle_queue if q.agent_action_id]
        _agent_map: dict = {}
        if _dispatched_ids:
            for _a in AgentAction.query.filter(AgentAction.id.in_(_dispatched_ids)).all():
                _agent_map[_a.id] = _a
        _cycle_records = []
        for q in _cycle_queue:
            _aa = _agent_map.get(q.agent_action_id) if q.agent_action_id else None
            if _aa:
                _cycle_records.append({
                    'id': _aa.id,
                    'action_type': q.action_type,
                    'layer_number': _aa.layer_number,
                    'status': _aa.status,
                    'date': _aa.created_at.strftime('%Y-%m-%d') if _aa.created_at else None,
                })
            else:
                _cycle_records.append({
                    'id': None,
                    'action_type': q.action_type,
                    'layer_number': q.source_layer,
                    'status': _qstatus_map.get(q.status, q.status),
                    'date': q.created_at.strftime('%Y-%m-%d') if q.created_at else None,
                })
    else:
        _cycle_records = []

    # Apply optional status filter on top of the cycle scope.
    _all_display_statuses = valid_statuses | {'escalated', 'rejected'}
    if status_filter in _all_display_statuses:
        display_records = [r for r in _cycle_records if r['status'] == status_filter]
    else:
        display_records = _cycle_records

    # Layer income totals
    outcome_rows = db.session.query(
        Layer6Outcome.layer_number,
        func.sum(Layer6Outcome.actual_income).label('total'),
    ).filter_by(simulation_id=sim_id).group_by(Layer6Outcome.layer_number).all()
    income_by_layer = {r.layer_number: float(r.total or 0) for r in outcome_rows}

    # Next cycle timing (FR-GCC-13)
    _cadence_hours = {
        'daily': 24, 'every_3_days': 72, 'weekly': 168,
        'every_12h': 12, 'every_48h': 48, 'every_72h': 72, 'every_168h': 168,
    }
    next_cycle_at_iso = None
    cycle_is_running = False
    if latest_cycle:
        cycle_is_running = (latest_cycle.cycle_completed_at is None)
        _cfg = Layer6Config.query.filter_by(simulation_id=sim_id).first()
        if _cfg:
            _hours = _cadence_hours.get(_cfg.cadence, 24)
            _next_dt = latest_cycle.cycle_started_at + timedelta(hours=_hours)
            next_cycle_at_iso = _next_dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    # Suggested agent per layer from latest cycle queue (top undispatched by layer)
    suggested_by_layer: dict = {}
    if latest_cycle:
        queue_items = Layer6ActionQueue.query.filter_by(
            cycle_id=latest_cycle.id,
            status='queued',
        ).order_by(Layer6ActionQueue.priority_score.desc()).all()
        for qi in queue_items:
            ln, defn = _get_action_layer(qi.action_type)
            if ln and ln not in suggested_by_layer:
                suggested_by_layer[ln] = defn.get('label', qi.action_type.replace('_', ' ').title())

    # Fallback: if queue empty for a layer, use top in_progress/pending action
    for _n in range(1, 6):
        if _n not in suggested_by_layer:
            _fb = next(
                (a for a in all_actions if a.layer_number == _n and a.status in ('in_progress', 'pending')),
                None,
            )
            if _fb:
                _defn = AGENT_ACTION_TYPES.get(_n, {}).get(_fb.action_type, {})
                suggested_by_layer[_n] = _defn.get('label', _fb.action_type.replace('_', ' ').title())
            else:
                # Nothing run yet — suggest the first defined agent type for this layer
                _layer_agents = AGENT_ACTION_TYPES.get(_n, {})
                if _layer_agents:
                    _first_type, _first_def = next(iter(_layer_agents.items()))
                    suggested_by_layer[_n] = _first_def.get('label', _first_type.replace('_', ' ').title())

    status_order = {'in_progress': 0, 'pending': 1, 'complete': 2, 'failed': 3, 'escalated': 4, 'rejected': 5}

    # Pre-fetch which actions actually have a current ArtifactVersion so that
    # has_artifact is accurate. Using bool(a.artifact) (the legacy column) can
    # diverge from ArtifactVersion reality and cause "View artifact" to link to
    # a page that immediately redirects back to GCC.
    from app.models.artifact import ArtifactVersion as _AV
    _action_ids_with_version = {
        row[0]
        for row in db.session.query(_AV.action_id)
        .filter(_AV.simulation_id == sim_id, _AV.is_current.is_(True))
        .all()
    }

    layers_out = []
    for n in range(1, 6):
        meta = _LAYER_META.get(n, {})
        layer_agent_defs = AGENT_ACTION_TYPES.get(n, {})
        all_agent_types = list(layer_agent_defs.keys())
        total_agents = len(all_agent_types)

        # Unique complete (unfiltered — progress ring always shows true completion)
        unique_complete = db.session.query(
            func.count(sa_distinct(AgentAction.action_type))
        ).filter(
            AgentAction.simulation_id == sim_id,
            AgentAction.action_type.in_(all_agent_types),
            AgentAction.status == AgentAction.STATUS_COMPLETE,
        ).scalar() or 0

        pct = round((unique_complete / total_agents) * 100) if total_agents else 0
        layer_income = income_by_layer.get(n, 0)

        # Action rows for this layer (filtered)
        layer_rows_raw = [r for r in display_records if r['layer_number'] == n]
        layer_rows_raw.sort(key=lambda r: status_order.get(r['status'], 9))

        action_rows = []
        for r in layer_rows_raw:
            defn = layer_agent_defs.get(r['action_type'], {})
            action_rows.append({
                'id': r['id'],
                'action_type': r['action_type'],
                'label': defn.get('label', r['action_type'].replace('_', ' ').title()),
                'status': r['status'],
                'date': r['date'],
                'has_artifact': r['id'] in _action_ids_with_version if r['id'] else False,
            })

        layers_out.append({
            'layer_number': n,
            'label': meta.get('label', f'Layer {n}'),
            'color': meta.get('color', '#0F7B72'),
            'completion_pct': pct,
            'unique_complete': unique_complete,
            'total_agents': total_agents,
            'total_actions': len(layer_rows_raw),
            'layer_income': layer_income,
            'suggested_action': suggested_by_layer.get(n),
            'actions': action_rows,
        })

    return jsonify({
        'layers': layers_out,
        'focus': {
            'cycle_number': latest_cycle.cycle_number if latest_cycle else None,
            'phase': latest_cycle.phase if latest_cycle else None,
            'suggested_action': suggested_by_layer.get(1),  # top suggestion for focus bar
            'next_cycle_at': next_cycle_at_iso,
            'simulation_status': sim.status,
            'cycle_is_running': cycle_is_running,
        },
        'total_actions': len(display_records),
        'status_filter': status_filter,
    }), 200
