INSERT INTO `rms_document_attribute` (
    `document_type`, `EIP_id`, `document_token`, `previous_document_token`, 
    `document_id`, `document_name`, `document_version`, `attribute`, 
    `department`, `author`, `approver`, `confirmer`, `rejecter`, 
    `issue_date`, `change_reason`, `change_summary`, `reject_reason`, `purpose`
) VALUES (
    0, '0000000000000000', '1111111111111111', NULL, 
    '3333333333333333', 'XX_製造條件指示書', 1.0, 
    '{"適用工程": "工程一", "所選機台": "機台A"}',  -- 這是 JSON 欄位的內容
    '生產部', '張三', '李四', '王五', NULL,
    '2025-09-24 10:00:00', NULL, NULL, NULL, '說明生產流程'
);