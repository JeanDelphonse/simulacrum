-- Founder Track (FOUNDER_PLAN.md section 3): founding team tables.
-- Run once on deploy, after 071. Additive.
--
-- alignment_response  each co-founder's own answer to one alignment question.
-- decision_right      who has the final say on a decision type (optionally per
--                     layer). Agents route approvals to that member.
--
-- PKs are CHAR(9) uppercase from utils.id_gen.generate_pk().

CREATE TABLE alignment_response (
    id            CHAR(9)      PRIMARY KEY,
    venture_id    VARCHAR(9)   NOT NULL,
    member_id     VARCHAR(9)   NOT NULL,
    question_key  VARCHAR(40)  NOT NULL,
    answer        TEXT         NULL,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_alignment_member_question (member_id, question_key),
    KEY ix_alignment_response_venture (venture_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE decision_right (
    id                CHAR(9)      PRIMARY KEY,
    venture_id        VARCHAR(9)   NOT NULL,
    decision_type     VARCHAR(20)  NOT NULL COMMENT 'publish_page | investor_contact | customer_contact | spend | hiring | product | legal',
    layer             INT          NULL,
    holder_member_id  VARCHAR(9)   NOT NULL,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_decision_right_venture (venture_id, decision_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
