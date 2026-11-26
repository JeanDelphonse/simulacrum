-- Member pronouns (services/pronouns.py). Run once on deploy, after 080.
-- 'he' | 'she' | 'they', or NULL = not stated: the AI then uses the member's
-- name or they/them and never infers gender from the name.
ALTER TABLE user_profiles ADD COLUMN pronouns VARCHAR(10) NULL;
