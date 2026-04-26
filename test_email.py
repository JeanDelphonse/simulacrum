"""
Email smoke test — run on the production server:

  /home/dburriyy6pdz/virtualenv/public_html/simulacrum/3.11/bin/python \
    /home/dburriyy6pdz/public_html/simulacrum/test_email.py you@example.com

Prints the provider, attempts a send, and reports pass/fail + full traceback.
"""
import sys, os

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_ROOT)

to_addr = sys.argv[1] if len(sys.argv) > 1 else None
if not to_addr:
    print('Usage: python test_email.py <to-address>')
    sys.exit(1)

from app import create_app
app = create_app('production')

with app.app_context():
    provider = app.config.get('EMAIL_PROVIDER', 'smtp')
    sender   = app.config.get('MAIL_DEFAULT_SENDER', '(not set)')
    print(f'Provider : {provider}')
    print(f'Sender   : {sender}')
    if provider == 'smtp':
        print(f'SMTP     : {app.config.get("MAIL_SERVER")}:{app.config.get("MAIL_PORT")}')
        print(f'Username : {app.config.get("MAIL_USERNAME")}')
    else:
        key = app.config.get('SENDGRID_API_KEY', '')
        print(f'SG key   : {key[:8]}... ({len(key)} chars)' if key else 'SG key   : NOT SET')

    print(f'\nSending test email to {to_addr} ...')
    try:
        from app.services.email_service import _send
        _send(
            subject='Simulacrum email test',
            recipients=[to_addr],
            body='This is a test email from Simulacrum production.\n\nIf you received this, outgoing email is working.',
        )
        print('SUCCESS — email sent.')
    except Exception as e:
        import traceback
        print('FAILED:')
        traceback.print_exc()
        sys.exit(1)
