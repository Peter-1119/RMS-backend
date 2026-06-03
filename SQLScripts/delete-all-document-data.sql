-- SET SQL_SAFE_UPDATES = 0;
-- DELETE t FROM sfdb.rms_document_snapshots AS t WHERE t.document_token = '53653418-34a8-4e3c-9650-58f34b19a0b1';
-- DELETE t FROM sfdb.rms_document_attributes AS t WHERE t.document_token = '53653418-34a8-4e3c-9650-58f34b19a0b1';
-- DELETE t FROM sfdb.rms_block_content AS t WHERE t.document_token = 'a18744ad-ec69-411e-b9c2-c95259391429';
-- DELETE t FROM sfdb.rms_assets AS t WHERE t.document_token = 'a18744ad-ec69-411e-b9c2-c95259391429';
-- DELETE t FROM sfdb.rms_references AS t WHERE t.document_token = 'a18744ad-ec69-411e-b9c2-c95259391429';
-- DELETE t FROM sfdb.rms_program_code AS t WHERE t.document_token = 'a18744ad-ec69-411e-b9c2-c95259391429';
-- SET SQL_SAFE_UPDATES = 1;

SET SQL_SAFE_UPDATES = 0;
DELETE t FROM sfdb.rms_document_snapshots AS t;
DELETE t FROM sfdb.rms_document_snapshot_payloads AS t;
DELETE t FROM sfdb.rms_document_attributes AS t;
DELETE t FROM sfdb.rms_block_content AS t;
DELETE t FROM sfdb.rms_assets AS t;
DELETE t FROM sfdb.rms_references AS t;
DELETE t FROM sfdb.rms_program_code AS t;
SET SQL_SAFE_UPDATES = 1;

-- SET SQL_SAFE_UPDATES = 0;
-- DELETE t FROM sfdb.rms_document_snapshots AS t WHERE document_token <> 'test';
-- DELETE t FROM sfdb.rms_document_attributes AS t WHERE document_token <> 'test';
-- DELETE t FROM sfdb.rms_block_content AS t WHERE document_token <> 'test';
-- DELETE t FROM sfdb.rms_assets AS t WHERE document_token <> 'test';
-- DELETE t FROM sfdb.rms_references AS t WHERE document_token <> 'test';
-- DELETE t FROM sfdb.rms_program_code AS t WHERE document_token <> 'test';
-- SET SQL_SAFE_UPDATES = 1;