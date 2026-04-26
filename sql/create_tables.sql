-- Simulacrum — Create all tables
-- Run this once against your MySQL database to initialize the schema.

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(9) PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255),
    full_name VARCHAR(255) NOT NULL,
    google_id VARCHAR(255) UNIQUE,
    email_verified TINYINT(1) NOT NULL DEFAULT 0,
    email_verify_token VARCHAR(255),
    password_reset_token VARCHAR(255),
    password_reset_expires DATETIME,
    simulation_count INT NOT NULL DEFAULT 0,
    total_spend INT NOT NULL DEFAULT 0,
    is_admin TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    updated_at DATETIME,
    INDEX ix_users_email (email)
);

CREATE TABLE IF NOT EXISTS resumes (
    id VARCHAR(9) PRIMARY KEY,
    user_id VARCHAR(9) NOT NULL,
    label VARCHAR(255) NOT NULL,
    file_path VARCHAR(500),
    file_type VARCHAR(10),
    source VARCHAR(20) NOT NULL DEFAULT 'upload',
    parsed_text TEXT,
    expertise_zones TEXT,
    linkedin_access_token_enc TEXT,
    linkedin_profile_url VARCHAR(500),
    created_at DATETIME NOT NULL,
    updated_at DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX ix_resumes_user_id (user_id)
);

CREATE TABLE IF NOT EXISTS simulations (
    id VARCHAR(9) PRIMARY KEY,
    user_id VARCHAR(9) NOT NULL,
    resume_id VARCHAR(9),
    name VARCHAR(255) NOT NULL,
    focus_hint TEXT,
    expertise_zone VARCHAR(500),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    stripe_payment_intent_id VARCHAR(255),
    stripe_charge_id VARCHAR(255),
    error_message TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (resume_id) REFERENCES resumes(id) ON DELETE SET NULL,
    INDEX ix_simulations_user_id (user_id),
    INDEX ix_simulations_resume_id (resume_id)
);

CREATE TABLE IF NOT EXISTS simulation_layers (
    id VARCHAR(9) PRIMARY KEY,
    simulation_id VARCHAR(9) NOT NULL,
    layer_number INT NOT NULL,
    layer_name VARCHAR(255) NOT NULL,
    income_type VARCHAR(100),
    ai_narrative TEXT,
    priority_score FLOAT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME,
    FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE CASCADE,
    INDEX ix_simulation_layers_simulation_id (simulation_id)
);

CREATE TABLE IF NOT EXISTS income_streams (
    id VARCHAR(9) PRIMARY KEY,
    layer_id VARCHAR(9) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    platform VARCHAR(255),
    est_monthly_low INT,
    est_monthly_high INT,
    ai_reasoning TEXT NOT NULL,
    deliverable_refs TEXT,
    automation_level VARCHAR(50),
    launch_timeline_weeks INT,
    created_at DATETIME NOT NULL,
    FOREIGN KEY (layer_id) REFERENCES simulation_layers(id) ON DELETE CASCADE,
    INDEX ix_income_streams_layer_id (layer_id)
);

CREATE TABLE IF NOT EXISTS collaborations (
    id VARCHAR(9) PRIMARY KEY,
    simulation_id VARCHAR(9) NOT NULL,
    invitee_email VARCHAR(255) NOT NULL,
    invitee_id VARCHAR(9),
    permission_level VARCHAR(20) NOT NULL DEFAULT 'viewer',
    share_token VARCHAR(64) NOT NULL UNIQUE,
    expires_at DATETIME NOT NULL,
    accepted_at DATETIME,
    revoked_at DATETIME,
    created_by VARCHAR(9),
    created_at DATETIME NOT NULL,
    FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE CASCADE,
    FOREIGN KEY (invitee_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
    INDEX ix_collaborations_simulation_id (simulation_id),
    INDEX ix_collaborations_invitee_email (invitee_email)
);

CREATE TABLE IF NOT EXISTS collab_activities (
    id VARCHAR(9) PRIMARY KEY,
    simulation_id VARCHAR(9) NOT NULL,
    collaborator_id VARCHAR(9),
    collaboration_id VARCHAR(9),
    activity_type VARCHAR(30) NOT NULL,
    layer_number INT,
    content TEXT,
    created_at DATETIME NOT NULL,
    FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE CASCADE,
    FOREIGN KEY (collaborator_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (collaboration_id) REFERENCES collaborations(id) ON DELETE SET NULL,
    INDEX ix_collab_activities_simulation_id (simulation_id)
);

CREATE TABLE IF NOT EXISTS platform_settings (
    id VARCHAR(9) PRIMARY KEY,
    `key` VARCHAR(100) NOT NULL UNIQUE,
    value TEXT NOT NULL,
    updated_by VARCHAR(9),
    updated_at DATETIME,
    FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL,
    INDEX ix_platform_settings_key (`key`)
);

CREATE TABLE IF NOT EXISTS ai_interactions (
    id VARCHAR(9) PRIMARY KEY,
    user_id VARCHAR(9),
    simulation_id VARCHAR(9),
    interaction_type VARCHAR(30) NOT NULL,
    prompt_tokens INT,
    completion_tokens INT,
    model VARCHAR(100) NOT NULL,
    created_at DATETIME NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE SET NULL,
    INDEX ix_ai_interactions_user_id (user_id),
    INDEX ix_ai_interactions_simulation_id (simulation_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id VARCHAR(9) PRIMARY KEY,
    user_id VARCHAR(9),
    action VARCHAR(100) NOT NULL,
    resource_id VARCHAR(9),
    metadata TEXT,
    created_at DATETIME NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX ix_audit_logs_user_id (user_id)
);
