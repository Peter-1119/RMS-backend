-- rms_document_snapshot_payloads 新增 form_attributes 欄
-- 對應 docs-design/db-migration-considerations.md §5
-- 用途：簽核時把該文件的 rms_document_form_attributes（標題/目的等彩色樣式）一併凍進快照，
--       sync-eip 寫回時還原回 rms_document_form_attributes。
-- 可為 NULL：舊快照（sfdb4070 無 form_attributes 表）此欄為 NULL，合理。
--
-- ⚠️ 部署順序：本 ALTER 必須先於「會 SELECT/INSERT form_attributes 的後端程式」上線，
--    否則 apply_snapshots / 快照建立會因欄位不存在而失敗。

ALTER TABLE `rms_document_snapshot_payloads`
  ADD COLUMN `form_attributes` JSON NULL AFTER `program_codes_rows`;
