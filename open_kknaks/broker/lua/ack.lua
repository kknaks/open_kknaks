-- ack.lua: Acknowledge task completion
-- KEYS[1] = {ns}:queue:{queue_name}.active  (active set)
-- KEYS[2] = {ns}:task:{task_id}             (task hash)
-- ARGV[1] = task_id
-- ARGV[2] = result_ttl (seconds)

redis.call("SREM", KEYS[1], ARGV[1])
redis.call("EXPIRE", KEYS[2], tonumber(ARGV[2]))
return 1
