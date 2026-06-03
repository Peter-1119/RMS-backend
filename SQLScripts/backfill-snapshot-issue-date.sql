-- 回填快照 document_row 的 issue_date = 該快照 rms_document_snapshots.created_at
--
-- 背景：舊的「下載」快照 writer（_create_snapshot_and_oracle_row 修正前）把 document_row 存成
--       前端 attribute 陣列(ARRAY)，沒有 issue_date 欄；簽核流程建立的 OBJECT 快照本來就有真實 issue_date。
-- 規則：只補「目前缺 issue_date」的快照（WHERE ... IS NULL），不覆蓋已有真實 issue_date 的 1152 筆。
-- 格式：寫成 ISO 'YYYY-MM-DDTHH:MM:SS'（與後端 _normalize_for_json 一致，reader 可 fromisoformat 解析）。
-- 同時支援 document_row 為 OBJECT($.issue_date) 與 ARRAY($[0].issue_date) 兩種型態。
-- 冪等：再跑一次只會處理仍為 NULL 的列。

UPDATE rms_document_snapshot_payloads p
JOIN rms_document_snapshots s ON p.snapshot_id = s.snapshot_id
SET p.document_row =
    CASE JSON_TYPE(p.document_row)
        WHEN 'ARRAY' THEN JSON_SET(p.document_row, '$[0].issue_date', DATE_FORMAT(s.created_at, '%Y-%m-%dT%H:%i:%s'))
        ELSE             JSON_SET(p.document_row, '$.issue_date',    DATE_FORMAT(s.created_at, '%Y-%m-%dT%H:%i:%s'))
    END
WHERE JSON_EXTRACT(
        p.document_row,
        CASE JSON_TYPE(p.document_row) WHEN 'ARRAY' THEN '$[0].issue_date' ELSE '$.issue_date' END
      ) IS NULL;
