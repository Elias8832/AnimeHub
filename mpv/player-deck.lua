-- Anime Hub: the "Deck" player look, Decision 54. The app's own look in mpv:
-- one slim dark glass row slides up from the bottom, pills everywhere, white for
-- the main action, one soft highlight for "selected", and the keys shown in
-- the app's little badges. The one-row dock, tabbed panels and right-click menu
-- came from Elias's VLC Figma design as layout ideas, not as a style.
-- Nothing is shared with the Soft look: a look is a whole script (Decision 52).
local utils = require "mp.utils"

local H = 720
local DOCK = 44                      -- the dock's height
local PANEL_GAP = 12
local GLASS, GLASS_A = "120F0E", 0x48     -- BGR, like all colours here; the app's --glass-h
local POP, POP_A = "1B1716", 0x14         -- the app's --glass-pop
local PICK_A = 0xDB                       -- white at 14 %, the app's --pick
local WHITE, SOFTTEXT, DIM, INK = "F1F3F3", "C6C9C9", "928C8C", "111111"
local UI, TITLE, MONO, HIDE_AFTER = "Hanken Grotesk", "Barlow Condensed", "JetBrains Mono", 2.5
local EDGE = "\\bord1\\3c&HFFFFFF&\\3a&HE6&"

local st = {
    w = 1280, scale = 1, mouse = { x = 0, y = 0 }, seen = false,
    slide = 0, want = false,               -- 0 hidden, 1 fully up
    glide = { x = 0, a = 0, target = nil },-- the moving highlight
    hits = {}, menu = nil, drag = nil, remaining = false,
}
local info, parts = {}, {}
local thumb
local screen = mp.create_osd_overlay("ass-events")

-- Colours ---------------------------------------------------------------

local function bgr(hex)
    local r, g, b = tostring(hex):match("#(%x%x)(%x%x)(%x%x)")
    return r and (b .. g .. r):upper() or "6FA4F2"
end

local function accent() return bgr(info.accent or "#f2a46f") end

local function blend(a, b, t)
    local out = ""
    for i = 1, 5, 2 do
        local x, y = tonumber(a:sub(i, i + 1), 16), tonumber(b:sub(i, i + 1), 16)
        out = out .. string.format("%02X", math.floor(x + (y - x) * t + 0.5))
    end
    return out
end

-- Shapes, as point lists: clockwise is solid, reversed cuts a hole ----------

local function arc(cx, cy, r, from, to, out, steps)
    for i = 0, steps do
        local t = math.rad(from + (to - from) * i / steps)
        out[#out + 1] = cx + r * math.cos(t)
        out[#out + 1] = cy + r * math.sin(t)
    end
    return out
end

local function path(points, reversed)
    local out, n = {}, #points / 2
    for i = 1, n do
        local k = reversed and n - i + 1 or i
        out[#out + 1] = string.format("%s %.2f %.2f", i == 1 and "m" or "l", points[k * 2 - 1], points[k * 2])
    end
    return table.concat(out, " ") .. " "
end

local function snap(v)
    -- Onto the screen's own pixels: an edge between two pixels looks soft.
    return math.floor(v * st.scale + 0.5) / st.scale
end

local function round_box(x, y, w, h, r, reversed)
    -- Real curves: a corner made of short lines shows its facets at 1440p.
    local l, t, R, b = snap(x), snap(y), snap(x + w), snap(y + h)
    r = math.min(r, (R - l) / 2, (b - t) / 2)
    local c = r * 0.4477
    local f = function(...) return string.format(...) end
    if r <= 0 then
        return reversed and f("m %.2f %.2f l %.2f %.2f l %.2f %.2f l %.2f %.2f ", l, t, l, b, R, b, R, t)
            or f("m %.2f %.2f l %.2f %.2f l %.2f %.2f l %.2f %.2f ", l, t, R, t, R, b, l, b)
    end
    if reversed then
        return f("m %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f "
            .. "l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f ",
            l + r, t, l + c, t, l, t + c, l, t + r, l, b - r, l, b - c, l + c, b, l + r, b,
            R - r, b, R - c, b, R, b - c, R, b - r, R, t + r, R, t + c, R - c, t, R - r, t)
    end
    return f("m %.2f %.2f l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f "
        .. "l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f ",
        l + r, t, R - r, t, R - c, t, R, t + c, R, t + r, R, b - r, R, b - c, R - c, b, R - r, b,
        l + r, b, l + c, b, l, b - c, l, b - r, l, t + r, l, t + c, l + c, t, l + r, t)
end

local function box(x, y, w, h, r) return round_box(x, y, w, h, r) end
local function hole(x, y, w, h, r) return round_box(x, y, w, h, r, true) end

local K = 1   -- icon units to screen units, while an icon is being built

local function along(v, width)
    -- A straight stroke's centre, so both its edges land on whole pixels.
    local dev = v * st.scale
    if math.floor(width * st.scale + 0.5) % 2 == 0 then return math.floor(dev + 0.5) / st.scale end
    return (math.floor(dev) + 0.5) / st.scale
end

local function pen(width, ...)
    -- Round strokes through the given x, y pairs.
    local pts, out = { ... }, {}
    local w = math.max(1, math.floor(width * K * st.scale + 0.5)) / st.scale
    local r = w / 2
    for i = 1, #pts - 3, 2 do
        local x1, y1, x2, y2 = pts[i] * K, pts[i + 1] * K, pts[i + 2] * K, pts[i + 3] * K
        if x1 == x2 then
            x1 = along(x1, w)
            x2 = x1
        elseif y1 == y2 then
            y1 = along(y1, w)
            y2 = y1
        end
        local th = math.deg(math.atan2(y2 - y1, x2 - x1))
        local p = {}
        arc(x2, y2, r, th - 90, th + 90, p, 10)
        arc(x1, y1, r, th + 90, th + 270, p, 10)
        out[#out + 1] = path(p)
    end
    return table.concat(out)
end

local function curve(width, cx, cy, radius, from, to)
    -- One smooth bent stroke with round ends, not many short ones.
    local w = math.max(1, math.floor(width * K * st.scale + 0.5)) / st.scale
    local r, R, p = w / 2, radius * K, {}
    cx, cy = cx * K, cy * K
    arc(cx, cy, R + r, from, to, p, 24)
    arc(cx + R * math.cos(math.rad(to)), cy + R * math.sin(math.rad(to)), r, to, to + 180, p, 10)
    arc(cx, cy, R - r, to, from, p, 24)
    arc(cx + R * math.cos(math.rad(from)), cy + R * math.sin(math.rad(from)), r, from + 180, from + 360, p, 10)
    return path(p)
end

local function blob(points, r)
    -- A filled shape with very round corners.
    local n, p = #points / 2, {}
    if K ~= 1 then
        local scaled = {}
        for i, v in ipairs(points) do scaled[i] = v * K end
        points, r = scaled, r * K
    end
    for i = 1, n do
        local a = (i - 2) % n
        local c = (i - 1) % n
        local b = i % n
        local ax, ay = points[a * 2 + 1], points[a * 2 + 2]
        local x, y = points[c * 2 + 1], points[c * 2 + 2]
        local bx, by = points[b * 2 + 1], points[b * 2 + 2]
        local la, lb = math.sqrt((ax - x) ^ 2 + (ay - y) ^ 2), math.sqrt((bx - x) ^ 2 + (by - y) ^ 2)
        for s = 0, 12 do
            local t = s / 12
            local px = (1 - t) ^ 2 * (x + (ax - x) * r / la) + 2 * t * (1 - t) * x + t ^ 2 * (x + (bx - x) * r / lb)
            local py = (1 - t) ^ 2 * (y + (ay - y) * r / la) + 2 * t * (1 - t) * y + t ^ 2 * (y + (by - y) * r / lb)
            p[#p + 1] = px
            p[#p + 1] = py
        end
    end
    return path(p)
end

-- Icons on a 24 box, one colour each: even outlines with round ends, nothing
-- smaller than the stroke. Built for the exact size and screen they appear on:
-- scaling a finished shape blurs its edges.
local function ibox(x, y, w, h, r) return box(x * K, y * K, w * K, h * K, r * K) end

local function ring(x, y, w, h, r, width)
    -- An outline with the same thickness on all four sides.
    local t = math.max(1, math.floor(width * K * st.scale + 0.5)) / st.scale
    local l, u = snap(x * K), snap(y * K)
    local W, Hh = snap((x + w) * K) - l, snap((y + h) * K) - u
    return box(l, u, W, Hh, r * K) .. hole(l + t, u + t, W - 2 * t, Hh - 2 * t, math.max(0, r * K - t))
end

local function speaker() return blob({ 2.5, 9.5, 6.5, 9.5, 12, 5, 12, 19, 6.5, 14.5, 2.5, 14.5 }, 1.1) end

local function corners()
    return curve(2.2, 8, 8, 4, 180, 270) .. pen(2.2, 4, 8, 4, 9.5) .. pen(2.2, 8, 4, 9.5, 4)
        .. curve(2.2, 16, 8, 4, 270, 360) .. pen(2.2, 20, 8, 20, 9.5) .. pen(2.2, 14.5, 4, 16, 4)
        .. curve(2.2, 8, 16, 4, 90, 180) .. pen(2.2, 4, 14.5, 4, 16) .. pen(2.2, 8, 20, 9.5, 20)
        .. curve(2.2, 16, 16, 4, 0, 90) .. pen(2.2, 20, 14.5, 20, 16) .. pen(2.2, 14.5, 20, 16, 20)
end

local ICON = {
    play = function() return blob({ 6, 3, 22, 12, 6, 21 }, 5) end,
    pause = function() return pen(3.2, 8.5, 5, 8.5, 19) .. pen(3.2, 15.5, 5, 15.5, 19) end,
    back = function() return pen(2.4, 11.5, 6, 5.5, 12, 11.5, 18) .. pen(2.4, 19, 6, 13, 12, 19, 18) end,
    forward = function() return pen(2.4, 12.5, 6, 18.5, 12, 12.5, 18) .. pen(2.4, 5, 6, 11, 12, 5, 18) end,
    speaker = function() return speaker() .. curve(2, 12, 12, 4.5, -38, 38) .. curve(2, 12, 12, 8.5, -38, 38) end,
    quiet = function() return speaker() .. curve(2, 12, 12, 4.5, -38, 38) end,
    muted = function() return speaker() .. pen(2, 16, 9.5, 21, 14.5) .. pen(2, 21, 9.5, 16, 14.5) end,
    subs = function()
        return ring(2, 5, 20, 14, 4.5, 2.2) .. pen(2.2, 6.5, 10.5, 12, 10.5) .. pen(2.2, 6.5, 14, 9, 14) .. pen(2.2, 13, 14, 17.5, 14)
    end,
    wave = function()
        return pen(2.2, 4, 10, 4, 14) .. pen(2.2, 8, 7, 8, 17) .. pen(2.2, 12, 4, 12, 20) .. pen(2.2, 16, 7, 16, 17) .. pen(2.2, 20, 10, 20, 14)
    end,
    tune = function()
        return pen(2.2, 3.5, 7.5, 20.5, 7.5) .. pen(2.2, 3.5, 16.5, 20.5, 16.5) .. ibox(12.5, 4, 7, 7, 3.5) .. ibox(4.5, 13, 7, 7, 3.5)
    end,
    list = function() return pen(2.2, 4, 6, 20, 6) .. pen(2.2, 4, 12, 20, 12) .. pen(2.2, 4, 18, 13, 18) end,
    full = corners,
    next = function() return pen(2.4, 6.5, 6, 12.5, 12, 6.5, 18) .. pen(2.4, 17.5, 6, 17.5, 18) end,
    film = function() return ring(2.5, 4.5, 19, 15, 4.5, 2.2) .. pen(2.2, 9, 12, 15, 12) end,
}

local built = {}

local function icon_shape(name, size)
    -- An even number of screen pixels: the box has a true middle.
    local pixels = math.floor(size * st.scale / 2 + 0.5) * 2
    local key = name .. "/" .. pixels .. "/" .. st.scale
    if not built[key] then
        K = pixels / st.scale / 24
        built[key] = ICON[name]()
        K = 1
    end
    return built[key]
end

-- Writing events --------------------------------------------------------

local function dy() return snap((1 - st.slide) * (DOCK + 24)) end

local function fill(ev, shape, color, a, tags)
    ev[#ev + 1] = string.format("{\\an7\\pos(0,%.2f)\\bord0\\shad0\\1c&H%s&\\1a&H%02X&%s\\p1}%s{\\p0}",
        dy(), color, a or 0, tags or "", shape)
end

local function write(ev, x, y, an, size, color, s, bold, font, a)
    ev[#ev + 1] = string.format("{\\an%d\\pos(%.1f,%.1f)\\fn%s\\fs%d\\b%d\\bord0\\shad0\\1c&H%s&\\1a&H%02X&}%s",
        an, snap(x), snap(y + dy()), font or UI, size, bold and 1 or 0, color, a or 0, s)
end

local function draw_icon(ev, name, cx, cy, color, size)
    size = size or 24
    ev[#ev + 1] = string.format("{\\an7\\pos(%.3f,%.3f)\\bord0\\shad0\\1c&H%s&\\p1}%s{\\p0}",
        snap(cx - size / 2), snap(cy - size / 2 + dy()), color, icon_shape(name, size))
end

local function badge(ev, x, y, key)
    -- The app's key badge, its right edge at x. Returns its left edge.
    local w = math.max(22, 10 + #key * 8)
    fill(ev, box(x - w, y - 11, w, 22, 7), "FFFFFF", 0xE0)
    write(ev, x - w / 2, y, 5, 13, SOFTTEXT, key, false, MONO)
    return x - w
end

local function clean(s)
    return tostring(s or ""):gsub("\\", "\\\\"):gsub("{", "\\{"):gsub("}", "\\}"):gsub("[\r\n]+", " ")
end

local function trim(s, n)
    if #s <= n then return s end
    local cut = n
    while cut > 1 and s:byte(cut) >= 128 and s:byte(cut) < 192 do cut = cut - 1 end
    return s:sub(1, cut - 1) .. "…"
end

local function clock(t)
    if type(t) ~= "number" then return "--:--" end
    t = math.max(0, math.floor(t))
    local h, m, s = math.floor(t / 3600), math.floor(t / 60) % 60, t % 60
    return h > 0 and string.format("%d:%02d:%02d", h, m, s) or string.format("%02d:%02d", m, s)
end

local function inside(x, y, w, h)
    local m = st.mouse
    return m.hover ~= false and m.x >= x and m.x <= x + w and m.y >= y and m.y <= y + h
end

local function hit(x, y, w, h, fields)
    fields.x, fields.y, fields.w, fields.h = x, y, w, h
    st.hits[#st.hits + 1] = fields
end

local moving = false

local function ease(from, to, speed)
    if math.abs(to - from) < 0.5 then return to end
    moving = true
    return from + (to - from) * speed
end

local function settle(from, to, speed)
    if math.abs(to - from) < 0.01 then return to end
    moving = true
    return from + (to - from) * speed
end

-- The menus' content ------------------------------------------------------

local NAMES = { ja = "Japanese", jpn = "Japanese", en = "English", eng = "English", de = "German", ger = "German",
    deu = "German", es = "Spanish", spa = "Spanish", fr = "French", fre = "French", fra = "French", it = "Italian",
    ita = "Italian", pt = "Portuguese", por = "Portuguese", ru = "Russian", rus = "Russian", ar = "Arabic",
    ara = "Arabic", zh = "Chinese", zho = "Chinese", chi = "Chinese", ko = "Korean", kor = "Korean" }

local function open_at(kind, tab, anchor)
    local m = st.menu
    if m and m.tab == tab then
        st.menu = nil
    elseif m and m.kind == kind then
        -- Another button of the same panel: it stays where it is.
        st.menu = { kind = kind, tab = tab, top = 0, anchor = m.anchor, pill = m.pill, body = m.body }
    else
        st.menu = { kind = kind, tab = tab, top = 0, anchor = anchor }
    end
end

local function open_panel(tab, anchor) open_at("panel", tab, anchor) end
local function open_list(tab, anchor) open_at("list", tab, anchor) end

local function tracks(kind, none)
    local rows = {}
    if none then
        rows[1] = { label = none, on = not mp.get_property_native("sid"), run = function() mp.set_property("sid", "no") end, stay = true }
    end
    for _, t in ipairs(mp.get_property_native("track-list") or {}) do
        if t.type == kind then
            local lang = t.lang and NAMES[t.lang:lower()] or t.lang
            local name = lang and t.title and t.title ~= "" and (lang .. " · " .. t.title) or lang or t.title or ("Track " .. t.id)
            rows[#rows + 1] = { label = name, on = t.selected, stay = true,
                run = function() mp.set_property_number(kind == "sub" and "sid" or "aid", t.id) end }
        end
    end
    return rows
end

local function picks(title, prop, list, note)
    local current, items = mp.get_property(prop), {}
    for _, p in ipairs(list) do
        items[#items + 1] = { text = p[1], on = current == p[2] or (tonumber(current) and tonumber(current) == tonumber(p[2])),
            run = function() mp.set_property(prop, p[2]) end }
    end
    return { picks = items, title = title, note = note }
end

local function shift(title, prop, limit)
    local v = mp.get_property_number(prop, 0)
    return { slider = true, title = title, value = v, limit = limit, step = 0.05, shown = string.format("%+.2f", v),
        set = function(n) mp.set_property_number(prop, math.max(-limit, math.min(limit, math.floor(n * 20 + 0.5) / 20))) end }
end

local function sleep_row()
    local minutes, items = st.sleep_minutes or 0, {}
    for _, m in ipairs({ 0, 30, 60, 90 }) do
        items[#items + 1] = { text = m == 0 and "Off" or (m .. "m"), on = minutes == m, run = function()
            st.sleep_minutes, st.sleep_end = m, m > 0 and mp.get_time() + m * 60 or nil
            st.sleep_asked = (st.sleep_asked or 0) + 1
            -- The hub keeps the time, so it survives the next episode. A new
            -- number each pick: the same minutes picked twice still count.
            mp.set_property_native("user-data/anime-hub/sleep", m + st.sleep_asked / 1000)
        end }
    end
    local left = st.sleep_end and math.ceil((st.sleep_end - mp.get_time()) / 60)
    return { picks = items, title = left and left > 0 and ("Sleep " .. left .. "m left") or "Sleep timer" }
end

local CONTENT = {
    Playback = function()
        local ahead = mp.get_property_number("demuxer-cache-duration")
        return { picks("Speed", "speed", { { "0.75×", "0.75" }, { "1×", "1" }, { "1.25×", "1.25" }, { "1.5×", "1.5" }, { "2×", "2" } }), sleep_row(),
            { head = "Loaded ahead: " .. (ahead and clock(ahead) or "--:--") } }
    end,
    Video = function()
        return { picks("Shape", "video-aspect-override", { { "Auto", "-1" }, { "16:9", "16:9" }, { "4:3", "4:3" }, { "21:9", "2.35" } }),
            picks("Fill screen", "panscan", { { "Off", "0" }, { "On", "1" } }),
            { label = "Take a screenshot", key = "s", stay = true, run = function() mp.commandv("screenshot") end } }
    end,
    Audio = function()
        local rows = { { head = "Language" } }
        for _, r in ipairs(tracks("audio")) do rows[#rows + 1] = r end
        rows[#rows + 1] = shift("Delay", "audio-delay", 2)
        return rows
    end,
    Subtitles = function()
        local rows = { { head = "Language" } }
        for _, r in ipairs(tracks("sub", "None")) do rows[#rows + 1] = r end
        rows[#rows + 1] = shift("Delay", "sub-delay", 10)
        return rows
    end,
    Episodes = function()
        local rows = {}
        for _, e in ipairs(info.episodes or {}) do
            local name = "Episode " .. tostring(e.number)
            if type(e.title) == "string" and e.title ~= "" then name = tostring(e.number) .. "  " .. e.title end
            rows[#rows + 1] = { label = name, on = e.id == info.current, faded = e.watched and e.id ~= info.current,
                note = e.online and "online" or nil,
                run = e.id ~= info.current and function() mp.commandv("script-message", "anime-hub-go", e.id) end or nil }
        end
        if #rows == 0 then rows[1] = { head = "No episode list for this one" } end
        return rows
    end,
    Chapters = function()
        local rows, now = {}, mp.get_property_number("chapter", -1)
        for i, c in ipairs(mp.get_property_native("chapter-list") or {}) do
            local name = type(c.title) == "string" and c.title ~= "" and c.title or ("Chapter " .. i)
            rows[#rows + 1] = { label = name, note = clock(c.time), on = i - 1 == now, stay = true,
                run = function() mp.commandv("seek", tostring(c.time), "absolute+exact") end }
        end
        if #rows == 0 then rows[1] = { head = "No chapters in this episode" } end
        return rows
    end,
}

local TABS = { panel = { "Playback", "Video", "Audio", "Subtitles" }, list = { "Episodes", "Chapters" } }

local function row_height(r)
    return r.head and 26 or (r.picks or r.slider) and 36 or 30
end

local function can_focus(r) return r.run or r.picks or r.slider end

local function row_back(ev, m, i, x, y, w, h)
    -- The row the keyboard is on.
    if m.kbd and m.focus == i then fill(ev, box(x, y, w, h, 8), "FFFFFF", 0xE8) end
end

local function draw_picks(ev, r, x, y, w, m, i)
    row_back(ev, m, i, x, y + 1, w, 34)
    write(ev, x + 10, y + 18, 4, 15, SOFTTEXT, clean(r.title))
    local widths, total = {}, 0
    for n, p in ipairs(r.picks) do
        widths[n] = math.max(32, 12 + #p.text * 7.2)
        total = total + widths[n]
    end
    local cx = x + w - total - 4
    fill(ev, box(cx, y + 5, total, 26, 8), "FFFFFF", 0xF0)
    for n, p in ipairs(r.picks) do
        local each = widths[n]
        if p.on then
            fill(ev, box(cx + 1, y + 6, each - 2, 24, 7), "FFFFFF", 0xC8)
        elseif inside(cx, y + 5, each, 26) then
            fill(ev, box(cx + 1, y + 6, each - 2, 24, 7), "FFFFFF", 0xE4)
        end
        write(ev, cx + each / 2, y + 18, 5, 14, (p.on or inside(cx, y + 5, each, 26)) and WHITE or SOFTTEXT, p.text, p.on)
        hit(cx, y + 5, each, 26, { run = p.run, stay = true, menu = true })
        cx = cx + each
    end
end

local function draw_slider(ev, r, x, y, w, m, i)
    row_back(ev, m, i, x, y + 1, w, 34)
    write(ev, x + 10, y + 18, 4, 15, SOFTTEXT, clean(r.title))
    local right = x + w - 6
    write(ev, right - 44, y + 18, 5, 13, r.value == 0 and DIM or WHITE, r.shown, false, MONO)
    hit(right - 66, y + 6, 44, 24, { menu = true, stay = true, run = function() r.set(0) end })
    local plus, minus = right - 11, x + 78
    for _, b in ipairs({ { minus, -1, "−" }, { plus, 1, "+" } }) do
        local on = inside(b[1] - 11, y + 7, 22, 22)
        if on then fill(ev, box(b[1] - 11, y + 7, 22, 22, 7), "FFFFFF", 0xE0) end
        write(ev, b[1], y + 17, 5, 17, on and WHITE or SOFTTEXT, b[3])
        hit(b[1] - 11, y + 7, 22, 22, { menu = true, stay = true, run = function() r.set(r.value + b[2] * r.step) end })
    end
    local sx, sw = minus + 16, right - 70 - 2 - (minus + 16)
    local mid, pos = sx + sw / 2, sx + sw * (r.value + r.limit) / (2 * r.limit)
    fill(ev, box(sx, y + 16, sw, 4, 2), "FFFFFF", 0xD0)
    local from, to = math.min(mid, pos), math.max(mid, pos)
    if to - from > 1 then fill(ev, box(from, y + 16, to - from, 4, 2), accent(), 0) end
    local hot = inside(sx - 6, y + 6, sw + 12, 24) or (st.drag and st.drag.slider == r.title)
    local k = hot and 7 or 5
    fill(ev, box(pos - k, y + 18 - k, k * 2, k * 2, k), "FFFFFF", 0)
    local function move(mx) r.set((math.max(0, math.min(1, (mx - sx) / sw)) * 2 - 1) * r.limit) end
    hit(sx - 6, y + 6, sw + 12, 24, { slider = r.title, drag = move, stay = true, menu = true,
        wheel = function(by) r.set(r.value + by * r.step) end })
end

local function draw_option(ev, r, x, y, w, m, i)
    local over = r.run and inside(x, y, w, 29)
    local focused = m.kbd and m.focus == i
    if r.on or over or focused or r.open then
        fill(ev, box(x, y, w, 29, 8), "FFFFFF", r.on and 0xD8 or 0xE8)
    end
    if r.icon then draw_icon(ev, r.icon, x + 16, y + 14, r.on and WHITE or SOFTTEXT, 16) end
    write(ev, x + (r.icon and 34 or 10), y + 14, 4, 15, (r.on or over) and WHITE or (r.faded and DIM or SOFTTEXT),
        clean(trim(r.label, r.note and 32 or 40)), r.on)
    if r.key then
        badge(ev, x + w - 6, y + 14, r.key)
    elseif r.sub then
        write(ev, x + w - 12, y + 13, 6, 16, DIM, "›")
    elseif r.note then
        write(ev, x + w - 10, y + 14, 6, 13, DIM, clean(r.note), false, MONO)
    end
    if r.run then hit(x, y, w, 29, { run = r.run, stay = r.stay, menu = true }) end
end

local function draw_rows(ev, rows, m, x, y, w, stop)
    m.live = rows
    local top = m.top or 0
    for i = top + 1, #rows do
        local r, h = rows[i], row_height(rows[i])
        if y + h > stop + 1 then return true end
        if r.head then
            write(ev, x + 10, y + 15, 4, 13, DIM, clean(r.head))
        elseif r.picks then
            draw_picks(ev, r, x, y, w, m, i)
        elseif r.slider then
            draw_slider(ev, r, x, y, w, m, i)
        else
            draw_option(ev, r, x, y, w, m, i)
        end
        y = y + h
    end
    return false
end

local function rows_height(rows, from)
    local h = 0
    for i = from or 1, #rows do h = h + row_height(rows[i]) end
    return h
end

local function fit_focus(m)
    -- Keep the keyboard's row in view.
    local rows = m.live
    if not rows or not m.focus or not m.room then return end
    if m.focus <= (m.top or 0) then m.top = m.focus - 1 end
    local used = 0
    for i = (m.top or 0) + 1, m.focus do used = used + row_height(rows[i]) end
    while used > m.room and m.top < m.focus - 1 do
        m.top = m.top + 1
        used = used - row_height(rows[m.top])
    end
end

local function draw_child(ev, m, parent)
    -- A right-click submenu, beside its row like any desktop menu.
    local c = m.child
    local rows = parent.sub()
    local w = 300
    local h = rows_height(rows) + 12
    local x = m.box[1] + m.box[3] + 4
    if x + w > st.w - 8 then x = m.box[1] - w - 4 end
    local y = math.max(8, math.min(H - h - 8, parent.y - 6))
    c.box = { x, y, w, h }
    fill(ev, box(x, y, w, h, 14), POP, POP_A, EDGE)
    hit(x, y, w, h, { menu = true, stay = true, run = function() end })
    draw_rows(ev, rows, c, x + 6, y + 6, w - 12, y + h)
end

local function draw_context(ev, m)
    local rows = m.rows()
    local w, h = 240, rows_height(rows) + 12
    local x, y = math.max(8, math.min(st.w - w - 8, m.x)), math.max(8, math.min(H - h - 8, m.y))
    m.box = { x, y, w, h }
    fill(ev, box(x, y, w, h, 14), POP, POP_A, EDGE)
    hit(x, y, w, h, { menu = true, stay = true, run = function() end })
    local on_child = m.child and m.child.box and inside((table.unpack or unpack)(m.child.box))
    local cy = y + 6
    for i, r in ipairs(rows) do
        r.y = cy
        if not on_child and not m.kbd and inside(x + 6, cy, w - 12, row_height(r)) and i ~= m.open then
            m.open = r.sub and i or nil
            m.child = r.sub and { top = 0 } or nil
        end
        r.open = m.open == i
        cy = cy + row_height(r)
    end
    draw_rows(ev, rows, m, x + 6, y + 6, w - 12, y + h)
    if m.open and rows[m.open] and rows[m.open].sub then draw_child(ev, m, rows[m.open]) end
end

local function draw_menu(ev)
    local m = st.menu
    if not m then return end
    if m.kind == "context" then return draw_context(ev, m) end
    local tabs, rows = TABS[m.kind], CONTENT[m.tab]()
    local w = m.kind == "list" and 360 or 330
    local x = math.max(12, math.min(st.w - w - 12, (m.anchor or st.w) - w / 2))
    local bottom = H - DOCK - PANEL_GAP
    local room = math.min(bottom - 90 - 40 - 12, 396)
    if not m.body then
        -- One height for every tab, so the tabs never move under the mouse.
        m.body = 120
        for _, name in ipairs(tabs) do m.body = math.max(m.body, rows_height(CONTENT[name]())) end
    end
    local body = math.min(room, m.body)
    if not m.placed then
        m.placed = true
        for i, r in ipairs(rows) do
            if r.on and m.kind == "list" then
                m.focus = i
                m.top = math.max(0, i - 4)
            end
        end
    end
    m.room = body
    m.top = math.max(0, math.min(m.top, #rows - 1))
    while m.top > 0 and rows_height(rows, m.top) <= body do m.top = m.top - 1 end
    local h = 40 + body + 12
    local y = bottom - h
    m.box = { x, y, w, h }
    fill(ev, box(x, y, w, h, 16), POP, POP_A, EDGE)
    hit(x, y, w, h, { menu = true, stay = true, run = function() end })
    -- Tabs: the app's nav, one pill gliding to the picked tab.
    local tw = (w - 12) / #tabs
    for i, name in ipairs(tabs) do
        if name == m.tab then
            local tx = x + 6 + (i - 1) * tw
            m.pill = m.pill and ease(m.pill, tx, 0.34) or tx
            fill(ev, box(m.pill, y + 6, tw, 28, 9), "FFFFFF", PICK_A)
        end
    end
    for i, name in ipairs(tabs) do
        local tx, on = x + 6 + (i - 1) * tw, name == m.tab
        write(ev, tx + tw / 2, y + 20, 5, 14, (on or inside(tx, y + 6, tw, 28)) and WHITE or DIM, name, on)
        hit(tx, y + 6, tw, 28, { menu = true, stay = true, run = function()
            m.tab, m.top, m.focus, m.placed = name, 0, nil, false
        end })
    end
    m.more = draw_rows(ev, rows, m, x + 6, y + 40, w - 16, y + 40 + body)
    -- A scroll thumb, so a long list says it is long.
    local all = rows_height(rows)
    if all > body + 1 then
        local before = rows_height(rows) - rows_height(rows, m.top + 1)
        local th = math.max(24, body * body / all)
        local ty = y + 40 + (body - th) * before / math.max(1, all - body)
        fill(ev, box(x + w - 7, ty, 3, th, 1.5), "FFFFFF", 0xB0)
    end
end

-- The seek track ----------------------------------------------------------

local function draw_track(ev, x, y, w, position, duration)
    st.track = { x = x, y = y, w = w, duration = duration }
    local on = (inside(x - 6, y - 10, w + 12, 24) or st.seeking) and not st.menu
    st.fat = settle(st.fat or 0, on and 1 or 0, 0.3)
    local h = 5 + 3 * st.fat
    local cy = y + 2
    local color = accent()
    if not duration or duration <= 0 then
        fill(ev, box(x, cy - h / 2, w, h, h / 2), "FFFFFF", 0xD0)
        return
    end
    local function px(t) return x + w * math.max(0, math.min(1, t / duration)) end
    local here = px(position or 0)
    local bar = box(x, cy - h / 2, w, h, h / 2)
    fill(ev, bar, "FFFFFF", 0xD0)
    local function clipped(tint, a, from, to)
        fill(ev, bar, tint, a, string.format("\\clip(%.1f,0,%.1f,%d)", from, to, H + 200))
    end
    if not info.prefs or info.prefs.loaded ~= "off" then -- what mpv has loaded so far
        for _, r in ipairs(st.loaded or {}) do
            if r["end"] > r.start then clipped("FFFFFF", 0xA4, px(r.start), px(r["end"])) end
        end
    end
    if on and not st.seeking then clipped("FFFFFF", 0xA8, x, math.min(x + w, st.mouse.x)) end
    clipped("FFFFFF", 0, x, here)
    for _, p in ipairs(parts) do
        local a, b = px(p.start), px(p.stop)
        clipped(color, here > a and 0 or 0x70, a, here > a and math.min(b, here) or b)
        if here > a and here < b then clipped(color, 0x70, here, b) end
    end
    for _, c in ipairs(mp.get_property_native("chapter-list") or {}) do
        if c.time > 1 and c.time < duration - 1 then
            fill(ev, box(px(c.time) - 1, cy - h / 2 - 1, 2, h + 2, 0), GLASS, 0x50)
        end
    end
    local k = 4 + 3 * st.fat
    fill(ev, box(here - 13, cy - 13, 26, 26, 13), color, 0x90, "\\blur6")
    fill(ev, box(here - k, cy - k, k * 2, k * 2, k), "FFFFFF", 0)
    hit(x - 6, y - 10, w + 12, 24, {
        drag = function(mx)
            st.seeking = math.max(0, math.min(1, (mx - x) / w)) * duration
            mp.commandv("seek", tostring(st.seeking), "absolute+keyframes")
        end,
        drop = function(mx)
            mp.commandv("seek", tostring(math.max(0, math.min(1, (mx - x) / w)) * duration), "absolute+exact")
            st.seeking = nil
        end,
    })
end

local function part_at(t)
    for _, p in ipairs(parts) do
        if t >= p.start and t < p.stop then return p.kind == "op" and "Opening" or "Ending" end
    end
    local name
    for _, c in ipairs(mp.get_property_native("chapter-list") or {}) do
        if c.time <= t then name = c.title end
    end
    return name ~= "Episode" and name or nil
end

local function draw_bubble(ev)
    local tr = st.track
    local on = tr and tr.duration and tr.duration > 0 and not st.menu
        and inside(tr.x, tr.y - 10, tr.w, 24) and st.slide > 0.95
    if not on then
        if st.thumb_on then
            mp.commandv("script-message-to", "thumbfast", "clear")
            st.thumb_on = false
        end
        return
    end
    local t = (st.mouse.x - tr.x) / tr.w * tr.duration
    local name = part_at(t)
    local label = clock(t) .. (name and name ~= "" and ("  " .. clean(trim(name, 22))) or "")
    local bw = 30 + #label * 8.6
    local x = math.max(bw / 2 + 8, math.min(st.w - bw / 2 - 8, st.mouse.x))
    local y = H - DOCK - 42
    fill(ev, box(x - bw / 2, y, bw, 30, 15), POP, POP_A, EDGE)
    write(ev, x, y + 15, 5, 15, WHITE, label, false, MONO)
    if thumb and thumb.available and thumb.width then
        local tx = math.max(8 * st.scale, math.min(st.w * st.scale - thumb.width - 8 * st.scale, x * st.scale - thumb.width / 2))
        mp.commandv("script-message-to", "thumbfast", "thumb", tostring(t), tostring(math.floor(tx)),
            tostring(math.floor((y - 8) * st.scale - thumb.height)))
        st.thumb_on = true
    end
end

-- The dock ------------------------------------------------------------------

local function draw_dock(ev)
    local w, top = st.w, H - DOCK
    local position = st.seeking or mp.get_property_number("time-pos")
    local duration = mp.get_property_number("duration")
    local here = duration and duration > 0 and (position or 0) / duration or 0

    -- The title sits in the top corner and leaves with the dock.
    local off = dy() * 2
    fill(ev, box(-120, -90 - off, 760, 170, 60), "000000", 0xB8, "\\blur50")
    local title = clean(trim(tostring(info.title or mp.get_property("media-title") or ""), 44)):upper()
    write(ev, 30, 34 - off, 4, 27, WHITE, title, true, TITLE)
    if info.episode then write(ev, 30, 58 - off, 4, 16, SOFTTEXT, clean(trim(tostring(info.episode), 40))) end

    -- The show's colour glows behind the dock, around where you are.
    local rows_y = top + 22
    local time_w = 12 + (math.max(#clock(duration), 5) + 1) * 9.4
    local start_x, rx = 212, w - 16 - 18
    local vol_w = w < 1000 and 0 or 62   -- narrow windows drop the slider
    local vx = rx - 160 - 30 - vol_w
    local speaker_x = vx - 24
    local show_loaded = not (info.prefs and info.prefs.loaded == "off") and st.ahead
    local loaded_w = show_loaded and 52 or 0
    local track_x = start_x + time_w
    local track_w = speaker_x - 24 - time_w - loaded_w - track_x
    fill(ev, box(track_x + track_w * here - 150, top - 12, 300, 26, 13), accent(), 0x9C, "\\blur22")
    fill(ev, box(-40, top, w + 80, DOCK + 60, 16), GLASS, GLASS_A, EDGE)

    local left = clock(position)
    local right = st.remaining and duration and position and ("-" .. clock(duration - position)) or clock(duration)
    write(ev, start_x, rows_y, 4, 15, WHITE, left, false, MONO)
    local right_x = track_x + track_w + 10
    write(ev, right_x, rows_y, 4, 15, inside(right_x, rows_y - 14, time_w, 28) and WHITE or SOFTTEXT, right, false, MONO)
    hit(right_x, rows_y - 14, time_w, 28, { run = function() st.remaining = not st.remaining end })
    if show_loaded then
        write(ev, right_x + time_w - 14, rows_y, 4, 13, DIM, "+" .. clock(st.ahead), false, MONO)
    end
    draw_track(ev, track_x, rows_y - 2, track_w, position, duration)

    -- Button positions first: the glide needs to know where it is going.
    local step = tonumber(info.prefs and info.prefs.skip) or 10
    local mid = 108
    local muted = mp.get_property_bool("mute")
    local function lit(...)
        for _, tab in ipairs({ ... }) do
            if st.menu and st.menu.tab == tab then return true end
        end
    end
    local sound = mp.get_property_number("volume", 100)
    local sound_icon = (muted or sound <= 0) and "muted" or sound < 50 and "quiet" or "speaker"
    local buttons = {
        { id = "back", cx = mid - 66, w = 52, run = function() mp.commandv("seek", tostring(-step), "relative+exact") end },
        { id = "forward", cx = mid + 66, w = 52, run = function() mp.commandv("seek", tostring(step), "relative+exact") end },
        { id = "full", cx = rx, run = function() mp.commandv("cycle", "fullscreen") end },
        { id = "list", cx = rx - 40, run = function() open_list("Episodes", rx - 40) end, lit = st.menu and st.menu.kind == "list" },
        { id = "tune", cx = rx - 80, run = function() open_panel("Playback", rx - 80) end, lit = lit("Playback", "Video") },
        { id = "wave", cx = rx - 120, run = function() open_panel("Audio", rx - 120) end, lit = lit("Audio") },
        { id = "subs", cx = rx - 160, run = function() open_panel("Subtitles", rx - 160) end, lit = lit("Subtitles") },
        { id = sound_icon, cx = speaker_x, run = function() mp.commandv("cycle", "mute") end },
    }
    local aim
    for _, b in ipairs(buttons) do
        local bw = b.w or 36
        if inside(b.cx - bw / 2, rows_y - 18, bw, 36) then aim = b end
    end
    local g = st.glide
    g.a = settle(g.a, aim and 1 or 0, 0.28)
    if aim then
        -- Left and right of the seek bar are two groups: the pill never
        -- crosses it, it fades in on the other side.
        local side = aim.cx < track_x and "left" or "right"
        if g.side ~= side then g.side, g.a = side, 0 end
        if g.a < 0.05 then g.x, g.width = aim.cx, aim.w or 36 end
        g.x = ease(g.x, aim.cx, 0.34)
        g.width = ease(g.width or 36, aim.w or 36, 0.34)
    end
    if g.a > 0.01 then
        local gw = g.width or 36
        fill(ev, box(g.x - gw / 2, rows_y - 16, gw, 32, 16), "FFFFFF", math.floor(255 - 36 * g.a))
    end
    for _, b in ipairs(buttons) do
        local color = (aim == b or b.lit) and WHITE or SOFTTEXT
        if b.lit then fill(ev, box(b.cx - 18, rows_y - 16, 36, 32, 16), "FFFFFF", PICK_A) end
        if b.id == "back" then
            draw_icon(ev, "back", b.cx - 10, rows_y, color, 16)
            write(ev, b.cx + 12, rows_y, 5, 13, color, tostring(step), false, MONO)
        elseif b.id == "forward" then
            draw_icon(ev, "forward", b.cx + 10, rows_y, color, 16)
            write(ev, b.cx - 12, rows_y, 5, 13, color, tostring(step), false, MONO)
        else
            draw_icon(ev, b.id, b.cx, rows_y, color, 20)
        end
        local bw = b.w or 36
        hit(b.cx - bw / 2, rows_y - 18, bw, 36, { run = b.run })
    end

    -- The main action is white, as in the app.
    local paused = mp.get_property_bool("pause")
    local over = inside(mid - 30, rows_y - 16, 60, 32)
    st.pill = settle(st.pill or 0, over and 1 or 0, 0.3)
    local grow = 2 * st.pill
    if st.pill > 0.02 then fill(ev, box(mid - 30, rows_y - 16, 60, 32, 16), "FFFFFF", math.floor(255 - 70 * st.pill), "\\blur8") end
    fill(ev, box(mid - 28 - grow, rows_y - 15, 56 + grow * 2, 30, 15), st.pill > 0.5 and "FFFFFF" or WHITE, 0)
    draw_icon(ev, paused and "play" or "pause", mid, rows_y, INK, 18)
    hit(mid - 30, rows_y - 16, 60, 32, { run = function() mp.commandv("cycle", "pause") end })

    -- Volume: a slim pill that fattens under the mouse.
    local vw = vol_w
    local top_volume = math.max(100, mp.get_property_number("volume-max", 130))
    local volume = mp.get_property_number("volume", 100)
    local level = muted and 0 or math.min(top_volume, volume) / top_volume
    local normal = 100 / top_volume
    if vw == 0 then return end
    local vol_on = inside(vx - 6, rows_y - 14, vw + 12, 28) or (st.drag and st.drag.volume)
    st.vol = settle(st.vol or 0, vol_on and 1 or 0, 0.3)
    local vh = 4 + 2 * st.vol
    -- One bar in one piece: the part above 100 % is the same shape, recoloured.
    fill(ev, box(vx, rows_y - vh / 2, vw, vh, vh / 2), "FFFFFF", 0xD0)
    if level > 0.01 then
        local bar = box(vx, rows_y - vh / 2, vw * level, vh, vh / 2)
        local split = snap(vx + vw * normal)
        fill(ev, bar, "FFFFFF", 0, string.format("\\clip(%.2f,0,%.2f,%d)", vx - 2, split, H + 200))
        if level > normal then
            fill(ev, bar, accent(), 0, string.format("\\clip(%.2f,0,%.2f,%d)", split, vx + vw + 2, H + 200))
        end
    end
    if st.vol > 0.02 then
        local k = 7 * st.vol
        fill(ev, box(vx + vw * level - k, rows_y - k, k * 2, k * 2, k), level > normal and accent() or "FFFFFF", 0)
        local shown = tostring(math.floor(volume + 0.5))
        fill(ev, box(vx + vw * level - 19, top - 36, 38, 28, 14), POP, POP_A, EDGE)
        write(ev, vx + vw * level, top - 22, 5, 14, WHITE, shown, false, MONO)
    end
    hit(vx - 6, rows_y - 14, vw + 12, 28, { volume = true, wheel = function(by) mp.commandv("add", "volume", tostring(by * 2)) end, drag = function(mx)
        mp.set_property_number("volume", math.floor(math.max(0, math.min(1, (mx - vx) / vw)) * top_volume + 0.5))
        mp.set_property_bool("mute", false)
    end })
end

local redraw

local function draw()
    st.hits = {}
    moving = false
    local ev = {}
    draw_dock(ev)
    draw_menu(ev)
    draw_bubble(ev)
    screen.res_x, screen.res_y, screen.data = st.w, H, table.concat(ev, "\n")
    screen:update()
end

-- Showing and hiding --------------------------------------------------------

local SUB_LIFT, sub_home = 8, nil

local function lift(on)
    if on and not sub_home then
        sub_home = mp.get_property_number("sub-pos", 100)
        mp.set_property_number("sub-pos", math.min(sub_home, 100 - SUB_LIFT))
    elseif not on and sub_home then
        mp.set_property_number("sub-pos", sub_home)
        sub_home = nil
    end
end

local tick, hide_timer
local FRAME = 0.008 -- no more than 120 redraws a second

local function step()
    tick = nil
    st.slide = settle(st.slide, st.want and 1 or 0, 0.24)
    if st.slide <= 0.005 and not st.want then
        st.slide = 0
        screen:remove()
        st.hits = {}
        return
    end
    st.last_draw = mp.get_time()
    draw()
    if moving then tick = mp.add_timeout(FRAME, step) end
end

redraw = function()
    if (st.slide > 0 or st.want) and not tick then
        -- A burst of mouse moves becomes one redraw.
        local wait = FRAME - (mp.get_time() - (st.last_draw or 0))
        if wait > 0 then tick = mp.add_timeout(wait, step) else step() end
    end
end

local function stays()
    return st.menu or st.drag or mp.get_property_bool("pause")
        or (st.mouse.hover ~= false and st.mouse.y >= H - DOCK)
end

local rebind

local function poke()
    st.want = true
    lift(true)
    if hide_timer then hide_timer:kill() end
    hide_timer = mp.add_timeout(HIDE_AFTER, function()
        hide_timer = nil
        if stays() then
            poke()
        else
            st.want = false
            lift(false)
            redraw()
        end
    end)
    redraw()
end

-- The mouse -------------------------------------------------------------------

local function top_hit()
    local m = st.mouse
    for i = #st.hits, 1, -1 do
        local h = st.hits[i]
        if m.x >= h.x and m.x <= h.x + h.w and m.y >= h.y and m.y <= h.y + h.h then return h end
    end
end

local function press()
    local h, was = top_hit(), st.menu
    if not h then
        st.menu = nil
    else
        if h.drag then
            st.drag = h
            h.drag(st.mouse.x)
        elseif h.run then
            h.run()
        end
        if was and st.menu == was and not h.stay then st.menu = nil end
    end
    rebind()
    mp.add_timeout(0.06, redraw)
end

local function release()
    if st.drag and st.drag.drop then st.drag.drop(st.mouse.x) end
    st.drag = nil
    rebind()
    redraw()
end

local function on_click(e)
    if e.event == "down" or e.event == "press" then press() end
    if e.event == "up" or e.event == "press" then release() end
end

local function wheel(by)
    local h = top_hit()
    if h and h.wheel then
        h.wheel(by)
    elseif st.menu and st.menu.kind ~= "context" then
        if by < 0 and not st.menu.more then return end
        st.menu.top = math.max(0, st.menu.top - by)
    elseif not st.menu then
        mp.commandv("add", "volume", tostring(by * 5))
    end
    redraw()
end

-- The keyboard, while a menu is open.
local function holder()
    local m = st.menu
    return m and m.in_child and m.child or m
end

local function move_focus(by)
    local m = holder()
    local rows = m and m.live
    if not rows then return end
    m.kbd = true
    local i = m.focus or (by > 0 and 0 or #rows + 1)
    repeat
        i = i + by
    until i < 1 or i > #rows or can_focus(rows[i])
    if i >= 1 and i <= #rows then m.focus = i end
    fit_focus(m)
end

local function switch_tab(by)
    local m = st.menu
    local tabs = TABS[m.kind]
    if not tabs then return end
    for i, name in ipairs(tabs) do
        if name == m.tab then
            m.tab = tabs[(i - 1 + by) % #tabs + 1]
            m.top, m.focus, m.placed = 0, nil, false
            return
        end
    end
end

local function side(by)
    local m, c = st.menu, holder()
    local r = c and c.live and c.focus and c.live[c.focus]
    c.kbd = true
    if r and r.picks then
        for i, p in ipairs(r.picks) do
            if p.on and r.picks[i + by] then
                r.picks[i + by].run()
                break
            end
        end
    elseif r and r.slider then
        r.set(r.value + by * r.step)
    elseif m.kind == "context" then
        if by > 0 and r and r.sub then
            m.open, m.child, m.in_child = c.focus, { top = 0, kbd = true }, true
            m.pending_focus = true
        elseif by < 0 and m.in_child then
            m.in_child, m.open, m.child = false, nil, nil
        end
    else
        switch_tab(by)
    end
end

local function enter()
    local m, c = st.menu, holder()
    local r = c and c.live and c.focus and c.live[c.focus]
    if not r then return end
    if r.sub then return side(1) end
    if r.run then
        local was = st.menu
        r.run()
        if st.menu == was and not r.stay then st.menu = nil end
        rebind()
    end
end

local function escape()
    local m = st.menu
    if m.in_child then
        m.in_child, m.open, m.child = false, nil, nil
    else
        st.menu = nil
    end
    rebind()
end

local KEYS = { { "UP", function() move_focus(-1) end }, { "DOWN", function() move_focus(1) end },
    { "LEFT", function() side(-1) end }, { "RIGHT", function() side(1) end }, { "ENTER", enter }, { "ESC", escape },
    { "TAB", function() if st.menu.kind ~= "context" then switch_tab(1) end end } }

local bound = false
rebind = function()
    -- Only on the dock or with a menu open, so everywhere else the picture
    -- does what the user's settings and input.conf say.
    local on = st.slide > 0 and (st.menu ~= nil or st.drag ~= nil or st.mouse.y >= H - DOCK)
    if on ~= bound then
    bound = on
    if on then
        -- A held button must not move the window instead.
        st.dragging_was = mp.get_property_bool("window-dragging")
        mp.set_property_bool("window-dragging", false)
        mp.add_forced_key_binding("MBTN_LEFT", "deck-click", on_click, { complex = true })
        mp.add_forced_key_binding("MBTN_LEFT_DBL", "deck-double", function() end)
        mp.add_forced_key_binding("WHEEL_UP", "deck-up", function() wheel(1) end)
        mp.add_forced_key_binding("WHEEL_DOWN", "deck-down", function() wheel(-1) end)
        mp.add_forced_key_binding("MBTN_RIGHT", "deck-right", function()
            if st.menu then
                st.menu = nil
                rebind()
                redraw()
            end
        end)
    else
        if st.dragging_was ~= nil then mp.set_property_bool("window-dragging", st.dragging_was) end
        for _, name in ipairs({ "deck-click", "deck-double", "deck-up", "deck-down", "deck-right" }) do
            mp.remove_key_binding(name)
        end
    end
    end
    local keys = st.menu ~= nil
    if keys ~= st.keys_bound then
        st.keys_bound = keys
        for _, k in ipairs(KEYS) do
            if keys then
                mp.add_forced_key_binding(k[1], "deck-key-" .. k[1], function()
                    if st.menu then
                        k[2]()
                        redraw()
                    end
                end, { repeatable = true })
            else
                mp.remove_key_binding("deck-key-" .. k[1])
            end
        end
    end
end

mp.observe_property("mouse-pos", "native", function(_, m)
    if type(m) ~= "table" or not m.x then return end
    local x, y = m.x / st.scale, m.y / st.scale
    local moved = x ~= st.mouse.x or y ~= st.mouse.y
    st.mouse = { x = x, y = y, hover = m.hover }
    if st.drag then st.drag.drag(x) end
    if moved and st.menu then
        st.menu.kbd = false
        if st.menu.child then st.menu.child.kbd = false end
        st.menu.in_child = false
    end
    if m.hover == false and not stays() then
        st.want = false
        lift(false)
    elseif moved then
        poke()
    end
    rebind()
    redraw()
end)

-- The right-click menu. A plain binding: the user's input.conf still wins.
local function context_rows()
    local function list(tab) return function() st.menu = { kind = "list", tab = tab, top = 0 } end end
    local function opens(i) return function() st.menu.open, st.menu.child = i, { top = 0 } end end
    local paused = mp.get_property_bool("pause")
    return {
        { icon = paused and "play" or "pause", label = paused and "Play" or "Pause", key = "Space", run = function() mp.commandv("cycle", "pause") end },
        { icon = "subs", label = "Subtitles", sub = CONTENT.Subtitles, run = opens(2), stay = true },
        { icon = "wave", label = "Audio", sub = CONTENT.Audio, run = opens(3), stay = true },
        { icon = "tune", label = "Speed and sleep", sub = CONTENT.Playback, run = opens(4), stay = true },
        { icon = "film", label = "Video", sub = CONTENT.Video, run = opens(5), stay = true },
        { icon = "list", label = "Episodes", run = list("Episodes") },
        { icon = "list", label = "Chapters", run = list("Chapters") },
        { icon = "next", label = "Finish episode", key = "Enter", run = function() mp.commandv("script-message", "anime-hub-finish") end },
        { icon = "full", label = "Fullscreen", key = "f", run = function() mp.commandv("cycle", "fullscreen") end },
    }
end

mp.add_key_binding("MBTN_RIGHT", "deck-context", function()
    poke()
    st.menu = { kind = "context", x = st.mouse.x, y = st.mouse.y, rows = context_rows, top = 0 }
    rebind()
    redraw()
end)

-- What mpv and the hub say ----------------------------------------------------

mp.observe_property("osd-dimensions", "native", function(_, d)
    if type(d) == "table" and d.h and d.h > 0 then
        st.w, st.scale = math.floor(H * d.w / d.h), d.h / H
        redraw()
    end
end)

local second
mp.observe_property("time-pos", "number", function(_, t)
    local now = t and math.floor(t)
    if now ~= second then
        second = now
        redraw()
    end
end)

mp.observe_property("pause", "bool", function(_, paused)
    if paused then poke() else redraw() end
end)

for _, name in ipairs({ "volume", "mute", "track-list", "chapter-list", "duration", "speed", "sub-delay", "audio-delay" }) do
    mp.observe_property(name, "native", function() redraw() end)
end

mp.register_script_message("anime-hub-info", function(raw)
    local data = utils.parse_json(raw or "")
    if type(data) ~= "table" then return end
    info = data
    if type(data.sleep) == "table" and type(data.sleep.left) == "number" then
        st.sleep_minutes, st.sleep_end = data.sleep.minutes, mp.get_time() + data.sleep.left
    end
    redraw()
end)

mp.register_script_message("anime-hub-segments", function(raw)
    local data = utils.parse_json(raw or "")
    parts = {}
    if type(data) == "table" and data.marks == true and type(data.segments) == "table" then
        for _, s in ipairs(data.segments) do
            if type(s) == "table" and type(s.start) == "number" and type(s["end"]) == "number" then
                parts[#parts + 1] = { kind = s.kind, start = s.start, stop = s["end"] }
            end
        end
    end
    redraw()
end)

mp.observe_property("demuxer-cache-state", "native", function(_, c)
    st.loaded = type(c) == "table" and c["seekable-ranges"] or nil
    local ahead = type(c) == "table" and c["cache-duration"] or nil
    local shown = ahead and math.floor(ahead)
    if shown ~= st.ahead then
        st.ahead = shown
        if mp.get_property_bool("pause") then redraw() end -- playing, the clock's tick shows it
    end
end)

mp.register_script_message("thumbfast-info", function(raw) thumb = utils.parse_json(raw or "") end)

mp.register_event("start-file", function()
    info, parts, st.menu = {}, {}, nil
end)

mp.register_event("file-loaded", function()
    local d = mp.get_property_native("osd-dimensions") or {}
    mp.msg.warn(string.format("deck: drawing at %sx%s", tostring(d.w), tostring(d.h)))
    mp.commandv("script-message", "osc-visibility", "never", "no-osd")
    mp.commandv("script-message-to", "thumbfast", "thumbfast-info")
    mp.commandv("script-message", "anime-hub-skip-bottom", "92")  -- above the hover labels
    poke()
end)
