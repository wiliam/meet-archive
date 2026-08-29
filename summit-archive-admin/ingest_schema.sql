-- Собственные таблицы хаба. Отдельным файлом от schema.sql, который является
-- машинным дампом схемы Summit и перегенерируется целиком при обновлении
-- приложения — руками сливать эти два набора не нужно.

-- Устройства, которым разрешено присылать встречи. Строка появляется при первом
-- успешном бандле; сам список разрешённых токенов живёт в переменной окружения.
CREATE TABLE IF NOT EXISTS ingest_devices (
    device_id    TEXT PRIMARY KEY,
    label        TEXT,
    first_seen   REAL NOT NULL,
    last_seen    REAL,
    bundle_count INTEGER NOT NULL DEFAULT 0
);

-- Разводка справочников между устройствами.
--
-- folders.name, tags.name и summarization_templates.name объявлены UNIQUE, а id
-- у одноимённых записей на разных машинах разные. Слияние сводит их к одному
-- целевому id, и это соответствие обязано быть персистентным: если тег позже
-- переименуют на одном устройстве, повторная отправка не должна уехать в другую
-- строку. Пересчёт по имени каждый раз этого не гарантирует.
CREATE TABLE IF NOT EXISTS ingest_id_map (
    device_id  TEXT NOT NULL,
    table_name TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    target_id  TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (device_id, table_name, source_id)
);

CREATE INDEX IF NOT EXISTS idx_ingest_id_map_target
    ON ingest_id_map (table_name, target_id);

-- Что уже принято: источник встречи и отпечаток содержимого.
--
-- Заменяет собой wall-clock watermark из archive-state.json. Клиент читает это
-- через /api/ingest/state и сравнивает modified_date только с копией, которую
-- записал он сам, — часы разных машин не сравниваются никогда.
CREATE TABLE IF NOT EXISTS ingest_documents (
    document_id   TEXT PRIMARY KEY,
    device_id     TEXT NOT NULL,
    modified_date REAL NOT NULL,
    content_hash  TEXT,
    ingested_at   REAL NOT NULL,
    audio_files   TEXT              -- JSON-массив имён; байты остаются на устройстве
);

CREATE INDEX IF NOT EXISTS idx_ingest_documents_device
    ON ingest_documents (device_id);
