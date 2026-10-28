-- Per-agent recipient ledger: each person is targeted once per agent per
-- simulation, across every cycle. Run once on deploy.
--
-- A recipient is recorded when an agent's artifact names them, not when an
-- email is sent. Sends can be held for channel approval; before this table the
-- same CRM contacts stayed "uncontacted" and were drafted again every cycle,
-- and once the channel was approved every cycle's steps emailed the same person.
--
-- recipient_key is the lower-cased email, or 'name:<first last>|<company>' for
-- contacts produced without an address. action_id is the run that claimed the
-- person; later runs of the same agent may not email them, while follow-up steps
-- of the claiming run still can.
--
-- No data migration: app/services/recipient_ledger.py backfills a simulation's
-- history (sent emails, scheduled steps, prior artifacts, agent-created
-- contacts) the first time an agent's ledger is read.
--
-- All PKs are CHAR(9), from utils.id_gen.generate_id().

CREATE TABLE agent_recipients (
    id              CHAR(9)      PRIMARY KEY,
    simulation_id   CHAR(9)      NOT NULL,
    action_type     VARCHAR(50)  NOT NULL                     COMMENT 'canonical agent name',
    recipient_key   VARCHAR(255) NOT NULL                     COMMENT 'lower-cased email or name:<first last>|<company>',
    contact_id      CHAR(9)      NULL,
    action_id       CHAR(9)      NULL                         COMMENT 'the agent run that claimed this recipient',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_agent_recipient UNIQUE (simulation_id, action_type, recipient_key),
    CONSTRAINT fk_ar_simulation FOREIGN KEY (simulation_id) REFERENCES simulations (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_recipient_sim_agent ON agent_recipients (simulation_id, action_type);
CREATE INDEX ix_agent_recipient_action    ON agent_recipients (action_id);
