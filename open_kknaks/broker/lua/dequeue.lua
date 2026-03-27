-- dequeue.lua: Atomically pop highest-priority task and mark as active
-- KEYS[1] = {ns}:queue:{queue_name}         (main queue sorted set)
-- KEYS[2] = {ns}:queue:{queue_name}.active   (active set)
-- Returns: task_id or nil

local result = redis.call("ZPOPMIN", KEYS[1])
if #result == 0 then
    return nil
end

local task_id = result[1]
redis.call("SADD", KEYS[2], task_id)
return task_id
