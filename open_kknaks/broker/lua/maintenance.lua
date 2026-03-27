-- maintenance.lua: Move delayed tasks that are due to main queue
-- KEYS[1] = {ns}:queue:{queue_name}.delayed  (delayed sorted set)
-- KEYS[2] = {ns}:queue:{queue_name}           (main queue sorted set)
-- ARGV[1] = current_timestamp (seconds since epoch)
-- ARGV[2] = default_score (priority score for promoted tasks)

-- Find all delayed tasks that are due (score <= current_timestamp)
local due = redis.call("ZRANGEBYSCORE", KEYS[1], "-inf", ARGV[1])
local count = 0

for _, task_id in ipairs(due) do
    redis.call("ZREM", KEYS[1], task_id)
    redis.call("ZADD", KEYS[2], tonumber(ARGV[2]), task_id)
    count = count + 1
end

return count
