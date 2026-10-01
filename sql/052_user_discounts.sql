-- Per-user simulation discount grants (admin comps).
-- Run once on deploy.
--
-- The existing simulation_discounts table is a *global*, time-windowed sale:
-- everyone who buys inside the window gets the same percentage. This table is
-- the other axis — one named user, granted a discount nobody else gets, from
-- the admin console. The two are independent and never stack: pricing takes
-- whichever is the better deal for the buyer (see price_for_user() in
-- app/services/pricing_service.py).
--
-- A grant is a voucher, not a status. max_uses bounds it, uses_count records
-- consumption, and a 100%-off grant with unlimited uses has to be chosen
-- explicitly (max_uses = 0) rather than arriving by default — an accidental
-- unlimited comp is unbounded generation spend, so the default is one use.
--
-- All PKs are CHAR(9), from utils.id_gen.generate_id().

CREATE TABLE user_discounts (
    id                  CHAR(9)      PRIMARY KEY,
    user_id             CHAR(9)      NOT NULL,
    discount_percentage INT          NOT NULL                     COMMENT '1-100; 100 = fully comped',

    -- Consumption. max_uses = 0 means unlimited, which the admin UI makes an
    -- explicit choice. uses_count is bumped by a guarded UPDATE at redemption
    -- so two concurrent checkouts cannot spend the same last use.
    max_uses            INT          NOT NULL DEFAULT 1           COMMENT '0 = unlimited',
    uses_count          INT          NOT NULL DEFAULT 0,

    reason              VARCHAR(200) NULL                         COMMENT 'why it was granted - shown in the console and the audit log',
    expires_at          DATETIME     NULL                         COMMENT 'NULL = no expiry',

    granted_by          CHAR(9)      NULL                         COMMENT 'the admin who granted it',
    revoked_at          DATETIME     NULL,
    revoked_by          CHAR(9)      NULL,
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_ud_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

-- The hot read is "does this user have a live grant", on every priced request
-- for a signed-in user, so it is one indexed probe.
CREATE INDEX idx_ud_user_live ON user_discounts (user_id, revoked_at, expires_at);
CREATE INDEX idx_ud_created   ON user_discounts (created_at);


-- Which grant paid for which simulation. Separate from the grant so the
-- redemption history survives a revoke, and so the console can answer "what did
-- this comp actually cost us" without inferring it from the simulation table.
CREATE TABLE user_discount_redemptions (
    id             CHAR(9)  PRIMARY KEY,
    discount_id    CHAR(9)  NOT NULL,
    user_id        CHAR(9)  NOT NULL,
    simulation_id  CHAR(9)  NULL,
    -- What the buyer would have paid, and what they did. The difference is the
    -- cost of the comp, recorded at redemption because the base price moves.
    base_price_cents   INT  NOT NULL DEFAULT 0,
    charged_cents      INT  NOT NULL DEFAULT 0,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_udr_discount FOREIGN KEY (discount_id)
        REFERENCES user_discounts (id) ON DELETE CASCADE
);
CREATE INDEX idx_udr_user       ON user_discount_redemptions (user_id, created_at);
CREATE INDEX idx_udr_discount   ON user_discount_redemptions (discount_id);
CREATE INDEX idx_udr_simulation ON user_discount_redemptions (simulation_id);
