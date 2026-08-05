-- ack.lua: Acknowledge task completion
-- KEYS[1] = {ns}:queue:{queue_name}.active  (active set)
-- KEYS[2] = {ns}:task:{task_id}             (task hash)
-- KEYS[3] = {ns}:stream:{task_id}           (chunk stream)
-- ARGV[1] = task_id
-- ARGV[2] = result_ttl (seconds)

local ttl = tonumber(ARGV[2])

redis.call("SREM", KEYS[1], ARGV[1])
redis.call("EXPIRE", KEYS[2], ttl)
-- Terminal state, so the stream is no longer being appended to; expiring it here is
-- what keeps {ns}:stream:* from growing without bound.
redis.call("EXPIRE", KEYS[3], ttl)
return 1
