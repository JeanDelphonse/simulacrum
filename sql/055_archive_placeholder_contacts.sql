-- Archive placeholder contacts that agents saved to the CRM.
-- Run once on deploy, after reviewing the preview (step 2).
--
-- When Cold Email had no real prospects to work from, the model filled its
-- prospect list with template rows such as
--     [First Name 1]  [Last Name 1]  [Enterprise Company 1]
-- and those were saved as CRM contacts. The code now rejects them
-- (contact_lookup.is_placeholder_contact); this script cleans up the ones
-- already stored. The rules mirror that function.
--
-- Scope is deliberately narrow:
--   * only agent-created contacts (source 'agent_generated' / 'agent_action') —
--     anything a person typed in is never touched;
--   * archived, not deleted (contacts are never deleted — see
--     cycle_admin_service), and marked do_not_contact;
--   * their still-scheduled action steps are cancelled.
-- Re-running it is harmless.

-- 1. Collect the candidates once.
DROP TEMPORARY TABLE IF EXISTS placeholder_contacts;
CREATE TEMPORARY TABLE placeholder_contacts AS
SELECT id FROM contacts
WHERE source IN ('agent_generated', 'agent_action')
  AND is_archived = 0
  AND (
        -- template markers: [ ] { } < >
        CONCAT_WS(' ', first_name, last_name, company_name, email) REGEXP '[\\[\\]{}<>]'
        -- name-field labels and filler
     OR first_name REGEXP '^(first|last|full) ?name$'
     OR last_name  REGEXP '^(first|last|full) ?name$'
     OR first_name REGEXP '^(tbd|tba|n/?a|xxx+|placeholder)$'
     OR last_name  REGEXP '^(tbd|tba|n/?a|xxx+|placeholder)$'
        -- numbered generic words: "Prospect 3", "Contact #2"
     OR first_name REGEXP '^(first|last|full|contact|prospect|person|client|lead|buyer|decision.?maker|hiring.?manager|recipient|name|sample|example|test|placeholder)( ?name)? ?#? ?[0-9]+$'
     OR last_name  REGEXP '^(first|last|full|contact|prospect|person|client|lead|buyer|decision.?maker|hiring.?manager|recipient|name|sample|example|test|placeholder)( ?name)? ?#? ?[0-9]+$'
        -- both names generic: "Contact Name", "Hiring Manager"
     OR (first_name REGEXP '^(first|last|full|contact|prospect|person|client|lead|buyer|decision.?maker|hiring.?manager|recipient|name|sample|example|test|placeholder)( ?name)?$'
         AND (last_name IS NULL OR last_name = ''
              OR last_name REGEXP '^(first|last|full|contact|prospect|person|client|lead|buyer|decision.?maker|hiring.?manager|recipient|name|sample|example|test|placeholder)( ?name)?$'))
     OR LOWER(CONCAT_WS(' ', first_name, last_name)) IN ('john doe', 'jane doe', 'john smith', 'jane smith', 'lorem ipsum')
        -- template company names: "Enterprise Company 1", "Company 3"
     OR company_name REGEXP '^((enterprise|target|sample|example|your|placeholder) )?(company|organi[sz]ation|firm|business|employer|client|corp|corporation|inc)( ?name)? ?#? ?[0-9]*$'
        -- template email addresses
     OR email REGEXP '@(example|test|domain|company|companyname|yourcompany|yourdomain|enterprisecompany[0-9]*|placeholder)\\.(com|org|net|io)$'
     OR email REGEXP '^(first|firstname|first\\.last|firstname\\.lastname|first\\.lastname|flast)@'
  );

-- 2. PREVIEW — review these rows before running step 3.
SELECT c.id, c.first_name, c.last_name, c.company_name, c.email, c.source, c.created_at
FROM contacts c JOIN placeholder_contacts p ON p.id = c.id
ORDER BY c.created_at DESC;

-- 3. Archive them and stop anything still scheduled for them.
UPDATE contacts
SET is_archived = 1, do_not_contact = 1
WHERE id IN (SELECT id FROM placeholder_contacts);

UPDATE action_steps s
JOIN placeholder_contacts p
  ON s.condition_ref = p.id
  OR s.payload LIKE CONCAT('%"contact_id": "', p.id, '"%')
SET s.status = 'cancelled'
WHERE s.status = 'scheduled';

DROP TEMPORARY TABLE IF EXISTS placeholder_contacts;
