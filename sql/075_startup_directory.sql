-- Founder Track: the investor directory at /startups.
-- Run once on deploy, after 074. Additive.
--
-- A published startup page is listed in the directory only when its founders
-- opt in from the startup page editor (the holder of the publishing decision
-- turns it on). Off by default, like bio pages' show_on_explore: listing a
-- raise widens its reach, which matters for 506(b) offerings.

ALTER TABLE startup_page
    ADD COLUMN show_in_directory TINYINT(1) NOT NULL DEFAULT 0
    COMMENT 'listed on /startups (founder opt-in)';

CREATE INDEX ix_startup_page_directory ON startup_page (status, show_in_directory);
