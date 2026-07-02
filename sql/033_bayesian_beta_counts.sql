-- 033: Beta-Binomial pseudo-counts for bayesian_posteriors.
-- update_posterior() now tracks alpha/beta counts; value = alpha / (alpha + beta).
-- Backfill treats each existing value as a prior of strength 4 (Beta(2,2) scale)
-- so current beliefs are preserved as a weak prior.

ALTER TABLE bayesian_posteriors ADD COLUMN alpha_count DECIMAL(12,4) NULL;
ALTER TABLE bayesian_posteriors ADD COLUMN beta_count DECIMAL(12,4) NULL;

UPDATE bayesian_posteriors
SET alpha_count = value * 4,
    beta_count  = (1 - value) * 4
WHERE alpha_count IS NULL;
