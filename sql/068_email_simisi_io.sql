-- SimiSI's own address becomes simi@simisi.io (was simi@simulacrumai.io).
-- The code defaults changed with this release; this updates copies of the old
-- address stored in the database. Run once on deploy; safe to re-run.
--
-- Not covered here, set by hand:
--   * cPanel environment variables MAIL_DEFAULT_SENDER and FOUNDER_EMAIL, if set,
--     override the code defaults (Setup Python App -> Environment variables).
--   * SendGrid must have simisi.io authenticated (or simi@simisi.io verified as a
--     single sender) before anything can be sent from the new address.
--   * The simi@simisi.io mailbox must exist to receive replies and requests.

-- Admin settings (e.g. enterprise_inquiry_notify_email, outreach copy saved there).
UPDATE platform_settings
   SET value = REPLACE(value, 'simi@simulacrumai.io', 'simi@simisi.io')
 WHERE value LIKE '%simi@simulacrumai.io%';

-- Admin-edited outreach templates (drips, broadcasts, the contact invite).
UPDATE outreach_templates
   SET subject = REPLACE(subject, 'simi@simulacrumai.io', 'simi@simisi.io'),
       preview_text = REPLACE(preview_text, 'simi@simulacrumai.io', 'simi@simisi.io'),
       body = REPLACE(body, 'simi@simulacrumai.io', 'simi@simisi.io')
 WHERE subject LIKE '%simi@simulacrumai.io%'
    OR preview_text LIKE '%simi@simulacrumai.io%'
    OR body LIKE '%simi@simulacrumai.io%';

-- Admin-edited legal text (footer, disclosures). Editing resets counsel approval
-- in the app; here the address is the only change, so the status is left as is.
UPDATE legal_texts
   SET body = REPLACE(body, 'simi@simulacrumai.io', 'simi@simisi.io')
 WHERE body LIKE '%simi@simulacrumai.io%';
