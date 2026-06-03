CREATE TABLE `rms_references` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `document_token` CHAR(36) NOT NULL,
  `refer_type` TINYINT NOT NULL,
  `refer_document` VARCHAR(64) NOT NULL,
  `refer_document_name` VARCHAR(255) DEFAULT NULL,
  `color` VARCHAR(20) NOT NULL DEFAULT 'black',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ux_ref_unique` (`document_token`,`refer_type`,`refer_document`),
  KEY `ix_ref_type` (`refer_type`),
  KEY `ix_ref_token` (`document_token`),
  CONSTRAINT `fk_ref_doc_token` FOREIGN KEY (`document_token`) REFERENCES `rms_document_attributes` (`document_token`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE `rms_references` ADD COLUMN `color` VARCHAR(20) NOT NULL DEFAULT 'black' AFTER `refer_document_name`;