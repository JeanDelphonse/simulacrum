-- SIM-PRD-VOICE-001 FR-VOICE-11: simulation_videos table
-- Run once on the production database after deploying the code.

CREATE TABLE IF NOT EXISTS simulation_videos (
  id               VARCHAR(9)   NOT NULL,
  simulation_id    VARCHAR(9)   NOT NULL,
  user_id          VARCHAR(9)   NOT NULL,
  script           MEDIUMTEXT   NULL,
  audio_path       VARCHAR(500) NULL,
  video_path       VARCHAR(500) NULL,
  thumbnail_path   VARCHAR(500) NULL,
  format           VARCHAR(20)  NOT NULL DEFAULT 'square',
  duration_seconds INT          NULL,
  embedded_on_bio  TINYINT(1)   NOT NULL DEFAULT 0,
  status           VARCHAR(20)  NOT NULL DEFAULT 'processing',
  created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY ix_simulation_videos_user_id       (user_id),
  KEY ix_simulation_videos_simulation_id (simulation_id),
  CONSTRAINT fk_simvid_simulation FOREIGN KEY (simulation_id) REFERENCES simulations (id),
  CONSTRAINT fk_simvid_user       FOREIGN KEY (user_id)       REFERENCES users       (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
