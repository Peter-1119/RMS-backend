CREATE TABLE IF NOT EXISTS `rms_program_code` (
  `id`             INT AUTO_INCREMENT PRIMARY KEY,
  `spec_code`      VARCHAR(50)   NOT NULL,           -- 製程代碼
  `serial_no`      INT           NOT NULL,           -- 流水號 (1,2,3,...)
  `program_code`   VARCHAR(20)   NOT NULL,           -- RE + 前 6 碼 + 3 碼流水
  `document_token` CHAR(36)      NULL,              -- 關聯到哪一份文件 (草稿)
  `status`         TINYINT       NOT NULL DEFAULT 0, -- 0=reserved(草稿), 1=final(正式), 9=released(可重用)
  `created_at`     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
                                 ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY `ux_program_code` (`program_code`),
  KEY `ix_spec_serial` (`spec_code`,`serial_no`),
  KEY `ix_program_doc` (`document_token`),
  CONSTRAINT `fk_program_doc`
    FOREIGN KEY (`document_token`)
    REFERENCES `rms_document_attributes` (`document_token`)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
