-- Documents submitted by more than one applicant (identities are salted hashes, never NRICs)
SELECT CASE WHEN key LIKE 'sha:%' THEN 'identical file' ELSE 'same content' END AS matched_on,
       COUNT(DISTINCT identity_hash)   AS applicants,
       group_concat(application_id, ', ') AS application_ids
FROM document_registry
GROUP BY key
HAVING COUNT(DISTINCT identity_hash) > 1
ORDER BY applicants DESC, application_ids
LIMIT 10;
