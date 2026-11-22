-- Why documents were not auto-accepted: finding codes by decision
SELECT json_extract(e.payload, '$.decision') AS decision,
       c.value                               AS finding,
       COUNT(*)                              AS documents
FROM audit_events AS e, json_each(e.payload, '$.codes') AS c
WHERE e.event_type = 'document_decision'
  AND json_extract(e.payload, '$.decision') <> 'auto_accept'
GROUP BY decision, finding
ORDER BY decision, documents DESC, finding;
