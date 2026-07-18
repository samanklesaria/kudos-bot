CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE kudos (
    id SERIAL PRIMARY KEY,
    giver_id VARCHAR(21) NOT NULL,
    recipient_id VARCHAR(21) NOT NULL,
    channel_id VARCHAR(21) NOT NULL,
    message_ts VARCHAR(21) NOT NULL,
    message_text TEXT,
    redeemed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    redeems INTEGER REFERENCES kudos(id) ON DELETE SET NULL,
    embedding vector(128),
    overflow BOOLEAN NOT NULL DEFAULT FALSE,
    CHECK(giver_id <> recipient_id)
);

CREATE TABLE users (
    id VARCHAR(21) PRIMARY KEY,
    display_name VARCHAR(64) NOT NULL
);

CREATE TABLE budgets (
    month_date DATE PRIMARY KEY,  -- 1st of the month
    point_budget INTEGER NOT NULL,
    conversion_rate NUMERIC NOT NULL
);

CREATE TABLE clusters (
    id SERIAL PRIMARY KEY,
    summary VARCHAR(128) NOT NULL,
    center vector(128) NOT NULL
);

CREATE TABLE cluster_members (
    cluster_id INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    kudos_id INTEGER NOT NULL REFERENCES kudos(id) ON DELETE CASCADE,
    PRIMARY KEY (cluster_id, kudos_id)
);

CREATE TABLE covariates (
    label VARCHAR(64) NOT NULL,
    week VARCHAR(8) NOT NULL,
    value NUMERIC NOT NULL,
    PRIMARY KEY (label, week)
);

CREATE INDEX idx_kudos_recipient ON kudos(recipient_id);
CREATE INDEX idx_kudos_giver_day ON kudos(giver_id, created_at);
CREATE INDEX idx_kudos_giver_recipient_month ON kudos(giver_id, recipient_id, created_at);
CREATE UNIQUE INDEX idx_kudos_channel_ts ON kudos(channel_id, message_ts);
CREATE INDEX idx_kudos_redeemed ON kudos(redeemed_at) WHERE redeemed_at IS NOT NULL;
CREATE INDEX idx_kudos_unredeemed ON kudos(recipient_id, created_at, id) WHERE redeemed_at IS NULL AND NOT overflow;
CREATE INDEX idx_kudos_unlinked ON kudos(giver_id, created_at, id) WHERE redeems IS NULL;
