-- Document decisions by type: volume, outcome and automation rate
SELECT json_extract(payload, '$.doc_type')                                        AS doc_type,
       COUNT(*)                                                                   AS documents,
       SUM(json_extract(payload, '$.decision') = 'auto_accept')                   AS auto_accept,
       SUM(json_extract(payload, '$.decision') = 'manual_review')                 AS manual_review,
       SUM(json_extract(payload, '$.decision') = 'reject')                        AS reject,
       ROUND(100.0 * SUM(json_extract(payload, '$.decision') <> 'manual_review') / COUNT(*), 1) AS automation_pct
FROM audit_events
WHERE event_type = 'document_decision'
GROUP BY doc_type
ORDER BY doc_type;
