CREATE TABLE IF NOT EXISTS `rms_document_snapshots` (
  `snapshot_id`       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `document_token`    CHAR(36)        NOT NULL,
  `rms_id`            VARCHAR(120)    NOT NULL,         -- 對應 Oracle.RMS_ID
  `document_id`       VARCHAR(30)     NULL,
  `document_version`  NUMERIC(5,2)    NULL,
  `document_name`     VARCHAR(80)     NULL,

  `document_row`      JSON            NOT NULL,         -- 一整列 rms_document_attributes (dict_cursor)
  `blocks_rows`       JSON            NOT NULL,         -- [ rows of rms_block_content ]
  `references_rows`   JSON            NOT NULL,         -- [ rows of rms_references ]

  `created_by`        VARCHAR(10)     NULL,             -- 使用者工號
  `created_at`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

  `synced_at`         DATETIME        NULL,
  `sync_status`       TINYINT         NOT NULL DEFAULT 0,
  -- 0: 待同步、1: 已被 EIP 使用並回寫主 DB、2: 被捨棄（同 token 其它版本）

  PRIMARY KEY (`snapshot_id`),
  UNIQUE KEY `ux_rms_id` (`rms_id`),
  KEY `ix_snapshot_token` (`document_token`),
  KEY `ix_snapshot_sync` (`sync_status`,`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
