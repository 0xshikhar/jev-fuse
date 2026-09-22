-- Arbiter Decision Log Schema
-- Single source of truth for all machine judgments, calibration, and audit trails.

CREATE TABLE IF NOT EXISTS decisions (
    trace_id TEXT PRIMARY KEY,
    timestamp_ns INTEGER NOT NULL,
    timestamp_iso TEXT NOT NULL,
    task TEXT NOT NULL,
    client_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    input_preview TEXT,
    decision_value TEXT NOT NULL,
    raw_score REAL NOT NULL,
    confidence REAL NOT NULL,
    action TEXT NOT NULL,
    latency_ms REAL NOT NULL,
    cached INTEGER NOT NULL DEFAULT 0,
    human_label TEXT,
    human_labeled_at TEXT,
    context_json TEXT,
    reason TEXT,
    model TEXT,
    questions_json TEXT,
    answers_json TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    status TEXT DEFAULT 'success'
);

-- Indices for fast real-time audit queries and analytical aggregations
CREATE INDEX IF NOT EXISTS idx_decisions_task_ts ON decisions(task, timestamp_ns DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_action ON decisions(action);
CREATE INDEX IF NOT EXISTS idx_decisions_input_hash ON decisions(input_hash);
CREATE INDEX IF NOT EXISTS idx_decisions_human_label ON decisions(task, human_label) WHERE human_label IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_decisions_client ON decisions(client_id);
