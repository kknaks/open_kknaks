-- enqueue.lua: Atomically store task data and add to queue
-- KEYS[1] = {ns}:task:{task_id}     (task hash)
-- KEYS[2] = {ns}:queue:{queue_name} (main queue sorted set)
-- KEYS[3] = {ns}:queue:{queue_name}.delayed (delayed queue sorted set)
-- KEYS[4] = {ns}:stream:{task_id}   (chunk stream)
-- ARGV[1] = task_id
-- ARGV[2] = task JSON
-- ARGV[3] = score (priority * 1e12 + timestamp_ms)
-- ARGV[4] = delay_until_score (0 if no delay)

-- Store task data first (HSET before ZADD for atomicity)
redis.call("HSET", KEYS[1], "data", ARGV[2])

-- A queued task must never expire mid-flight. HSET/XADD do not clear an existing TTL,
-- so drop any TTL left over from a previous terminal state (ack/nack) on retry.
redis.call("PERSIST", KEYS[1])
redis.call("PERSIST", KEYS[4])

local delay_score = tonumber(ARGV[4])
if delay_score > 0 then
    -- Delayed task: add to delayed sorted set
    redis.call("ZADD", KEYS[3], delay_score, ARGV[1])
else
    -- Immediate task: add to main queue
    redis.call("ZADD", KEYS[2], tonumber(ARGV[3]), ARGV[1])
end

return 1
