use sfdb;

INSERT INTO `rms_conditions` (`condition_name`, `enable`) VALUES ('銅電式樣', 1);
INSERT INTO `rms_condition_parameters` (`condition_id`, `parameter_name`, `enable`) VALUES
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅電式樣'), '全鍍', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅電式樣'), '多層板內外層', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅電式樣'), '局部銅電鍍', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅電式樣'), '雙面板無鍍銅品', 1);

INSERT INTO `rms_conditions` (`condition_name`, `enable`) VALUES ('製品式樣', 1);
INSERT INTO `rms_condition_parameters` (`condition_id`, `parameter_name`, `enable`) VALUES
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '雙面板', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '多層板外層', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '雙面板無鍍銅品', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '多層板內外層', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '多層板內外層局部銅電鍍品', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '無鍍銅品', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '多層板', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '多層板外層線路', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '多層板外層局部銅電鍍品', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '全板銅電鍍品', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '局部銅電鍍品', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '多層板內層', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '單面板', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), 'FP品目', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '製品式樣'), '單面板雙面銅材無鍍銅', 1);

INSERT INTO `rms_conditions` (`condition_name`, `enable`) VALUES ('流程', 1);
INSERT INTO `rms_condition_parameters` (`condition_id`, `parameter_name`, `enable`) VALUES
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '流程'), 'RTR', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '流程'), 'RTS', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '流程'), 'SBS', 1);

INSERT INTO `rms_conditions` (`condition_name`, `enable`) VALUES ('原銅厚度', 1);
INSERT INTO `rms_condition_parameters` (`condition_id`, `parameter_name`, `enable`) VALUES
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '原銅厚度'), '1', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '原銅厚度'), '1/2', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '原銅厚度'), '1/3', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '原銅厚度'), '1/4', 1);

INSERT INTO `rms_conditions` (`condition_name`, `enable`) VALUES ('鍍銅厚度', 1);
INSERT INTO `rms_condition_parameters` (`condition_id`, `parameter_name`, `enable`) VALUES
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '鍍銅厚度'), '8', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '鍍銅厚度'), '10', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '鍍銅厚度'), '12', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '鍍銅厚度'), '14', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '鍍銅厚度'), '15', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '鍍銅厚度'), '18', 1);

INSERT INTO `rms_conditions` (`condition_name`, `enable`) VALUES ('銅材種類', 1);
INSERT INTO `rms_condition_parameters` (`condition_id`, `parameter_name`, `enable`) VALUES
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅材種類'), 'ED銅', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅材種類'), '非HA銅', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅材種類'), 'HA銅', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅材種類'), 'LCP材', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅材種類'), 'LCP', 1);

INSERT INTO `rms_conditions` (`condition_name`, `enable`) VALUES ('乾膜種類', 1);
INSERT INTO `rms_condition_parameters` (`condition_id`, `parameter_name`, `enable`) VALUES
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '乾膜種類'), 'ADC-301', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '乾膜種類'), 'FF-1030', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '乾膜種類'), 'HS-930', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '乾膜種類'), 'HW-630', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '乾膜種類'), 'AQ-209A', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '乾膜種類'), 'HY-920', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '乾膜種類'), 'ADW-401', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '乾膜種類'), 'H-9540', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '乾膜種類'), 'FF-1040', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '乾膜種類'), 'FF-1020', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '乾膜種類'), 'AQ-1558', 1);

INSERT INTO `rms_conditions` (`condition_name`, `enable`) VALUES ('銅材疊構厚度：CU/PI/CU、CU/PI', 1);
INSERT INTO `rms_condition_parameters` (`condition_id`, `parameter_name`, `enable`) VALUES
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅材疊構厚度：CU/PI/CU、CU/PI'), '0.5oz', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅材疊構厚度：CU/PI/CU、CU/PI'), '1oz', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅材疊構厚度：CU/PI/CU、CU/PI'), '2oz', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅材疊構厚度：CU/PI/CU、CU/PI'), '3oz', 1),
((SELECT `condition_id` FROM `rms_conditions` WHERE `condition_name` = '銅材疊構厚度：CU/PI/CU、CU/PI'), '5oz', 1);
