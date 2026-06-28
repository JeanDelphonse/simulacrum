"""
Outreach email pipeline smoke test — run on the production server:

  /home/dburriyy6pdz/virtualenv/public_html/simulacrum/3.11/bin/python \
    /home/dburriyy6pdz/public_html/simulacrum/test_outreach_email.py you@example.com

Tests the full outreach path: SendGrid API key, sender domain, tracking
settings, and delivery — the same code path used by consulting_outreach.
"""
import sys, os

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_ROOT)

to_addr = sys.argv[1] if len(sys.argv) > 1 else None
if not to_addr:
    print('Usage: python test_outreach_email.py <to-address>')
    sys.exit(1)

from app import create_app
app = create_app('production')

with app.app_context():
    api_key = app.config.get('SENDGRID_API_KEY', '')
    sender  = app.config.get('MAIL_DEFAULT_SENDER', '(not set)')
    name    = app.config.get('MAIL_DEFAULT_SENDER_NAME', 'SimulacrumAI.io')
    print(f'Provider : sendgrid (outreach path)')
    print(f'SG key   : {api_key[:8]}...({len(api_key)} chars)' if api_key else 'SG key   : NOT SET')
    print(f'Sender   : {name} <{sender}>')
    print(f'To       : {to_addr}')

    if not api_key:
        print('\nFAILED: SENDGRID_API_KEY is not configured.')
        sys.exit(1)

    print('\nSending via outreach pipeline ...')
    try:
        import sendgrid as sg_module
        from sendgrid.helpers.mail import (
            Mail, TrackingSettings, OpenTracking, ClickTracking,
        )

        html_body = """
        <p>This is a <strong>production outreach email test</strong> from SimulacrumAI.io.</p>
        <p>If you received this, the outreach email pipeline (SendGrid + tracking) is working correctly.</p>
        <p style="color:#6b7280;font-size:12px;">Sent via test_outreach_email.py</p>
        """

        message = Mail(
            from_email=(sender, name),
            to_emails=to_addr,
            subject='[Simulacrum Test] Outreach email pipeline check',
            html_content=html_body,
        )
        message.tracking_settings = TrackingSettings(
            open_tracking=OpenTracking(enable=True),
            click_tracking=ClickTracking(enable=True, enable_text=True),
        )
        message.custom_arg = [
            ('simulation_id', 'test'),
            ('contact_id',    'test'),
            ('step_id',       'smoke_test'),
        ]

        client = sg_module.SendGridAPIClient(api_key)
        response = client.send(message)
        msg_id = response.headers.get('X-Message-Id', '(none)')
        print(f'SUCCESS — status {response.status_code}, message_id: {msg_id}')
    except Exception:
        import traceback
        print('FAILED:')
        traceback.print_exc()
        sys.exit(1)
