-- Which rule / prompt versions made decisions, and when: answers "what policy applied to this customer?"
SELECT event_type,
       COALESCE(json_extract(payload, '$.rules_version'), json_extract(payload, '$.prompt_version')) AS version,
       COUNT(*)  AS decisions,
       MIN(ts)   AS first_seen,
       MAX(ts)   AS last_seen
FROM audit_events
GROUP BY event_type, version
ORDER BY event_type, first_seen;
