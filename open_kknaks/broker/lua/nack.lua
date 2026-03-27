-- nack.lua: Negative acknowledge — move task to DLQ
-- KEYS[1] = {ns}:queue:{queue_name}.active  (active set)
-- KEYS[2] = {ns}:queue:{queue_name}.dlq     (DLQ list)
-- ARGV[1] = task_id

redis.call("SREM", KEYS[1], ARGV[1])
redis.call("RPUSH", KEYS[2], ARGV[1])
return 1
