-- nack.lua: Negative acknowledge — move task to DLQ
-- KEYS[1] = {ns}:queue:{queue_name}.active  (active set)
-- KEYS[2] = {ns}:queue:{queue_name}.dlq     (DLQ list)
-- KEYS[3] = {ns}:task:{task_id}             (task hash)
-- KEYS[4] = {ns}:stream:{task_id}           (chunk stream)
-- ARGV[1] = task_id
-- ARGV[2] = dlq_ttl (seconds)

local ttl = tonumber(ARGV[2])

redis.call("SREM", KEYS[1], ARGV[1])
redis.call("RPUSH", KEYS[2], ARGV[1])
-- Failed tasks stay inspectable/retryable for dlq_ttl (longer than result_ttl) instead
-- of living forever. enqueue.lua PERSISTs both keys again if the task is retried.
redis.call("EXPIRE", KEYS[3], ttl)
redis.call("EXPIRE", KEYS[4], ttl)
return 1
