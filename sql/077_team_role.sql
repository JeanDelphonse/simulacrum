-- Founder Track: each member's team role (Founder, Co-founder, Advisor, Team member).
-- Run once on deploy, after 076. Additive.
--
-- venture_member.role stays the account permission ('owner' set up the venture
-- and manages invites; 'cofounder' joined it). team_role is what the person is
-- on the team, so the account owner need not be a founder. NULL means the
-- default: Founder for the owner, Co-founder for everyone else. Only founders
-- and co-founders take the alignment check.

ALTER TABLE venture_member
    ADD COLUMN team_role VARCHAR(12) NULL
    COMMENT 'founder | cofounder | advisor | team_member; NULL = default from role (sql/077)';
