-- =====================================================================
-- rms_document_form_attributes
--   主表欄位的 tiptap 樣式 JSON（key-value 設計）
--   key 名稱即 field_name，對齊 rms_document_attributes 的欄位名：
--     - document_name  (本表 column)
--     - applyProject   (存在 attribute JSON 內)
--     - purpose        (本表 column)
--   未來若要支援更多帶樣式的欄位，加 field_name 即可，免改 schema。
--
--   寫入規則:
--     value 非 null → INSERT ... ON DUPLICATE KEY UPDATE
--     value 為 null → DELETE 該 (document_token, field_name) 列
--   讀取規則:
--     依 document_token 撈出所有列，組成 { field_name: style_json } 物件
--     查不到的欄位回 null，前端 fallback 到主表純文字
-- =====================================================================

CREATE TABLE `rms_document_form_attributes` (
  `id`             BIGINT       NOT NULL AUTO_INCREMENT,
  `document_token` CHAR(36)     NOT NULL,
  `field_name`     VARCHAR(64)  NOT NULL COMMENT '對齊主表欄位名：document_name / applyProject / purpose',
  `style_json`     JSON         DEFAULT NULL COMMENT 'tiptap document JSON；null=無樣式',
  `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ux_doc_field` (`document_token`, `field_name`),
  CONSTRAINT `fk_form_attr_doc`
    FOREIGN KEY (`document_token`)
    REFERENCES `rms_document_attributes` (`document_token`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
