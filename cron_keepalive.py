"""
cron_keepalive.py — keep Passenger warm by hitting https://simisi.io/ping every 5 min.

Without a keepalive, GoDaddy Passenger kills the Python process after ~5 min
of inactivity. The next user request then waits 10-20 s for cold startup.

It matters for more than cold starts: agents run in background threads, and a
process killed for inactivity takes any agent run with it (the UI later marks
it failed). A Cold Email run with research takes several minutes.

cPanel cron command (run every 5 minutes) — layout since the SimiSI cutover
(app root ~/simisi, served at simisi.io):
  */5 * * * * curl -s --max-time 10 https://simisi.io/ping > /dev/null 2>&1

Pre-cutover entries pointing at public_html/simulacrum or /simulacrum/ping no
longer keep the live app warm.
"""
import urllib.request
import urllib.error
import sys
import os
import logging
from datetime import datetime

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(
    filename=os.path.join(APP_ROOT, 'cron_keepalive.log'),
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(message)s',
)
log = logging.getLogger(__name__)

# Set this to your production domain + path
PING_URL = os.environ.get('SIMULACRUM_PING_URL', 'https://simisi.io/ping')

try:
    req = urllib.request.Request(PING_URL, headers={'User-Agent': 'SimulacrumKeepalive/1.0'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        status = resp.getcode()
        log.info('Ping OK — %s → HTTP %d', PING_URL, status)
except urllib.error.URLError as e:
    log.warning('Ping failed — %s: %s', PING_URL, e)
    sys.exit(1)
except Exception as e:
    log.error('Ping error — %s: %s', PING_URL, e)
    sys.exit(1)
