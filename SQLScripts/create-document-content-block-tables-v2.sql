-- =====================================================================
-- rms_block_content v2 — 階層化（adjacency list）新 schema
-- 對應 docs-design/block-hierarchy-redesign-spec.md §3.1
--
-- 與舊版差異：
--   - 移除：tier_no, sub_no
--   - 新增：parent_id, sort_order, depth
--   - 新增：自參考 FK fk_block_parent (ON DELETE CASCADE) — 刪父自動連帶刪子孫（§9）
--   - unique key 含 step_type（§18 F1）：避免不同 step 的 root 撞號
--
-- 部署方式：新 DB 直接用本檔 CREATE；既有資料由 Python 遷移腳本轉換後寫入
-- （見 modules/block_tree.normalize_legacy_blocks）。
-- =====================================================================

CREATE TABLE `rms_block_content` (
  `content_id`     char(36)  NOT NULL,                 -- PK，後端產生
  `document_token` char(36)  NOT NULL,
  `step_type`      int       NOT NULL,                 -- L1 章節（root 分組鍵），見 spec §5.3
  `parent_id`      char(36)  DEFAULT NULL,             -- NULL = step root 的直接子節點 (L2)
  `sort_order`     int       NOT NULL,                 -- 同一 parent 下 1-based 排序
  `depth`          int       NOT NULL,                 -- 2..8（L1=step 本身不存 row）
  `content_type`   int       NOT NULL,                 -- option：0無 / 1文字 / 2表格 / 3插入文件 / 4參數表
  `header_text`    text,                               -- 標題純文字鏡像
  `header_json`    json      DEFAULT NULL,             -- 標題 rich text
  `content_text`   longtext,                           -- 內文純文字鏡像
  `content_json`   json      DEFAULT NULL,             -- 內文 rich text
  `table_text`     longtext,                           -- 表格純文字鏡像
  `table_json`     json      DEFAULT NULL,             -- ct=2 單表 / ct=4 { parameterTable, conditionTable }
  `files`          json      DEFAULT NULL,             -- [{name,size,path_to_save}] 圖片唯一真相（§14）
  `metadata`       json      DEFAULT NULL,             -- ct=4 放 programs[]；其餘擴充用
  `created_at`     datetime  NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`     datetime  NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`content_id`),
  UNIQUE KEY `ux_block_coord` (`document_token`,`step_type`,`parent_id`,`sort_order`),
  KEY `ix_block_doc` (`document_token`),
  KEY `ix_block_parent` (`parent_id`),
  CONSTRAINT `fk_block_doc`
    FOREIGN KEY (`document_token`) REFERENCES `rms_document_attributes` (`document_token`) ON DELETE CASCADE,
  CONSTRAINT `fk_block_parent`
    FOREIGN KEY (`parent_id`)      REFERENCES `rms_block_content` (`content_id`)            ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------
-- 既有 DB 就地升級（若不走「新 DB」路線，改用以下 ALTER 流程；務必先備份）：
--
--   -- 1) 先加新欄位（可為 NULL，暫不加約束）
--   ALTER TABLE rms_block_content
--     ADD COLUMN parent_id  char(36) DEFAULT NULL AFTER step_type,
--     ADD COLUMN sort_order int      DEFAULT NULL AFTER parent_id,
--     ADD COLUMN depth      int      DEFAULT NULL AFTER sort_order;
--
--   -- 2) 用 Python 遷移腳本（normalize_legacy_blocks）回填 parent_id/sort_order/depth、
--   --    合併 step2 參數/條件、退掉 sub_no，再 UPDATE 回各 row。
--
--   -- 3) 收尾：補約束、移除舊欄位
--   ALTER TABLE rms_block_content
--     MODIFY sort_order int NOT NULL,
--     MODIFY depth      int NOT NULL,
--     DROP INDEX ux_block_coord,
--     ADD UNIQUE KEY ux_block_coord (document_token, step_type, parent_id, sort_order),
--     ADD KEY ix_block_parent (parent_id),
--     ADD CONSTRAINT fk_block_parent FOREIGN KEY (parent_id)
--         REFERENCES rms_block_content (content_id) ON DELETE CASCADE,
--     DROP COLUMN tier_no,
--     DROP COLUMN sub_no;
-- ---------------------------------------------------------------------
