USE sfdb4070;

CREATE TABLE `rms_document_attributes` (
    `document_type` int,
    `EIP_id` char(30),
    `status` int,
    `document_token` char(36),
    `previous_document_token` char(36),
    `document_id` char(30),
    `document_name` varchar(200),  -- 這裡已修改為 varchar(200)
    `document_version` numeric(5,2),
    `attribute` JSON,
    `department` varchar(30),
    `author_id` varchar(10),
    `author` varchar(30),
    `approver` varchar(30),
    `confirmer` varchar(30),
    `rejecter` varchar(30),
    `issue_date` datetime,
    `change_reason` varchar(255),
    `change_summary` varchar(255),
    `reject_reason` varchar(255),
    `purpose` varchar(255),
    `create_date` DATETIME DEFAULT CURRENT_TIMESTAMP, -- 直接在這裡新增，系統會自動帶入當下時間
    PRIMARY KEY (`document_token`),
    UNIQUE KEY `ux_document_token` (`document_token`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE `rms_document_attributes` ADD COLUMN `create_date` DATETIME DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE `rms_document_attributes` MODIFY COLUMN `document_name` VARCHAR(200);