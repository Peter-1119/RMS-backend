CREATE TABLE `rms_document_list` (
  `document_id` varchar(50) DEFAULT NULL,
  `document_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `document_version` decimal(5,2) DEFAULT NULL,
  `style_no` varchar(50) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;