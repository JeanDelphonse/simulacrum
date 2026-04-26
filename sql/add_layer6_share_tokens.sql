-- Layer 6 share tokens — read-only diagram share links (30-day expiry)
-- Run against MySQL/MariaDB production database.

CREATE TABLE IF NOT EXISTS layer6_share_tokens (
    id            VARCHAR(9)   NOT NULL,
    simulation_id VARCHAR(9)   NOT NULL,
    cycle_id      VARCHAR(9)   NULL,
    token         VARCHAR(9)   NOT NULL,
    created_by    VARCHAR(9)   NOT NULL,
    expires_at    DATETIME     NOT NULL,
    created_at    DATETIME     NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_layer6_share_tokens_token (token),
    KEY ix_layer6_share_tokens_simulation_id (simulation_id),
    CONSTRAINT fk_l6st_sim   FOREIGN KEY (simulation_id) REFERENCES simulations(id)    ON DELETE CASCADE,
    CONSTRAINT fk_l6st_cycle FOREIGN KEY (cycle_id)      REFERENCES layer6_cycles(id)  ON DELETE CASCADE,
    CONSTRAINT fk_l6st_user  FOREIGN KEY (created_by)    REFERENCES users(id)          ON DELETE CASCADE
);

INSERT IGNORE INTO alembic_version (version_num) VALUES ('g7h8i9j0k1l2');
