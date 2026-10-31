"""Bio Page Enhancements, quick wins: fact box, "Updated" date, owner writing
check (hype words, lead paragraph), honest AI chat label, sameAs, contents list.

Against in-memory SQLite; no network.

    python test_bio_quick_wins.py
"""
import os
import re
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


def text_of(html):
    html = re.sub(r'(?is)<(style|script)\b.*?</\1>', ' ', html)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))


def main():
    from app import create_app
    from app.extensions import db, bcrypt
    from utils.id_gen import generate_id

    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False)

    with app.app_context():
        from flask import g
        from app.models.bio_page import BioPage
        from app.models.profile import UserProfile, SimulationVisibility
        from app.models.user import User
        from app.models.simulation import Simulation
        from app.models.agent_action import AgentAction
        from app.models.calibration import CalibrationRun, ReferenceDataset
        from app.services import bio_facts, bio_quality, bio_service

        db.create_all()
        now = datetime.utcnow()
        u = User(id=generate_id(), email='jean@t.local', full_name='Jean M. Delphonse',
                 password_hash=bcrypt.generate_password_hash('pw12345678').decode(),
                 email_verified=True, onboarding_completed_at=now)
        db.session.add(u)
        db.session.commit()
        prof = UserProfile(user_id=u.id, display_name='Jean M. Delphonse', username='jeanmdelphonse',
                           is_published=True, location='Santa Clara, CA',
                           linkedin_url='https://www.linkedin.com/in/jean', website_url='https://simisi.io',
                           tagline='Technical program manager for AI and enterprise data programs.',
                           bio=('Jean M. Delphonse is a senior technical program manager who runs AI and '
                                'enterprise data programs. He has led programs at JPMorgan Chase and Adobe. '
                                'He is a visionary, world-class leader.'))
        prof.career_history = [
            {'company_name': 'SimiSI', 'role': 'Founder', 'start_date': '2023', 'end_date': 'Present',
             'company_url': 'https://simisi.io', 'source': 'manual',
             'description': 'Built a cutting-edge simulation platform.'},
            {'company_name': 'JPMorgan Chase', 'role': 'Senior TPM', 'start_date': '2008-04',
             'end_date': '2015-06', 'source': 'resume', 'description': 'Led a 40-person data migration.'},
        ]
        db.session.add(prof)
        sim = Simulation(id=generate_id(), user_id=u.id, name='S', expertise_zone='AI Program Management',
                         status='complete')
        db.session.add(sim)
        db.session.flush()
        bp = BioPage(id=generate_id(), user_id=u.id, slug='jeanmdelphonse', simulation_id=sim.id,
                     status=BioPage.STATUS_PUBLISHED, theme='light',
                     published_at=now - timedelta(days=30))
        db.session.add(bp)
        db.session.add(SimulationVisibility(id=generate_id(), simulation_id=sim.id, user_id=u.id,
                                            is_public=True, availability='limited', services=[]))
        rc = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1, action_type='rate_card',
                         status='complete', artifact='# Rate card\n\nAdvisory: $9,999/hr', created_by=u.id,
                         completed_at=now)
        db.session.add(rc)
        ds = ReferenceDataset(id=generate_id(), layer='L1', name='Contract TPM rates 2026', source='Upwork',
                              unit='usd_hour')
        db.session.add(ds)
        db.session.flush()
        db.session.add(CalibrationRun(id=generate_id(), simulation_id=sim.id, action_id=rc.id,
                                      agent_key='rate_card', output_field='billable_hourly_rate',
                                      field_label='Your billable hourly rate', unit='usd_hour',
                                      raw_value=180, cal_low=150, cal_mid=175, cal_high=210,
                                      confidence_tier='moderate', dataset_id=ds.id))
        db.session.add(CalibrationRun(id=generate_id(), simulation_id=sim.id, action_id=rc.id,
                                      agent_key='rate_card', output_field='projected_annual_income',
                                      field_label='Projected annual income', unit='usd_year',
                                      raw_value=372000, cal_low=300000, cal_mid=372000, cal_high=410000,
                                      confidence_tier='moderate', dataset_id=ds.id))
        db.session.commit()
        prof_id, user_id = prof.id, u.id

        client = app.test_client()

        print('\n1. Fact box (visitor)')
        html = client.get('/u/jeanmdelphonse').get_data(as_text=True)
        check('page renders', 'Jean M. Delphonse' in html)
        box = html.split('id="at-a-glance"')[1].split('</aside>')[0] if 'id="at-a-glance"' in html else ''
        t = text_of(box)
        check('fact box present', bool(box))
        check('role and current employer from the career history',
              'Currently' in t and 'SimiSI' in t, t[:300])
        check('location', 'Santa Clara, CA' in t)
        years = now.year - 2008
        check('years of experience from the earliest start date', '%d+ years' % years in t)
        check('focus areas from public simulations', 'AI Program Management' in t)
        check('availability from simulation visibility', 'Limited availability' in t)
        check('rate range is the calibrated range, not the rate card prose',
              '$150–$210/hr' in t and '9,999' not in t, t)
        check('rate range cites its source and confidence',
              'Source: Contract TPM rates 2026' in t and 'Moderate confidence' in t
              and 'confidence confidence' not in t)
        check('income projections never appear', '372' not in t and '410' not in t)
        check('links: LinkedIn and Website', 'LinkedIn' in t and 'Website' in t)
        check('"Updated" date shown', re.search(r'Updated [A-Z][a-z]{2} \d{1,2}, \d{4}', t) is not None, t[:120])
        check('the owner writing check is not shown to visitors', 'id="owner-quality"' not in html)

        print('\n2. Structured data, contents, chat label')
        ld = re.search(r'"sameAs": (\[[^\]]*\])', html)
        check('sameAs includes the website', ld and 'https://simisi.io' in ld.group(1), ld and ld.group(1))
        check('contents list placeholder rendered for the builder', 'id="bio-toc"' in html)
        check('chat header says it is an AI on the owner\'s behalf',
              "Simi, Jean's AI assistant" in html and 'An AI, not Jean.' in html)
        check('chat label promises notification, not a live hand-off, when takeover is off',
              'is notified of your conversation' in html and 'can join it live' not in html)
        bp = BioPage.query.get(bp.id)
        bp.chat_settings = dict(bp.chat_settings, live_takeover_enabled=True)
        db.session.commit()
        html2 = client.get('/u/jeanmdelphonse').get_data(as_text=True)
        check('…and mentions joining live only when takeover is on', 'can join it live' in html2)

        print('\n3. Owner writing check')
        with client.session_transaction() as sess:
            sess['_user_id'] = user_id
            sess['_fresh'] = True
        g.pop('_login_user', None)
        html = client.get('/u/jeanmdelphonse').get_data(as_text=True)
        oq = html.split('id="owner-quality"')[1].split('</div>')[0] if 'id="owner-quality"' in html else ''
        check('owner sees the writing check', bool(oq))
        for term in ('visionary', 'world-class', 'cutting-edge'):
            check('flags "%s"' % term, '&ldquo;%s&rdquo;' % term in oq)
        check('names the field each was found in', 'in About' in oq and 'in Career: SimiSI' in oq)
        check('says publishing is not blocked', "Publishing isn't blocked" in oq)
        check('a good lead paragraph gets no tip', 'Opening paragraph' not in oq)

        print('\n4. Lead paragraph heuristic')
        check('lead naming the person passes',
              bio_quality.lead_tip(prof.bio, 'Jean M. Delphonse', '') is None)
        check('a bio that opens with a section is flagged',
              'starts with a section' in (bio_quality.lead_tip('**Career**\nWorked at X.', 'Jean D', '') or ''))
        check('a first sentence that never says who is flagged',
              "doesn't say who you are" in (bio_quality.lead_tip('Programs are hard. Data is messy.', 'Jean Delphonse', '') or ''))
        check('an over-long opening paragraph is flagged',
              'long' in (bio_quality.lead_tip('Jean runs programs. ' + 'It works. ' * 6, 'Jean', '') or ''))
        check('leverage as a verb is flagged',
              any(f['term'] == 'leveraged' for f in bio_quality.find_hype({'About': 'He leveraged data.'})))
        check('a hyphenated near-miss is not flagged ("world-classic" ≠ "world-class")',
              not bio_quality.find_hype({'About': 'a world-classic novel'}))

        print('\n5. One source for the bio writer and the checker')
        check('writer prompt lists every checked term',
              all(term in bio_service.BIO_SYSTEM_PROMPT for term in bio_quality.HYPE_TERMS))
        check('writer prompt asks for a 2-3 sentence "who is this?" lead',
              'two or three sentences' in bio_service.BIO_SYSTEM_PROMPT and 'who is this?' in bio_service.BIO_SYSTEM_PROMPT)
        ed = client.get('/u/jeanmdelphonse/edit').get_data(as_text=True)
        check('editor carries the same term list for its live check',
              'var HYPE = {' in ed and '"world-class"' in ed and ed.count('class="hype-note"') == 3)

        print('\n6. Fact box edge cases')
        bare = bio_facts.fact_box(UserProfile.query.get(prof_id), {'full_name': 'X'}, None)
        check('empty context yields no invented rows except what the profile stores',
              [r['label'] for r in bare['rows']] in (['Location', 'Rate range'], ['Location']), bare['rows'])
        check('hidden availability shows nothing', bio_facts.availability([{'availability': 'hidden'}]) is None)
        check('no dates, no experience row', bio_facts.years_of_experience([{'start_date': ''}]) is None)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
