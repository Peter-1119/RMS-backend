-- =====================================================================
-- rms_department_process
--   課別-製程 M:N 綁定表（單表設計）
--
-- 設計重點：
--   - 不另建 rms_department 主表：課別權威為 Oracle IDBUSER.RMS_DEPT
--   - department_code 對齊 Oracle RMS_DEPT.DEPT_NO (e.g. KJ1100)
--   - process_code   對齊 SAJET.SYS_PROCESS.PROCESS_DESC
--   - process_name 快取 Oracle 的 PROCESS_NAME，免每次 JOIN
--   - 跨 DB 無法 FK，資料完整性靠應用端：寫入前先確認 Oracle 中存在
--
-- 啟用 / 停用語意：
--   一個課別「有效」= 在本表至少有一筆綁定；清空 = 隱含停用
--   不需要額外狀態欄位
-- =====================================================================

CREATE TABLE `rms_department_process` (
    `id`              INT          NOT NULL AUTO_INCREMENT,
    `department_code` VARCHAR(20)  NOT NULL COMMENT '= Oracle IDBUSER.RMS_DEPT.DEPT_NO',
    `process_code`    VARCHAR(50)  NOT NULL COMMENT '= SAJET.SYS_PROCESS.PROCESS_DESC',
    `process_name`    VARCHAR(200) NOT NULL COMMENT '快取 PROCESS_NAME 避免每次 JOIN Oracle',
    `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_dept_process` (`department_code`, `process_code`),
    INDEX `idx_department` (`department_code`),
    INDEX `idx_process` (`process_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
