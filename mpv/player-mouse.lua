-- Anime Hub: what Settings → Player says about the mouse on the picture and
-- keys (Decisions 53, 74), and the fullscreen close button. Loaded beside
-- every player look. Plain bindings, so the user's input.conf still wins,
-- unless Who wins says "These settings"; "own" binds nothing.
local utils = require "mp.utils"

-- Settings → Player → Who wins: "settings" binds forced, over input.conf.
local priority = "config"
local function bind(key, name, fn, opts)
    if priority == "settings" then
        mp.add_forced_key_binding(key, name, fn, opts)
    else
        mp.add_key_binding(key, name, fn, opts)
    end
end

-- Click, Double-click, Right click and Right double-click share one list.
-- "own" binds nothing; "off" binds nothing to do, over mpv's own.
local CLICKS = {
    menu = function() mp.commandv("script-message", "anime-hub-menu", "mouse") end,
    pause = function() mp.commandv("cycle", "pause") end,
    fullscreen = function() mp.commandv("cycle", "fullscreen") end,
    off = function() end,
}
local BUTTONS = { { "MBTN_LEFT", "click", "own" }, { "MBTN_LEFT_DBL", "double", "own" },
    { "MBTN_RIGHT", "right", "menu" }, { "MBTN_RIGHT_DBL", "right_double", "own" } }

local function bind_buttons(prefs)
    for _, b in ipairs(BUTTONS) do
        local name = "hub-" .. b[2]
        mp.remove_key_binding(name)
        local fn = CLICKS[prefs[b[2]] or b[3]]
        if fn then bind(b[1], name, fn) end
    end
end
-- Before the hub has said anything: right click opens the menu.
bind_buttons({})

local function apply(prefs)
    priority = prefs.priority == "settings" and "settings" or "config"
    bind_buttons(prefs)
    for _, name in ipairs({ "hub-wheel-up", "hub-wheel-down" }) do
        mp.remove_key_binding(name)
    end
    if prefs.wheel == "volume" then
        bind("WHEEL_UP", "hub-wheel-up", function() mp.commandv("add", "volume", "5") end)
        bind("WHEEL_DOWN", "hub-wheel-down", function() mp.commandv("add", "volume", "-5") end)
    elseif prefs.wheel == "seek" then
        local step = tostring(tonumber(prefs.skip) or 10)
        bind("WHEEL_UP", "hub-wheel-up", function() mp.commandv("seek", step, "relative+exact") end)
        bind("WHEEL_DOWN", "hub-wheel-down", function() mp.commandv("seek", "-" .. step, "relative+exact") end)
    end
end

-- Settings → mpv → Keys: only the keys the user moved, as plain bindings
-- (input.conf still wins). Each matches what mpv's own default key does.
local ACTIONS = {
    pause = { "cycle", "pause" }, back = { "seek", "-5" }, forward = { "seek", "5" },
    vol_up = { "add", "volume", "2" }, vol_down = { "add", "volume", "-2" }, mute = { "cycle", "mute" },
    fullscreen = { "cycle", "fullscreen" }, subs = { "cycle", "sub" }, audio = { "cycle", "audio" },
    speed_up = { "multiply", "speed", "1.1" }, speed_down = { "multiply", "speed", "0.9090909" },
    screenshot = { "screenshot" }, menu = { "script-message", "anime-hub-menu" }, quit = { "quit" },
}
local REPEATS = { back = true, forward = true, vol_up = true, vol_down = true, speed_up = true, speed_down = true }
local key_names = {}
local unpack_ = table.unpack or unpack

local function apply_keys(keys)
    for _, name in ipairs(key_names) do mp.remove_key_binding(name) end
    key_names = {}
    if type(keys) ~= "table" then return end
    for _, key in ipairs(type(keys.free) == "table" and keys.free or {}) do
        local name = "hub-free-" .. #key_names
        bind(key, name, function() end)
        key_names[#key_names + 1] = name
    end
    for action, key in pairs(type(keys.bind) == "table" and keys.bind or {}) do
        local cmd = ACTIONS[action]
        if cmd and type(key) == "string" and key ~= "" then
            local name = "hub-key-" .. action
            bind(key, name, function() mp.commandv(unpack_(cmd)) end,
                { repeatable = REPEATS[action] or false })
            key_names[#key_names + 1] = name
        end
    end
end

mp.register_script_message("anime-hub-info", function(raw)
    local data = utils.parse_json(raw or "")
    if type(data) == "table" and type(data.prefs) == "table" then
        apply(data.prefs)
        apply_keys(data.prefs.keys)
    end
end)

-- The close button: fullscreen has no window corner to close from, so resting
-- the mouse in the top right corner brings one up.
local ZONE, HIT, INSET, FRAME = 110, 56, 22, 0.008
local close = { v = 0, from = 0, to = 0, t0 = -10, dur = 0.26, hot = 0 }
local layer = mp.create_osd_overlay("ass-events")
layer.z = 30
local screen = { w = 1280, scale = 1, x = 0, y = 0, hover = false }
local timer

local function curve_x(t) return 3 * (1 - t) ^ 2 * t * 0.32 + t ^ 3 end
local function curve_y(t) return 3 * (1 - t) ^ 2 * t * 0.72 + 3 * (1 - t) * t ^ 2 + t ^ 3 end

local function apple(x)
    -- cubic-bezier(.32, .72, 0, 1), as the skip button moves
    local lo, hi = 0, 1
    for _ = 1, 14 do
        local mid = (lo + hi) / 2
        if curve_x(mid) < x then lo = mid else hi = mid end
    end
    return curve_y((lo + hi) / 2)
end

local function now_at()
    local k = math.min(1, (mp.get_time() - close.t0) / close.dur)
    return k >= 1 and close.to or close.from + (close.to - close.from) * apple(k), k < 1
end

local function cross(cx, cy, r, t)
    -- Two strokes, each a thin box on the diagonal.
    local a, b = r * 0.7071, t * 0.7071 / 2
    local function stroke(ax, ay, bx, by, ex, ey, fx, fy)
        return string.format("m %.2f %.2f l %.2f %.2f l %.2f %.2f l %.2f %.2f ",
            cx + ax, cy + ay, cx + bx, cy + by, cx + ex, cy + ey, cx + fx, cy + fy)
    end
    -- Both wind the same way, or their crossing would cancel out.
    return stroke(-a - b, -a + b, -a + b, -a - b, a + b, a - b, a - b, a + b)
        .. stroke(a - b, -a - b, a + b, -a + b, -a + b, a + b, -a - b, a - b)
end

local function draw(v)
    -- Only the X, tucked into the corner: a soft shadow keeps it readable.
    local s = (0.8 + 0.2 * v) * (1 + 0.15 * close.hot)
    local function see(a) return math.floor(255 - (255 - a) * v + 0.5) end
    layer.res_x, layer.res_y = screen.w, 720
    layer.data = string.format("{\\an7\\pos(0,0)\\bord1\\shad0\\blur2\\1c&HFFFFFF&\\3c&H000000&\\1a&H%02X&\\3a&H%02X&\\p1}%s{\\p0}",
        see(0x40 * (1 - close.hot)), see(0x70), cross(screen.w - INSET, INSET, 10 * s, 2.4))
    layer:update()
end

local function over()
    -- The whole corner counts: throw the mouse at it and you are on the X.
    return close.to == 1 and screen.x >= screen.w - HIT and screen.y <= HIT
end

local bound = false

local function tick()
    timer = nil
    local v, moving = now_at()
    local hot = over() and 1 or 0
    if math.abs(hot - close.hot) < 0.01 then close.hot = hot else
        close.hot, moving = close.hot + (hot - close.hot) * 0.2, true
    end
    if v <= 0 and close.to == 0 then
        layer:remove()
        return
    end
    draw(v)
    if moving then timer = mp.add_timeout(FRAME, tick) end
end

local function update()
    local want = mp.get_property_bool("fullscreen") and screen.hover
        and screen.x >= screen.w - ZONE and screen.y <= ZONE and 1 or 0
    if want ~= close.to then
        close.from, close.to, close.t0, close.dur = (now_at()), want, mp.get_time(), want == 1 and 0.26 or 0.2
    end
    if over() ~= bound then
        bound = over()
        if bound then
            mp.add_forced_key_binding("MBTN_LEFT", "hub-close", function() mp.commandv("quit") end)
        else
            mp.remove_key_binding("hub-close")
        end
    end
    if not timer then tick() end
end

mp.observe_property("osd-dimensions", "native", function(_, d)
    if type(d) == "table" and d.h and d.h > 0 then
        screen.w, screen.scale = 720 * d.w / d.h, d.h / 720
        update()
    end
end)
mp.observe_property("mouse-pos", "native", function(_, m)
    if type(m) == "table" then
        screen.x, screen.y, screen.hover = m.x / screen.scale, m.y / screen.scale, m.hover ~= false
        update()
    end
end)
mp.observe_property("fullscreen", "bool", function() update() end)
