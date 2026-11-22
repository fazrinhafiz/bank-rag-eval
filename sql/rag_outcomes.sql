-- Assistant outcomes by model and prompt version: answered, abstained, blocked
SELECT json_extract(payload, '$.model')           AS model,
       json_extract(payload, '$.prompt_version')  AS prompt_version,
       COUNT(*)                                   AS questions,
       SUM(json_extract(payload, '$.outcome') = 'answered')  AS answered,
       SUM(json_extract(payload, '$.outcome') = 'abstained') AS abstained,
       SUM(json_extract(payload, '$.outcome') = 'blocked')   AS blocked,
       ROUND(AVG(json_extract(payload, '$.confidence')), 3)   AS mean_confidence
FROM audit_events
WHERE event_type = 'rag_decision'
GROUP BY model, prompt_version;
