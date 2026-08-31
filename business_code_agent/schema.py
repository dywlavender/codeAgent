from __future__ import annotations

import sqlite3


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS repository (
  id TEXT PRIMARY KEY, root_path TEXT NOT NULL, indexed_at TEXT NOT NULL
);
-- Deployment topology is deliberately separate from business_entity(SYSTEM).
-- It describes where code runs, not what the business means.
CREATE TABLE IF NOT EXISTS software_system (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE'
);
CREATE TABLE IF NOT EXISTS application (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, system_id TEXT NOT NULL,
  repository_id TEXT NOT NULL, source_root TEXT NOT NULL DEFAULT '.',
  app_type TEXT NOT NULL, language TEXT NOT NULL DEFAULT '',
  framework TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'ACTIVE',
  UNIQUE(repository_id, source_root)
);
CREATE TABLE IF NOT EXISTS code_file (
  id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, path TEXT NOT NULL,
  content_hash TEXT NOT NULL, UNIQUE(repository_id, path)
);
CREATE TABLE IF NOT EXISTS application_code_file (
  application_id TEXT NOT NULL, file_id TEXT NOT NULL,
  PRIMARY KEY(application_id, file_id)
);
CREATE TABLE IF NOT EXISTS code_symbol (
  id TEXT PRIMARY KEY, file_id TEXT NOT NULL, kind TEXT NOT NULL,
  qualified_name TEXT NOT NULL, name TEXT NOT NULL, line_start INTEGER NOT NULL,
  line_end INTEGER NOT NULL, UNIQUE(file_id, qualified_name, line_start)
);
CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
  source_version TEXT NOT NULL, locator TEXT NOT NULL, line_start INTEGER,
  line_end INTEGER, chunk_id TEXT, content_hash TEXT NOT NULL, excerpt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_lifecycle (
  evidence_id TEXT PRIMARY KEY, status TEXT NOT NULL,
  superseded_at TEXT, trigger_type TEXT, trigger_id TEXT
);
CREATE TABLE IF NOT EXISTS code_fact (
  id TEXT PRIMARY KEY, symbol_id TEXT NOT NULL, fact_type TEXT NOT NULL,
  subject TEXT NOT NULL, target TEXT NOT NULL, evidence_id TEXT NOT NULL,
  UNIQUE(symbol_id, fact_type, subject, target, evidence_id)
);
CREATE TABLE IF NOT EXISTS cross_application_edge (
  id TEXT PRIMARY KEY, source_application_id TEXT NOT NULL,
  source_symbol_id TEXT NOT NULL, edge_type TEXT NOT NULL,
  target_application_id TEXT NOT NULL, target_symbol_id TEXT NOT NULL,
  protocol TEXT NOT NULL, edge_key TEXT NOT NULL,
  status TEXT NOT NULL, confidence REAL NOT NULL,
  evidence_ids_json TEXT NOT NULL DEFAULT '[]', resolved_at TEXT NOT NULL,
  UNIQUE(source_symbol_id, edge_type, target_symbol_id, edge_key)
);
CREATE INDEX IF NOT EXISTS idx_application_repository
  ON application(repository_id, source_root);
CREATE INDEX IF NOT EXISTS idx_application_file
  ON application_code_file(file_id, application_id);
CREATE INDEX IF NOT EXISTS idx_cross_edge_source
  ON cross_application_edge(source_symbol_id, status);
CREATE INDEX IF NOT EXISTS idx_cross_edge_target
  ON cross_application_edge(target_symbol_id, status);
CREATE TABLE IF NOT EXISTS ingestion_change (
  id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, file_path TEXT NOT NULL,
  change_type TEXT NOT NULL, previous_hash TEXT, current_hash TEXT,
  indexed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parse_diagnostic (
  file_id TEXT PRIMARY KEY, backend TEXT NOT NULL, has_error INTEGER NOT NULL,
  root_type TEXT NOT NULL, checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requirement (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, source_path TEXT NOT NULL,
  version TEXT NOT NULL, content_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE', current_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS requirement_change (
  id TEXT PRIMARY KEY, requirement_id TEXT NOT NULL, change_type TEXT NOT NULL,
  previous_hash TEXT, current_hash TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requirement_version (
  id TEXT PRIMARY KEY, requirement_id TEXT NOT NULL, version INTEGER NOT NULL,
  source_file TEXT NOT NULL, source_type TEXT NOT NULL, content_hash TEXT NOT NULL,
  original_text TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(requirement_id, version)
);
CREATE TABLE IF NOT EXISTS requirement_digest_v2 (
  requirement_version_id TEXT PRIMARY KEY, business_goal TEXT, background TEXT,
  digest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requirement_rule (
  id TEXT PRIMARY KEY, requirement_version_id TEXT NOT NULL, statement TEXT NOT NULL,
  objects_json TEXT NOT NULL, conditions_json TEXT NOT NULL, result TEXT NOT NULL,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requirement_chunk_v2 (
  id TEXT PRIMARY KEY, requirement_version_id TEXT NOT NULL,
  section_path_json TEXT NOT NULL, sequence INTEGER NOT NULL, content TEXT NOT NULL,
  content_hash TEXT NOT NULL, page INTEGER, paragraph_start INTEGER,
  paragraph_end INTEGER, start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
  evidence_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requirement_evidence (
  fact_type TEXT NOT NULL, fact_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
  start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
  PRIMARY KEY(fact_type, fact_id, chunk_id, start_offset, end_offset)
);
CREATE TABLE IF NOT EXISTS requirement_relation (
  id TEXT PRIMARY KEY, requirement_version_id TEXT NOT NULL,
  source_object_type TEXT NOT NULL, source_object_id TEXT NOT NULL,
  relation_type TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  origin TEXT NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL,
  reason TEXT NOT NULL, requirement_evidence_id TEXT, code_evidence_id TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(requirement_version_id, source_object_type, source_object_id, relation_type, target_type, target_id)
);
CREATE TABLE IF NOT EXISTS requirement_version_change (
  id TEXT PRIMARY KEY, requirement_id TEXT NOT NULL,
  from_version_id TEXT, to_version_id TEXT NOT NULL,
  added_rules_json TEXT NOT NULL, removed_rules_json TEXT NOT NULL,
  changed_rules_json TEXT NOT NULL, affected_knowledge_json TEXT NOT NULL,
  affected_code_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS requirement_fts USING fts5(
  requirement_id UNINDEXED, requirement_version_id UNINDEXED,
  title, digest, rules, tags
);
CREATE VIRTUAL TABLE IF NOT EXISTS requirement_chunk_fts USING fts5(
  requirement_id UNINDEXED, requirement_version_id UNINDEXED,
  chunk_id UNINDEXED, section_path, content
);
CREATE TABLE IF NOT EXISTS requirement_digest (
  requirement_id TEXT PRIMARY KEY, digest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requirement_chunk (
  id TEXT PRIMARY KEY, requirement_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
  content TEXT NOT NULL, evidence_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_run (
  id TEXT PRIMARY KEY, question TEXT NOT NULL, state_json TEXT NOT NULL,
  evidence_status TEXT NOT NULL, iterations INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS query_agent_run (
  id TEXT PRIMARY KEY, question TEXT NOT NULL, intent TEXT NOT NULL,
  status TEXT NOT NULL, evidence_status TEXT NOT NULL, iterations INTEGER NOT NULL,
  source_characters INTEGER NOT NULL, answer_json TEXT NOT NULL,
  state_json TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS query_agent_step (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_name TEXT NOT NULL,
  iteration INTEGER NOT NULL, input_summary_json TEXT NOT NULL,
  output_summary_json TEXT NOT NULL, evidence_count INTEGER NOT NULL,
  duration_ms REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS query_tool_call (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_id TEXT NOT NULL,
  tool_name TEXT NOT NULL, tool_input_json TEXT NOT NULL,
  result_count INTEGER NOT NULL, iteration INTEGER NOT NULL,
  duration_ms REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS query_checkpoint (
  run_id TEXT NOT NULL, sequence INTEGER NOT NULL, node_name TEXT NOT NULL,
  state_json TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(run_id, sequence)
);
CREATE TABLE IF NOT EXISTS query_conversation (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS query_message (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, run_id TEXT NOT NULL,
  role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES query_conversation(id)
);
CREATE INDEX IF NOT EXISTS idx_query_message_conversation ON query_message(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS query_feedback (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, rating TEXT NOT NULL,
  comment TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES query_agent_run(id)
);

-- Function-centred knowledge V2. Human-authored definitions and generated
-- analysis are deliberately stored separately so a code refresh can never
-- rewrite the source document.
CREATE TABLE IF NOT EXISTS functional_knowledge (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases_json TEXT NOT NULL,
  tags_json TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
  scenarios_json TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
  source_path TEXT NOT NULL UNIQUE, source_fingerprint TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE', refreshed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS functional_entry_anchor (
  id TEXT PRIMARY KEY, function_id TEXT NOT NULL, project_name TEXT NOT NULL,
  entry_type TEXT NOT NULL, class_name TEXT NOT NULL,
  symbol_id TEXT, resolution_status TEXT NOT NULL,
  candidate_ids_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE(function_id, project_name, entry_type, class_name),
  FOREIGN KEY(function_id) REFERENCES functional_knowledge(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS functional_key_table (
  id TEXT PRIMARY KEY, function_id TEXT NOT NULL, table_name TEXT NOT NULL,
  purpose TEXT NOT NULL DEFAULT '',
  UNIQUE(function_id, table_name),
  FOREIGN KEY(function_id) REFERENCES functional_knowledge(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS functional_retrieval_link (
  id TEXT PRIMARY KEY, function_id TEXT NOT NULL,
  source_type TEXT NOT NULL, source_id TEXT NOT NULL,
  relation_type TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  evidence_id TEXT, created_at TEXT NOT NULL,
  UNIQUE(function_id, source_type, source_id, relation_type, target_type, target_id, evidence_id),
  FOREIGN KEY(function_id) REFERENCES functional_knowledge(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS functional_analysis (
  function_id TEXT PRIMARY KEY, status TEXT NOT NULL,
  flow_json TEXT NOT NULL DEFAULT '[]', rules_json TEXT NOT NULL DEFAULT '[]',
  coverage_json TEXT NOT NULL DEFAULT '{}', mode TEXT NOT NULL,
  analyzed_at TEXT, message TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(function_id) REFERENCES functional_knowledge(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_functional_knowledge_status ON functional_knowledge(status, refreshed_at);
CREATE INDEX IF NOT EXISTS idx_functional_entry_function ON functional_entry_anchor(function_id, resolution_status);
CREATE INDEX IF NOT EXISTS idx_functional_link_function ON functional_retrieval_link(function_id, relation_type);

-- MVP business baseline.  Human source, structured business knowledge and
-- business/code mappings are separate records.  Re-indexing code therefore
-- never rewrites a human-authored business statement.
CREATE TABLE IF NOT EXISTS business_baseline_source (
  id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  source_revision TEXT NOT NULL, content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE', imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS business_entity (
  id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, name TEXT NOT NULL,
  aliases_json TEXT NOT NULL DEFAULT '[]', definition TEXT NOT NULL DEFAULT '',
  attributes_json TEXT NOT NULL DEFAULT '{}', source_type TEXT NOT NULL,
  source_id TEXT NOT NULL, source_evidence_id TEXT,
  confidence REAL NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(source_id, entity_type, name),
  FOREIGN KEY(source_id) REFERENCES business_baseline_source(id)
);
CREATE TABLE IF NOT EXISTS business_relation_v2 (
  id TEXT PRIMARY KEY, from_entity_id TEXT, from_label TEXT NOT NULL,
  relation_type TEXT NOT NULL, to_entity_id TEXT, to_label TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT '', attributes_json TEXT NOT NULL DEFAULT '{}',
  source_type TEXT NOT NULL, source_id TEXT NOT NULL, evidence_id TEXT,
  confidence REAL NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(source_id, from_label, relation_type, to_label, scope),
  FOREIGN KEY(source_id) REFERENCES business_baseline_source(id)
);

-- Stable navigation hints maintained with business knowledge. These are not
-- code mappings: they keep only an application and a human-authored entry
-- name. The current symbol, method and source location are resolved at query
-- time from the latest code index.
CREATE TABLE IF NOT EXISTS business_entry_anchor (
  id TEXT PRIMARY KEY, business_type TEXT NOT NULL, business_id TEXT NOT NULL,
  application_id TEXT NOT NULL, entry_type TEXT NOT NULL, entry_name TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'HUMAN', status TEXT NOT NULL DEFAULT 'ACTIVE',
  source_evidence_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(business_type, business_id, application_id, entry_type, entry_name),
  FOREIGN KEY(application_id) REFERENCES application(id)
);
CREATE INDEX IF NOT EXISTS idx_business_entry_anchor_business
  ON business_entry_anchor(business_type, business_id, status);
CREATE INDEX IF NOT EXISTS idx_business_entry_anchor_application
  ON business_entry_anchor(application_id, entry_name, status);
CREATE TABLE IF NOT EXISTS business_code_mapping (
  id TEXT PRIMARY KEY, business_type TEXT NOT NULL, business_id TEXT NOT NULL,
  relation_type TEXT NOT NULL, code_symbol_id TEXT,
  code_reference TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
  confidence REAL NOT NULL, evidence_ids_json TEXT NOT NULL DEFAULT '[]',
  search_terms_json TEXT NOT NULL DEFAULT '[]', message TEXT NOT NULL DEFAULT '',
  source_type TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(business_type, business_id, relation_type, code_reference)
);
CREATE INDEX IF NOT EXISTS idx_business_entity_type_status
  ON business_entity(entity_type, status, name);
CREATE INDEX IF NOT EXISTS idx_business_entity_source
  ON business_entity(source_id, status);
CREATE INDEX IF NOT EXISTS idx_business_relation_source
  ON business_relation_v2(source_id, status);
CREATE INDEX IF NOT EXISTS idx_business_mapping_target
  ON business_code_mapping(business_type, business_id, status);
CREATE INDEX IF NOT EXISTS idx_business_mapping_symbol
  ON business_code_mapping(code_symbol_id);

-- MVP2. A query may reveal a useful business-to-code relationship, but a
-- query result is not a human business definition.  Keep the observation in
-- its own table until an administrator confirms it.  This prevents an answer
-- from silently changing the authored baseline while still making the
-- discovery reusable by later questions.
CREATE TABLE IF NOT EXISTS business_code_mapping_observation (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, question TEXT NOT NULL,
  business_type TEXT NOT NULL, business_id TEXT NOT NULL,
  relation_type TEXT NOT NULL, code_symbol_id TEXT,
  code_reference TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'CANDIDATE',
  confidence REAL NOT NULL, evidence_ids_json TEXT NOT NULL DEFAULT '[]',
  reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  reviewed_at TEXT, reviewer_note TEXT NOT NULL DEFAULT '',
  UNIQUE(run_id, business_type, business_id, relation_type, code_reference)
);
CREATE INDEX IF NOT EXISTS idx_mapping_observation_status
  ON business_code_mapping_observation(status, created_at);
CREATE INDEX IF NOT EXISTS idx_mapping_observation_business
  ON business_code_mapping_observation(business_type, business_id, status);
CREATE INDEX IF NOT EXISTS idx_mapping_observation_symbol
  ON business_code_mapping_observation(code_symbol_id, status);
"""


def connect(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    _migrate(connection)
    return connection


def _migrate(connection: sqlite3.Connection) -> None:
    """Remove retired schemas and apply additive migrations."""
    _purge_retired_business_schema(connection)
    _purge_legacy_function_governance(connection)
    additions = {
        "requirement": (
            ("status", "TEXT NOT NULL DEFAULT 'ACTIVE'"),
            ("current_version", "INTEGER NOT NULL DEFAULT 1"),
            ("created_at", "TEXT"), ("updated_at", "TEXT"),
        ),
    }
    for table, columns in additions.items():
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns:
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    entry_columns = {row[1] for row in connection.execute("PRAGMA table_info(functional_entry_anchor)")}
    if "repository_id" in entry_columns and "project_name" not in entry_columns:
        connection.execute("ALTER TABLE functional_entry_anchor RENAME COLUMN repository_id TO project_name")
    connection.commit()


def _purge_legacy_function_governance(connection: sqlite3.Connection) -> None:
    """Remove the superseded proposal/version model from existing databases."""
    tables = (
        "knowledge_proposal_review",
        "proposal_item_evidence",
        "knowledge_update_proposal_item",
        "function_item_evidence",
        "function_data_impact",
        "function_entry",
        "function_rule",
        "function_scenario",
        "business_function_version",
        "knowledge_update_proposal",
        "business_function",
    )
    existing = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if not existing.intersection(tables):
        return
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        for table in tables:
            if table in existing:
                connection.execute(f'DROP TABLE "{table}"')
        connection.commit()
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _purge_retired_business_schema(connection: sqlite3.Connection) -> None:
    """Remove the pre-governance business-knowledge storage on first open.

    The application no longer has a reader or writer for these tables.  A
    one-time migration keeps an existing local database from retaining a
    second, confusing source of truth.  Only evidence owned by the retired
    records is removed; code and requirement evidence remain untouched.
    """
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='business_knowledge'"
    ).fetchone():
        old_evidence = [row[0] for row in connection.execute(
            "SELECT evidence_id FROM business_knowledge WHERE evidence_id IS NOT NULL"
        )]
        retired_tables = (
            "business_knowledge_fts", "business_knowledge_fts_config",
            "business_knowledge_fts_content", "business_knowledge_fts_data",
            "business_knowledge_fts_docsize", "business_knowledge_fts_idx",
            "business_review", "relation_evidence", "knowledge_relation",
            "business_knowledge_tag", "knowledge_change", "business_knowledge",
        )
        for table in retired_tables:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                connection.execute(f'DROP TABLE "{table}"')
        if old_evidence:
            marks = ",".join("?" for _ in old_evidence)
            connection.execute(f"DELETE FROM evidence_lifecycle WHERE evidence_id IN ({marks})", old_evidence)
            connection.execute(f"DELETE FROM evidence WHERE id IN ({marks})", old_evidence)
    _purge_retired_runs(connection)


def _purge_retired_runs(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT id FROM query_agent_run WHERE state_json LIKE '%BK-%' OR answer_json LIKE '%BK-%'"
    ).fetchall()
    run_ids = [row[0] for row in rows]
    if not run_ids:
        return
    marks = ",".join("?" for _ in run_ids)
    for table in ("query_feedback", "query_tool_call", "query_agent_step", "query_checkpoint", "query_message"):
        connection.execute(f"DELETE FROM {table} WHERE run_id IN ({marks})", run_ids)
    connection.execute(f"DELETE FROM query_agent_run WHERE id IN ({marks})", run_ids)
