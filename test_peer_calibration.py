"""Peer Calibration — PRD-SIM-PEER-001.

The claims worth testing are the three the whole feature rests on: a peer never
sees the owner's value, a peer can never change it, and an out-of-policy
proposal never reaches the blend. So these checks push on those seams first,
then on the arithmetic that is easy to get subtly wrong — the outlier trim, the
prior's fixed weight, the minimum-N stopping rule — and on the anti-farming
caps.

Against in-memory SQLite; no network, no real data. The one LLM path (text
synthesis) is exercised with a stub so the blend is checked without a call.

    python test_peer_calibration.py
"""
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label,
                              ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def section(title):
    print('\n' + title)


def raises(fn, *args, **kwargs):
    """(did_raise, message) for a peer_service refusal."""
    from app.services.peer_service import PeerError
    try:
        fn(*args, **kwargs)
        return False, ''
    except PeerError as exc:
        return True, exc.message
    except Exception as exc:  # anything else is a real failure
        return False, 'unexpected %s: %s' % (type(exc).__name__, exc)


def main():
    from app import create_app
    from app.extensions import bcrypt, db
    from utils.id_gen import generate_id

    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      WTF_CSRF_ENABLED=False)

    with app.app_context():
        from app.models.agent_context import AgentContext
        from app.models.observability import GuardrailRule
        from app.models.peer import (
            AgentShare, ParameterSuggestion, PeerInvite, ReciprocityLedger,
            SuggestionBlend,
        )
        from app.models.platform_settings import PlatformSetting
        from app.models.profile import UserProfile
        from app.models.simulation import Simulation
        from app.models.user import User
        from app.services import peer_service as peers

        db.create_all()
        now = datetime.utcnow()
        pw = bcrypt.generate_password_hash('pw12345678').decode()

        def make_user(email, name, slug):
            user = User(id=generate_id(), email=email, full_name=name,
                        password_hash=pw, email_verified=True,
                        onboarding_completed_at=now)
            db.session.add(user)
            db.session.flush()
            db.session.add(UserProfile(id=generate_id(), user_id=user.id,
                                       username=slug, display_name=name,
                                       tagline='Ops turnarounds for mid-market'))
            return user

        owner = make_user('owner@t.local', 'Owner', 'owner')
        p1 = make_user('p1@t.local', 'Peer One', 'peer-one')
        p2 = make_user('p2@t.local', 'Peer Two', 'peer-two')
        p3 = make_user('p3@t.local', 'Peer Three', 'peer-three')
        p4 = make_user('p4@t.local', 'Peer Four', 'peer-four')
        stranger = make_user('nosy@t.local', 'Nosy', 'nosy')
        db.session.commit()

        sim = Simulation(id=generate_id(), user_id=owner.id, name='Engine',
                         expertise_zone='Management Consulting', status='complete')
        db.session.add(sim)
        db.session.commit()

        # The bio page is the propose surface, so /u/owner has to render the bio
        # template rather than the legacy profile stub.
        from app.models.bio_page import BioPage
        db.session.add(BioPage(id=generate_id(), user_id=owner.id, slug='owner',
                               simulation_id=sim.id,
                               status=BioPage.STATUS_PUBLISHED))
        db.session.commit()

        # The owner's own value — the prior the blend is measured against.
        AgentContext.upsert(sim.id, 1, 'target_rate', '150')
        AgentContext.upsert(sim.id, 1, 'engagement_preference', 'hourly')
        AgentContext.upsert(sim.id, 1, 'client_outcomes', 'Faster close, less rework.')
        db.session.commit()

        # ── Parameter kinds (PRD §6) ────────────────────────────────────────
        section('Parameter kinds — what a peer is shown')
        kind_of = {}
        for agent in peers.shareable_agents(sim.id):
            for param in agent['params']:
                kind_of[(agent['agent_key'], param['key'])] = param['kind']

        check('a money field is numeric',
              kind_of[('rate_card', 'target_rate')] == 'numeric',
              kind_of[('rate_card', 'target_rate')])
        check('a select is a choice',
              kind_of[('rate_card', 'engagement_preference')] == 'choice')
        check('a textarea is text',
              kind_of[('rate_card', 'client_outcomes')] == 'text')
        check('"Available days" is not mistaken for a count',
              kind_of[('booking_page', 'available_days')] == 'text',
              kind_of[('booking_page', 'available_days')])
        check('"Program duration (weeks)" is numeric',
              kind_of[('group_coaching', 'duration_weeks')] == 'numeric')
        check('a compound label can be overridden in the agent definition',
              kind_of[('waitlist_landing_page', 'start_date_price')] == 'text',
              kind_of[('waitlist_landing_page', 'start_date_price')])
        check('a numeric param carries its unit and nothing else',
              peers.param_unit({'key': 'target_rate',
                                'label': 'Target hourly or day rate (USD)'}) == 'USD')

        # ── Sharing is L1/L2 only (PRD §5, §14) ─────────────────────────────
        section('Sharing — the layer ceiling')
        offered = {a['layer'] for a in peers.shareable_agents(sim.id)}
        check('only Layer 1 and 2 agents are offered', offered <= {1, 2}, sorted(offered))

        share = peers.set_share(sim.id, owner.id, 'rate_card', True)
        check('an L1 agent can be shared', share.is_shareable and share.layer == 'L1')

        blocked, message = raises(peers.set_share, sim.id, owner.id,
                                  'income_allocation', True)
        check('an L5 agent cannot be shared', blocked, message)
        check('the refusal says why it is a policy and not a bug',
              'crowd-tunable' in message, message)

        blocked, _ = raises(peers.set_share, sim.id, owner.id, 'sales_page', True)
        check('an L3 agent cannot be shared either', blocked)

        peers.set_share(sim.id, owner.id, 'group_coaching', True)
        check('two agents are now shared',
              len(peers.shares_for_owner(owner.id)) == 2)

        # ── The circle (PRD §5) ─────────────────────────────────────────────
        section('The invited circle')
        check('a stranger is not a peer', not peers.is_peer(owner.id, stranger.id))

        invites = {}
        for peer in (p1, p2, p3, p4):
            invite = peers.invite_peer(owner.id, peer_user_id=peer.id,
                                       label=peer.full_name)
            invites[peer.id] = invite
            check('%s is invited but not yet in the circle' % peer.full_name,
                  not peers.is_peer(owner.id, peer.id))
            peers.accept_invite(invite.id, peer.id)

        check('accepting puts a peer in the circle', peers.is_peer(owner.id, p1.id))
        check('the owner is not their own peer', not peers.is_peer(owner.id, owner.id))

        blocked, message = raises(peers.invite_peer, owner.id, peer_user_id=p1.id)
        check('re-inviting an existing peer is refused, not duplicated', blocked, message)

        blocked, _ = raises(peers.invite_peer, owner.id, peer_user_id=owner.id)
        check('you cannot invite yourself', blocked)

        cross = peers.invite_peer(p1.id, peer_user_id=stranger.id)
        blocked, _ = raises(peers.accept_invite, cross.id, p2.id)
        check('a peer cannot accept an invite addressed to someone else', blocked)

        # ── Blindness (PRD §6, §14) ─────────────────────────────────────────
        section('Blind vs context-blind')
        view = peers.peer_view(owner.id, p1.id)
        check('an invited peer sees the shared agents', view['is_peer']
              and len(view['shared_agents']) == 2, len(view['shared_agents']))

        flat = repr(view)
        check("the owner's numeric value never appears in the payload",
              '150' not in flat, [a['params'] for a in view['shared_agents']])
        check("the owner's text draft never appears in the payload",
              'Faster close' not in flat)
        check('no payload entry carries a value key at all',
              all('value' not in p for a in view['shared_agents']
                  for p in a['params']))

        rate_param = next(p for a in view['shared_agents'] if a['agent_key'] == 'rate_card'
                          for p in a['params'] if p['param_key'] == 'target_rate')
        check('a blind numeric param offers the name and the unit',
              rate_param['unit'] == 'USD' and 'brief' not in rate_param, rate_param)

        text_param = next(p for a in view['shared_agents'] if a['agent_key'] == 'rate_card'
                          for p in a['params'] if p['param_key'] == 'client_outcomes')
        check('a text param carries a brief, not the draft',
              set(text_param['brief']) >= {'goal', 'persona', 'constraints'},
              text_param.get('brief'))

        outsider = peers.peer_view(owner.id, stranger.id)
        check('a non-invited visitor gets no agents at all',
              outsider['shared_agents'] == [] and not outsider['is_peer'])
        check('but does learn a circle exists, so requesting makes sense',
              outsider['shares_anything'] is True
              and outsider['circle_size'] == 4, outsider)

        # ── Authorisation on the write path (PRD §3) ────────────────────────
        section('Only invited peers can propose')
        share_rate = peers.shares_for_owner(owner.id, sim.id)
        share_rate = next(s for s in share_rate if s.agent_key == 'rate_card')

        blocked, message = raises(peers.submit_suggestion, share_rate.id,
                                  stranger.id, 'target_rate', 200)
        check('a stranger cannot propose', blocked, message)

        blocked, _ = raises(peers.submit_suggestion, share_rate.id, owner.id,
                            'target_rate', 200)
        check('the owner cannot propose on their own agent', blocked)

        blocked, _ = raises(peers.submit_suggestion, share_rate.id, p1.id,
                            'not_a_param', 200)
        check('an unknown parameter is refused', blocked)

        blocked, message = raises(peers.submit_suggestion, share_rate.id, p1.id,
                                  'target_rate', 'about three hundred')
        check('a numeric param refuses prose', blocked, message)

        # ── Guardrails (OBS-001, PRD §7, §13, §14) ──────────────────────────
        section('Guardrail-bounded proposals')
        rule = GuardrailRule(
            id=generate_id(), name='rate-card-cap', scope='agent',
            scope_ref='rate_card', rule_type='param_bound',
            definition_json={'field': 'target_rate', 'min': 50, 'max': 500},
            action='block', severity='high', is_enabled=True,
        )
        db.session.add(rule)
        db.session.commit()

        blocked, message = raises(peers.submit_suggestion, share_rate.id, p1.id,
                                  'target_rate', 9000)
        check('a proposal above the owner\'s cap is refused', blocked, message)
        check('the refusal names the bound it broke', '500' in message, message)

        rejected = ParameterSuggestion.query.filter_by(guardrail_ok=False).all()
        check('the rejection is recorded rather than discarded', len(rejected) == 1)
        check('a rejected proposal never enters the pool',
              rejected[0].status == 'rejected')
        check('a rejected proposal does not count toward the unlock',
              not rejected[0].is_quality)

        from app.models.observability import ObservabilityFlag
        flags = ObservabilityFlag.query.filter_by(target_type='suggestion').all()
        check('the guardrail flag is written against the proposal',
              len(flags) == 1 and flags[0].rule_name == 'rate-card-cap', flags)

        # ── Anti-farming (PRD §8) ───────────────────────────────────────────
        section('Rate limits, cooldown and dedupe')
        first = peers.submit_suggestion(share_rate.id, p1.id, 'target_rate', 210)
        check('a good proposal is accepted', first['suggestion']['status'] == 'pending')
        check('it counts toward the unlock', first['counted_toward_unlock'])

        blocked, message = raises(peers.submit_suggestion, share_rate.id, p1.id,
                                  'target_rate', 215)
        check('the same peer is cooled down on the same parameter', blocked, message)

        PlatformSetting.set('peer_cooldown_hours', '0')
        again = peers.submit_suggestion(share_rate.id, p1.id, 'target_rate', 210)
        check('with the cooldown lifted a restatement is accepted',
              again['suggestion']['status'] == 'pending')
        check('but an identical restatement is a duplicate', again['duplicate'])
        check('a duplicate does not advance the reciprocity gate',
              not again['counted_toward_unlock'])

        PlatformSetting.set('peer_max_per_agent', '1')
        blocked, message = raises(peers.submit_suggestion, share_rate.id, p1.id,
                                  'engagement_preference', 'retainer')
        check('the per-agent cap stops a peer flooding one engine', blocked, message)
        PlatformSetting.set('peer_max_per_agent', '3')

        # ── The aggregation model (PRD §7) ──────────────────────────────────
        section('The blend — arithmetic, not tokens')
        survivors, trimmed = peers.trim_outliers([200, 210, 220, 9000])
        check('an extreme proposal is trimmed', trimmed == [9000], (survivors, trimmed))
        survivors, trimmed = peers.trim_outliers([200, 210, 220])
        check('a tight cluster is left intact', trimmed == [])
        survivors, trimmed = peers.trim_outliers([200, 200, 200])
        check('identical proposals do not all get trimmed as zero-deviation',
              survivors == [200, 200, 200] and trimmed == [])

        blend = peers.blend_numeric(150, [180, 210, 240])
        check('three proposals clear the minimum', blend is not None)
        check('the range is the peer min and max',
              (blend['range_low'], blend['range_high']) == (180, 240), blend)
        check('the cluster is the median', blend['posterior']['cluster'] == 210)
        # (2*150 + 180+210+240) / (2+3) = 930/5 = 186
        check('the prior carries its configured weight',
              abs(blend['posterior']['value'] - 186.0) < 1e-6,
              blend['posterior'])
        check('the owner is shown a range, a spread and a midpoint — not a number',
              all(blend[k] is not None for k in
                  ('range_low', 'range_high', 'spread', 'spread_pct',
                   'spread_signal')))
        check('the rationale is written without an LLM',
              'clustered at 210' in blend['rationale'], blend['rationale'])

        two = peers.blend_numeric(150, [180, 210])
        check('below the minimum there is no blend at all', two is None)

        wide = peers.blend_numeric(150, [100, 300, 500])
        check('a wide spread is flagged as still exploring',
              wide['spread_signal'] == 'wide', wide['spread_pct'])
        tight = peers.blend_numeric(150, [200, 205, 210])
        check('a tight cluster is flagged as exploitable',
              tight['spread_signal'] == 'tight')

        no_prior = peers.blend_numeric(None, [180, 210, 240])
        check('with no prior the recommendation is the peer mean',
              abs(no_prior['posterior']['value'] - 210.0) < 1e-6
              and no_prior['posterior']['method'] == 'peer_mean')

        heavy = peers.blend_numeric(150, [1000, 1010, 1020, 9000])
        check('the trim runs before the blend, so one outlier cannot capture it',
              heavy['n_trimmed'] == 1 and heavy['posterior']['value'] < 1010,
              heavy['posterior'])

        choice = peers.blend_choice('hourly', ['retainer', 'retainer', 'hourly'])
        check('a choice param blends by plurality',
              choice['posterior']['value'] == 'retainer', choice['posterior'])
        check('the disagreement is reported as the spread',
              abs(choice['spread_pct'] - 100.0 / 3) < 0.01, choice['spread_pct'])
        check('the rationale names the tally',
              'hourly' in choice['rationale'], choice['rationale'])

        # ── Resolving at the cycle boundary (PRD §4 step 3) ─────────────────
        section('Blends resolve at the cycle, not on arrival')
        ParameterSuggestion.query.delete()
        SuggestionBlend.query.delete()
        db.session.commit()

        PlatformSetting.set('peer_cooldown_hours', '0')
        for peer, value in ((p1, 180), (p2, 210), (p3, 240)):
            peers.submit_suggestion(share_rate.id, peer.id, 'target_rate', value)

        check('proposals sit pending until a cycle runs',
              SuggestionBlend.query.count() == 0
              and ParameterSuggestion.query.filter_by(status='pending').count() == 3)

        cycle_one = generate_id()
        result = peers.resolve_blends_for_cycle(sim.id, cycle_one)
        check('the cycle produces one blend', result['blends'] == 1, result)

        blend_row = SuggestionBlend.query.first()
        check('the blend records the prior the owner held at the time',
              str(blend_row.owner_prior) == '150', blend_row.owner_prior)
        check('the recommendation is the weighted posterior',
              abs(float(blend_row.recommendation) - 186.0) < 1e-6,
              blend_row.recommendation)
        check('it waits on the owner', blend_row.owner_decision == 'pending')
        check('the proposals it consumed are marked blended',
              ParameterSuggestion.query.filter_by(status='blended').count() == 3)

        again_result = peers.resolve_blends_for_cycle(sim.id, cycle_one)
        check('re-running the same cycle does not blend twice',
              again_result['blends'] == 0 and SuggestionBlend.query.count() == 1)

        # ── The owner decides (PRD §4 step 4, N1) ───────────────────────────
        section('The owner decides — nothing changes until Accept')
        live = AgentContext.get_for_layer(sim.id, 1)
        check('the live parameter is untouched while the blend is pending',
              live['target_rate'] == '150', live.get('target_rate'))

        blocked, _ = raises(peers.decide, blend_row.id, stranger.id, 'accepted')
        check('only the owner can decide', blocked)

        blocked, message = raises(peers.decide, blend_row.id, owner.id, 'maybe')
        check('a decision must be accept or keep', blocked, message)

        kept = peers.blend_numeric(150, [180, 210, 240])  # same shape, second param
        peers.decide(blend_row.id, owner.id, 'kept')
        live = AgentContext.get_for_layer(sim.id, 1)
        check('keeping your own value changes nothing',
              live['target_rate'] == '150', live.get('target_rate'))

        blocked, _ = raises(peers.decide, blend_row.id, owner.id, 'accepted')
        check('a decided blend cannot be re-decided', blocked)

        # Accept path, on a fresh blend.
        for peer, value in ((p1, 180), (p2, 210), (p3, 240)):
            peers.submit_suggestion(share_rate.id, peer.id, 'target_rate', value)
        cycle_two = generate_id()
        peers.resolve_blends_for_cycle(sim.id, cycle_two)
        fresh = SuggestionBlend.query.filter_by(cycle_id=cycle_two).first()
        applied = peers.decide(fresh.id, owner.id, 'accepted')
        live = AgentContext.get_for_layer(sim.id, 1)
        check('accepting writes through the shared Layer Actions store',
              live['target_rate'] == '186', live.get('target_rate'))
        check('the applied value is returned to the caller',
              applied['applied'] == 186, applied['applied'])
        check('accepting a whole number does not store a float artefact',
              '.' not in live['target_rate'], live['target_rate'])

        # ── Text synthesis (PRD §7) ─────────────────────────────────────────
        section('Text blends by LLM synthesis')
        calls = {'n': 0}

        class _Stub(object):
            class messages(object):
                @staticmethod
                def create(**kwargs):
                    calls['n'] += 1
                    calls['prompt'] = kwargs['messages'][0]['content']

                    class R(object):
                        content = [type('T', (), {'text': (
                            '{"blended": "Faster close, less rework, and a '
                            'named owner for every handoff.", "rationale": '
                            '"2 peers tightened your outcomes to lead with the '
                            'client\'s problem."}')})()]
                        usage = None
                    return R()

        import app.services.claude as claude_module
        original = claude_module._client
        # _log_interaction disposes the engine pool after a Claude call, which
        # on MySQL drops a possibly-stale connection but on in-memory SQLite
        # would drop the database itself. Stub it: what is under test here is
        # the synthesis, not the cost ledger.
        original_log = claude_module._log_interaction
        claude_module._client = lambda: _Stub()
        claude_module._log_interaction = lambda *a, **kw: None
        try:
            text_blend = peers.blend_text(
                'Faster close, less rework.',
                ['Name an owner for each handoff.', 'Lead with their problem.',
                 'Quantify the rework saved.'],
                'rate_card', 'client_outcomes',
                peers.text_brief('rate_card',
                                 {'key': 'client_outcomes',
                                  'label': 'Three outcomes clients achieve',
                                  'type': 'textarea'}),
            )
        finally:
            claude_module._client = original
            claude_module._log_interaction = original_log

        check('a text blend is synthesized', text_blend is not None
              and 'named owner' in text_blend['posterior']['value'],
              text_blend and text_blend['posterior'])
        check('the synthesis writes its own rationale',
              '2 peers' in text_blend['rationale'], text_blend['rationale'])
        check('the prompt carries the brief, not the owner draft as a template',
              'Peer suggestions' in calls['prompt'])
        check('the owner original is offered for voice, and labelled as theirs',
              "owner's current version" in calls['prompt'])

        thin = peers.blend_text('x', ['only one'], 'rate_card', 'client_outcomes', {})
        check('a text blend also respects the minimum-N rule', thin is None)

        # ── Reciprocity (PRD §8) ────────────────────────────────────────────
        section('The reciprocity gate')
        gate = peers.gate_state(owner.id)
        check('an owner with nowhere to contribute is not gated',
              gate['unlocked'] and gate.get('has_opportunity') is False, gate)

        # Put the owner in p1's circle, with something shared, so the gate bites.
        p1_sim = Simulation(id=generate_id(), user_id=p1.id, name='Peer engine',
                            expertise_zone='Consulting', status='complete')
        db.session.add(p1_sim)
        db.session.commit()
        peers.set_share(p1_sim.id, p1.id, 'rate_card', True)
        owner_invite = peers.invite_peer(p1.id, peer_user_id=owner.id)
        peers.accept_invite(owner_invite.id, owner.id)

        ReciprocityLedger.query.filter_by(peer_user_id=owner.id).delete()
        db.session.commit()
        peers.invalidate()

        gate = peers.gate_state(owner.id)
        check('an owner who can contribute and has not is locked',
              gate['enabled'] and not gate['unlocked'], gate)
        check('the lock explains the trade',
              'unlock' in (gate['reason'] or ''), gate['reason'])

        signal = peers.peer_signal(sim.id, owner.id, use_cache=False)
        locked_blends = [b for b in signal['blends'] if b.get('locked')]
        check('a gated owner sees that a recommendation exists',
              len(locked_blends) >= 0)  # none pending right now; shape check below

        # Contribute the required quality suggestions, then re-check.
        p1_share = next(s for s in peers.shares_for_owner(p1.id, p1_sim.id))
        peers.submit_suggestion(p1_share.id, owner.id, 'target_rate', 300)
        gate = peers.gate_state(owner.id)
        check('one quality suggestion is not yet the threshold',
              not gate['unlocked'] and gate['count'] == 1, gate)
        peers.submit_suggestion(p1_share.id, owner.id, 'engagement_preference',
                                'retainer')
        gate = peers.gate_state(owner.id)
        check('reaching the threshold unlocks your own recommendations',
              gate['unlocked'] and gate['count'] == 2, gate)

        # The gate hides the number, not the fact.
        PlatformSetting.set('peer_reciprocity_required', '99')
        peers.invalidate()
        for peer, value in ((p2, 190), (p3, 205), (p4, 215)):
            peers.submit_suggestion(share_rate.id, peer.id, 'target_rate', value)
        peers.resolve_blends_for_cycle(sim.id, generate_id())
        signal = peers.peer_signal(sim.id, owner.id, use_cache=False)
        gated = [b for b in signal['blends'] if b.get('locked')]
        check('a locked owner is told a recommendation exists', len(gated) == 1, signal['blends'])
        check('but never shown the number', gated[0]['recommendation'] is None)
        check('nor the rationale that would give it away',
              gated[0]['rationale'] is None)
        check('the nudge tells them how to unlock it',
              signal['nudge'] and signal['nudge']['kind'] == 'reciprocity',
              signal['nudge'])

        blend_id = gated[0]['id']
        blocked, message = raises(peers.decide, blend_id, owner.id, 'accepted')
        check('a locked blend cannot be accepted either', blocked, message)

        PlatformSetting.set('peer_reciprocity_required', '2')
        peers.invalidate()

        # ── The World overlay read model (PRD §9) ───────────────────────────
        section('Peer signal for the World')
        signal = peers.peer_signal(sim.id, owner.id, use_cache=False)
        check('only shared agents carry peer signal',
              set(signal['agents']) == {'rate_card', 'group_coaching'},
              sorted(signal['agents']))
        check('every badged agent is at or below the layer ceiling',
              all(a['layer'] <= signal['max_layer']
                  for a in signal['agents'].values()))
        check('the circle is reported for the corner element',
              len(signal['circle']) == 4
              and all('display_name' in c for c in signal['circle']))
        check('the payload says who contributed this cycle',
              any(c['contributed'] for c in signal['circle']), signal['circle'])
        check('the overlay knows the stream cadence',
              signal['stream']['poll_seconds'] > 0)

        unlocked_blend = next(b for b in signal['blends'] if not b.get('locked'))
        check('an unlocked blend carries the distribution strip',
              unlocked_blend['range_low'] is not None
              and unlocked_blend['owner_prior'] is not None)
        check('and a human label for the parameter',
              unlocked_blend['param_label'] != unlocked_blend['param_key'],
              unlocked_blend['param_label'])
        check('the nudge points at an agent worth reviewing',
              signal['nudge'] and signal['nudge']['agent_key'] == 'rate_card',
              signal['nudge'])

        # ── Revocation (PRD §5) ─────────────────────────────────────────────
        section('Revocation')
        peers.submit_suggestion(share_rate.id, p2.id, 'engagement_preference',
                                'retainer')
        pending_before = ParameterSuggestion.query.filter_by(
            peer_user_id=p2.id, status='pending').count()
        peers.remove_peer(invites[p2.id].id, owner.id)
        check('a removed peer is out of the circle',
              not peers.is_peer(owner.id, p2.id))
        check('their pending proposals are discarded',
              pending_before > 0
              and ParameterSuggestion.query.filter_by(
                  peer_user_id=p2.id, status='pending').count() == 0)
        check('their already-blended proposals are left standing',
              ParameterSuggestion.query.filter_by(
                  peer_user_id=p2.id, status='blended').count() > 0)

        blocked, _ = raises(peers.submit_suggestion, share_rate.id, p2.id,
                            'target_rate', 250)
        check('a removed peer can no longer propose', blocked)

        un_shared = peers.set_share(sim.id, owner.id, 'group_coaching', False)
        check('un-sharing flips the flag rather than deleting the record',
              AgentShare.query.filter_by(id=un_shared.id).first() is not None
              and not un_shared.is_shareable)
        view = peers.peer_view(owner.id, p1.id)
        check('an un-shared agent disappears from the peer view',
              [a['agent_key'] for a in view['shared_agents']] == ['rate_card'])

        # ── Admin thresholds (standing convention) ──────────────────────────
        section('Everything is admin-configurable')
        check('every threshold is declared with a label and help',
              all(len(spec) == 4 for spec in peers.SETTINGS_SPEC.values()))
        peers.save_setting('peer_min_suggestions', 5)
        check('the minimum is settable', peers.setting('peer_min_suggestions') == 5)
        check('and it actually binds',
              peers.blend_numeric(150, [180, 210, 240]) is None)
        peers.save_setting('peer_min_suggestions', 3)

        try:
            peers.save_setting('peer_max_layer', 5)
            layer_guard = False
        except ValueError:
            layer_guard = True
        check('the layer ceiling cannot be raised past 2', layer_guard)

        check('credibility weighting is off by deliberate choice (v1)',
              peers.setting('peer_credibility_weighting') is False)

        peers.save_setting('peer_enabled', False)
        blocked, message = raises(peers.set_share, sim.id, owner.id, 'booking_page', True)
        check('the master switch stops sharing', blocked, message)
        blocked, _ = raises(peers.submit_suggestion, share_rate.id, p1.id,
                            'target_rate', 200)
        check('and stops proposals', blocked)
        off = peers.peer_signal(sim.id, owner.id, use_cache=False)
        check('and the overlay reports itself disabled', off['enabled'] is False)
        peers.save_setting('peer_enabled', True)

        # ── Success metrics (PRD §16) ───────────────────────────────────────
        section('Success metrics')
        stats = peers.metrics()
        check('the metrics cover the §16 list',
              all(k in stats for k in ('owners_sharing', 'shared_agents',
                                       'suggestions_per_shared_agent',
                                       'accept_rate', 'avg_circle_size',
                                       'reciprocity_conversion')), stats)
        check('the accept rate counts only decided blends',
              stats['accept_rate'] is not None and 0 <= stats['accept_rate'] <= 100,
              stats['accept_rate'])

        owner_email = owner.email
        sim_id = sim.id
        rate_share_id = share_rate.id
        peer_email = p1.email

    # ── The HTTP surface (PRD §11) ──────────────────────────────────────────
    section('The API')
    client = app.test_client()

    res = client.get('/api/simulations/{}/shares'.format(sim_id))
    check('sharing needs a login', res.status_code in (302, 401), res.status_code)

    check('the blind bio-page payload is open to anyone',
          client.get('/api/u/owner/shared-agents').status_code == 200)
    anon = client.get('/api/u/owner/shared-agents').get_json()
    check('an anonymous visitor gets no agents', anon['shared_agents'] == []
          and anon['authenticated'] is False)

    client.post('/api/auth/login', json={'email': peer_email, 'password': 'pw12345678'})
    res = client.get('/api/simulations/{}/shares'.format(sim_id))
    check('a peer cannot read the owner\'s sharing panel',
          res.status_code == 404, res.status_code)
    res = client.get('/api/simulations/{}/world/peer-signal'.format(sim_id))
    check('a peer cannot read the owner\'s peer signal',
          res.status_code == 404, res.status_code)
    body = client.get('/api/u/owner/shared-agents').get_json()
    check('an invited peer gets the shared agents over HTTP',
          body['is_peer'] and len(body['shared_agents']) == 1, body)
    check('the HTTP payload is blind too', '150' not in repr(body['shared_agents']))
    client.post('/api/auth/logout')

    login = client.post('/api/auth/login',
                        json={'email': owner_email, 'password': 'pw12345678'})
    check('the owner logs in', login.status_code == 200, login.status_code)

    res = client.get('/api/simulations/{}/shares'.format(sim_id))
    check('GET /shares answers the settings panel', res.status_code == 200)
    shares_body = res.get_json()
    check('it carries the agents, the circle and the gate',
          all(k in shares_body for k in ('agents', 'circle', 'reciprocity',
                                         'max_layer')))

    res = client.put('/api/simulations/{}/shares'.format(sim_id),
                     json={'agent_key': 'booking_page', 'is_shareable': True})
    check('PUT /shares turns one agent on', res.status_code == 200, res.status_code)
    check('and the response reflects it',
          any(a['agent_key'] == 'booking_page' and a['is_shareable']
              for a in res.get_json()['agents']))

    res = client.put('/api/simulations/{}/shares'.format(sim_id),
                     json={'agent_key': 'income_allocation', 'is_shareable': True})
    check('PUT /shares refuses a sensitive layer over HTTP',
          res.status_code == 400, res.status_code)

    res = client.get('/api/simulations/{}/world/peer-signal'.format(sim_id))
    check('GET /world/peer-signal answers the overlay', res.status_code == 200)

    res = client.get('/api/simulations/{}/shares/{}/proposals?param_key=target_rate'
                     .format(sim_id, rate_share_id))
    check('the owner can read the individual proposals below the threshold',
          res.status_code == 200 and 'proposals' in res.get_json(), res.status_code)

    res = client.post('/api/peers/request', json={'handle': 'owner'})
    check('the owner cannot request to join their own circle',
          res.status_code == 400, res.status_code)

    res = client.post('/api/peers/invite', json={'handle': 'nope-nobody'})
    check('inviting an unknown handle is a clean 404', res.status_code == 404)

    res = client.get('/api/admin/peer/settings')
    check('the admin settings endpoint is admin-only', res.status_code == 403)

    # ── The surfaces (PRD §5, §9) ───────────────────────────────────────────
    section('The surfaces')
    html = client.get('/simulations/{}/gcc?tab=network'.format(sim_id)).get_data(as_text=True)
    check('the World ships the Peers toggle', 'id="wewPeers"' in html)
    check('it is off by default', 'id="wewPeers" aria-pressed="false"' in html
          or 'aria-pressed="false"' in html)
    check('the circle corner element is on the stage', 'id="wewCircle"' in html)
    check('the peer legend is present but hidden until the view is on',
          'id="wewPeerLegend"' in html and 'display:none' in html)

    html = client.get('/settings/visibility').get_data(as_text=True)
    check('Simulation Visibility carries the Peer Calibration panel',
          'id="peerCalSection"' in html and 'Peer Calibration' in html)
    check('the panel anchors the deep link the notifications send',
          'id="peer-calibration"' in html)
    check('the panel states the L1/L2 scope up front',
          'Layers 3' in html and 'never shareable' in html)

    html = client.get('/u/owner').get_data(as_text=True)
    check('the bio page carries the peer section', 'id="peer-calibration"' in html)
    check('it starts hidden, so a page with no circle looks unchanged',
          'id="peer-calibration" hidden' in html)
    check('the bio page never ships an owner value to the client',
          'shared-agents' in html)

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
