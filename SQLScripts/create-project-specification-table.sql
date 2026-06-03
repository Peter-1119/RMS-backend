CREATE TABLE rms_spec_flat (
  id INT AUTO_INCREMENT PRIMARY KEY,
  dept_code VARCHAR(20) NOT NULL,
  work_center_name VARCHAR(100) NOT NULL,
  spec_code VARCHAR(50) NOT NULL,
  spec_name VARCHAR(200) NOT NULL,
  project VARCHAR(100) NOT NULL,
  UNIQUE KEY uq_row (dept_code, work_center_name, spec_code, project)
);