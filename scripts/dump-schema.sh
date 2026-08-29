#!/usr/bin/env bash
# Генерирует DDL консолидированной базы хаба из архивной базы Summit.
#
# Читает базу строго read-only и печатает схему в stdout. Данных встреч в выводе
# нет: из sqlite_master берётся только DDL, плюс идентификаторы grdb_migrations —
# они нужны, чтобы сервер мог сверять версию схемы с присылающими устройствами.
#
# Использование:
#   ./scripts/dump-schema.sh summit-archive/database.sqlite > summit-archive-admin/schema.sql
#
# Файл машинный. Руками не правится: наши собственные таблицы живут отдельно,
# в summit-archive-admin/ingest_schema.sql.
set -euo pipefail

SRC="${1:-}"
if [[ -z "$SRC" || ! -f "$SRC" ]]; then
    echo "Использование: $0 <путь к архивной database.sqlite>" >&2
    exit 2
fi

RO_URI="file:$(cd "$(dirname "$SRC")" && pwd)/$(basename "$SRC")?mode=ro"

# Shadow-таблицы FTS5 (…_data/_idx/_content/_docsize/_config) создаёт сама
# CREATE VIRTUAL TABLE. В дамп они попадать не должны: SQLite отвергает эти имена
# как зарезервированные, и сырой .schema проходит лишь потому, что они идут
# следом за своей virtual table и вырождаются в no-op. Не полагаемся на порядок.
DDL=$(sqlite3 "$RO_URI" <<'SQL'
SELECT sql || ';'
FROM sqlite_master
WHERE sql IS NOT NULL
  AND name NOT LIKE 'sqlite_%'
  AND name NOT IN (
      SELECT name FROM sqlite_master
      WHERE type = 'table'
        AND (name LIKE '%_fts_data'
          OR name LIKE '%_fts_idx'
          OR name LIKE '%_fts_content'
          OR name LIKE '%_fts_docsize'
          OR name LIKE '%_fts_config'))
ORDER BY CASE type WHEN 'table' THEN 1 WHEN 'index' THEN 2 ELSE 3 END, rootpage;
SQL
)

MIGRATIONS=$(sqlite3 "$RO_URI" \
    "SELECT 'INSERT INTO grdb_migrations(identifier) VALUES (''' || replace(identifier, '''', '''''') || ''');' FROM grdb_migrations ORDER BY rowid;")
MIGRATION_COUNT=$(sqlite3 "$RO_URI" "SELECT count(*) FROM grdb_migrations;")

cat <<EOH
-- Схема консолидированной базы meet-archive (хаб).
--
-- СГЕНЕРИРОВАНО АВТОМАТИЧЕСКИ — не редактировать руками.
--   собрано  : $(date -u +%Y-%m-%d)
--   миграций : $MIGRATION_COUNT
--
-- Пересобрать после обновления Summit:
--   ./scripts/dump-schema.sh summit-archive/database.sqlite > summit-archive-admin/schema.sql
--
-- Данных встреч здесь нет: только DDL и идентификаторы миграций.
-- Собственные таблицы хаба — в ingest_schema.sql.

PRAGMA foreign_keys = OFF;

$DDL

-- Версия схемы Summit, с которой сверяются присылающие устройства.
$MIGRATIONS

PRAGMA foreign_keys = ON;
EOH
