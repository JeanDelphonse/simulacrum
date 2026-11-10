-- The SME Console guide belongs to one simulation: it opens from
-- /simulations/<id> (and its Growth Command Center Journey tab) as
-- /guide?sim=<id>. Sessions and goal cards record which simulation they're
-- about, and an approved goal only reaches that simulation's agents.
-- Run once on deploy. Additive.
--
-- Existing rows keep simulation_id NULL. A goal approved before this change
-- still applies to every simulation that has no goal of its own for that layer.

ALTER TABLE sme_sessions ADD COLUMN simulation_id VARCHAR(9) NULL;
CREATE INDEX ix_sme_sessions_simulation_id ON sme_sessions (simulation_id);

ALTER TABLE goal_cards ADD COLUMN simulation_id VARCHAR(9) NULL;
CREATE INDEX ix_goal_cards_simulation_id ON goal_cards (simulation_id);
