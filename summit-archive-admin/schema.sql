-- Схема консолидированной базы meet-archive (хаб).
--
-- СГЕНЕРИРОВАНО АВТОМАТИЧЕСКИ — не редактировать руками.
--   собрано  : 2026-11-18
--   миграций : 38
--
-- Пересобрать после обновления Summit:
--   ./scripts/dump-schema.sh summit-archive/database.sqlite > summit-archive-admin/schema.sql
--
-- Данных встреч здесь нет: только DDL и идентификаторы миграций.
-- Собственные таблицы хаба — в ingest_schema.sql.

PRAGMA foreign_keys = OFF;

CREATE VIRTUAL TABLE "documents_fts" USING fts5(title, notes, tokenize='''porter'' ''unicode61''');
CREATE VIRTUAL TABLE "transcription_fts" USING fts5(text, document_id UNINDEXED, tokenize='''porter'' ''unicode61''');
CREATE VIRTUAL TABLE "topics_fts" USING fts5(content, document_id UNINDEXED, tokenize='''porter'' ''unicode61''');
CREATE VIRTUAL TABLE "action_items_fts" USING fts5(content, document_id UNINDEXED, tokenize='''unicode61''');
CREATE TABLE grdb_migrations (identifier TEXT NOT NULL PRIMARY KEY);
CREATE TABLE "summarization_templates" ("id" TEXT PRIMARY KEY, "name" TEXT NOT NULL UNIQUE, "topics_prompt" TEXT NOT NULL, "action_items_prompt" TEXT NOT NULL, "call_summary_prompt" TEXT NOT NULL DEFAULT '', "is_default" INTEGER NOT NULL DEFAULT 0, "created_date" DOUBLE NOT NULL, "modified_date" DOUBLE NOT NULL);
CREATE TABLE "documents" ("id" TEXT PRIMARY KEY, "title" TEXT NOT NULL, "created_date" DOUBLE NOT NULL, "modified_date" DOUBLE NOT NULL, "recording_duration" DOUBLE DEFAULT 0.0, "word_count" INTEGER DEFAULT 0, "has_recordings" INTEGER DEFAULT 0, "has_transcription" INTEGER DEFAULT 0, "notes" TEXT, "detected_language" TEXT, "language_confidence" DOUBLE, "title_source" TEXT DEFAULT 'ai_generated', "folder_id" TEXT, "sort_order" INTEGER DEFAULT 0, "template_id" TEXT REFERENCES "summarization_templates"("id") ON DELETE SET NULL, "archived_at" DOUBLE, "deleted_at" DOUBLE);
CREATE TABLE "folders" ("id" TEXT PRIMARY KEY, "name" TEXT NOT NULL, "color" TEXT, "icon" TEXT, "sort_order" INTEGER DEFAULT 0, "created_date" DOUBLE NOT NULL, "modified_date" DOUBLE NOT NULL, "template_id" TEXT REFERENCES "summarization_templates"("id") ON DELETE SET NULL, "context" TEXT, "parent_folder_id" TEXT REFERENCES "folders"("id") ON DELETE CASCADE);
CREATE TABLE "topics" ("id" TEXT PRIMARY KEY, "document_id" TEXT NOT NULL REFERENCES "documents"("id") ON DELETE CASCADE, "content" TEXT NOT NULL, "sort_order" INTEGER DEFAULT 0, "created_date" DOUBLE NOT NULL, "confidence" DOUBLE, "source" TEXT, "user_edited" INTEGER NOT NULL DEFAULT 0, "bookmark_timestamps" TEXT);
CREATE TABLE "action_items" ("id" TEXT PRIMARY KEY, "document_id" TEXT NOT NULL REFERENCES "documents"("id") ON DELETE CASCADE, "content" TEXT NOT NULL, "sort_order" INTEGER DEFAULT 0, "completed" INTEGER DEFAULT 0, "due_date" DOUBLE, "assignee" TEXT, "priority" TEXT, "created_date" DOUBLE NOT NULL, "completed_date" DOUBLE, "user_edited" INTEGER NOT NULL DEFAULT 0);
CREATE TABLE "tags" ("id" TEXT PRIMARY KEY, "name" TEXT NOT NULL UNIQUE, "color" TEXT, "created_date" DOUBLE NOT NULL);
CREATE TABLE "document_tags" ("document_id" TEXT NOT NULL REFERENCES "documents"("id") ON DELETE CASCADE, "tag_id" TEXT NOT NULL REFERENCES "tags"("id") ON DELETE CASCADE, "created_date" DOUBLE NOT NULL, PRIMARY KEY ("document_id", "tag_id"));
CREATE TABLE "speakers" ("id" TEXT PRIMARY KEY, "name" TEXT NOT NULL, "created_date" DOUBLE NOT NULL, "embedding" BLOB, "embedding_model" TEXT, "last_seen" DOUBLE, "parent_speaker_id" TEXT REFERENCES "speakers"("id") ON DELETE SET NULL, "email" TEXT, "company" TEXT, "position" TEXT, "notes" TEXT, "is_identified" BOOLEAN NOT NULL DEFAULT 0);
CREATE TABLE "document_speakers" ("document_id" TEXT NOT NULL REFERENCES "documents"("id") ON DELETE CASCADE, "speaker_id" TEXT NOT NULL REFERENCES "speakers"("id") ON DELETE CASCADE, "created_date" DOUBLE NOT NULL, PRIMARY KEY ("document_id", "speaker_id"));
CREATE TABLE "transcription_segments" ("id" TEXT PRIMARY KEY, "document_id" TEXT NOT NULL REFERENCES "documents"("id") ON DELETE CASCADE, "text" TEXT NOT NULL, "start_time" DOUBLE NOT NULL, "end_time" DOUBLE NOT NULL, "source" TEXT NOT NULL, "word_confidences_json" TEXT, "average_confidence" DOUBLE, "user_edited" INTEGER NOT NULL DEFAULT 0, "starts_new_block" INTEGER NOT NULL DEFAULT 0, "speaker_id" TEXT REFERENCES "speakers"("id") ON DELETE SET NULL, "original_text" TEXT, "is_irrelevant" INTEGER NOT NULL DEFAULT 0, "cleanup_applied" INTEGER NOT NULL DEFAULT 0, "cleanup_timestamp" DOUBLE);
CREATE TABLE "bookmarks" ("id" TEXT PRIMARY KEY, "document_id" TEXT NOT NULL REFERENCES "documents"("id") ON DELETE CASCADE, "timestamp" DOUBLE NOT NULL, "label" TEXT, "transcript_context" TEXT, "sort_order" INTEGER DEFAULT 0, "created_date" DOUBLE NOT NULL);
CREATE TABLE "attachments" ("id" TEXT PRIMARY KEY, "document_id" TEXT NOT NULL REFERENCES "documents"("id") ON DELETE CASCADE, "file_name" TEXT NOT NULL, "file_size" INTEGER NOT NULL DEFAULT 0, "is_image" BOOLEAN NOT NULL DEFAULT 0, "sort_order" INTEGER NOT NULL DEFAULT 0, "created_date" DOUBLE NOT NULL, "recording_timestamp" DOUBLE, UNIQUE ("document_id", "file_name"));
CREATE TABLE "call_detection_ignored_apps" ("bundle_identifier" TEXT PRIMARY KEY, "app_name" TEXT NOT NULL, "created_date" DOUBLE NOT NULL);
CREATE TABLE "document_analytics" ("document_id" TEXT PRIMARY KEY REFERENCES "documents"("id") ON DELETE CASCADE, "time_spent_seconds" INTEGER DEFAULT 0, "estimated_billable_value" DOUBLE, "view_count" INTEGER DEFAULT 0, "edit_count" INTEGER DEFAULT 0, "share_count" INTEGER DEFAULT 0, "last_viewed_at" DOUBLE, "is_client_deliverable" INTEGER DEFAULT 0, "delivered_at" DOUBLE, "client_name" TEXT);
CREATE TABLE "search_history" ("id" TEXT PRIMARY KEY, "query" TEXT NOT NULL, "result_count" INTEGER DEFAULT 0, "clicked_document_id" TEXT, "searched_at" DOUBLE NOT NULL);
CREATE TABLE "document_versions" ("id" TEXT PRIMARY KEY, "document_id" TEXT NOT NULL REFERENCES "documents"("id") ON DELETE CASCADE, "version_number" INTEGER NOT NULL, "title" TEXT, "summary_snapshot" TEXT, "changed_by" TEXT, "change_description" TEXT, "created_at" DOUBLE NOT NULL);
CREATE TABLE "calendar_events" ("id" TEXT PRIMARY KEY, "external_id" TEXT NOT NULL, "provider" TEXT NOT NULL, "calendar_id" TEXT NOT NULL, "calendar_name" TEXT, "title" TEXT NOT NULL, "event_description" TEXT, "start_time" DOUBLE NOT NULL, "end_time" DOUBLE NOT NULL, "attendees_json" TEXT, "recurring_event_id" TEXT, "video_link" TEXT, "location" TEXT, "auto_record_enabled" INTEGER NOT NULL DEFAULT 0, "never_record_enabled" INTEGER NOT NULL DEFAULT 0, "response_status" TEXT NOT NULL DEFAULT 'needsAction', "is_all_day" INTEGER NOT NULL DEFAULT 0, "last_synced" DOUBLE NOT NULL, "linked_document_id" TEXT REFERENCES "documents"("id") ON DELETE SET NULL, "default_folder_id" TEXT REFERENCES "folders"("id") ON DELETE SET NULL);
CREATE TABLE "chat_messages" ("id" TEXT PRIMARY KEY, "session_id" TEXT NOT NULL REFERENCES "chat_sessions"("id") ON DELETE CASCADE, "role" TEXT NOT NULL, "content" TEXT NOT NULL, "citations_json" TEXT, "created_at" DOUBLE NOT NULL);
CREATE TABLE "transcription_chunks" ("id" TEXT PRIMARY KEY, "document_id" TEXT NOT NULL REFERENCES "documents"("id") ON DELETE CASCADE, "segment_ids_json" TEXT NOT NULL, "chunk_text" TEXT NOT NULL, "start_time" DOUBLE NOT NULL, "end_time" DOUBLE NOT NULL, "word_count" INTEGER NOT NULL, "embedding_indexed" INTEGER DEFAULT 0, "created_at" DOUBLE NOT NULL);
CREATE TABLE "chat_sessions" (
  id TEXT PRIMARY KEY NOT NULL,
  document_id TEXT,
  title TEXT NOT NULL DEFAULT 'New Chat',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  deleted_at REAL,
  scope_type TEXT,
  scope_data_json TEXT
);
CREATE TABLE "calendar_providers" ("provider" TEXT PRIMARY KEY, "is_connected" INTEGER NOT NULL DEFAULT 0, "last_sync_date" DOUBLE, "selected_calendar_ids_json" TEXT, "error_message" TEXT);
CREATE TABLE "calendar_selections" ("calendar_id" TEXT NOT NULL, "provider" TEXT NOT NULL, "name" TEXT NOT NULL, "color" TEXT, "is_selected" INTEGER NOT NULL DEFAULT 1, "is_primary" INTEGER NOT NULL DEFAULT 0, PRIMARY KEY ("calendar_id", "provider"));
CREATE TABLE "calendar_series_preferences" ("provider" TEXT NOT NULL, "series_key" TEXT NOT NULL, "never_record_enabled" INTEGER NOT NULL DEFAULT 0, "updated_at" DOUBLE NOT NULL, PRIMARY KEY ("provider", "series_key"));
CREATE TABLE "saved_chat_definitions" ("id" TEXT PRIMARY KEY, "name" TEXT NOT NULL, "live_query_json" TEXT NOT NULL, "date_rule_json" TEXT NOT NULL, "starting_prompt" TEXT NOT NULL, "schedule_json" TEXT NOT NULL, "is_enabled" INTEGER NOT NULL DEFAULT 1, "linked_chat_session_id" TEXT REFERENCES "chat_sessions"("id") ON DELETE SET NULL, "next_run_at" DOUBLE, "last_run_at" DOUBLE, "last_run_status" TEXT, "created_at" DOUBLE NOT NULL, "updated_at" DOUBLE NOT NULL, "deleted_at" DOUBLE);
CREATE TABLE "saved_chat_runs" ("id" TEXT PRIMARY KEY, "saved_definition_id" TEXT REFERENCES "saved_chat_definitions"("id") ON DELETE SET NULL, "chat_session_id" TEXT NOT NULL REFERENCES "chat_sessions"("id") ON DELETE CASCADE, "user_message_id" TEXT REFERENCES "chat_messages"("id") ON DELETE SET NULL, "assistant_message_id" TEXT REFERENCES "chat_messages"("id") ON DELETE SET NULL, "resolved_start" DOUBLE NOT NULL, "resolved_end" DOUBLE NOT NULL, "included_sources_json" TEXT NOT NULL DEFAULT '[]', "skipped_sources_json" TEXT NOT NULL DEFAULT '[]', "status" TEXT NOT NULL, "error_message" TEXT, "idempotency_key" TEXT NOT NULL UNIQUE, "scheduled_boundary" DOUBLE, "created_at" DOUBLE NOT NULL, "completed_at" DOUBLE);
CREATE TABLE "purged_documents" ("id" TEXT PRIMARY KEY, "purged_at" DOUBLE NOT NULL);
CREATE TABLE "trashed_calendar_links" ("document_id" TEXT NOT NULL REFERENCES "documents"("id") ON DELETE CASCADE, "provider" TEXT NOT NULL, "external_id" TEXT NOT NULL, PRIMARY KEY ("document_id", "provider", "external_id"));
CREATE TABLE "split_documents" ("id" TEXT PRIMARY KEY, "split_at" DOUBLE NOT NULL, "part_count" INTEGER NOT NULL);
CREATE TABLE "split_carried_tags" ("document_id" TEXT NOT NULL REFERENCES "documents"("id") ON DELETE CASCADE, "tag_id" TEXT NOT NULL REFERENCES "tags"("id") ON DELETE CASCADE, PRIMARY KEY ("document_id", "tag_id"));
CREATE TABLE "person_email_links" ("id" TEXT PRIMARY KEY, "speaker_id" TEXT NOT NULL REFERENCES "speakers"("id") ON DELETE CASCADE, "email" TEXT NOT NULL, "status" TEXT NOT NULL, "attendee_name" TEXT, "source_event_title" TEXT, "source_event_start" DOUBLE, "filled_primary_email" BOOLEAN NOT NULL DEFAULT 0, "created_date" DOUBLE NOT NULL, "updated_date" DOUBLE NOT NULL, UNIQUE ("speaker_id", "email"));
CREATE TABLE "calendar_document_links" ("provider" TEXT NOT NULL, "external_id" TEXT NOT NULL, "document_id" TEXT NOT NULL REFERENCES "documents"("id") ON DELETE CASCADE, "event_start" DOUBLE NOT NULL, PRIMARY KEY ("provider", "external_id"));
CREATE INDEX "idx_templates_default" ON "summarization_templates"("is_default");
CREATE INDEX idx_documents_created ON documents(created_date DESC);
CREATE INDEX idx_documents_modified ON documents(modified_date DESC);
CREATE INDEX "idx_documents_folder" ON "documents"("folder_id", "sort_order");
CREATE INDEX idx_documents_archived ON documents(archived_at) WHERE archived_at IS NOT NULL;
CREATE INDEX "idx_folders_sort" ON "folders"("sort_order");
CREATE INDEX "idx_folders_parent" ON "folders"("parent_folder_id");
CREATE INDEX "idx_topics_document" ON "topics"("document_id", "sort_order");
CREATE INDEX "idx_action_items_document" ON "action_items"("document_id", "sort_order");
CREATE INDEX "idx_action_items_completed" ON "action_items"("completed", "due_date");
CREATE INDEX idx_action_items_assignee ON action_items(assignee) WHERE assignee IS NOT NULL;
CREATE INDEX "idx_document_tags_tag" ON "document_tags"("tag_id");
CREATE INDEX "idx_document_tags_document" ON "document_tags"("document_id");
CREATE INDEX "idx_speakers_parent" ON "speakers"("parent_speaker_id");
CREATE INDEX "idx_document_speakers_speaker" ON "document_speakers"("speaker_id");
CREATE INDEX "idx_document_speakers_document" ON "document_speakers"("document_id");
CREATE INDEX "idx_segments_document" ON "transcription_segments"("document_id", "start_time");
CREATE INDEX "idx_segments_source" ON "transcription_segments"("source");
CREATE INDEX "idx_segments_confidence" ON "transcription_segments"("average_confidence");
CREATE INDEX "idx_segments_cleanup" ON "transcription_segments"("cleanup_applied");
CREATE INDEX "idx_bookmarks_document" ON "bookmarks"("document_id", "timestamp");
CREATE INDEX idx_bookmarks_label ON bookmarks(label)
WHERE label IS NOT NULL;
CREATE INDEX "idx_attachments_document" ON "attachments"("document_id", "sort_order");
CREATE INDEX "idx_call_detection_ignored_apps_name" ON "call_detection_ignored_apps"("app_name");
CREATE INDEX idx_analytics_billable ON document_analytics(estimated_billable_value DESC);
CREATE INDEX idx_analytics_client ON document_analytics(client_name) WHERE client_name IS NOT NULL;
CREATE INDEX idx_search_history_date ON search_history(searched_at DESC);
CREATE INDEX idx_versions_document ON document_versions(document_id, version_number DESC);
CREATE INDEX idx_chat_sessions_document ON chat_sessions(document_id);
CREATE INDEX idx_chat_sessions_global ON chat_sessions(updated_at DESC) WHERE document_id IS NULL;
CREATE INDEX "idx_chat_messages_session" ON "chat_messages"("session_id");
CREATE INDEX "idx_chunks_document" ON "transcription_chunks"("document_id");
CREATE INDEX idx_speakers_email ON speakers(email) WHERE email IS NOT NULL;
CREATE INDEX idx_calendar_events_date
ON calendar_events(start_time, end_time);
CREATE UNIQUE INDEX "idx_calendar_events_external" ON "calendar_events"("provider", "external_id");
CREATE INDEX idx_calendar_events_recurring
ON calendar_events(recurring_event_id)
WHERE recurring_event_id IS NOT NULL;
CREATE INDEX idx_calendar_events_auto_record
ON calendar_events(auto_record_enabled, start_time)
WHERE auto_record_enabled = 1;
CREATE INDEX idx_calendar_events_document
ON calendar_events(linked_document_id)
WHERE linked_document_id IS NOT NULL;
CREATE INDEX idx_speakers_name_lower ON speakers(LOWER(name));
CREATE INDEX "idx_saved_chat_definitions_due" ON "saved_chat_definitions"("is_enabled", "next_run_at");
CREATE INDEX "idx_saved_chat_definitions_session" ON "saved_chat_definitions"("linked_chat_session_id");
CREATE INDEX "idx_saved_chat_runs_definition" ON "saved_chat_runs"("saved_definition_id", "created_at");
CREATE INDEX "idx_saved_chat_runs_session" ON "saved_chat_runs"("chat_session_id", "created_at");
CREATE INDEX "idx_person_email_links_email" ON "person_email_links"("email");
CREATE INDEX "idx_calendar_document_links_document" ON "calendar_document_links"("document_id");
CREATE TRIGGER documents_fts_insert AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, title, notes)
    VALUES (new.rowid, new.title, COALESCE(new.notes, ''));
END;
CREATE TRIGGER documents_fts_update AFTER UPDATE ON documents BEGIN
    UPDATE documents_fts
    SET title = new.title, notes = COALESCE(new.notes, '')
    WHERE rowid = new.rowid;
END;
CREATE TRIGGER documents_fts_delete AFTER DELETE ON documents BEGIN
    DELETE FROM documents_fts WHERE rowid = old.rowid;
END;
CREATE TRIGGER transcription_fts_insert AFTER INSERT ON transcription_segments BEGIN
    INSERT INTO transcription_fts(rowid, text, document_id)
    VALUES (new.rowid, new.text, new.document_id);
END;
CREATE TRIGGER transcription_fts_update AFTER UPDATE ON transcription_segments BEGIN
    UPDATE transcription_fts SET text = new.text
    WHERE rowid = new.rowid;
END;
CREATE TRIGGER transcription_fts_delete AFTER DELETE ON transcription_segments BEGIN
    DELETE FROM transcription_fts WHERE rowid = old.rowid;
END;
CREATE TRIGGER topics_fts_insert AFTER INSERT ON topics BEGIN
    INSERT INTO topics_fts(rowid, content, document_id)
    VALUES (new.rowid, new.content, new.document_id);
END;
CREATE TRIGGER topics_fts_update AFTER UPDATE ON topics BEGIN
    UPDATE topics_fts SET content = new.content WHERE rowid = new.rowid;
END;
CREATE TRIGGER topics_fts_delete AFTER DELETE ON topics BEGIN
    DELETE FROM topics_fts WHERE rowid = old.rowid;
END;
CREATE TRIGGER action_items_fts_insert AFTER INSERT ON action_items BEGIN
    INSERT INTO action_items_fts(rowid, content, document_id)
    VALUES (new.rowid, new.content, new.document_id);
END;
CREATE TRIGGER action_items_fts_update AFTER UPDATE ON action_items BEGIN
    UPDATE action_items_fts SET content = new.content WHERE rowid = new.rowid;
END;
CREATE TRIGGER action_items_fts_delete AFTER DELETE ON action_items BEGIN
    DELETE FROM action_items_fts WHERE rowid = old.rowid;
END;

-- Версия схемы Summit, с которой сверяются присылающие устройства.
INSERT INTO grdb_migrations(identifier) VALUES ('v1_initial_schema');
INSERT INTO grdb_migrations(identifier) VALUES ('v2_add_edit_protection');
INSERT INTO grdb_migrations(identifier) VALUES ('v3_remove_state_columns');
INSERT INTO grdb_migrations(identifier) VALUES ('v4_add_language_detection');
INSERT INTO grdb_migrations(identifier) VALUES ('v5_add_transcription_user_edited');
INSERT INTO grdb_migrations(identifier) VALUES ('v6_add_speaker_diarization');
INSERT INTO grdb_migrations(identifier) VALUES ('v7_add_speaker_embeddings');
INSERT INTO grdb_migrations(identifier) VALUES ('v8_add_speaker_hierarchy');
INSERT INTO grdb_migrations(identifier) VALUES ('v9_add_transcription_cleanup');
INSERT INTO grdb_migrations(identifier) VALUES ('v10_add_summarization_templates');
INSERT INTO grdb_migrations(identifier) VALUES ('v11_add_chat_support');
INSERT INTO grdb_migrations(identifier) VALUES ('v12_seed_summarization_templates');
INSERT INTO grdb_migrations(identifier) VALUES ('v13_add_speaker_profile');
INSERT INTO grdb_migrations(identifier) VALUES ('v14_global_chat_sessions');
INSERT INTO grdb_migrations(identifier) VALUES ('v15_add_calendar_integration');
INSERT INTO grdb_migrations(identifier) VALUES ('v16_add_bookmarks');
INSERT INTO grdb_migrations(identifier) VALUES ('v17_add_bookmark_timestamps');
INSERT INTO grdb_migrations(identifier) VALUES ('v18_add_speaker_name_index');
INSERT INTO grdb_migrations(identifier) VALUES ('v19_add_action_items_fts');
INSERT INTO grdb_migrations(identifier) VALUES ('v20_reset_embeddings_for_multilingual_model');
INSERT INTO grdb_migrations(identifier) VALUES ('v21_seed_optimized_default_prompts');
INSERT INTO grdb_migrations(identifier) VALUES ('v22_add_attachments');
INSERT INTO grdb_migrations(identifier) VALUES ('v23_add_folder_context');
INSERT INTO grdb_migrations(identifier) VALUES ('v24_add_subfolders');
INSERT INTO grdb_migrations(identifier) VALUES ('v25_fix_folder_self_reference');
INSERT INTO grdb_migrations(identifier) VALUES ('v26_add_call_summary_prompt');
INSERT INTO grdb_migrations(identifier) VALUES ('v27_add_speaker_identified_flag');
INSERT INTO grdb_migrations(identifier) VALUES ('v28_add_calendar_event_default_folder');
INSERT INTO grdb_migrations(identifier) VALUES ('v29_add_call_detection_ignored_apps');
INSERT INTO grdb_migrations(identifier) VALUES ('v30_add_attachment_recording_timestamp');
INSERT INTO grdb_migrations(identifier) VALUES ('v31_add_transcription_block_boundaries');
INSERT INTO grdb_migrations(identifier) VALUES ('v32_add_calendar_event_never_record');
INSERT INTO grdb_migrations(identifier) VALUES ('v33_add_calendar_series_preferences');
INSERT INTO grdb_migrations(identifier) VALUES ('v34_add_saved_live_query_chats');
INSERT INTO grdb_migrations(identifier) VALUES ('v35_add_recently_deleted');
INSERT INTO grdb_migrations(identifier) VALUES ('v36_add_split_documents');
INSERT INTO grdb_migrations(identifier) VALUES ('v37_add_person_email_links');
INSERT INTO grdb_migrations(identifier) VALUES ('v38_add_calendar_document_links');

PRAGMA foreign_keys = ON;
