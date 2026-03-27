-- requeue.lua: Return task from active to main queue
-- KEYS[1] = {ns}:queue:{queue_name}.active  (active set)
-- KEYS[2] = {ns}:queue:{queue_name}         (main queue sorted set)
-- ARGV[1] = task_id
-- ARGV[2] = score (original priority score)

redis.call("SREM", KEYS[1], ARGV[1])
redis.call("ZADD", KEYS[2], tonumber(ARGV[2]), ARGV[1])
return 1
