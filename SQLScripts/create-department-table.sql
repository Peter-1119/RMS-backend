CREATE TABLE rms_department_process (
    id INT AUTO_INCREMENT PRIMARY KEY,
    department_code VARCHAR(20) NOT NULL COMMENT '= Oracle IDBUSER.RMS_DEPT.DEPT_NO',
    process_code VARCHAR(50) NOT NULL  COMMENT '= SAJET.SYS_PROCESS.PROCESS_DESC',
    process_name VARCHAR(200) NOT NULL COMMENT '快取 PROCESS_NAME，免每次 JOIN Oracle',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_dept_process (department_code, process_code),
    INDEX idx_department (department_code),
    INDEX idx_process (process_code)
);