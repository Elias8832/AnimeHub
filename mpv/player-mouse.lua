-- Anime Hub: what Settings → mpv says about the mouse on the picture,
-- Decision 53, and the fullscreen close button. Loaded beside every player
-- look. Plain bindings, so the user's input.conf still wins, and "own" binds
-- nothing.
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
