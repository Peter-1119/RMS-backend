CREATE TABLE `rms_block_content` (
  `content_id` char(36) NOT NULL,
  `document_token` char(36) NOT NULL,
  `step_type` int NOT NULL,
  `tier_no` int NOT NULL,
  `sub_no` int NOT NULL,
  `content_type` int NOT NULL,
  `header_text` text,
  `header_json` json DEFAULT NULL,
  `content_text` longtext,
  `content_json` json DEFAULT NULL,
  `table_text` longtext,
  `table_json` json DEFAULT NULL,
  `files` json DEFAULT NULL,
  `metadata` json DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`content_id`),
  UNIQUE KEY `ux_block_coord` (`document_token`,`step_type`,`tier_no`,`sub_no`),
  KEY `ix_block_doc` (`document_token`),
  CONSTRAINT `fk_block_doc` FOREIGN KEY (`document_token`) REFERENCES `rms_document_attributes` (`document_token`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `rms_assets` (
  `asset_id`     CHAR(36)       NOT NULL,           -- UUIDv4
  `storage_key`  VARCHAR(255)   NOT NULL,           -- e.g. uploads/temp/2025/10/19/abc.png
  `mime_type`    VARCHAR(100)   NULL,
  `byte_size`    BIGINT         NULL,
  `created_at`   DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`asset_id`),
  UNIQUE KEY `ux_storage_key` (`storage_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `rms_asset_links` (
  `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `asset_id`       CHAR(36)        NOT NULL,
  `document_token` CHAR(36)        NULL,
  `content_id`     CHAR(36)        NULL,
  `created_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_link_asset` (`asset_id`),
  KEY `ix_link_token_content` (`document_token`, `content_id`),
  CONSTRAINT `fk_link_asset`
    FOREIGN KEY (`asset_id`)
    REFERENCES `rms_assets` (`asset_id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_link_doc`
    FOREIGN KEY (`document_token`)
    REFERENCES `rms_document_attributes` (`document_token`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_link_content`
    FOREIGN KEY (`content_id`)
    REFERENCES `rms_block_content` (`content_id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

