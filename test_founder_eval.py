"""Founder Track evaluation and readiness report (FOUNDER_PLAN.md WP2).

Faked Claude: citations not in the text are rejected (with one retry), a
missing artifact is a gap and never a zero, the per-(artifact, dimension)
cache, revised artifacts re-run only their own dimensions, rubric weights,
the preview cut-offs (rung / scores / top3) and paid, ratings, member-only
access and the disclaimer. In-memory SQLite; requests is blocked.

    python test_founder_eval.py
"""
import io
import json
import os
import re
import sys
import threading
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []

NEW_PY = ['app/services/founder_evaluation_service.py', 'app/blueprints/founder/report.py']

DECK = (
    "Acme Dental: we help dental clinics cut no-shows.\n"
    "Clinics lose $40,000 a year to missed appointments.\n"
    "Three clinic managers told us: “no-shows cost us   two hours a day”.\n"
    "Our team is two engineers and no one in sales.\n"
    "We will charge $199 per clinic per month."
)
SUMMARY = (
    "Executive summary. The US has 200,000 dental clinics, so the market is large.\n"
    "We are raising $500,000 to build the first version."
)
SUMMARY_V2 = SUMMARY + "\nWe sized the market bottom-up from state licensing lists."


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


class FakeClaude(object):
    """messages.create returns a forced evaluate_dimension tool call whose
    content depends on the document and the dimension asked about."""

    def __init__(self):
        self.calls = []            # (kind, dimension, is_retry)
        self.lock = threading.Lock()
        self.fail = False
        self.fail_keys = set()     # (kind, dimension) pairs that raise
        self.messages = self

    def create(self, **kw):
        if self.fail:
            raise RuntimeError('api down')
        msgs = kw['messages']
        first = msgs[0]['content']
        doc = first[0]['text']
        kind = re.search(r'kind="([a-z_]+)"', doc).group(1)
        dim = re.search(r'dimension "([a-z_]+)"', first[1]['text']).group(1)
        if (kind, dim) in self.fail_keys:
            raise RuntimeError('timeout')
        retry = len(msgs) > 1
        with self.lock:
            self.calls.append((kind, dim, retry))
        out = self.answer(kind, dim, retry, doc)
        block = SimpleNamespace(type='tool_use', id='tu_%d' % len(self.calls), name='evaluate_dimension', input=out)
        return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=1000, output_tokens=200))

    def answer(self, kind, dim, retry, doc):
        if kind == 'pitch_deck':
            if dim == 'problem':
                if not retry:
                    return {'covered': True, 'score': 80, 'findings': [
                        {'ftype': 'strength', 'severity': 'low', 'title': 'Pain is costed',
                         'detail': 'The deck puts a number on the pain.',
                         'citation_text': 'Clinics lose $40,000 a year to missed appointments', 'confidence': 0.8},
                        {'ftype': 'assumption', 'severity': 'high', 'title': 'Invented market loss',
                         'detail': 'x', 'citation_text': 'Clinics lose millions every single year',
                         'confidence': 0.9}]}
                return {'covered': True, 'score': 75, 'findings': [
                    {'ftype': 'assumption', 'severity': 'high', 'title': 'Pain figure has no source',
                     'detail': 'The $40,000 figure is not sourced.',
                     'citation_text': 'Clinics lose $40,000 a year', 'confidence': 0.7,
                     'recommended_agent': 'fnd_pain_evidence'}]}
            if dim == 'customer' and 'paid pilot' in doc:
                # the founder's own deck claims every rung, with real quotes
                q = 'Smile Co signed a paid pilot'
                return {'covered': True, 'score': 70, 'findings': [], 'ladder_evidence': [
                    {'rung': 2, 'citation_text': 'no-shows cost us two hours a day'},
                    {'rung': 3, 'citation_text': q}, {'rung': 4, 'citation_text': q},
                    {'rung': 5, 'citation_text': q}]}
            if dim == 'customer':
                return {'covered': True, 'score': 55, 'findings': [
                    {'ftype': 'strength', 'severity': 'medium', 'title': 'Customers quoted',
                     'detail': 'Clinic managers describe the pain.',
                     # curly quotes and extra spaces in the document; plain here: still found
                     'citation_text': '"no-shows cost us two hours a day"', 'confidence': 0.6}],
                    'ladder_evidence': [{'rung': 2, 'citation_text': 'no-shows cost us two hours a day'},
                                        {'rung': 3, 'citation_text': 'Signed proof of concept with Smile Co'}]}
            if dim == 'market':
                # bad citation both times: dropped, the score stands
                return {'covered': True, 'score': 50, 'findings': [
                    {'ftype': 'gap', 'severity': 'medium', 'title': 'No market size',
                     'detail': 'x', 'citation_text': 'The market is worth $9 billion', 'confidence': 0.5}]}
            if dim == 'team':
                return {'covered': True, 'score': 30, 'findings': [
                    {'ftype': 'gap', 'severity': 'high', 'title': 'No one sells',
                     'detail': 'Nobody on the team owns sales.',
                     'citation_text': 'no one in sales', 'confidence': 0.9, 'recommended_agent': 'fnd_hiring_plan'}]}
            if dim == 'business_model':
                return {'covered': True, 'score': 60, 'findings': [
                    {'ftype': 'contradiction', 'severity': 'medium', 'title': 'Price vs pain',
                     'detail': 'Price is small next to the stated loss.',
                     'citation_text': '$199 per clinic per month', 'confidence': 0.4,
                     'recommended_agent': 'not_an_agent'}]}
            return {'covered': dim not in ('operations', 'fundraising'), 'score': 65, 'findings': []}
        if kind == 'exec_summary':
            if dim == 'market':
                return {'covered': True, 'score': 40 if 'bottom-up' not in doc else 70, 'findings': [
                    {'ftype': 'assumption', 'severity': 'medium', 'title': 'Top-down sizing',
                     'detail': 'Clinic count is not a market size.',
                     'citation_text': 'so the market is large', 'confidence': 0.75}]}
            if dim == 'fundraising':
                return {'covered': True, 'score': 45, 'findings': [
                    {'ftype': 'gap', 'severity': 'low', 'title': 'Use of funds is thin',
                     'detail': 'One line on what the money buys.',
                     'citation_text': 'raising $500,000 to build the first version', 'confidence': 0.5}]}
            return {'covered': False, 'score': 0, 'findings': []}
        return {'covered': False, 'score': 0, 'findings': []}

    def primary(self):
        return [c for c in self.calls if not c[2]]


def main():
    import requests
    requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))

    print('\n1. New files are Python 3.9 safe')
    missing = [f for f in NEW_PY
               if 'from __future__ import annotations' not in io.open(os.path.join(ROOT, f), encoding='utf-8').read()]
    check('every new .py file has "from __future__ import annotations"', not missing, missing)

    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False)

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.audit_log import AuditLog
        from app.models.founder import (EvaluationFinding, EvaluationRun, FindingRating, FounderEvalCache,
                                        FounderVenture, VentureArtifact, VentureMember)
        from app.models.user import User
        from app.services import founder_config, founder_feature, founder_ventures, legal_text
        from app.services import founder_evaluation_service as fes
        now = datetime.utcnow()

        def user(email, admin=False):
            u = User(id=generate_id(), email=email, full_name=email.split('@')[0].title(), password_hash='x',
                     is_admin=admin, email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u

        founder, cofounder, other, admin = (user('founder@x.com'), user('co@x.com'), user('other@x.com'),
                                            user('admin@x.com', admin=True))
        F_ID, C_ID, O_ID, A_ID = founder.id, cofounder.id, other.id, admin.id
        v = founder_ventures.create_venture(founder, 'Acme Dental', 'Fewer no-shows', 'idea')
        V_ID = v.id
        db.session.add(VentureMember(venture_id=V_ID, user_id=C_ID, role='cofounder', status='active', joined_at=now))
        db.session.commit()

        def artifact(kind, text, version=1):
            VentureArtifact.query.filter_by(venture_id=V_ID, kind=kind, is_current=True).update({'is_current': False})
            a = VentureArtifact(venture_id=V_ID, kind=kind, version=version, is_current=True, filename=kind + '.pdf',
                                text_content=text, char_count=len(text), uploaded_by=F_ID)
            db.session.add(a)
            db.session.commit()
            return a

        fake = FakeClaude()
        fes.AI_CLIENT = fake

        client = app.test_client()
        for verb in ('get', 'post'):
            orig = getattr(client, verb)
            setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid):
            with client.session_transaction() as s:
                if uid:
                    s['_user_id'], s['_fresh'] = uid, True
                else:
                    s.pop('_user_id', None)

        def venture():
            return db.session.get(FounderVenture, V_ID)

        print('\n2. Track off: every report route is 404')
        login(F_ID)
        for path in ('/founder/v/%s/report' % V_ID, '/api/founder/v/%s/evaluation' % V_ID):
            check('GET %s is 404 while the track is off' % path.split(V_ID)[-1], client.get(path).status_code == 404)
        check('POST evaluate is 404 while off', client.post('/api/founder/v/%s/evaluate' % V_ID).status_code == 404)
        founder_feature.set_mode('founder_track', 'on', A_ID)

        print('\n3. Member-only access')
        login(O_ID)
        check('a non-member gets 403 on the report page', client.get('/founder/v/%s/report' % V_ID).status_code == 403)
        check('a non-member gets 403 on the evaluation JSON',
              client.get('/api/founder/v/%s/evaluation' % V_ID).status_code == 403)
        check('a non-member cannot start an evaluation',
              client.post('/api/founder/v/%s/evaluate' % V_ID).status_code == 403)
        login(None)
        check('anonymous gets 404', client.get('/founder/v/%s/report' % V_ID).status_code == 404)
        login(F_ID)
        check('an unknown venture is 404', client.get('/founder/v/NOPE12345/report').status_code == 404)
        r = client.post('/api/founder/v/%s/evaluate' % V_ID)
        check('evaluate with no documents is refused with a message', r.status_code == 400
              and 'document' in (r.get_json() or {}).get('message', ''), r.get_data(as_text=True)[:200])
        html = client.get('/founder/v/%s/report' % V_ID).get_data(as_text=True)
        check('the empty report page renders with the disclaimer',
              legal_text.get('founder_report_disclaimer')[:50] in html and 'No report yet' in html)

        print('\n4. First run: citations are checked against the text')
        deck = artifact('pitch_deck', DECK)
        summ = artifact('exec_summary', SUMMARY)
        run1 = fes.run(venture(), founder)
        check('the run finishes', run1.status == 'done', run1.status)
        check('one primary call per (artifact, dimension): 2 x 9 = 18', len(fake.primary()) == 18, len(fake.primary()))
        retries = [c for c in fake.calls if c[2]]
        check('rejected citations trigger one retry for that call only',
              sorted(retries) == [('pitch_deck', 'market', True), ('pitch_deck', 'problem', True)], retries)
        rows = EvaluationFinding.query.filter_by(evaluation_run_id=run1.id).all()
        texts = {a.id: fes.normalize(a.text_content) for a in (deck, summ)}
        cited = [f for f in rows if f.citation_text != fes.NOT_PROVIDED]
        check('every cited finding quotes its own artifact verbatim (whitespace normalised)',
              cited and all(fes.normalize(f.citation_text) in texts[f.artifact_id] for f in cited),
              [(f.title, f.citation_text) for f in cited if fes.normalize(f.citation_text) not in texts.get(f.artifact_id, '')])
        titles = {f.title for f in rows}
        check('a finding whose quote is not in the text is dropped',
              'Invented market loss' not in titles and 'No market size' not in titles)
        check('the retry adds the finding it re-quoted correctly',
              'Pain figure has no source' in titles and 'Pain is costed' in titles)
        check('typographic quotes and spacing in the document still match', 'Customers quoted' in titles)
        check('citation_ref names the artifact kind and version',
              all(f.citation_ref == 'pitch_deck@v1' for f in rows if f.artifact_id == deck.id))
        bad_agent = next(f for f in rows if f.title == 'Price vs pain')
        from app.services import founder_registry
        check('an unknown recommended_agent falls back to a catalog agent for the dimension',
              bad_agent.recommended_agent in founder_registry.DIMENSION_AGENTS['business_model'],
              bad_agent.recommended_agent)
        team = next(f for f in rows if f.title == 'No one sells')
        check('a valid recommended_agent is kept; findings carry their layer',
              team.recommended_agent == 'fnd_hiring_plan' and team.layer == 4 and team.dimension == 'team')
        check('strengths recommend no agent', all(f.recommended_agent is None for f in rows if f.ftype == 'strength'))
        check('confidence is a Beta prior: mean of (alpha, beta) equals the stated confidence',
              abs(team.alpha / (team.alpha + team.beta) - 0.9) < 1e-6 and abs(team.confidence - 0.9) < 1e-6
              and abs(team.alpha + team.beta - fes.PRIOR_STRENGTH) < 1e-6)

        print('\n5. Missing artifacts are gaps, never zero')
        gaps = [f for f in rows if f.citation_text == fes.NOT_PROVIDED]
        kinds_missing = {'startup_plan', 'ops_plan', 'financial_model', 'pitch_transcript'}
        check('each missing artifact kind is a gap citing "not provided"',
              all(any(f.citation_ref == '%s:not provided' % k and f.ftype == 'gap' for f in gaps) for k in kinds_missing),
              [f.citation_ref for f in gaps])
        check('provided kinds are not reported missing',
              not any(f.citation_ref.startswith(('pitch_deck:', 'exec_summary:')) for f in gaps))
        kind_agents = {f.citation_ref.split(':')[0]: f.recommended_agent for f in gaps
                       if not f.citation_ref.startswith('all:')}
        check('a missing document points at the agent that helps write it',
              kind_agents == {'startup_plan': 'fnd_mvp_scope', 'ops_plan': 'fnd_incorporation_checklist',
                              'financial_model': 'fnd_runway_budget', 'pitch_transcript': 'fnd_problem_statement'}
              and all(founder_registry.get(a) for a in kind_agents.values()), kind_agents)
        check('a dimension no document covers has no score (not zero)', 'operations' not in (run1.scores or {})
              and 0 not in (run1.scores or {}).values(), run1.scores)
        check('... and a gap finding saying so', any(f.dimension == 'operations' and f.title.endswith('not addressed')
                                                     for f in gaps))
        check('a covered dimension averages its artifacts: market (deck 50, summary 40) = 45',
              run1.scores.get('market') == 45, run1.scores)
        check('the evidence ladder estimate counts only quoted, contiguous rungs (2, not the unquoted 3)',
              run1.rung_at_run == 2 and venture().rung == 2, (run1.rung_at_run, venture().rung))
        check('the rung change is audit-logged',
              AuditLog.query.filter_by(action='founder_rung_changed', resource_id=V_ID).count() == 1)
        check('artifact_versions records the current versions',
              run1.artifact_versions == {'pitch_deck': 1, 'exec_summary': 1}, run1.artifact_versions)
        check('the cache holds one row per (artifact, dimension)', FounderEvalCache.query.count() == 18)

        print('\n6. Weights')
        w = fes.weights()
        expect = round(sum(run1.scores[d] * w[d] for d in run1.scores) / sum(w[d] for d in run1.scores), 1)
        check('equal default weights: overall is the mean of scored dimensions', run1.overall_score == expect,
              (run1.overall_score, expect))
        founder_config.set('fnd_rubric_weights', json.dumps({'team': 10}), A_ID)

        print('\n7. Cache: an unchanged artifact is not re-scored')
        before = len(fake.calls)
        run2 = fes.run(venture(), founder)
        check('a second run with unchanged documents makes no Claude call', len(fake.calls) == before,
              fake.calls[before:])
        w = fes.weights()
        expect = round(sum(run2.scores[d] * w[d] for d in run2.scores) / sum(w[d] for d in run2.scores), 1)
        check('admin weights are applied (team weighted 10x pulls the overall down)',
              run2.overall_score == expect and run2.overall_score < run1.overall_score,
              (run1.overall_score, run2.overall_score, expect))
        check('the same findings come back from the cache',
              {f.title for f in EvaluationFinding.query.filter_by(evaluation_run_id=run2.id)} == titles)
        check('the earlier run\'s findings are superseded',
              EvaluationFinding.query.filter_by(evaluation_run_id=run1.id, superseded_at=None).count() == 0
              and all(f.evaluation_run_id == run2.id for f in fes.findings(venture())))

        print('\n8. A revised artifact re-runs only its own dimensions')
        before = len(fake.primary())
        artifact('exec_summary', SUMMARY_V2, version=2)
        run3 = fes.run(venture(), founder)
        new = fake.primary()[before:]
        check('only the 9 dimensions of the new version are called', len(new) == 9
              and all(c[0] == 'exec_summary' for c in new), new)
        check('the revised score shows (market: deck 50, summary v2 70 = 60)', run3.scores.get('market') == 60,
              run3.scores)
        check('artifact_versions moves to v2', run3.artifact_versions.get('exec_summary') == 2)
        before = len(fake.primary())
        fes.run(venture(), founder, artifact_ids=[deck.id])
        check('artifact_ids forces a fresh call for that artifact only',
              len(fake.primary()) - before == 9 and all(c[0] == 'pitch_deck' for c in fake.primary()[before:]))

        print('\n9. Preview cut-offs')
        def view():
            return fes.report_view(venture(), founder)

        def blob(d):
            return json.dumps(d)

        some_quote = 'no one in sales'
        founder_config.set('fnd_preview_level', 'rung', A_ID)
        d = view()
        check('rung: shows the rung', d['rung']['current'] == 2 and d['locked'] and d['preview_level'] == 'rung')
        check('rung: no score bands, no counts, no findings',
              all('band' not in x and 'score' not in x for x in d['dimensions']) and 'counts' not in d
              and d['findings'] == [] and some_quote not in blob(d) and 'scores' not in (d['run'] or {}))
        check('rung: which dimensions are covered and how many findings exist are locked too',
              all('scored' not in x for x in d['dimensions']) and 'total_findings' not in d
              and 'hidden_count' not in d)
        html = client.get('/founder/v/%s/report' % V_ID).get_data(as_text=True)
        check('rung page renders without counts', 'Your full report is ready' in html
              and 'Unlock the full report' in html, html[:0])
        founder_config.set('fnd_preview_level', 'scores', A_ID)
        d = view()
        check('scores: a band per dimension, no numbers',
              all('band' in x and 'score' not in x for x in d['dimensions']) and 'overall_score' not in d)
        check('scores: counts by type and severity',
              d['counts']['by_type']['gap'] >= 1 and sum(d['counts']['by_type'].values()) == d['total_findings'])
        check('scores: no finding text or citations', d['findings'] == [] and some_quote not in blob(d)
              and 'No one sells' not in blob(d))
        check('an unscored dimension reads "Not scored", not zero',
              next(x for x in d['dimensions'] if x['key'] == 'operations')['band_label'] == 'Not scored')
        founder_config.set('fnd_preview_level', 'top3', A_ID)
        d = view()
        check('top3: exactly three findings in full', len(d['findings']) == 3 and d['hidden_count'] == d['total_findings'] - 3)
        check('top3: the most severe, never strengths',
              all(f['ftype'] != 'strength' for f in d['findings'])
              and [f['severity'] for f in d['findings']] == sorted([f['severity'] for f in d['findings']],
                                                                   key=lambda s: -fes.SEVERITY_RANK[s])
              and d['findings'][0]['severity'] == 'high')
        check('top3: still no numeric scores', all('score' not in x for x in d['dimensions']))
        for lvl in ('rung', 'scores', 'top3'):
            founder_config.set('fnd_preview_level', lvl, A_ID)
            resp = client.get('/api/founder/v/%s/evaluation' % V_ID)
            locked_json, lj = resp.get_data(as_text=True), resp.get_json()
            check('%s: the locked JSON has no overall score, run scores, rung estimate or cost anywhere' % lvl,
                  '"overall_score"' not in locked_json and '"scores":' not in locked_json
                  and '"cost_cents"' not in locked_json and '"rung_at_run"' not in locked_json
                  and set(lj['run']) <= {'id', 'status', 'started_at', 'finished_at', 'artifact_versions'},
                  sorted(lj['run']))
        founder_config.set('fnd_preview_level', 'top3', A_ID)
        login(F_ID)
        html = client.get('/founder/v/%s/report' % V_ID).get_data(as_text=True)
        hidden = [f for f in fes.findings(venture()) if f.id not in {x['id'] for x in d['findings']}]
        check('top3 page: hidden findings are not in the HTML',
              not any(f.title in html for f in hidden if f.citation_text != fes.NOT_PROVIDED))
        check('locked page: counts and an unlock link to checkout',
              '/founder/v/%s/checkout' % V_ID in html and 'Unlock the full report' in html)

        print('\n10. Paid shows everything')
        venture().paid_at = datetime.utcnow()
        db.session.commit()
        d = view()
        check('paid: every finding, numeric scores and the overall score',
              not d['locked'] and len(d['findings']) == d['total_findings'] and d['hidden_count'] == 0
              and all('score' in x for x in d['dimensions']) and d.get('overall_score') is not None)
        check('paid: the model cost is never sent to members', '"cost_cents"' not in blob(d))
        check('paid: citations present', some_quote in blob(d))
        check('findings grouped by layer', all(f['layer'] == L['layer'] for L in d['layers'] for f in L['findings']))
        html = client.get('/founder/v/%s/report' % V_ID).get_data(as_text=True)
        check('paid page: cited passage, agent link, type, severity and confidence shown',
              some_quote in html and '/founder/v/%s/team#fnd_hiring_plan' % V_ID in html and 'Hiring Plan' in html
              and 'High severity' in html and '90% confidence' in html)
        check('the page carries the disclaimer prominently',
              legal_text.get('founder_report_disclaimer')[:50] in html and 'class="fr-disclaimer"' in html)
        check('no hardcoded disclaimer wording beside the legal text', 'Not a funding prediction' not in html)
        check('evidence ladder: 5 rungs, current marked', html.count('class="fr-rung') == 5
              and 'aria-current="step"' in html and 'What moves you up' in html)
        check('no unlock prompt once paid', 'Unlock the full report' not in html)
        login(C_ID)
        check('a co-founder sees the same report', client.get('/founder/v/%s/report' % V_ID).status_code == 200)

        print('\n11. Ratings')
        fid = next(f.id for f in fes.findings(venture()) if f.title == 'No one sells')
        login(F_ID)
        r = client.post('/api/founder/findings/%s/rating' % fid, json={'fair': False, 'comment': 'We hired one.'})
        check('rating saved', r.status_code == 200 and r.get_json()['fair'] is False, r.get_data(as_text=True)[:200])
        r = client.post('/api/founder/findings/%s/rating' % fid, json={'fair': True})
        rows_r = FindingRating.query.filter_by(finding_id=fid, user_id=F_ID).all()
        check('re-rating updates the one row', len(rows_r) == 1 and rows_r[0].fair is True)
        mine = next(f for f in view()['findings'] if f['id'] == fid)
        other_f = next(f for f in view()['findings'] if f['id'] != fid)
        check('the report returns my rating per finding', mine.get('my_rating') is True
              and other_f.get('my_rating') is None, (mine.get('my_rating'), other_f.get('my_rating')))
        html = client.get('/founder/v/%s/report' % V_ID).get_data(as_text=True)
        check('the page shows the rating already given (aria-pressed)',
              ('data-rate="%s" data-fair="1" aria-pressed="true"' % fid) in html
              and ('data-rate="%s" data-fair="0" aria-pressed="false"' % fid) in html)
        login(C_ID)
        html = client.get('/founder/v/%s/report' % V_ID).get_data(as_text=True)
        check("a co-founder doesn't see the founder's rating as theirs",
              ('data-rate="%s" data-fair="1" aria-pressed="false"' % fid) in html)
        login(F_ID)
        login(O_ID)
        check('a non-member cannot rate', client.post('/api/founder/findings/%s/rating' % fid,
                                                     json={'fair': True}).status_code == 403)
        login(F_ID)
        check('rating needs a verdict', client.post('/api/founder/findings/%s/rating' % fid, json={}).status_code == 400)
        venture().paid_at = None
        db.session.commit()
        founder_config.set('fnd_preview_level', 'scores', A_ID)
        check('a locked finding cannot be rated before paying',
              client.post('/api/founder/findings/%s/rating' % fid, json={'fair': True}).status_code == 403)

        print('\n12. API: start, status, failure')
        fes._spawn = lambda run_id, uid, ids: fes._execute(run_id, uid, ids)     # run inline
        r = client.post('/api/founder/v/%s/evaluate' % V_ID)
        body = r.get_json() or {}
        check('evaluate returns run_id, status and message', r.status_code == 202 and body.get('run_id')
              and body.get('status') and body.get('message'), body)
        d = client.get('/api/founder/v/%s/evaluation' % V_ID).get_json()
        check('evaluation JSON reports status and the preview', d['status'] == 'done' and d['locked']
              and 'findings' in d and d['findings'] == [])
        stuck = EvaluationRun(venture_id=V_ID, status='running', started_at=datetime.utcnow())
        db.session.add(stuck)
        db.session.commit()
        r = client.post('/api/founder/v/%s/evaluate' % V_ID)
        check('a run in flight is reused, not doubled', (r.get_json() or {}).get('run_id') == stuck.id)
        stuck.status = 'done'
        db.session.commit()
        fake.fail = True
        artifact('pitch_deck', DECK + '\nNew traction slide.', version=2)
        bad = fes.run(venture(), founder)
        check('when every call fails the run is marked failed', bad.status == 'failed', bad.status)
        check('a failed run leaves the previous findings current',
              all(f.evaluation_run_id != bad.id for f in fes.findings(venture())) and fes.findings(venture()))
        fake.fail = False
        check('rung is never lowered by an evaluation', venture().rung == 2)

        print('\n12b. Partial failures, one run at a time, citation window')
        good_before = {f.id for f in fes.findings(venture())}
        fake.fail_keys = {('pitch_deck', 'team')}
        part = fes.run(venture(), founder)
        check('one failed call fails the run (no report from partial results)', part.status == 'failed', part.status)
        check('... and the previous findings stay current, with no false "not addressed" gap',
              {f.id for f in fes.findings(venture())} == good_before
              and EvaluationFinding.query.filter_by(evaluation_run_id=part.id).count() == 0)
        deck2 = VentureArtifact.query.filter_by(venture_id=V_ID, kind='pitch_deck', is_current=True).first()
        check('the answers that did come back are cached',
              FounderEvalCache.query.filter_by(artifact_id=deck2.id).count() == 8)
        fake.fail_keys = set()
        before = len(fake.primary())
        ok = fes.run(venture(), founder)
        check('the re-run calls only the failed pair and finishes', ok.status == 'done'
              and fake.primary()[before:] == [('pitch_deck', 'team', False)], fake.primary()[before:])

        older = EvaluationRun(venture_id=V_ID, status='running', started_at=datetime.utcnow())
        db.session.add(older)
        db.session.commit()
        real_running = fes.running_run
        fes.running_run = lambda v: None          # simulate the race: both requests saw no running run
        try:
            n_runs = EvaluationRun.query.filter_by(venture_id=V_ID).count()
            got, created = fes._claim_run(venture())
        finally:
            fes.running_run = real_running
        check('a racing second run defers to the older running run and leaves no row behind',
              got.id == older.id and not created
              and EvaluationRun.query.filter_by(venture_id=V_ID).count() == n_runs, (got.id, created))
        older.status = 'done'
        db.session.commit()

        class Tail(object):
            def __init__(self):
                self.messages = self

            def create(self, **kw):
                inp = {'covered': True, 'score': 50, 'findings': [
                    {'ftype': 'gap', 'severity': 'low', 'title': 'Seen', 'detail': 'x',
                     'citation_text': 'HEAD MARKER sentence', 'confidence': 0.5},
                    {'ftype': 'gap', 'severity': 'low', 'title': 'Unseen', 'detail': 'x',
                     'citation_text': 'TAIL MARKER sentence', 'confidence': 0.5}]}
                return SimpleNamespace(content=[SimpleNamespace(type='tool_use', id='t', input=inp)], usage=None)
        long_doc = 'HEAD MARKER sentence. ' + ('filler ' * (fes.MAX_DOC_CHARS // 7 + 10)) + 'TAIL MARKER sentence.'
        res = fes._evaluate_one(Tail(), 'm', 's', fes._tool(), 'pitch_deck', long_doc, 'problem')
        check('a quote from past the text the model was shown is rejected',
              [f['title'] for f in res['payload']['findings']] == ['Seen'], res['payload']['findings'])
        check('the prompt and the validator agree on quote length',
              ('%d to %d characters' % (fes.MIN_CITATION_CHARS, fes.MAX_CITATION_CHARS)) in fes._system_prompt()
              and not fes.citation_found('a' * (fes.MAX_CITATION_CHARS + 1), 'a' * 1000))

        print('\n12c. Documents alone cannot climb the ladder')
        artifact('pitch_deck', DECK + '\nSmile Co signed a paid pilot at $2,000 a month.', version=3)
        claim = fes.run(venture(), founder)
        check('the document estimate is recorded on the run', claim.status == 'done' and claim.rung_at_run == 5,
              (claim.status, claim.rung_at_run))
        check('... but the venture rung stays at the evaluation cap, below the intro gate',
              venture().rung == fes.eval_rung_cap() == 2
              and venture().rung < founder_config.get('fnd_gate_rung_intros'), venture().rung)
        d = view()
        check('the report says "your documents suggest" instead of recording progress',
              d['rung']['current'] == 2 and d['rung']['documents_suggest'] == 5)
        html = client.get('/founder/v/%s/report' % V_ID).get_data(as_text=True)
        check('the page shows the suggestion separately from the rung', 'Your documents suggest rung 5' in html
              and 'Evidence ladder: rung 2 of 5' in html)
        founder_config.set('fnd_gate_rung_intros', '2', A_ID)
        check('the cap stays below a lowered intro gate', fes.eval_rung_cap() == 1)
        founder_config.set('fnd_gate_rung_intros', '4', A_ID)

        print('\n13. Observability and compliance')
        from app.models.observability import AgentRun
        from app.services import observability_service as obs
        if obs.layer_code('F1') == 'F1':
            check('runs are recorded under F1 (WP7 present)',
                  AgentRun.query.filter_by(agent_key='fnd_evaluation').count() >= 1)
        else:
            check('no run lands in the wealth L6 rollups before WP7 adds F codes',
                  AgentRun.query.filter_by(agent_key='fnd_evaluation').count() == 0)
        real_layer_code = obs.layer_code
        obs.layer_code = lambda layer: 'F1' if layer == 'F1' else real_layer_code(layer)   # as WP7 will
        try:
            from app.models.founder import FounderRunMeta
            cur_deck = VentureArtifact.query.filter_by(venture_id=V_ID, kind='pitch_deck', is_current=True).first()
            traced = fes.run(venture(), founder, artifact_ids=[cur_deck.id])
            meta = FounderRunMeta.query.filter_by(venture_id=V_ID).all()
            hdr = AgentRun.query.filter_by(agent_key='fnd_evaluation').first()
            if obs.setting('obs_capture_enabled'):
                check('with F codes, the run is traced: agent_runs F1 + founder_run_meta + obs_run_id',
                      traced.status == 'done' and hdr is not None and hdr.layer == 'F1'
                      and traced.obs_run_id == hdr.id and len(meta) == 1 and meta[0].obs_run_id == hdr.id
                      and (hdr.prompt_tokens or 0) > 0, (traced.status, hdr and hdr.layer, len(meta)))
            else:
                check('with capture off, no meta row is written', traced.status == 'done' and not meta)
        finally:
            obs.layer_code = real_layer_code
        sys.path.insert(0, ROOT)
        import test_compliance_phrases as tcp
        src = io.open(os.path.join(ROOT, 'app/templates/founder/report.html'), encoding='utf-8').read()
        hits = tcp.scan(tcp.visible(src)) + tcp.scan(tcp.py_strings(
            os.path.join(ROOT, 'app/services/founder_evaluation_service.py')))
        check('report copy and prompts carry no banned phrases', not hits, hits)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
