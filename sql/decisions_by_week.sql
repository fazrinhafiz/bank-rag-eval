-- Document decisions per ISO week: is the review queue growing?
SELECT strftime('%Y-W%W', ts)                                    AS week,
       COUNT(*)                                                   AS documents,
       SUM(json_extract(payload, '$.decision') = 'manual_review') AS to_review,
       SUM(json_extract(payload, '$.decision') = 'reject')        AS rejected
FROM audit_events
WHERE event_type = 'document_decision'
GROUP BY week
ORDER BY week;
