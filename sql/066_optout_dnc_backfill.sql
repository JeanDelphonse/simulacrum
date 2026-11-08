-- Opt-outs now set Do Not Contact on every contact with that address (unsubscribe
-- link, reply STOP, spam report). Before this, the unsubscribe link only added
-- the address to email_suppressions, so sends were already blocked but the
-- contact's Do Not Contact flag stayed off. Run once on deploy; safe to re-run.

UPDATE contacts c
  JOIN email_suppressions s ON s.email = LOWER(TRIM(c.email))
   SET c.do_not_contact = 1,
       c.updated_at = UTC_TIMESTAMP()
 WHERE s.reason IN ('unsubscribe', 'complaint')
   AND c.do_not_contact = 0;
