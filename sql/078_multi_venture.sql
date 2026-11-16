-- Founder Track: a founder can own more than one venture.
-- Run once on deploy, after 077. Replaces the one-venture-per-owner UNIQUE key
-- with a plain index. Each venture keeps its own documents, team, report,
-- startup page and one-time Founder Track payment. The app caps ventures per
-- owner (founder_ventures.MAX_VENTURES_PER_OWNER) and refuses a duplicate name
-- for the same owner.

ALTER TABLE founder_venture
    DROP INDEX uq_founder_venture_owner,
    ADD INDEX ix_founder_venture_owner (owner_user_id);
