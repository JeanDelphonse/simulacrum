import sys
sys.setrecursionlimit(10000)

import os
import traceback
from datetime import datetime

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser('~')


def _log(msg):
    ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    line = '[%s] %s\n' % (ts, msg)
    for path in [os.path.join(APP_ROOT, 'error.log'),
                 os.path.join(HOME, 'simulacrum_error.log')]:
        try:
            with open(path, 'a') as f:
                f.write(line)
        except Exception:
            pass


_log('passenger_wsgi starting - APP_ROOT=%s Python=%s' % (APP_ROOT, sys.version.split()[0]))

try:
    sys.path.insert(0, APP_ROOT)

    from app import create_app
    _log('create_app imported OK')

    _flask_app = create_app('production')
    _log('Flask app created OK')

    application = _flask_app
    _log('Startup complete - ready to serve')

except Exception:
    err = traceback.format_exc()
    _log('STARTUP FAILED:\n' + err)

    def application(environ, start_response):
        body = err.encode('utf-8', errors='replace')
        start_response('500 Internal Server Error', [
            ('Content-Type', 'text/plain'),
            ('Content-Length', str(len(body))),
        ])
        return [body]
