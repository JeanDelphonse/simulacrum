"""The startup one-pager PDF (FOUNDER_PLAN.md WP8, PRD "One-pager").

    render_pdf(page, investor: bool, access=None) -> bytes

Dated, built with fitz.Story like artifact_actions.export_pdf. Same claims
rules as the page: founder-stated items carry the label, traction is always
founder-stated, nothing projects what investors may earn (page copy is refused at
save time by startup_page_service). The public version leaves out the ask;
the investor version includes it and is served only to investors with access.
"""
from __future__ import annotations

import html as _html
import io
from datetime import datetime

_CSS = """
* { font-family: sans-serif; }
body { font-size: 9.5pt; line-height: 1.4; color: #1f2937; }
h1 { font-size: 20pt; margin: 0 0 2pt 0; color: #0f172a; }
h2 { font-size: 11.5pt; margin: 9pt 0 2pt 0; color: #0f172a; }
p { margin: 0 0 4pt 0; }
p.lede { font-size: 11pt; color: #334155; margin: 0 0 6pt 0; }
p.memo { font-size: 8pt; color: #64748b; margin: 0 0 8pt 0; }
p.note { font-size: 8pt; color: #475569; margin: 8pt 0 0 0; }
span.fs { font-size: 7.5pt; color: #6b4a0c; }
table { border-collapse: collapse; margin: 3pt 0; width: 100%; }
th, td { border: 1px solid #cbd5e1; padding: 3pt 5pt; font-size: 9pt; vertical-align: top; text-align: left; }
th { background-color: #f1f5f9; }
blockquote { color: #475569; margin: 3pt 0 3pt 10pt; }
"""


def _e(text) -> str:
    return _html.escape(str(text or ''))


def _fs(on: bool) -> str:
    return ' <span class="fs">[founder-stated]</span>' if on else ''


def _body(ctx, field, empty='Not provided yet.') -> str:
    s = ctx['sections'][field]
    return '<p>%s%s</p>' % (_e(s['text']) if s['text'] else '<i>%s</i>' % _e(empty),
                            _fs(s['founder_stated'] and s['text']))


def build_html(page, investor: bool, access=None) -> str:
    """The one-pager as HTML (also used by the tests)."""
    from app.services import legal_text
    from app.services import startup_page_service as sps
    if investor:
        from app.services import startup_access_service as sas
        ctx = sas.investor_context(page, access) if access is not None else sps.investor_context(page, True)
        ask = ctx.get('ask') if ctx.get('ask') else None
    else:
        ctx = sps.public_context(page)
        ask = None
    today = datetime.utcnow()
    out = ['<h1>%s</h1>' % _e(ctx['name'])]
    if ctx['one_liner']:
        out.append('<p class="lede">%s%s</p>' % (_e(ctx['one_liner']), _fs(ctx['one_liner_founder_stated'])))
    memo = ['%s one-pager' % ('Investor' if investor and ask else 'Company'),
            'Prepared %s' % today.strftime('%d %b %Y'),
            'Page as of %s' % ctx['as_of'].strftime('%d %b %Y')]
    if ctx['stage_label']:
        memo.append('Stage: %s' % ctx['stage_label'])
    memo.append('Evidence rung %d of 5' % ctx['rung'])
    out.append('<p class="memo">%s</p>' % ' · '.join(_e(m) for m in memo))

    out.append('<h2>Problem</h2>' + _body(ctx, 'problem'))
    out.append('<h2>Customer</h2>' + _body(ctx, 'customer'))
    out.append('<h2>Solution</h2>' + _body(ctx, 'solution'))

    cur = next((r for r in ctx['ladder'] if r['current']), None)
    proof = ['<h2>Proof</h2>', '<p>Evidence ladder: rung %d of 5%s.</p>'
             % (ctx['rung'], (', %s' % _e(cur['title'].lower())) if cur else '')]
    if ctx['proof'].get('benefits'):
        proof.append('<p><b>Benefits case (measured):</b> %s</p>' % _e(ctx['proof']['benefits']))
    for q in ctx['proof'].get('quotes') or []:
        proof.append('<blockquote>"%s" (a customer, quoted with permission)</blockquote>' % _e(q))
    out.append(''.join(proof))

    if ctx['metrics']:
        rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td></tr>'
                       % (_e(m.get('label')), _e(m.get('value')),
                          _e(' · '.join(x for x in (('as of ' + m['as_of']) if m.get('as_of') else '',
                                                    m.get('note') if investor else '') if x)))
                       for m in ctx['metrics'])
        out.append('<h2>Traction <span class="fs">[founder-stated]</span></h2>'
                   '<table><tr><th>Metric</th><th>Value</th><th>Detail</th></tr>%s</table>' % rows)

    out.append('<h2>Market</h2>' + _body(ctx, 'market'))
    if ctx['sections']['market_method']['text']:
        out.append('<p><b>Sizing method:</b> %s%s</p>' % (_e(ctx['sections']['market_method']['text']),
                                                          _fs(ctx['sections']['market_method']['founder_stated'])))
    out.append('<h2>Business model</h2>' + _body(ctx, 'business_model'))
    if ctx['team']:
        out.append('<h2>Team</h2><p>%s</p>' % '; '.join('%s, %s' % (_e(t['name']), _e(t['title']))
                                                       for t in ctx['team']))
    if ask:
        amount = ask.get('amount')
        out.append('<h2>The ask <span class="fs">[founder-stated]</span></h2><table>'
                   '<tr><th>Round</th><td>%s</td></tr><tr><th>Amount</th><td>%s</td></tr>'
                   '<tr><th>Use of funds</th><td>%s</td></tr></table>'
                   % (_e(ask.get('round') or 'Not stated'),
                      _e('US$ {:,}'.format(int(amount)) if amount else 'Not stated'),
                      _e(ask.get('use_of_funds') or 'Not stated')))
    out.append('<p class="note">%s</p>' % _e(legal_text.get('startup_claims_note')))
    if investor and ask:
        from app.services import startup_access_service as sas
        out.append('<p class="note">%s</p>' % _e(legal_text.get('investor_access_terms',
                                                               days=sas.refund_window_days())))
    out.append('<p class="note">Startup page: /startup/%s · Made with SimiSI</p>' % _e(ctx['slug']))
    return ''.join(out)


def render_pdf(page, investor: bool, access=None) -> bytes:
    """Letter-size PDF. investor=True includes the ask (the caller checks access)."""
    import fitz
    story = fitz.Story(html=build_html(page, investor, access), user_css=_CSS)
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    rect = fitz.paper_rect('letter')
    where = rect + (48, 44, -48, -44)
    more = True
    while more:
        dev = writer.begin_page(rect)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()
