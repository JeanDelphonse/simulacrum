"""Per-user simulation discount grants (admin comps).

The thing worth testing is not that a percentage multiplies correctly — it is
that a comp cannot leak. So these checks push on the boundaries: a grant applies
to exactly one user, is spent once per simulation rather than standing open, does
not stack with the global sale, does not rewrite the public marketing price, and
cannot be granted or revoked by anyone but an admin.

Against in-memory SQLite; no network, no real data, no Stripe.

    python test_user_discount.py
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


def main():
    from app import create_app
    from app.extensions import bcrypt, db
    from utils.id_gen import generate_id

    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      WTF_CSRF_ENABLED=False)

    with app.app_context():
        from app.models.discount import (
            SimulationDiscount, UserDiscount, UserDiscountRedemption,
        )
        from app.models.platform_settings import PlatformSetting
        from app.models.simulation import Simulation
        from app.models.user import User
        from app.services import pricing_service as pricing

        db.create_all()
        now = datetime.utcnow()
        pw = bcrypt.generate_password_hash('pw12345678').decode()

        def make_user(email, name, is_admin=False):
            u = User(id=generate_id(), email=email, full_name=name,
                     password_hash=pw, email_verified=True, is_admin=is_admin,
                     onboarding_completed_at=now)
            db.session.add(u)
            return u

        admin = make_user('admin@t.local', 'Admin', is_admin=True)
        alice = make_user('alice@t.local', 'Alice')
        bob = make_user('bob@t.local', 'Bob')
        db.session.commit()

        BASE = 69500
        PlatformSetting.set('simulation_price', str(BASE))
        db.session.commit()

        # ── Baseline ────────────────────────────────────────────────────────
        section('Baseline — nobody has a grant')
        p = pricing.price_for_user(alice.id)
        check('an ungranted user pays the base price',
              p['discounted_price_cents'] == BASE, p['discounted_price_cents'])
        check('and is not marked comped', p['is_comped'] is False)
        check('an anonymous caller is handled',
              pricing.price_for_user(None)['discounted_price_cents'] == BASE)

        # ── A 100% grant ────────────────────────────────────────────────────
        section('A 100% grant')
        grant = UserDiscount(user_id=alice.id, discount_percentage=100,
                             max_uses=1, reason='Design partner',
                             granted_by=admin.id)
        db.session.add(grant)
        db.session.commit()

        p = pricing.price_for_user(alice.id)
        check('the granted user pays nothing', p['discounted_price_cents'] == 0, p)
        check('the price is flagged as a comp', p['is_comped'] is True)
        check('the grant is named on the price', p['user_discount_id'] == grant.id)
        check('the reason becomes the label', p['label'] == 'Design partner')
        check('the base price is still reported, so the saving is visible',
              p['base_price_cents'] == BASE)

        check('the grant does not leak to another user',
              pricing.price_for_user(bob.id)['discounted_price_cents'] == BASE)
        check('nor to anonymous callers',
              pricing.price_for_user(None)['is_comped'] is False)

        # ── Pricing is a read; redemption is the write ──────────────────────
        section('Pricing does not spend the grant')
        for _ in range(4):
            pricing.price_for_user(alice.id)
        db.session.refresh(grant)
        check('quoting the price four times spends nothing',
              grant.uses_count == 0, grant.uses_count)
        check('the grant is still live', grant.is_live)

        # ── Redemption ──────────────────────────────────────────────────────
        section('Redemption spends exactly one use')
        sim1 = Simulation(id=generate_id(), user_id=alice.id, name='First',
                          expertise_zone='Consulting', status='pending')
        db.session.add(sim1)
        db.session.flush()

        p = pricing.price_for_user(alice.id)
        check('redeeming succeeds', pricing.redeem_user_discount(alice.id, sim1, p))
        db.session.commit()
        db.session.refresh(grant)
        check('one use is spent', grant.uses_count == 1, grant.uses_count)
        check('a one-use grant is now exhausted', grant.is_exhausted)
        check('and no longer live', not grant.is_live)
        check('its status reads as used', grant.status() == 'used', grant.status())

        red = UserDiscountRedemption.query.filter_by(user_id=alice.id).first()
        check('the redemption is recorded against the simulation',
              red is not None and red.simulation_id == sim1.id)
        check('it records what the comp cost us',
              red.comped_cents == BASE, red.comped_cents)

        p = pricing.price_for_user(alice.id)
        check('the next purchase is at full price',
              p['discounted_price_cents'] == BASE and p['is_comped'] is False, p)

        sim2 = Simulation(id=generate_id(), user_id=alice.id, name='Second',
                          expertise_zone='Consulting', status='pending')
        db.session.add(sim2)
        db.session.flush()
        check('an exhausted grant cannot be redeemed again',
              not pricing.redeem_user_discount(alice.id, sim2, p))

        # ── Double-spend guard ──────────────────────────────────────────────
        section('A one-use grant cannot be spent twice')
        g2 = UserDiscount(user_id=bob.id, discount_percentage=100, max_uses=1,
                          granted_by=admin.id)
        db.session.add(g2)
        db.session.commit()

        priced_once = pricing.price_for_user(bob.id)
        priced_twice = pricing.price_for_user(bob.id)   # two concurrent checkouts
        s_a = Simulation(id=generate_id(), user_id=bob.id, name='A',
                         expertise_zone='Consulting', status='pending')
        s_b = Simulation(id=generate_id(), user_id=bob.id, name='B',
                         expertise_zone='Consulting', status='pending')
        db.session.add_all([s_a, s_b])
        db.session.flush()

        first = pricing.redeem_user_discount(bob.id, s_a, priced_once)
        second = pricing.redeem_user_discount(bob.id, s_b, priced_twice)
        db.session.commit()
        db.session.refresh(g2)
        check('the first checkout wins', first)
        check('the second is refused rather than overselling', not second)
        check('the counter landed on exactly one', g2.uses_count == 1, g2.uses_count)

        # ── Multi-use and unlimited ─────────────────────────────────────────
        section('Multi-use and unlimited grants')
        g3 = UserDiscount(user_id=bob.id, discount_percentage=100, max_uses=3,
                          granted_by=admin.id)
        db.session.add(g3)
        db.session.commit()
        for i in range(3):
            s = Simulation(id=generate_id(), user_id=bob.id, name='M%d' % i,
                           expertise_zone='Consulting', status='pending')
            db.session.add(s)
            db.session.flush()
            pricing.redeem_user_discount(bob.id, s, pricing.price_for_user(bob.id))
        db.session.commit()
        db.session.refresh(g3)
        check('a 3-use grant spends three times', g3.uses_count == 3, g3.uses_count)
        check('then stops', not g3.is_live)
        check('and the user is back to full price',
              pricing.price_for_user(bob.id)['discounted_price_cents'] == BASE)

        g4 = UserDiscount(user_id=bob.id, discount_percentage=100,
                          max_uses=UserDiscount.UNLIMITED, granted_by=admin.id)
        db.session.add(g4)
        db.session.commit()
        check('unlimited is max_uses = 0', g4.is_unlimited)
        check('it reports no remaining count', g4.uses_remaining is None)
        for i in range(5):
            s = Simulation(id=generate_id(), user_id=bob.id, name='U%d' % i,
                           expertise_zone='Consulting', status='pending')
            db.session.add(s)
            db.session.flush()
            pricing.redeem_user_discount(bob.id, s, pricing.price_for_user(bob.id))
        db.session.commit()
        db.session.refresh(g4)
        check('an unlimited grant keeps working', g4.is_live)
        check('but still counts, so a standing comp can be audited',
              g4.uses_count == 5, g4.uses_count)
        g4.revoked_at = datetime.utcnow()
        db.session.commit()

        # ── Expiry and revocation ───────────────────────────────────────────
        section('Expiry and revocation')
        expired = UserDiscount(user_id=alice.id, discount_percentage=100,
                               max_uses=1, granted_by=admin.id,
                               expires_at=datetime.utcnow() - timedelta(days=1))
        db.session.add(expired)
        db.session.commit()
        check('an expired grant does not price',
              pricing.price_for_user(alice.id)['discounted_price_cents'] == BASE)
        check('its status reads as expired', expired.status() == 'expired')

        revoked = UserDiscount(user_id=alice.id, discount_percentage=100,
                               max_uses=1, granted_by=admin.id,
                               revoked_at=datetime.utcnow(), revoked_by=admin.id)
        db.session.add(revoked)
        db.session.commit()
        check('a revoked grant does not price',
              pricing.price_for_user(alice.id)['discounted_price_cents'] == BASE)
        check('its status reads as revoked', revoked.status() == 'revoked')

        # ── No stacking with the global sale ────────────────────────────────
        section('The global sale and a comp never stack')
        UserDiscount.query.delete()
        db.session.commit()
        db.session.add(SimulationDiscount(
            id=generate_id(), discount_percentage=50,
            start_at=datetime.utcnow() - timedelta(hours=1),
            end_at=datetime.utcnow() + timedelta(days=1),
            label='Launch', created_by=admin.id,
        ))
        db.session.commit()

        p = pricing.price_for_user(alice.id)
        check('with only a sale running, the sale applies',
              p['discounted_price_cents'] == BASE // 2 and not p['is_comped'], p)

        small = UserDiscount(user_id=alice.id, discount_percentage=20,
                             max_uses=1, granted_by=admin.id)
        db.session.add(small)
        db.session.commit()
        p = pricing.price_for_user(alice.id)
        check('a weaker comp does not beat a stronger sale',
              p['discounted_price_cents'] == BASE // 2, p['discounted_price_cents'])
        check('20% + 50% is not 70% off', p['discount_percentage'] == 50)
        check('and the weaker comp is left unspent for a better day',
              not p['is_comped'])
        check('redeeming is refused when the comp did not set the price',
              not pricing.redeem_user_discount(alice.id, sim2, p))

        small.discount_percentage = 100
        db.session.commit()
        p = pricing.price_for_user(alice.id)
        check('a stronger comp beats the sale',
              p['discounted_price_cents'] == 0 and p['is_comped'], p)
        check('and reports 100, not 150', p['discount_percentage'] == 100)

        # ── The best grant wins ─────────────────────────────────────────────
        section('The best of several grants wins')
        UserDiscount.query.delete()
        SimulationDiscount.query.delete()
        db.session.commit()
        db.session.add_all([
            UserDiscount(user_id=alice.id, discount_percentage=25, max_uses=1,
                         granted_by=admin.id),
            UserDiscount(user_id=alice.id, discount_percentage=100, max_uses=1,
                         granted_by=admin.id),
        ])
        db.session.commit()
        check('the higher percentage is chosen',
              pricing.price_for_user(alice.id)['discount_percentage'] == 100)

        # ── The public price is untouched ───────────────────────────────────
        section('Marketing copy is not rewritten by a comp')
        check('get_current_price ignores per-user grants entirely',
              pricing.get_current_price()['discounted_price_cents'] == BASE,
              pricing.get_current_price())
        check('and reports no discount',
              pricing.get_current_price()['is_discounted'] is False)

        UserDiscount.query.delete()
        db.session.commit()

        admin_email, alice_email, bob_email = admin.email, alice.email, bob.email
        alice_id, bob_id = alice.id, bob.id

    # ── The admin API ───────────────────────────────────────────────────────
    section('The admin API')
    client = app.test_client()

    res = client.post('/api/admin/users/%s/discounts' % alice_id,
                      json={'discount_percentage': 100})
    check('granting needs a login', res.status_code in (302, 401), res.status_code)

    client.post('/api/auth/login', json={'email': alice_email, 'password': 'pw12345678'})
    res = client.post('/api/admin/users/%s/discounts' % alice_id,
                      json={'discount_percentage': 100})
    check('a normal user cannot comp themselves', res.status_code == 403, res.status_code)
    res = client.get('/api/admin/users/%s/discounts' % alice_id)
    check('nor read the grants list', res.status_code == 403)
    res = client.get('/api/admin/user-discounts')
    check('nor the platform-wide comp table', res.status_code == 403)
    client.post('/api/auth/logout')

    login = client.post('/api/auth/login',
                        json={'email': admin_email, 'password': 'pw12345678'})
    check('the admin logs in', login.status_code == 200, login.status_code)

    res = client.post('/api/admin/users/%s/discounts' % alice_id,
                      json={'discount_percentage': 100, 'max_uses': 1,
                            'reason': 'Design partner', 'expires_in_days': 30})
    check('the admin grants a 100% comp', res.status_code == 201, res.status_code)
    body = res.get_json()
    check('the grant comes back with its status',
          body['status'] == 'active' and body['discount_percentage'] == 100, body)
    check('the expiry was applied', body['expires_at'] is not None)
    grant_id = body['id']

    res = client.get('/api/admin/users/%s/discounts' % alice_id)
    detail = res.get_json()
    check('the console can read the grant back', len(detail['grants']) == 1)
    check('it reports what the user pays right now',
          detail['effective_price']['price_cents'] == 0
          and detail['effective_price']['is_comped'], detail['effective_price'])
    check('it offers percentage presets with 100 first',
          detail['percentage_presets'][0] == 100)

    res = client.get('/api/simulations/price')
    quote = res.get_json()
    check('the admin themself is not comped',
          quote['amount_cents'] == 69500 and quote['is_comped'] is False, quote)

    # Validation
    section('Validation')
    for payload, why in (
        ({'discount_percentage': 0}, 'zero percent'),
        ({'discount_percentage': 101}, 'over 100 percent'),
        ({'discount_percentage': 'free'}, 'a non-numeric percentage'),
        ({'discount_percentage': 100, 'max_uses': -1}, 'negative uses'),
        ({'discount_percentage': 100, 'expires_in_days': 0.5}, 'a fractional expiry'),
    ):
        res = client.post('/api/admin/users/%s/discounts' % bob_id, json=payload)
        check('rejects ' + why, res.status_code == 400,
              '%s -> %s' % (res.status_code, (res.get_json() or {}).get('error')))

    res = client.post('/api/admin/users/nosuchuser/discounts',
                      json={'discount_percentage': 100})
    check('rejects an unknown user', res.status_code == 404, res.status_code)

    res = client.post('/api/admin/users/%s/discounts' % bob_id, json={})
    check('an empty body defaults to a single-use 100% comp',
          res.status_code == 201
          and res.get_json()['discount_percentage'] == 100
          and res.get_json()['max_uses'] == 1, res.get_json())

    # Revocation
    section('Revocation')
    res = client.delete('/api/admin/users/%s/discounts/%s' % (alice_id, grant_id))
    check('the admin revokes the grant', res.status_code == 200, res.status_code)
    check('it reads as revoked, not deleted',
          res.get_json()['status'] == 'revoked')
    check('revoking twice is idempotent',
          client.delete('/api/admin/users/%s/discounts/%s'
                        % (alice_id, grant_id)).status_code == 200)
    check('the record survives revocation',
          len(client.get('/api/admin/users/%s/discounts' % alice_id)
              .get_json()['grants']) == 1)
    res = client.delete('/api/admin/users/%s/discounts/%s' % (bob_id, grant_id))
    check('a grant cannot be revoked through another user\'s URL',
          res.status_code == 404, res.status_code)

    res = client.get('/api/admin/user-discounts')
    table = res.get_json()
    check('the platform comp table lists every grant',
          len(table['grants']) >= 2, len(table['grants']))
    check('it carries the email so the table is readable',
          any(g.get('user_email') for g in table['grants']))
    check('and totals what comps have cost',
          'total_comped_usd' in table, table.get('total_comped_usd'))

    # ── End to end: a comped user buys ──────────────────────────────────────
    # The point of the whole feature. A 100% comp has to take the buyer down the
    # same free path an org credit does: no PaymentIntent, generation starts,
    # and the grant is spent exactly once.
    section('End to end — a comped user buys')

    # Generation would call Claude, so the task is stubbed. What is under test is
    # the charge decision, not the agent.
    import app.tasks.simulation as sim_tasks
    started = []

    class _StubTask(object):
        @staticmethod
        def apply(args=None, **kw):
            started.append((args or [None])[0])

    original_task = sim_tasks.generate_simulation_task
    sim_tasks.generate_simulation_task = _StubTask

    try:
        with app.app_context():
            from app.extensions import db as _db
            from app.models.discount import UserDiscount, UserDiscountRedemption
            from app.models.resume import Resume
            from app.models.simulation import Simulation
            from utils.id_gen import generate_id as _gid

            UserDiscount.query.delete()
            UserDiscountRedemption.query.delete()
            _db.session.add(Resume(id=_gid(), user_id=alice_id, label='CV',
                                   file_path='x.pdf', file_type='pdf',
                                   parsed_text='Ops leader. Twenty years.'))
            _db.session.add(UserDiscount(user_id=alice_id, discount_percentage=100,
                                         max_uses=1, reason='Design partner'))
            _db.session.commit()
            resume_id = Resume.query.filter_by(user_id=alice_id).first().id

        client.post('/api/auth/logout')
        client.post('/api/auth/login',
                    json={'email': alice_email, 'password': 'pw12345678'})

        quote = client.get('/api/simulations/price').get_json()
        check('the comped buyer is quoted $0 before they buy',
              quote['amount_cents'] == 0 and quote['is_comped'], quote)

        res = client.post('/api/simulations', json={
            'resume_id': resume_id, 'expertise_zone': 'Operations',
            'name': 'Comped run',
        })
        check('checkout succeeds', res.status_code == 201, res.status_code)
        body = res.get_json()
        check('it takes the free path, with no PaymentIntent',
              body.get('is_free') is True and body.get('amount_cents') == 0
              and 'client_secret' not in body, body)
        check('generation actually started', len(started) == 1, started)

        with app.app_context():
            from app.models.audit_log import AuditLog
            from app.models.discount import UserDiscount, UserDiscountRedemption
            from app.models.simulation import Simulation

            sim = Simulation.query.get(body['simulation_id'])
            check('the simulation records the 100% discount',
                  sim.discount_applied_percentage == 100, sim.discount_applied_percentage)
            check('and that nothing was charged', sim.amount_charged_cents == 0)
            check('while keeping the price it would have been',
                  sim.base_price_at_purchase_cents == 69500,
                  sim.base_price_at_purchase_cents)
            check('no Stripe PaymentIntent was attached',
                  sim.stripe_payment_intent_id is None)

            grant = UserDiscount.query.filter_by(user_id=alice_id).first()
            check('the grant was spent once', grant.uses_count == 1, grant.uses_count)
            check('the redemption points at the simulation',
                  UserDiscountRedemption.query.filter_by(
                      simulation_id=sim.id).count() == 1)
            check('the redemption is in the audit log',
                  AuditLog.query.filter_by(action='user_discount_redeemed').count() == 1)

        # The comp is now spent, so the next purchase must be a real checkout.
        quote = client.get('/api/simulations/price').get_json()
        check('the spent comp no longer discounts the quote',
              quote['amount_cents'] == 69500 and not quote['is_comped'], quote)

        client.post('/api/auth/logout')
        client.post('/api/auth/login',
                    json={'email': admin_email, 'password': 'pw12345678'})
    finally:
        sim_tasks.generate_simulation_task = original_task

    # ── The console surface ─────────────────────────────────────────────────
    section('The console surface')
    html = client.get('/admin').get_data(as_text=True)
    check('every user row has a Comp action', 'openCompModal(' in html)
    check('the comp modal ships', 'id="compModal"' in html)
    check('it defaults to 100% for one simulation',
          'value="100" selected' in html and 'value="1" selected' in html)
    check('unlimited is an explicit option, not the default',
          'Unlimited — never pays' in html)
    check('the unlimited combination is called out before it is granted',
          'id="compWarning"' in html and 'updateCompWarning' in html)
    check('the grant history renders in the modal', 'id="compGrants"' in html)

    # ── Regression: the checkout screen must not call Stripe for a free run ──
    # The bug this covers: the page ignored `is_free`, fell through to
    # stripe.confirmCardPayment(undefined) and threw, which surfaced to the user
    # as "Request failed. Please try again." A 100% comp made it reachable, but a
    # 100% global sale and an org credit hit the same path.
    section('Regression - free checkout skips Stripe')
    with app.app_context():
        from app.models.resume import Resume
        rid = Resume.query.filter_by(user_id=alice_id).first().id

    client.post('/api/auth/logout')
    client.post('/api/auth/login',
                json={'email': alice_email, 'password': 'pw12345678'})
    page = client.get('/resumes/%s' % rid).get_data(as_text=True)

    check('the page branches on is_free before touching Stripe',
          'if (data.is_free)' in page)
    # Anchored on the real call site, not the word: the fix's own comment
    # mentions confirmCardPayment and sits above the branch.
    check('the is_free check precedes the Stripe call',
          'if (data.is_free)' in page
          and 'stripe.confirmCardPayment(data.client_secret' in page
          and page.index('if (data.is_free)')
              < page.index('stripe.confirmCardPayment(data.client_secret'))
    check('the free branch redirects to the confirmed page',
          '/confirmed' in page.split('if (data.is_free)')[1][:260],
          page.split('if (data.is_free)')[1][:200] if 'if (data.is_free)' in page else '')
    check('the card fields are addressable so they can be hidden',
          'id="simCardFields"' in page)
    check('a no-charge note exists for a comped buyer', 'id="simFreeNote"' in page)
    check('the button label drops "Charge" when the price is zero',
          "isFree ? 'Generate" in page)
    client.post('/api/auth/logout')
    client.post('/api/auth/login',
                json={'email': admin_email, 'password': 'pw12345678'})

    # ── Regression: a missing migration is not a 500 ─────────────────────────
    section('Regression - missing migration reports the fix')
    with app.app_context():
        from app.extensions import db as _db
        from app.models.discount import UserDiscount, UserDiscountRedemption
        UserDiscountRedemption.__table__.drop(_db.engine)
        UserDiscount.__table__.drop(_db.engine)

    try:
        for label, res in (
            ('reading grants', client.get('/api/admin/users/%s/discounts' % alice_id)),
            ('granting', client.post('/api/admin/users/%s/discounts' % alice_id,
                                     json={'discount_percentage': 100})),
            ('the comp table', client.get('/api/admin/user-discounts')),
            ('revoking', client.delete('/api/admin/users/%s/discounts/zzzzzzzzz'
                                       % alice_id)),
        ):
            body = res.get_json() or {}
            check('%s answers 503, not 500' % label,
                  res.status_code == 503, res.status_code)
            check('%s names the migration to run' % label,
                  body.get('setup_required') is True
                  and '052_user_discounts.sql' in (body.get('error') or ''),
                  body.get('error'))

        res = client.get('/api/simulations/price')
        check('checkout pricing still works without the tables',
              res.status_code == 200 and res.get_json()['amount_cents'] == 69500,
              res.get_json())
        check('and reports no comp rather than failing',
              res.get_json()['is_comped'] is False)

        html = client.get('/admin').get_data(as_text=True)
        check('the console renders the setup message instead of an HTTP code',
              'setup_required' in html)
    finally:
        with app.app_context():
            from app.extensions import db as _db
            from app.models.discount import UserDiscount, UserDiscountRedemption
            UserDiscount.__table__.create(_db.engine)
            UserDiscountRedemption.__table__.create(_db.engine)


    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
