-- Everything recorded for one request id or application id (parameter :id)
SELECT seq, ts, event_type, request_id, subject_id, payload
FROM audit_events
WHERE request_id = :id OR subject_id = :id
ORDER BY seq;
