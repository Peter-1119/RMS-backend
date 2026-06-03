use sfdb4070;

-- 1. Create the 'rms_conditions' table.
-- 'auto_increment' must be specified in the column definition.
create table `rms_conditions`(
	`condition_id` int auto_increment,
    `condition_name` varchar(100) UNIQUE,
    `enable` bool,
    PRIMARY KEY (`condition_id`)
);

-- 2. Create the 'rms_condition_parameters' table.
-- 'FOREIGN KEY' must reference the specific column and table it links to.
-- Also, a primary key or unique index is needed for data integrity.
create table `rms_condition_parameters`(
	`condition_id` int,
    `parameter_name` varchar(100),
    `enable` bool,
    PRIMARY KEY (`condition_id`, `parameter_name`),
    FOREIGN KEY (`condition_id`) REFERENCES `rms_conditions`(`condition_id`) ON DELETE CASCADE
);

-- 3. Create the 'rms_condition_groups' table.
-- 'FOREIGN KEY' reference and a primary key are needed.
create table `rms_condition_groups`(
	`condition_id` int,
    `group_id` varchar(12),
    `group_name` varchar(100),
    PRIMARY KEY(`condition_id`, `group_id`),
    FOREIGN KEY (`condition_id`) REFERENCES `rms_conditions`(`condition_id`) ON DELETE CASCADE
);

-- 4. Create the 'rms_group_machines' table.
-- This tablrms_condition_parametersrms_condition_parametersrms_conditionsrms_conditionsrms_condition_parametersrms_condition_parametersrms_condition_parametersrms_condition_parametersrms_condition_parametersrms_condition_parameterse seems to have a logical flaw, as 'condition_id' is already linked
-- via 'group_id' in a separate table. A better design links directly to the group.
-- Also, a FOREIGN KEY constraint is missing.
create table `rms_group_machines`(
	`condition_id` int,
	`group_id` varchar(12),
    `machine_id` varchar(12),
    `machine_name` varchar(100),
    PRIMARY KEY(`condition_id`, `group_id`, `machine_id`),
    FOREIGN KEY (`condition_id`, `group_id`) REFERENCES `rms_condition_groups`(`condition_id`, `group_id`) ON DELETE CASCADE
);

-- 依 machine_id 快速抓出該機台的所有條件（主力查詢）
CREATE INDEX idx_rgm_mid_cid ON rms_group_machines (machine_id, condition_id);

-- 如果你偶爾需要用 machine_name 落回條件（備援）
CREATE INDEX idx_rgm_mname_cid ON rms_group_machines (machine_name, condition_id);

-- 對「單查條件集合」也有幫助（可選）
CREATE INDEX idx_rgm_cid_mid ON rms_group_machines (condition_id, machine_id);
