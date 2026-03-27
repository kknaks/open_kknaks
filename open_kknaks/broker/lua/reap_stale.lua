-- reap_stale.lua: Requeue active tasks from a stale worker
-- KEYS[1] = {ns}:queue:{queue_name}.active  (active set)
-- KEYS[2] = {ns}:queue:{queue_name}         (main queue sorted set)
-- ARGV[1] = default_score (priority score for requeued tasks)
--
-- Returns: number of tasks requeued from this active set

local active = redis.call("SMEMBERS", KEYS[1])
local count = 0

for _, task_id in ipairs(active) do
    redis.call("SREM", KEYS[1], task_id)
    redis.call("ZADD", KEYS[2], tonumber(ARGV[1]), task_id)
    count = count + 1
end

return count
