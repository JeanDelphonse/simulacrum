"""SITE_UPDATE_PLAN — the marketing page renders from the same price as checkout,
exactly five layers, and the "agents that act" copy stays honest.

    python test_site_pricing.py
"""
import os
import re
import sys
from datetime import datetime, timedelta

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from utils.id_gen import generate_id                          # noqa: E402

PASS, FAIL = [], []
ROOT = os.path.dirname(os.path.abspath(__file__))


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    mark = 'ok  ' if condition else 'FAIL'
    line = '  {} {}{}'.format(mark, label, ('  -- ' + str(detail)) if detail else '')
    print(line.encode(sys.stdout.encoding or 'ascii', 'replace')
              .decode(sys.stdout.encoding or 'ascii'))


def text_of(html):
    """Visible text, roughly: no tags, no <style>/<script> bodies."""
    html = re.sub(r'(?is)<(style|script)\b.*?</\1>', ' ', html)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))


def main():
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.create_all()
        from app.models.discount import SimulationDiscount
        from app.models.platform_settings import PlatformSetting
        from app.models.user import User
        from app.services import site_context

        client = app.test_client()

        print('\n1. Template source has no hardcoded price')
        src = open(os.path.join(ROOT, 'app', 'templates', 'landing.html'), encoding='utf-8').read()
        body_src = re.sub(r'(?is)<(style|script)\b.*?</\1>', ' ', src)
        literals = [m for m in re.findall(r'\$\d[\d,]*(?:\.\d+)?', body_src) if m != '$0']
        check('no dollar literal in landing.html other than the free tier\'s $0', not literals, literals)
        check('the old base-price-only variable is gone', 'sim_price_usd' not in src and 'sim_pricing' not in src)

        print('\n2. Base price, no discount')
        PlatformSetting.set('simulation_price', '16700')
        db.session.commit()
        page = client.get('/').get_data(as_text=True)
        text = text_of(page)
        api = client.get('/api/simulations/price').get_json()
        check('checkout quotes $167', api['amount_usd'] == '$167', api)
        check('pricing card shows the checkout price', '$167' in text and 'line-through' not in page.split('id="pricing"')[1][:200])
        check('no strikethrough or launch label without a discount', '% launch pricing' not in text)
        check('FAQ quotes the same price', 'You pay $167 once' in text)

        print('\n3. 50% launch discount on (admin)')
        admin = User(id=generate_id(), email='a@x.com', full_name='A', password_hash='x', is_admin=True,
                     email_verified=True)
        db.session.add(admin)
        db.session.add(SimulationDiscount(id=generate_id(), discount_percentage=50,
                                          start_at=datetime.utcnow() - timedelta(hours=1),
                                          end_at=datetime.utcnow() + timedelta(days=2),
                                          label=None, created_by=admin.id))
        db.session.commit()
        page = client.get('/').get_data(as_text=True)
        text = text_of(page)
        api = client.get('/api/simulations/price').get_json()
        check('checkout charges $83.50', api['amount_usd'] == '$83.50', api)
        pricing_html = page.split('id="pricing"')[1].split('</section>')[0]
        check('card shows the original struck through next to the current price',
              '<span class="price-original">$167</span>' in pricing_html
              and '<span class="price-current">$83.50</span>' in pricing_html)
        check('card labels it "50% launch pricing"', '50% launch pricing' in text_of(pricing_html))
        check('FAQ quotes original and current from the same source (the old bug)',
              'normally $167; with 50% launch pricing you pay $83.50 once' in text)
        check('FAQ no longer says "$167 once"', 'You pay $167 once' not in text)
        SimulationDiscount.query.update({'label': 'Founders'})
        db.session.commit()
        check('an admin label replaces the default label',
              'Founders' in text_of(client.get('/').get_data(as_text=True).split('id="pricing"')[1][:3000]))
        SimulationDiscount.query.delete()
        db.session.commit()

        print('\n4. Pricing unavailable')
        orig = site_context.pricing_context
        import app.services.pricing_service as ps
        real = ps.get_current_price
        ps.get_current_price = lambda: (_ for _ in ()).throw(RuntimeError('db down'))
        text = text_of(client.get('/').get_data(as_text=True))
        ps.get_current_price = real
        check('no made-up price when pricing fails', 'See price at checkout' in text and '$695' not in text)
        site_context.pricing_context = orig

        print('\n5. Exactly five layers')
        layers = site_context.site_layers()
        check('five layers, 49 agents from agents.json',
              layers['count'] == 5 and layers['agent_total'] == 49
              and [l['agents'] for l in layers['layers']] == [11, 8, 10, 10, 10], layers['agent_total'])
        page = client.get('/').get_data(as_text=True)
        text = text_of(page)
        check('no "6 layers" / "six layers" anywhere on the page',
              not re.search(r'\b(6|six)\s+(income\s+)?layers\b', text, re.I))
        check('FAQ says five income layers', 'across 5 income layers' in text)
        check('the ladder renders five rows', page.count('class="layer-badge"') == 5)
        for name in ('Active Income', 'Leveraged Income', 'Productized Income',
                     'Automated Residual Income', 'Wealth Deployment'):
            check('ladder shows %s' % name, name in text)

        print('\n6. Positioning sections A-H')
        check('A: hero says what the agents do', '10 emails to copy-paste' in text)
        how = text_of(page.split('id="how-it-works"')[1].split('</section>')[0])
        check('B: five how-it-works steps, generate then act',
              all(t in how for t in ('Agents build the deliverables', 'Agents act on them',
                                     'Decide with foresight')) and page.split('id="how-it-works"')[1]
              .split('</section>')[0].count('class="n"') == 5)
        check('C: "From plan to done." section with a 4-step loop',
              'From plan to done.' in text and page.split('id="plan-to-done"')[1]
              .split('</section>')[0].count('class="n"') == 4)
        for card in ('Agents that act, not just advise', 'You decide what runs', 'Works through your tools'):
            check('E: card "%s"' % card, card in text)
        check('D: control pillar next to Calibrated / Validated',
              all(t in text for t in ("You're always in control", 'Calibrated', 'Validated',
                                      'Never touches your money')))
        intg = page.split('id="integrations"')[1].split('</section>')[0]
        check('F: integrations section lists what each unlocks',
              'Connect your tools; your agents work through them.' in text
              and all(n in intg for n in ('Apollo', 'ConvertKit', 'Cal.com', 'Stripe', 'Kajabi', 'PandaDoc')))
        check('F: not-yet-live integrations are marked rolling out',
              intg.count('Rolling out') == 2 and 'never places trades' in intg)
        check('F: FAQ asks what each integration unlocks',
              'What does each integration unlock?' in text and 'Do I need all nine' not in text)
        check('G: value line', 'Not 49 documents. 49 agents that act.' in text)
        for q in ('Will SimiSI email people without my permission?', 'Does it use my own email account?',
                  'Can agents move my money?', 'What if an agent makes a mistake?'):
            check('H: FAQ "%s"' % q, q in text)

        print('\n7. Copy honesty (Rule 4)')
        for claim in ('never ours', 'nothing goes out without your okay', 'books the calls',
                      'approval at every step', 'from your own account'):
            check('does not claim "%s"' % claim, claim.lower() not in text.lower())
        check('brand email preserved', 'simi@simulacrumai.io' in page)
        check('checkout entry points still linked', page.count('Run your simulation') >= 1
              and '/register' in page)

        print('\n8. Careers posting')
        jobs = open(os.path.join(ROOT, 'config', 'jobs.json'), encoding='utf-8').read()
        check('no hardcoded simulation price in the job postings', '$167' not in jobs)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
