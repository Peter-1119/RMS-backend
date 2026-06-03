-- 1) 先把 JSON 欄位拆出去（可以先新增新表，再做 migration）
CREATE TABLE IF NOT EXISTS `rms_document_snapshots` (
  `snapshot_id`       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `document_token`    CHAR(36)        NOT NULL,
  `rms_id`            VARCHAR(120)    NOT NULL,         -- 對應 Oracle.RMS_ID
  `document_id`       VARCHAR(30)     NULL,
  `document_version`  NUMERIC(5,2)    NULL,
  `document_name`     VARCHAR(80)     NULL,
  `created_by`        VARCHAR(10)     NULL,             -- 使用者工號
  `created_at`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `synced_at`         DATETIME        NULL,
  `sync_status`       TINYINT         NOT NULL DEFAULT 0,

  PRIMARY KEY (`snapshot_id`),
  UNIQUE KEY `ux_rms_id` (`rms_id`),
  KEY `ix_snapshot_token` (`document_token`),
  KEY `ix_snapshot_sync` (`sync_status`,`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `rms_document_snapshot_payloads` (
  `snapshot_id`    BIGINT UNSIGNED NOT NULL,
  `document_row`   JSON            NOT NULL,
  `blocks_rows`    JSON            NOT NULL,
  `references_rows` JSON           NOT NULL,
  PRIMARY KEY (`snapshot_id`),
  CONSTRAINT `fk_snapshot_payload_snapshot`
    FOREIGN KEY (`snapshot_id`)
    REFERENCES `rms_document_snapshots` (`snapshot_id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE `rms_document_snapshot_payloads` ADD COLUMN `program_codes_rows` JSON NOT NULL AFTER `references_rows`;