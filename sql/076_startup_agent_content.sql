-- Founder Track: agent analyses on the startup page.
-- Run once on deploy, after 075. Additive.
--
-- agent_content records which agent outputs the founders approved for the
-- page, and for whom: {agent_key: {"output_id", "version", "audience":
-- "public" | "investor", "by", "at"}}. The exact version a founder reviewed is
-- pinned; a newer run is not shown until they approve it. Layer 1 (Validate)
-- analyses may go on the public page; every layer may go in the investor view
-- (paying and invited investors). NULL means nothing is shown.

ALTER TABLE startup_page
    ADD COLUMN agent_content JSON NULL
    COMMENT 'founder-approved agent outputs shown on the page (sql/076)';
