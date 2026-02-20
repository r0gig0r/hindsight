# Database Cleanup Recommendations for OpenClaw Bank

These SQL queries clean up noisy/low-value facts that accumulated before the retain pipeline improvements. They target the `openclaw` bank specifically. **Review results before running DELETE — these are recommendations, not automated scripts.**

## Prerequisites

```bash
# Connect to the openclaw pg0 instance
PGPASSWORD=hindsight ~/.pg0/installation/18.1.0/bin/psql -h localhost -p 5432 -U hindsight -d hindsight
```

All queries below assume the default schema. Adjust if using multi-tenant schemas.

## 1. Remove cron workflow instruction leaks

Facts extracted from cron prompt templates that describe workflow steps rather than actual knowledge.

```sql
-- Preview: count affected rows
SELECT COUNT(*) FROM memory_units
WHERE bank_id = 'openclaw'
  AND type = 'world'
  AND (
    text ILIKE '%workflow%step%'
    OR text ILIKE '%cron%prompt%'
    OR text ILIKE '%you are an AI assistant%'
    OR text ILIKE '%check for any new messages%'
    OR text ILIKE '%summarize the conversation%'
    OR text ILIKE '%provide a brief summary%'
  );

-- Delete (run after reviewing preview)
DELETE FROM memory_units
WHERE bank_id = 'openclaw'
  AND type = 'world'
  AND (
    text ILIKE '%workflow%step%'
    OR text ILIKE '%cron%prompt%'
    OR text ILIKE '%you are an AI assistant%'
    OR text ILIKE '%check for any new messages%'
    OR text ILIKE '%summarize the conversation%'
    OR text ILIKE '%provide a brief summary%'
  );
```

## 2. Remove markdown-bloated observations

Observations where the text is mostly markdown formatting (headers, bold, bullets) with little actual content. These were generated before the retain pipeline's markdown stripping fix.

```sql
-- Preview: find observations with excessive markdown
SELECT id, LEFT(text, 200) AS preview, LENGTH(text) AS len
FROM memory_units
WHERE bank_id = 'openclaw'
  AND type = 'observation'
  AND (
    text LIKE '## %'
    OR text LIKE '# %'
    OR (LENGTH(text) - LENGTH(REPLACE(REPLACE(text, '**', ''), '- ', ''))) > LENGTH(text) * 0.15
  )
ORDER BY created_at DESC
LIMIT 50;

-- Count
SELECT COUNT(*) FROM memory_units
WHERE bank_id = 'openclaw'
  AND type = 'observation'
  AND (text LIKE '## %' OR text LIKE '# %');
```

## 3. Remove near-duplicate world facts

Find clusters of facts that say essentially the same thing (e.g., multiple variants of "Igor is the CTO").

```sql
-- Find potential duplicates by checking common prefixes
SELECT LEFT(text, 60) AS prefix, COUNT(*) AS cnt, array_agg(id) AS ids
FROM memory_units
WHERE bank_id = 'openclaw'
  AND type = 'world'
GROUP BY LEFT(text, 60)
HAVING COUNT(*) > 1
ORDER BY cnt DESC
LIMIT 30;
```

For each cluster, keep the most recent (or most complete) version and delete the rest:

```sql
-- Example: keep the newest, delete older duplicates in a cluster
-- Replace the prefix with actual values from the query above
DELETE FROM memory_units
WHERE bank_id = 'openclaw'
  AND type = 'world'
  AND text ILIKE 'Igor is the CTO%'
  AND id NOT IN (
    SELECT id FROM memory_units
    WHERE bank_id = 'openclaw'
      AND type = 'world'
      AND text ILIKE 'Igor is the CTO%'
    ORDER BY created_at DESC
    LIMIT 1
  );
```

## 4. Remove very short/trivial facts

Facts under ~20 characters that are too vague to be useful.

```sql
-- Preview
SELECT id, text FROM memory_units
WHERE bank_id = 'openclaw'
  AND type = 'world'
  AND LENGTH(text) < 20
ORDER BY created_at DESC;

-- Delete after review
DELETE FROM memory_units
WHERE bank_id = 'openclaw'
  AND type = 'world'
  AND LENGTH(text) < 20;
```

## 5. Verify cleanup results

```sql
-- Memory count by type after cleanup
SELECT type, COUNT(*) as count, AVG(LENGTH(text))::int as avg_len
FROM memory_units
WHERE bank_id = 'openclaw'
GROUP BY type
ORDER BY count DESC;
```
