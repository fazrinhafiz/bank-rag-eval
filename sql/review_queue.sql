-- Manual review queue, oldest first, with the rules that sent each document there
SELECT seq,
       substr(ts, 1, 10)                     AS received,
       subject_id                            AS application_id,
       json_extract(payload, '$.doc_type')   AS doc_type,
       json_extract(payload, '$.rules')      AS rules
FROM audit_events
WHERE event_type = 'document_decision'
  AND json_extract(payload, '$.decision') = 'manual_review'
ORDER BY ts, seq
LIMIT 10;
