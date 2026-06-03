-- 1. Disable safe mode
SET SQL_SAFE_UPDATES = 0;

-- 2. Run your delete query
DELETE a
FROM sfdb.rms_assets a
LEFT JOIN sfdb.rms_asset_links l 
  ON l.asset_id = a.asset_id
WHERE l.asset_id IS NULL;

-- 3. (Good Practice) Re-enable safe mode
SET SQL_SAFE_UPDATES = 1;