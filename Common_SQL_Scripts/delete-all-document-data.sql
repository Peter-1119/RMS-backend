SET SQL_SAFE_UPDATES = 0;
DELETE t FROM sfdb.rms_document_snapshots AS t;
DELETE t FROM sfdb.rms_document_snapshot_payloads AS t;
DELETE t FROM sfdb.rms_document_attributes AS t;
DELETE t FROM sfdb.rms_block_content AS t;
DELETE t FROM sfdb.rms_assets AS t;
DELETE t FROM sfdb.rms_references AS t;
DELETE t FROM sfdb.rms_program_code AS t;
SET SQL_SAFE_UPDATES = 1;