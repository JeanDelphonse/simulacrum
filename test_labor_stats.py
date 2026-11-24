"""Live Labor Department layoff figures on /transition-package (services/labor_stats.py).

    python test_labor_stats.py
"""
import json
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def rows(year, values, prelim_last=True):
    out = []
    for i, v in enumerate(values):
        fn = [{'code': 'P', 'text': 'preliminary'}] if prelim_last and i == len(values) - 1 else [{}]
        out.append({'year': str(year), 'period': 'M%02d' % (i + 1), 'value': str(v), 'footnotes': fn})
    return list(reversed(out))


class Resp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def main():
    import requests
    import app.services.labor_stats as ls
    T, I = ls.TOTAL, ls.INFO

    # summarize
    d = ls.summarize({T: rows(2026, [2167, 1469, 1548]) + rows(2025, [1900] * 12, False)
                      + [{'year': '2026', 'period': 'M13', 'value': '9999'}],
                      I: rows(2026, [71, 42]) + rows(2025, [40] * 12, False)})
    check('year-to-date from the latest year, months both series report',
          d and d['year'] == 2026 and d['total'] == (2167 + 1469) * 1000 and d['info'] == (71 + 42) * 1000, d)
    check('period label "Jan–Feb 2026"', d and d['period'] == 'Jan–Feb 2026', d and d['period'])
    check('M13 annual averages ignored', d and d['total'] < 9999000)
    check('preliminary flag from the latest month footnote', d and d['preliminary'] is True)
    d1 = ls.summarize({T: rows(2026, [2167]), I: rows(2026, [71])})
    check('single month label "Jan 2026"', d1 and d1['period'] == 'Jan 2026')
    check('no data -> None', ls.summarize({}) is None and ls.summarize({T: [], I: []}) is None)
    check('bad values skipped', ls.summarize({T: [{'year': '2026', 'period': 'M01', 'value': '-'}], I: []}) is None)

    # fetch
    calls = []
    payload = {'status': 'REQUEST_SUCCEEDED', 'Results': {'series': [
        {'seriesID': T, 'data': rows(2026, [2167, 1469, 1548, 1521, 1587, 1669, 1701, 1973, 1774])},
        {'seriesID': I, 'data': rows(2026, [71, 42, 60, 56, 43, 61, 34, 38, 50])}]}}
    state = {'fail': False}

    def post(url, json=None, timeout=None):
        calls.append((url, json, timeout))
        if state['fail']:
            raise requests.ConnectionError('down')
        return Resp(payload)
    requests.post = post
    os.environ['BLS_API_KEY'] = 'k123'
    d = ls.fetch()
    check('fetch: BLS v2 endpoint, both JOLTS series, short timeout, optional key',
          calls[0][0] == ls.API_URL and calls[0][1]['seriesid'] == [T, I] and calls[0][2] <= 5
          and calls[0][1]['registrationkey'] == 'k123')
    check('fetch: Jan–Sep 2026 sums', d['period'] == 'Jan–Sep 2026' and d['total'] == 15409000 and d['info'] == 455000, d)
    os.environ.pop('BLS_API_KEY')
    payload_bad = {'status': 'REQUEST_NOT_PROCESSED', 'message': ['limit']}
    requests.post = lambda *a, **k: Resp(payload_bad)
    check('fetch: refused request -> None', ls.fetch() is None)
    requests.post = post

    from app import create_app
    from app.extensions import db
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.create_all()
        from app.models.platform_settings import PlatformSetting
        client = app.test_client()

        calls.clear()
        ls._memo.clear()
        res = client.get('/transition-package')
        html = res.get_data(as_text=True)
        check('page 200 with live figures in the overlay', res.status_code == 200 and 'data-total="15409000"' in html
              and 'data-info="455000"' in html and 'workers laid off, Jan–Sep 2026' in html, res.status_code)
        check('source credited, preliminary noted', 'U.S. Bureau of Labor Statistics, JOLTS' in html
              and 'latest month preliminary' in html)
        check('screen-reader text carries the figures', '15,409,000 workers laid off' in html)
        check('no old hardcoded figures', '617,189' not in html and '171,501' not in html and 'Challenger' not in html)
        check('figures cached in platform_settings', json.loads(PlatformSetting.get(ls.CACHE_KEY))['total'] == 15409000)
        n = len(calls)
        ls._memo.clear()
        client.get('/transition-package')
        check('fresh cache: no second BLS call (even in a new process)', len(calls) == n)

        # stale cache + BLS down: keep the last good figures, retry later
        c = json.loads(PlatformSetting.get(ls.CACHE_KEY))
        c['fetched_at'] = (datetime.utcnow() - timedelta(hours=ls.CACHE_HOURS + 1)).isoformat()
        PlatformSetting.set(ls.CACHE_KEY, json.dumps(c))
        ls._memo.clear()
        state['fail'] = True
        html = client.get('/transition-package').get_data(as_text=True)
        check('BLS down: last good figures stay up', 'data-total="15409000"' in html and len(calls) == n + 1)
        client.get('/transition-package')
        check('…and no retry within the hour', len(calls) == n + 1)

        # never loaded: the page still works, no overlay
        PlatformSetting.query.filter_by(key=ls.CACHE_KEY).delete()
        db.session.commit()
        ls._memo.clear()
        res = client.get('/transition-package')
        html = res.get_data(as_text=True)
        check('no figures ever loaded: page 200, video kept, no overlay', res.status_code == 200
              and 'simisi_transition_full.mp4' in html and 'id="tpLs"' not in html)
        check('full video: fullscreen control off so the figures stay drawn', 'controlsList="nofullscreen"' in html)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
