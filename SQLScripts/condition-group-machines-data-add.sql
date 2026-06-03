use sfdb;

INSERT INTO `rms_condition_groups` (`condition_id`, `group_id`, `group_name`) VALUES
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅電式樣'), 'M-R26G', 'SBS LPSM 顯影線'),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅電式樣'), 'M-L26B', 'TCSM 連續印刷'),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅電式樣'), 'M-R26C', 'SBS LPSM 自動印刷機');
	
INSERT INTO `rms_group_machines` (`condition_id`, `group_id`, `machine_id`, `machine_name`) VALUES
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅電式樣'), 'M-R26G', 'R26G01', 'SBS LPSM 顯影線-01'),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅電式樣'), 'M-L26B', 'L26R02', 'RTR TCSM 印刷線-01'),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅電式樣'), 'M-R26C', 'R26C02', 'SBS LPSM 自動印刷機-02'),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅電式樣'), 'M-R26C', 'R26C03', 'SBS LPSM 自動印刷機-03'),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅電式樣'), 'M-R26C', 'R26C04', 'SBS LPSM 自動印刷機-04');

