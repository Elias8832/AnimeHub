-- Anime Hub: what Settings → mpv says about the mouse on the picture,
-- Decision 53. Loaded beside every player look. Plain bindings, so the user's
-- input.conf still wins, and "own" binds nothing.
local utils = require "mp.utils"

local click = "own"

-- Bound from the start, so the skip button's own binding, added later, wins
-- while it shows.
mp.add_key_binding("MBTN_LEFT", "hub-picture-click", function()
    if click == "pause" then mp.commandv("cycle", "pause") end
end)

local function apply(prefs)
    click = prefs.click
    for _, name in ipairs({ "hub-picture-double", "hub-wheel-up", "hub-wheel-down" }) do
        mp.remove_key_binding(name)
    end
    if prefs.double == "fullscreen" then
        mp.add_key_binding("MBTN_LEFT_DBL", "hub-picture-double", function() mp.commandv("cycle", "fullscreen") end)
    elseif prefs.double == "off" then
        mp.add_key_binding("MBTN_LEFT_DBL", "hub-picture-double", function() end)
    end
    if prefs.wheel == "volume" then
        mp.add_key_binding("WHEEL_UP", "hub-wheel-up", function() mp.commandv("add", "volume", "5") end)
        mp.add_key_binding("WHEEL_DOWN", "hub-wheel-down", function() mp.commandv("add", "volume", "-5") end)
    elseif prefs.wheel == "seek" then
        local step = tostring(tonumber(prefs.skip) or 10)
        mp.add_key_binding("WHEEL_UP", "hub-wheel-up", function() mp.commandv("seek", step, "relative+exact") end)
        mp.add_key_binding("WHEEL_DOWN", "hub-wheel-down", function() mp.commandv("seek", "-" .. step, "relative+exact") end)
    end
end

mp.register_script_message("anime-hub-info", function(raw)
    local data = utils.parse_json(raw or "")
    if type(data) == "table" and type(data.prefs) == "table" then apply(data.prefs) end
end)
