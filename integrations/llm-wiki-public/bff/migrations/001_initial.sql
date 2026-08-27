CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    upstream_session_id TEXT NOT NULL,
    visitor_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX idx_conversations_cleanup ON conversations(expires_at);

CREATE TABLE turns (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL,
    assistant_message_id TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY(conversation_id, turn_id)
);

CREATE TABLE idempotency (
    visitor_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('running', 'done')),
    encoded_events TEXT NOT NULL DEFAULT '',
    expires_at INTEGER NOT NULL,
    PRIMARY KEY(visitor_id, conversation_id, idempotency_key)
);
CREATE INDEX idx_idempotency_expiry ON idempotency(expires_at);

CREATE TABLE rate_buckets (
    bucket_key TEXT PRIMARY KEY,
    tokens REAL NOT NULL,
    updated_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE rate_counters (
    counter_key TEXT PRIMARY KEY,
    count INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE leases (
    resource_kind TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    lease_id TEXT NOT NULL UNIQUE,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY(resource_kind, resource_key, lease_id)
);
CREATE INDEX idx_leases_resource_expiry ON leases(resource_kind, resource_key, expires_at);
