import sys
import os
import traceback
from datetime import datetime

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
HOME     = os.path.expanduser('~')

def _log(msg):
    ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}\n'
    for path in [os.path.join(APP_ROOT, 'error.log'),
                 os.path.join(HOME, 'simulacrum_error.log')]:
        try:
            with open(path, 'a') as f:
                f.write(line)
        except Exception:
            pass

_log(f'passenger_wsgi starting — APP_ROOT={APP_ROOT}  Python={sys.version.split()[0]}')

try:
    sys.path.insert(0, APP_ROOT)

    class _PrefixMiddleware:
        """Strip /simulacrum prefix so Flask sees clean paths."""
        def __init__(self, app, prefix=''):
            self.app = app
            self.prefix = prefix.rstrip('/')

        def __call__(self, environ, start_response):
            path = environ.get('PATH_INFO', '')
            if path.startswith(self.prefix):
                environ['SCRIPT_NAME'] = self.prefix
                environ['PATH_INFO'] = path[len(self.prefix):] or '/'

            # Catch any exception that escapes Flask and log it
            try:
                return self.app(environ, start_response)
            except Exception:
                err = traceback.format_exc()
                _log('REQUEST-LEVEL EXCEPTION:\n' + err)
                body = b'Internal Server Error'
                start_response('500 Internal Server Error', [
                    ('Content-Type', 'text/plain'),
                    ('Content-Length', str(len(body))),
                ])
                return [body]

    from app import create_app
    _log('create_app imported OK')

    _flask_app = create_app('production')
    _log('Flask app created OK')

    application = _PrefixMiddleware(_flask_app, prefix='/simulacrum')
    _log('Startup complete — ready to serve')

except Exception:
    err = traceback.format_exc()
    _log('STARTUP FAILED:\n' + err)

    def application(environ, start_response):
        body = err.encode()
        start_response('500 Internal Server Error', [
            ('Content-Type', 'text/plain'),
            ('Content-Length', str(len(body))),
        ])
        return [body]
