-- Anime Hub: the "Soft" player look, Decision 52. Everything is ASS
-- drawn on OSD overlays in a 720-high space whose width follows the window.
-- Works alone (it reads mpv's properties); the hub adds the show's name, its
-- colour and the episode list (script-message anime-hub-info) and the
-- opening/ending ranges (anime-hub-segments). Picked episodes go through
-- skip-credits.lua, which already owns "quit and tell the hub".
local utils = require "mp.utils"

local RES_Y = 720
local BAR_Y, BAR_H = 622, 6       -- the seek bar
local ROW_Y = 670                 -- centre of the button row
local SIDE = 40                   -- left and right edge of the bar
local ZONE_Y = 606                -- below this the mouse is on the controls
local HIDE_AFTER = 2              -- seconds without the mouse moving
local FADE_STEP = 0.2
local UI, TITLE = "Hanken Grotesk", "Barlow Condensed"
local MENU_ROWS = 9

local info = {}                   -- what the hub said about this episode
local segments = {}               -- { {kind, start, stop} }
local ui = { w = 1280, scale = 1, opacity = 0, hit = {}, menu = nil, mouse = { x = 0, y = 0 } }
local back, front, hover_layer
local thumb = nil                 -- thumbfast's size, when the user has it

-- Drawing ---------------------------------------------------------------

local function snap(v)
    -- Onto the screen's own pixels: an edge between two pixels looks soft.
    return math.floor(v * ui.scale + 0.5) / ui.scale
end

local function rr(x, y, w, h, r, hole)
    -- A rounded box as a drawing path; `hole` winds it the other way, which
    -- cuts it out of the shape before it.
    local l, t, R, b = snap(x), snap(y), snap(x + w), snap(y + h)
    r = math.min(r, (R - l) / 2, (b - t) / 2)
    local c = r * 0.5523
    if hole then
        return string.format("m %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f "
            .. "l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f ",
            l + r, t, l + r - c, t, l, t + r - c, l, t + r,
            l, b - r, l, b - r + c, l + r - c, b, l + r, b,
            R - r, b, R - r + c, b, R, b - r + c, R, b - r,
            R, t + r, R, t + r - c, R - r + c, t, R - r, t)
    end
    return string.format("m %.2f %.2f l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f "
        .. "l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f l %.2f %.2f b %.2f %.2f %.2f %.2f %.2f %.2f ",
        l + r, t, R - r, t, R - r + c, t, R, t + r - c, R, t + r,
        R, b - r, R, b - r + c, R - r + c, b, R - r, b,
        l + r, b, l + r - c, b, l, b - r + c, l, b - r,
        l, t + r, l, t + r - c, l + r - c, t, l + r, t)
end

local function poly(points)
    local out = {}
    for i = 1, #points, 2 do
        out[#out + 1] = string.format("%s %.2f %.2f", i == 1 and "m" or "l", points[i], points[i + 1])
    end
    return table.concat(out, " ") .. " "
end

local function arc(cx, cy, r, from, to, out, steps)
    for i = 0, steps do
        local t = math.rad(from + (to - from) * i / steps)
        out[#out + 1] = cx + r * math.cos(t)
        out[#out + 1] = cy + r * math.sin(t)
    end
    return out
end

local K = 1   -- icon units to screen units, while an icon is being built

local function whole(width)
    return math.max(1, math.floor(width * K * ui.scale + 0.5)) / ui.scale
end

local function along(v, width)
    -- A straight stroke's centre, so both its edges land on whole pixels.
    local dev = v * ui.scale
    if math.floor(width * ui.scale + 0.5) % 2 == 0 then return math.floor(dev + 0.5) / ui.scale end
    return (math.floor(dev) + 0.5) / ui.scale
end

local function pen(width, ...)
    -- Round strokes through the given x, y pairs.
    local pts, out, w = { ... }, {}, whole(width)
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
        out[#out + 1] = poly(p)
    end
    return table.concat(out)
end

local function curve(width, cx, cy, radius, from, to)
    -- One smooth bent stroke with round ends, not many short ones.
    local w = whole(width)
    local r, R, p = w / 2, radius * K, {}
    cx, cy = cx * K, cy * K
    arc(cx, cy, R + r, from, to, p, 24)
    arc(cx + R * math.cos(math.rad(to)), cy + R * math.sin(math.rad(to)), r, to, to + 180, p, 10)
    arc(cx, cy, R - r, to, from, p, 24)
    arc(cx + R * math.cos(math.rad(from)), cy + R * math.sin(math.rad(from)), r, from + 180, from + 360, p, 10)
    return poly(p)
end

local function blob(points, r)
    -- A filled shape with very round corners.
    local n, p, pts = #points / 2, {}, {}
    for i, v in ipairs(points) do pts[i] = v * K end
    r = r * K
    for i = 1, n do
        local a, c, b = (i - 2) % n, (i - 1) % n, i % n
        local ax, ay = pts[a * 2 + 1], pts[a * 2 + 2]
        local x, y = pts[c * 2 + 1], pts[c * 2 + 2]
        local bx, by = pts[b * 2 + 1], pts[b * 2 + 2]
        local la, lb = math.sqrt((ax - x) ^ 2 + (ay - y) ^ 2), math.sqrt((bx - x) ^ 2 + (by - y) ^ 2)
        for s = 0, 12 do
            local t = s / 12
            p[#p + 1] = (1 - t) ^ 2 * (x + (ax - x) * r / la) + 2 * t * (1 - t) * x + t ^ 2 * (x + (bx - x) * r / lb)
            p[#p + 1] = (1 - t) ^ 2 * (y + (ay - y) * r / la) + 2 * t * (1 - t) * y + t ^ 2 * (y + (by - y) * r / lb)
        end
    end
    return poly(p)
end

local function dot(x, y, w, r) return rr(x * K, y * K, w * K, w * K, r * K) end

local function outline(x, y, w, h, r, width)
    -- An outline with the same thickness on all four sides.
    local t = whole(width)
    local l, u = snap(x * K), snap(y * K)
    local W, H2 = snap((x + w) * K) - l, snap((y + h) * K) - u
    return rr(l, u, W, H2, r * K) .. rr(l + t, u + t, W - 2 * t, H2 - 2 * t, math.max(0, r * K - t), true)
end

local function speaker() return blob({ 2.5, 9.5, 6.5, 9.5, 12, 5, 12, 19, 6.5, 14.5, 2.5, 14.5 }, 1.1) end

local function corners()
    return curve(2.2, 8, 8, 4, 180, 270) .. pen(2.2, 4, 8, 4, 9.5) .. pen(2.2, 8, 4, 9.5, 4)
        .. curve(2.2, 16, 8, 4, 270, 360) .. pen(2.2, 20, 8, 20, 9.5) .. pen(2.2, 14.5, 4, 16, 4)
        .. curve(2.2, 8, 16, 4, 90, 180) .. pen(2.2, 4, 14.5, 4, 16) .. pen(2.2, 8, 20, 9.5, 20)
        .. curve(2.2, 16, 16, 4, 0, 90) .. pen(2.2, 20, 14.5, 20, 16) .. pen(2.2, 14.5, 20, 16, 20)
end

-- Built for the exact screen they appear on: scaling a finished shape blurs it.
local ICONS = {
    play = function() return blob({ 6, 3, 22, 12, 6, 21 }, 5) end,
    pause = function() return pen(3.2, 8.5, 5, 8.5, 19) .. pen(3.2, 15.5, 5, 15.5, 19) end,
    back = function() return pen(2.4, 11.5, 6, 5.5, 12, 11.5, 18) .. pen(2.4, 19, 6, 13, 12, 19, 18) end,
    forward = function() return pen(2.4, 12.5, 6, 18.5, 12, 12.5, 18) .. pen(2.4, 5, 6, 11, 12, 5, 18) end,
    volume = function() return speaker() .. curve(2, 12, 12, 4.5, -38, 38) .. curve(2, 12, 12, 8.5, -38, 38) end,
    quiet = function() return speaker() .. curve(2, 12, 12, 4.5, -38, 38) end,
    muted = function() return speaker() .. pen(2, 16, 9.5, 21, 14.5) .. pen(2, 21, 9.5, 16, 14.5) end,
    subs = function()
        return outline(2, 5, 20, 14, 4.5, 2.2) .. pen(2.2, 6.5, 10.5, 12, 10.5) .. pen(2.2, 6.5, 14, 9, 14) .. pen(2.2, 13, 14, 17.5, 14)
    end,
    audio = function()
        return pen(2.2, 4, 10, 4, 14) .. pen(2.2, 8, 7, 8, 17) .. pen(2.2, 12, 4, 12, 20) .. pen(2.2, 16, 7, 16, 17) .. pen(2.2, 20, 10, 20, 14)
    end,
    list = function() return pen(2.2, 4, 6, 20, 6) .. pen(2.2, 4, 12, 20, 12) .. pen(2.2, 4, 18, 13, 18) end,
    tune = function()
        return pen(2.2, 3.5, 7.5, 20.5, 7.5) .. pen(2.2, 3.5, 16.5, 20.5, 16.5) .. dot(12.5, 4, 7, 3.5) .. dot(4.5, 13, 7, 3.5)
    end,
    next = function() return pen(2.4, 6.5, 6, 12.5, 12, 6.5, 18) .. pen(2.4, 17.5, 6, 17.5, 18) end,
    film = function() return outline(2.5, 4.5, 19, 15, 4.5, 2.2) .. pen(2.2, 9, 12, 15, 12) end,
    full = corners,
}

local built = {}

local function icon_shape(name, size)
    size = size or 24
    -- An even number of screen pixels: the box has a true middle.
    local pixels = math.floor(size * ui.scale / 2 + 0.5) * 2
    local key = name .. "/" .. pixels .. "/" .. ui.scale
    if not built[key] then
        K = pixels / ui.scale / 24
        built[key] = ICONS[name]()
        K = 1
    end
    return built[key]
end

local function bgr(hex)
    local r, g, b = hex:match("#(%x%x)(%x%x)(%x%x)")
    if not r then return "FFFFFF" end
    return (b .. g .. r):upper()
end

local function accent() return bgr(info.accent or "#f2a46f") end

local function alpha(base)
    -- base 0 = solid, 255 = gone; the whole UI fades through ui.opacity.
    return string.format("%02X", math.floor(255 - (255 - base) * ui.opacity + 0.5))
end

local function shape(path, color, base, extra)
    return string.format("{\\an7\\pos(0,0)\\bord0\\shad0\\1c&H%s&\\1a&H%s&%s\\p1}%s{\\p0}", color, alpha(base), extra or "", path)
end

local function text(x, y, an, size, color, s, font, bold, base)
    return string.format("{\\an%d\\pos(%.1f,%.1f)\\fn%s\\fs%d\\b%d\\bord0\\shadow1\\4c&H000000&\\4a&H%s&\\1c&H%s&\\1a&H%s&}%s",
        an, snap(x), snap(y), font or UI, size, bold and 1 or 0, alpha(math.max(150, base or 0)), color, alpha(base or 0), s)
end

local function mix(a, b, t)
    -- Two BGR colours, t of the way from a to b.
    local out = ""
    for i = 1, 5, 2 do
        local x, y = tonumber(a:sub(i, i + 1), 16), tonumber(b:sub(i, i + 1), 16)
        out = out .. string.format("%02X", math.floor(x + (y - x) * t + 0.5))
    end
    return out
end

local function escape(s)
    return tostring(s or ""):gsub("\\", "\\\\"):gsub("{", "\\{"):gsub("}", "\\}"):gsub("[\r\n]+", " ")
end

local function clock(t)
    if type(t) ~= "number" then return "--:--" end
    t = math.max(0, math.floor(t))
    local h, m, s = math.floor(t / 3600), math.floor(t / 60) % 60, t % 60
    if h > 0 then return string.format("%d:%02d:%02d", h, m, s) end
    return string.format("%02d:%02d", m, s)
end

-- Motion: every value is a small spring, so things overshoot a little and
-- settle instead of sliding at one speed. The screen redraws by itself until
-- everything has come to rest.
local springs, unsettled = {}, false
local ripples, floats = {}, {}

local function spring(name, target, stiff)
    local s = springs[name]
    if not s then
        s = { x = target, v = 0 }
        springs[name] = s
    end
    s.v = (s.v + (target - s.x) * (stiff or 0.2)) * 0.68
    s.x = s.x + s.v
    if math.abs(target - s.x) < 0.004 and math.abs(s.v) < 0.004 then
        s.x, s.v = target, 0
    else
        unsettled = true
    end
    return s.x
end

local function kick(name, amount)
    local s = springs[name]
    if s then s.v = s.v + amount end
end

local function ring(cx, cy, r, w)
    return rr(cx - r, cy - r, r * 2, r * 2, r) .. rr(cx - r + w, cy - r + w, (r - w) * 2, (r - w) * 2, r - w, true)
end

local function draw_effects(ev)
    -- Rings spreading from a click, and "+10" drifting up from a skip.
    for i = #ripples, 1, -1 do
        local e = ripples[i]
        e.t = e.t + 0.055
        if e.t >= 1 then
            table.remove(ripples, i)
        else
            local k = 1 - (1 - e.t) ^ 3
            ev[#ev + 1] = shape(ring(e.x, e.y, 18 + 26 * k, 2.4 * (1 - e.t) + 0.4), accent(), 90 + 165 * e.t)
            unsettled = true
        end
    end
    for i = #floats, 1, -1 do
        local e = floats[i]
        e.t = e.t + 0.03
        if e.t >= 1 then
            table.remove(floats, i)
        else
            local k = 1 - (1 - e.t) ^ 3
            ev[#ev + 1] = text(e.x, e.y - 44 * k, 5, 22, "FFFFFF", e.label, UI, true, math.floor(255 * e.t ^ 3))
            unsettled = true
        end
    end
end

local function icon(ev, name, cx, cy, scale, turn, base, color)
    -- 24 units wide, drawn around its centre. At rest it is exact; only the
    -- moment it moves is it scaled.
    local s = scale or 1
    if math.abs(s - 1) < 0.005 then s = 1 end
    local moving = s ~= 1 or (turn and math.abs(turn) > 0.05)
    ev[#ev + 1] = string.format("{\\an7\\pos(%.2f,%.2f)%s\\bord0\\shad0\\1c&H%s&\\1a&H%s&\\p1}%s{\\p0}",
        snap(cx - 12 * s), snap(cy - 12 * s),
        moving and string.format("\\org(%.1f,%.1f)\\fscx%.2f\\fscy%.2f\\frz%.1f", cx, cy, s * 100, s * 100, turn or 0) or "",
        color or "FFFFFF", alpha(base or 0), icon_shape(name))
end

-- The seek bar ----------------------------------------------------------

local function bar_pieces(duration)
    -- The bar's stretches: chapter to chapter, openings and endings marked.
    local cuts, seen = { 0, duration }, {}
    for _, c in ipairs(mp.get_property_native("chapter-list") or {}) do
        if c.time > 0 and c.time < duration then cuts[#cuts + 1] = c.time end
    end
    for _, s in ipairs(segments) do
        cuts[#cuts + 1] = s.start
        cuts[#cuts + 1] = math.min(s.stop, duration)
    end
    table.sort(cuts)
    local pieces, last = {}, nil
    for _, t in ipairs(cuts) do
        if last and t - last > duration * 0.004 then
            local mark = false
            for _, s in ipairs(segments) do
                if (last + t) / 2 >= s.start and (last + t) / 2 < s.stop then mark = true end
            end
            pieces[#pieces + 1] = { from = last, to = t, mark = mark }
            last = t
        elseif not last then
            last = t
        end
        seen[t] = true
    end
    return pieces
end

local function lens(a, b, cy, h, swell, at)
    -- The bar as an outline: round at both ends, swelling around the mouse.
    local r, top, bottom = h / 2, {}, {}
    local function half(x)
        local v = h / 2
        if swell > 0.01 and at then v = v + swell * math.exp(-((x - at) / 34) ^ 2) end
        local edge = math.min(x - a, b - x)
        if edge < r then v = v * math.sqrt(math.max(0, 1 - ((r - edge) / r) ^ 2)) end
        return v
    end
    -- Fine steps through both round ends, wider ones between them.
    for i = 0, 6 do top[#top + 1] = a + math.min(r, (b - a) / 2) * i / 6 end
    local x = a + r + 5
    while x < b - r do
        top[#top + 1] = x
        x = x + 5
    end
    for i = 6, 0, -1 do top[#top + 1] = b - math.min(r, (b - a) / 2) * i / 6 end
    local pts = {}
    for _, v in ipairs(top) do
        pts[#pts + 1] = v
        pts[#pts + 1] = cy - half(v)
    end
    for i = #top, 1, -1 do
        pts[#pts + 1] = top[i]
        pts[#pts + 1] = cy + half(top[i])
    end
    return poly(pts)
end

local function draw_bar(ev, x0, x1, position, duration, heat, hover_x)
    local w, cy = x1 - x0, BAR_Y + BAR_H / 2
    ui.bar = { x0 = x0, x1 = x1, duration = duration }
    if not duration or duration <= 0 then
        ev[#ev + 1] = shape(rr(x0, BAR_Y, w, BAR_H, BAR_H / 2), "FFFFFF", 200)
        return
    end
    local pos = math.max(0, math.min(position or 0, duration))
    local color, head = accent(), x0 + w * pos / duration
    local swell = 7 * heat
    local ranges = (not info.prefs or info.prefs.loaded ~= "off") and ui.loaded or {}
    -- Only under the mouse: chasing the playing position would never settle,
    -- and the screen would redraw at full speed for as long as the bar shows.
    local at = hover_x and spring("bar:x", hover_x, 0.3) or head
    if not hover_x then springs["bar:x"] = nil end
    local h = BAR_H + 2 * heat
    for _, p in ipairs(bar_pieces(duration)) do
        local a, b = x0 + w * p.from / duration, x0 + w * p.to / duration - 3
        if b - a > 0.5 then
            local tint, outline = p.mark and color or "FFFFFF", lens(a, b, cy, h, swell, at)
            ev[#ev + 1] = shape(outline, tint, p.mark and 102 or 190)
            for _, r in ipairs(ranges) do -- what mpv has loaded so far
                local from, to = math.max(a, x0 + w * r.start / duration), math.min(b, x0 + w * r["end"] / duration)
                if to > from then
                    ev[#ev + 1] = shape(outline, "FFFFFF", 164, string.format("\\clip(%.1f,0,%.1f,720)", from, to))
                end
            end
            local ahead = hover_x and math.min(b, hover_x)
            if ahead and ahead > a then -- how far a click would land
                ev[#ev + 1] = shape(outline, tint, 150, string.format("\\clip(%.1f,0,%.1f,720)", a - 2, ahead))
            end
            local fill = math.min(b, head)
            if fill > a then
                ev[#ev + 1] = shape(outline, tint, 0, string.format("\\clip(%.1f,0,%.1f,720)", a - 2, fill))
            end
        end
    end
    if hover_x and heat > 0.02 then -- rounds off where the preview ends
        local k = math.max(0.1, h / 2 + swell)
        ev[#ev + 1] = shape(rr(hover_x - k, cy - k, k * 2, k * 2, k), "FFFFFF", 0)
    end
    if heat > 0.02 then
        local k = math.max(0.1, 8 * heat)
        ev[#ev + 1] = shape(rr(head - 15, cy - 15, 30, 30, 15), color, 255 - 130 * math.min(1, heat), "\\blur7")
        ev[#ev + 1] = shape(rr(head - k, cy - k, k * 2, k * 2, k), "FFFFFF", 0)
    end
end

local function bar_time(x)
    local b = ui.bar
    if not b or not b.duration or b.duration <= 0 then return nil end
    return math.max(0, math.min(1, (x - b.x0) / (b.x1 - b.x0))) * b.duration
end

local function part_name(t)
    for _, s in ipairs(segments) do
        if t >= s.start and t < s.stop then return s.kind == "op" and "Opening" or "Ending" end
    end
    local name = nil
    for _, c in ipairs(mp.get_property_native("chapter-list") or {}) do
        if c.time <= t then name = c.title end
    end
    return name ~= "Episode" and name or nil
end

-- The menus -------------------------------------------------------------

local LANGS = { ja = "Japanese", jpn = "Japanese", en = "English", eng = "English", de = "German", ger = "German",
    deu = "German", es = "Spanish", spa = "Spanish", fr = "French", fre = "French", fra = "French",
    it = "Italian", ita = "Italian", pt = "Portuguese", por = "Portuguese", ru = "Russian", rus = "Russian",
    ar = "Arabic", ara = "Arabic", zh = "Chinese", chi = "Chinese", zho = "Chinese", ko = "Korean", kor = "Korean" }

local function track_rows(kind)
    local rows = {}
    for _, t in ipairs(mp.get_property_native("track-list") or {}) do
        if t.type == kind then
            local label = t.lang and LANGS[t.lang:lower()] or t.lang or ("Track " .. t.id)
            if t.title and t.title ~= "" then label = t.lang and (label .. " · " .. t.title) or t.title end
            rows[#rows + 1] = { label = label, on = t.selected, run = function()
                mp.set_property_number(kind == "sub" and "sid" or "aid", t.id)
            end }
        end
    end
    return rows
end

local function fit(s, chars)
    if #s <= chars then return s end
    local cut = chars - 1
    while cut > 1 and s:byte(cut + 1) and s:byte(cut + 1) >= 128 and s:byte(cut + 1) < 192 do cut = cut - 1 end
    return s:sub(1, cut) .. "…"
end

-- The panel, the lists and the right-click menu ---------------------------

local function inside(x, y, w, h)
    local m = ui.mouse
    return m.hover ~= false and m.x >= x and m.x <= x + w and m.y >= y and m.y <= y + h
end

local hits -- where the menus being drawn leave their click areas

local function add_hit(x, y, w, h, f)
    f.x, f.y, f.w, f.h, f.keep = x, y, w, h, f.stay or false
    hits[#hits + 1] = f
end

local function fill(ev, path, color, a, tags) ev[#ev + 1] = shape(path, color, a or 0, tags) end
local function write(ev, x, y, an, size, color, s, bold) ev[#ev + 1] = text(x, y, an, size, color, s, UI, bold) end

local function small_icon(ev, name, cx, cy, color, size)
    ev[#ev + 1] = string.format("{\\an7\\pos(%.2f,%.2f)\\bord0\\shad0\\1c&H%s&\\1a&H%s&\\p1}%s{\\p0}",
        snap(cx - size / 2), snap(cy - size / 2), color, alpha(0), icon_shape(name, size))
end

local WHITE, SOFTTEXT, DIM = "F1F3F3", "C6C9C9", "928C8C"

local function badge(ev, x, y, key)
    local w = math.max(22, 10 + #key * 8)
    fill(ev, rr(x - w, y - 11, w, 22, 7), "FFFFFF", 0xE0)
    write(ev, x - w / 2, y, 5, 13, SOFTTEXT, key)
end

local TABS = { panel = { "Playback", "Video", "Audio", "Subtitles" }, list = { "Episodes", "Chapters" } }

local function open_at(kind, tab, anchor)
    local m = ui.menu
    if m and m.tab == tab then
        ui.menu = nil
    elseif m and m.kind == kind then
        -- Another button of the same panel: it stays where it is.
        ui.menu = { kind = kind, tab = tab, top = 0, anchor = m.anchor, body = m.body, pill = m.pill }
    else
        ui.menu = { kind = kind, tab = tab, top = 0, anchor = anchor }
    end
end

local function picks(title, prop, list)
    local current, items = mp.get_property(prop), {}
    for _, p in ipairs(list) do
        items[#items + 1] = { text = p[1], on = current == p[2] or (tonumber(current) and tonumber(current) == tonumber(p[2])),
            run = function() mp.set_property(prop, p[2]) end }
    end
    return { picks = items, title = title }
end

local function shift(prop, limit)
    local v = mp.get_property_number(prop, 0)
    return { slider = true, title = "Delay", value = v, limit = limit, step = 0.05, shown = string.format("%+.2f", v),
        set = function(n) mp.set_property_number(prop, math.max(-limit, math.min(limit, math.floor(n * 20 + 0.5) / 20))) end }
end

local function sleep_row()
    local minutes, items = ui.sleep_minutes or 0, {}
    for _, m in ipairs({ 0, 30, 60, 90 }) do
        items[#items + 1] = { text = m == 0 and "Off" or (m .. "m"), on = minutes == m, run = function()
            ui.sleep_minutes, ui.sleep_end = m, m > 0 and mp.get_time() + m * 60 or nil
            ui.sleep_asked = (ui.sleep_asked or 0) + 1
            -- The hub keeps the time, so it survives the next episode. A new
            -- number each pick: the same minutes picked twice still count.
            mp.set_property_native("user-data/anime-hub/sleep", m + ui.sleep_asked / 1000)
        end }
    end
    local left = ui.sleep_end and math.ceil((ui.sleep_end - mp.get_time()) / 60)
    return { picks = items, title = left and left > 0 and ("Sleep " .. left .. "m left") or "Sleep timer" }
end

local function options(rows, kind)
    for _, r in ipairs(track_rows(kind)) do
        rows[#rows + 1] = { label = r.label, on = r.on, run = r.run, stay = true }
    end
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
        options(rows, "audio")
        rows[#rows + 1] = shift("audio-delay", 2)
        return rows
    end,
    Subtitles = function()
        local rows = { { head = "Language" }, { label = "None", on = not mp.get_property_native("sid"), stay = true,
            run = function() mp.set_property("sid", "no") end } }
        options(rows, "sub")
        rows[#rows + 1] = shift("sub-delay", 10)
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

local function row_height(r) return r.head and 26 or (r.picks or r.slider) and 36 or 30 end

local function rows_height(rows, from)
    local h = 0
    for i = from or 1, #rows do h = h + row_height(rows[i]) end
    return h
end

local function row_back(ev, m, i, x, y, w, h)
    if m.kbd and m.focus == i then fill(ev, rr(x, y, w, h, 8), "FFFFFF", 0xE8) end
end

local function draw_picks(ev, r, x, y, w, m, i)
    row_back(ev, m, i, x, y + 1, w, 34)
    write(ev, x + 10, y + 18, 4, 15, SOFTTEXT, escape(r.title))
    local widths, sum = {}, 0
    for n, p in ipairs(r.picks) do
        widths[n] = math.max(32, 12 + #p.text * 7.2)
        sum = sum + widths[n]
    end
    local cx = x + w - sum - 4
    fill(ev, rr(cx, y + 5, sum, 26, 8), "FFFFFF", 0xF0)
    for n, p in ipairs(r.picks) do
        local each = widths[n]
        local on = inside(cx, y + 5, each, 26)
        if p.on or on then fill(ev, rr(cx + 1, y + 6, each - 2, 24, 7), "FFFFFF", p.on and 0xC8 or 0xE4) end
        write(ev, cx + each / 2, y + 18, 5, 14, (p.on or on) and WHITE or SOFTTEXT, p.text, p.on)
        add_hit(cx, y + 5, each, 26, { run = p.run, stay = true })
        cx = cx + each
    end
end

local function draw_slider(ev, r, x, y, w, m, i)
    row_back(ev, m, i, x, y + 1, w, 34)
    write(ev, x + 10, y + 18, 4, 15, SOFTTEXT, r.title)
    local right = x + w - 6
    write(ev, right - 44, y + 18, 5, 13, r.value == 0 and DIM or WHITE, r.shown)
    add_hit(right - 66, y + 6, 44, 24, { stay = true, run = function() r.set(0) end })
    local plus, minus = right - 11, x + 78
    for _, b in ipairs({ { minus, -1, "−" }, { plus, 1, "+" } }) do
        local on = inside(b[1] - 11, y + 7, 22, 22)
        if on then fill(ev, rr(b[1] - 11, y + 7, 22, 22, 7), "FFFFFF", 0xE0) end
        write(ev, b[1], y + 17, 5, 17, on and WHITE or SOFTTEXT, b[3])
        add_hit(b[1] - 11, y + 7, 22, 22, { stay = true, run = function() r.set(r.value + b[2] * r.step) end })
    end
    local sx, sw = minus + 16, right - 70 - 2 - (minus + 16)
    local mid, pos = sx + sw / 2, sx + sw * (r.value + r.limit) / (2 * r.limit)
    fill(ev, rr(sx, y + 16, sw, 4, 2), "FFFFFF", 0xD0)
    local from, to = math.min(mid, pos), math.max(mid, pos)
    if to - from > 1 then fill(ev, rr(from, y + 16, to - from, 4, 2), accent(), 0) end
    local k = inside(sx - 6, y + 6, sw + 12, 24) and 7 or 5
    fill(ev, rr(pos - k, y + 18 - k, k * 2, k * 2, k), "FFFFFF", 0)
    local function move(mx) r.set((math.max(0, math.min(1, (mx - sx) / sw)) * 2 - 1) * r.limit) end
    add_hit(sx - 6, y + 6, sw + 12, 24, { drag = move, stay = true, wheel = function(by) r.set(r.value + by * r.step) end })
end

local function draw_option(ev, r, x, y, w, m, i)
    local under = r.run and inside(x, y, w, 29)
    if r.on or under or r.open or (m.kbd and m.focus == i) then
        fill(ev, rr(x, y, w, 29, 8), "FFFFFF", r.on and 0xD8 or 0xE8)
    end
    if r.icon then small_icon(ev, r.icon, x + 16, y + 14, r.on and WHITE or SOFTTEXT, 16) end
    write(ev, x + (r.icon and 34 or 10), y + 14, 4, 15, (r.on or under) and WHITE or (r.faded and DIM or SOFTTEXT),
        escape(fit(r.label, r.note and 32 or 40)), r.on)
    if r.key then
        badge(ev, x + w - 6, y + 14, r.key)
    elseif r.sub then
        write(ev, x + w - 12, y + 13, 6, 16, DIM, "›")
    elseif r.note then
        write(ev, x + w - 10, y + 14, 6, 13, DIM, escape(r.note))
    end
    if r.run then add_hit(x, y, w, 29, { run = r.run, stay = r.stay }) end
end

local function draw_rows(ev, rows, m, x, y, w, stop)
    m.live = rows
    for i = (m.top or 0) + 1, #rows do
        local r, h = rows[i], row_height(rows[i])
        if y + h > stop + 1 then return true end
        if r.head then
            write(ev, x + 10, y + 15, 4, 13, DIM, escape(r.head))
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

local function card(ev, x, y, w, h, r)
    fill(ev, rr(x, y, w, h, r), "1B1716", 20, "\\bord1\\3c&HFFFFFF&\\3a&H" .. alpha(225) .. "&")
end

local function fit_focus(m)
    if not (m.live and m.focus and m.room) then return end
    if m.focus <= (m.top or 0) then m.top = m.focus - 1 end
    local used = 0
    for i = (m.top or 0) + 1, m.focus do used = used + row_height(m.live[i]) end
    while used > m.room and m.top < m.focus - 1 do
        m.top = m.top + 1
        used = used - row_height(m.live[m.top])
    end
end

local function draw_context(ev, m)
    local rows = m.rows()
    local w, h = 240, rows_height(rows) + 12
    local x, y = math.max(8, math.min(ui.w - w - 8, m.x)), math.max(8, math.min(RES_Y - h - 8, m.y))
    m.box = { x, y, w, h }
    card(ev, x, y, w, h, 14)
    local on_child = m.child and m.child.box and inside((table.unpack or unpack)(m.child.box))
    local cy = y + 6
    for i, r in ipairs(rows) do
        r.y = cy
        if not on_child and not m.kbd and i ~= m.open and inside(x + 6, cy, w - 12, row_height(r)) then
            m.open = r.sub and i or nil
            m.child = r.sub and { top = 0 } or nil
        end
        r.open = m.open == i
        cy = cy + row_height(r)
    end
    draw_rows(ev, rows, m, x + 6, y + 6, w - 12, y + h)
    local parent = m.open and rows[m.open]
    if parent and parent.sub then
        local c, sub = m.child, parent.sub()
        local cw, ch = 300, rows_height(sub) + 12
        local cx = x + w + 4
        if cx + cw > ui.w - 8 then cx = x - cw - 4 end
        local top = math.max(8, math.min(RES_Y - ch - 8, parent.y - 6))
        c.box = { cx, top, cw, ch }
        card(ev, cx, top, cw, ch, 14)
        draw_rows(ev, sub, c, cx + 6, top + 6, cw - 12, top + ch)
    end
end

local function draw_menu(ev, hit)
    local m = ui.menu
    if not m then return end
    hits = hit
    if m.kind == "context" then return draw_context(ev, m) end
    local tabs, rows = TABS[m.kind], CONTENT[m.tab]()
    local w = m.kind == "list" and 360 or 330
    local x = math.max(12, math.min(ui.w - w - 12, (m.anchor or ui.w) - w / 2))
    local bottom = BAR_Y - 16
    local room = math.min(bottom - 100 - 40 - 12, 396)
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
    card(ev, x, y, w, h, 16)
    local tw = (w - 12) / #tabs
    for i, name in ipairs(tabs) do
        local tx = x + 6 + (i - 1) * tw
        if name == m.tab then
            -- The same glide as Deck's tabs: straight there, no bounce.
            if not m.pill or math.abs(tx - m.pill) < 0.5 then
                m.pill = tx
            else
                m.pill = m.pill + (tx - m.pill) * 0.34
                unsettled = true
            end
            fill(ev, rr(m.pill, y + 6, tw, 28, 9), "FFFFFF", 0xDB)
        end
    end
    for i, name in ipairs(tabs) do
        local tx, on = x + 6 + (i - 1) * tw, name == m.tab
        write(ev, tx + tw / 2, y + 20, 5, 14, (on or inside(tx, y + 6, tw, 28)) and WHITE or DIM, name, on)
        add_hit(tx, y + 6, tw, 28, { stay = true, run = function() m.tab, m.top, m.focus, m.placed = name, 0, nil, false end })
    end
    m.more = draw_rows(ev, rows, m, x + 6, y + 40, w - 16, y + 40 + body)
    local all = rows_height(rows)
    if all > body + 1 then -- a scroll thumb, so a long list says it is long
        local before = all - rows_height(rows, m.top + 1)
        local th = math.max(24, body * body / all)
        fill(ev, rr(x + w - 7, y + 40 + (body - th) * before / math.max(1, all - body), 3, th, 1.5), "FFFFFF", 0xB0)
    end
end

local function over(x, y, w, h)
    local m = ui.mouse
    return m.hover ~= false and m.x >= x and m.x <= x + w and m.y >= y and m.y <= y + h
end

local function near(cx)
    -- How close the mouse is along the button row, 0 to 1: neighbours swell a
    -- little too, like a dock.
    local m = ui.mouse
    if ui.menu or m.hover == false or math.abs(m.y - ROW_Y) > 34 then return 0 end
    return math.max(0, 1 - math.abs(m.x - cx) / 74) ^ 1.5
end

local function clicked(name, x)
    kick("tap:" .. name, 0.42)
    ripples[#ripples + 1] = { x = x, y = ROW_Y, t = 0 }
end

local function tooltip(ev, label, cx, heat)
    if heat < 0.03 or #floats > 0 or ui.menu then return end -- a "+10" is rising through that spot
    local bw = 24 + #label * 8.2
    local y = ROW_Y - 62 - 8 * heat
    local x = math.max(bw / 2 + 8, math.min(ui.w - bw / 2 - 8, cx))
    local base = math.floor(255 * (1 - math.min(1, heat)))
    ev[#ev + 1] = shape(rr(x - bw / 2, y - 14, bw, 28, 14), "120F0E", math.max(35, base))
    ev[#ev + 1] = text(x, y, 5, 15, "FFFFFF", label, UI, true, base)
end

local function button(ev, hit, name, cx, run, draw)
    local on = over(cx - 25, ROW_Y - 25, 50, 50)
    local heat = spring("hover:" .. name, on and 1 or 0)
    local lift = spring("near:" .. name, near(cx), 0.3)
    local tap = spring("tap:" .. name, 0, 0.26)
    local glow = math.min(1, math.max(0, heat))
    if glow > 0.02 then
        ev[#ev + 1] = shape(rr(cx - 26, ROW_Y - 26 - 4 * lift, 52, 52, 26), accent(), 255 - 125 * glow, "\\blur10")
    end
    local scale = 1 + 0.26 * lift + 0.08 * heat - 0.5 * math.abs(tap)
    local y = ROW_Y - 4 * lift
    local color = mix("FFFFFF", accent(), 0.4 * glow)
    if draw then
        draw(cx, y, scale, tap, heat, color)
    else
        icon(ev, name, cx, y, scale, 0, 40 - 40 * math.min(1, lift + heat), color)
    end
    hit[#hit + 1] = { x = cx - 25, y = ROW_Y - 25, w = 50, h = 50, run = function()
        clicked(name, cx)
        run()
    end }
end

local function draw_back()
    -- The two soft shadows only change with the window and the fade.
    local w = ui.w
    back.res_x, back.res_y = w, RES_Y
    back.data = table.concat({
        shape(rr(-60, 620, w + 120, 180, 0), "000000", 60, "\\blur46"),
        shape(rr(-60, -120, w + 120, 190, 0), "000000", 110, "\\blur46"),
    }, "\n")
    back:update()
end

local function draw_front()
    local ev, hit, tips = {}, {}, {}
    local position, duration = mp.get_property_number("time-pos"), mp.get_property_number("duration")
    local w = ui.w

    ev[#ev + 1] = text(SIDE, 30, 7, 36, "FFFFFF",
        escape(fit(tostring(info.title or mp.get_property("media-title") or ""), 70)):upper(), TITLE, true)
    if info.episode then
        ev[#ev + 1] = text(SIDE, 70, 7, 18, "E1E4E4", escape(info.episode))
    end

    local left, right = clock(position), clock(duration)
    ev[#ev + 1] = text(SIDE, BAR_Y + BAR_H / 2, 4, 18, "FFFFFF", left, UI, true)
    local loaded = (not info.prefs or info.prefs.loaded ~= "off") and ui.ahead
    local extra = loaded and 62 or 0
    if ui.remaining and duration and position then right = "-" .. clock(duration - position) end
    local right_w = 12 + #right * 10
    local right_hot = inside(w - SIDE - extra - right_w, BAR_Y - 12, right_w, 30)
    ev[#ev + 1] = text(w - SIDE - extra, BAR_Y + BAR_H / 2, 6, 18, right_hot and "FFFFFF" or "C6C9C9", right, UI, true)
    hit[#hit + 1] = { x = w - SIDE - extra - right_w, y = BAR_Y - 12, w = right_w, h = 30,
        run = function() ui.remaining = not ui.remaining end }
    if loaded then ev[#ev + 1] = text(w - SIDE, BAR_Y + BAR_H / 2, 6, 15, "92908C", "+" .. clock(loaded), UI) end
    local inset = 14 + math.max(#left, #right) * 9.5
    local on_bar = ui.mouse.hover ~= false and ui.mouse.y >= BAR_Y - 12 and ui.mouse.y <= BAR_Y + BAR_H + 12
        and ui.mouse.x >= SIDE + inset and ui.mouse.x <= w - SIDE - inset - extra
    draw_bar(ev, SIDE + inset, w - SIDE - inset - extra, ui.drag_to or position, duration,
        spring("hover:bar", (on_bar or ui.drag_to) and 1 or 0), on_bar and ui.mouse.x or nil)

    local volume = mp.get_property_number("volume", 100)
    local muted = mp.get_property_bool("mute")
    local x = 32 + 23
    if (info.episodes and #info.episodes > 0) or mp.get_property_number("chapter-list/count", 0) > 0 then
        button(ev, hit, "list", x, function() open_at("list", "Episodes", 212) end)
        x = x + 50
    end
    local sound = mp.get_property_number("volume", 100)
    button(ev, hit, (muted or sound <= 0) and "muted" or sound < 50 and "quiet" or "volume", x,
        function() mp.commandv("cycle", "mute") end)
    local sx, sw = x + 28, 90
    local on_slider = ui.slide or over(sx - 10, ROW_Y - 22, sw + 20, 44)
    local heat = spring("hover:slider", on_slider and 1 or 0)
    local sh = 5 + 3 * heat
    local top_volume = math.max(100, mp.get_property_number("volume-max", 130))
    ui.top_volume = top_volume
    local level = muted and 0 or math.min(top_volume, volume) / top_volume
    local normal = 100 / top_volume
    ev[#ev + 1] = shape(rr(sx, ROW_Y - sh / 2, sw, sh, sh / 2), "FFFFFF", 190)
    if level > 0.01 then -- one bar in one piece: above 100 % it just changes colour
        local bar, split = rr(sx, ROW_Y - sh / 2, sw * level, sh, sh / 2), snap(sx + sw * normal)
        ev[#ev + 1] = shape(bar, "FFFFFF", 0, string.format("\\clip(%.2f,0,%.2f,720)", sx - 2, split))
        if level > normal then
            ev[#ev + 1] = shape(bar, accent(), 0, string.format("\\clip(%.2f,0,%.2f,720)", split, sx + sw + 2))
        end
    end
    if heat > 0.02 then
        local kx, k = sx + sw * spring("slider:x", level, 0.34), math.max(0.1, 8 * heat)
        ev[#ev + 1] = shape(rr(kx - 13, ROW_Y - 13, 26, 26, 13), accent(), 255 - 125 * math.min(1, heat), "\\blur6")
        ev[#ev + 1] = shape(rr(kx - k, ROW_Y - k, k * 2, k * 2, k), level > normal and accent() or "FFFFFF", 0)
        tips[#tips + 1] = function() tooltip(ev, string.format("%d", muted and 0 or sound), kx, heat) end
    end
    ui.slider = { x = sx, w = sw }

    local mid = w / 2
    local function skipper(name, label, cx, seconds)
        local sign = seconds > 0 and 1 or -1
        button(ev, hit, name, cx, function()
            local now = mp.get_time()
            ui.skip_sum = (ui.skip_at and now - ui.skip_at < 0.9 and (ui.skip_sum or 0) * seconds > 0) and ui.skip_sum + seconds or seconds
            ui.skip_at = now
            floats[#floats + 1] = { x = cx, y = ROW_Y - 30, t = 0, label = string.format("%+d s", ui.skip_sum) }
            mp.commandv("seek", tostring(seconds), "relative+exact")
        end, function(bx, y, scale, tap, hot, color)
            local shift = sign * (11 + 3 * math.min(1, hot) + 26 * math.abs(tap))
            icon(ev, name, bx + shift, y, scale * 0.84, 0, 40 - 40 * math.min(1, hot), color)
            ev[#ev + 1] = text(bx - sign * 15, y, 5, math.floor(17 * scale + 0.5), color, tostring(math.abs(seconds)), UI, true)
        end)
    end
    local step = tonumber(info.prefs and info.prefs.skip) or 10
    skipper("back", "Back " .. step .. " s", mid - 96, -step)
    local paused = mp.get_property_bool("pause")
    local hot = over(mid - 30, ROW_Y - 30, 60, 60)
    local glow, tap = spring("hover:play", hot and 1 or 0), spring("tap:play", 0, 0.26)
    local bloom = spring("near:play", near(mid), 0.3)
    local morph = spring("morph", paused and 1 or 0, 0.16)
    local r = 26 + 4 * bloom + 2 * glow - 12 * math.abs(tap)
    local py = ROW_Y - 3 * bloom
    if glow > 0.02 then
        ev[#ev + 1] = shape(rr(mid - r - 8, py - r - 8, (r + 8) * 2, (r + 8) * 2, r + 8), accent(), 255 - 120 * math.min(1, glow), "\\blur12")
    end
    ev[#ev + 1] = shape(rr(mid - r, py - r, r * 2, r * 2, r), "FFFFFF", 209 - 40 * math.min(1, math.max(0, glow)))
    local m = math.max(0, math.min(1, morph))
    local size = 1 + 0.14 * bloom - 0.3 * math.abs(tap)
    if m < 0.98 then icon(ev, "pause", mid, py, size * (1 - 0.45 * m), 90 * m, math.floor(255 * m)) end
    if m > 0.02 then icon(ev, "play", mid, py, size * (0.55 + 0.45 * m), -90 * (1 - m), math.floor(255 * (1 - m))) end
    hit[#hit + 1] = { x = mid - 30, y = ROW_Y - 30, w = 60, h = 60, run = function()
        clicked("play", mid)
        mp.commandv("cycle", "pause")
    end }
    skipper("forward", "Forward " .. step .. " s", mid + 96, step)

    local rx = w - 32 - 23
    local full = mp.get_property_bool("fullscreen")
    button(ev, hit, "full", rx, function() mp.commandv("cycle", "fullscreen") end)
    button(ev, hit, "tune", rx - 50, function() open_at("panel", "Playback", rx - 100) end)
    button(ev, hit, "audio", rx - 100, function() open_at("panel", "Audio", rx - 100) end)
    button(ev, hit, "subs", rx - 150, function() open_at("panel", "Subtitles", rx - 100) end)
    for _, show in ipairs(tips) do show() end
    draw_effects(ev)

    draw_menu(ev, hit)
    ui.hit = hit
    front.res_x, front.res_y, front.data = w, RES_Y, table.concat(ev, "\n")
    front:update()
end

local redraw -- defined below
local tick, last_draw = nil, 0
local FRAME = 0.008 -- no more than 120 redraws a second

local function keep_moving()
    if unsettled and not tick and ui.opacity > 0 then
        tick = mp.add_timeout(FRAME, function()
            tick = nil
            redraw()
        end)
    end
    unsettled = false
end

local function draw_hover()
    -- Under the mouse on the bar: the time, the part's name, thumbfast's
    -- picture when the user has it. Its own layer, it moves with the mouse.
    local b = ui.bar
    local t = b and ui.mouse.y >= BAR_Y - 12 and ui.mouse.y <= BAR_Y + BAR_H + 12
        and ui.mouse.x >= b.x0 and ui.mouse.x <= b.x1 and bar_time(ui.mouse.x)
    if not t or ui.opacity < 1 then
        if ui.thumb_on then
            mp.commandv("script-message-to", "thumbfast", "clear")
            ui.thumb_on = false
        end
        hover_layer:remove()
        return
    end
    local name = part_name(t)
    local label = clock(t) .. (name and name ~= "" and ("  " .. escape(fit(name, 24))) or "")
    local bw = 26 + #label * 9.5
    local x = math.max(bw / 2 + 8, math.min(ui.w - bw / 2 - 8, spring("pill:x", ui.mouse.x, 0.32)))
    local y = BAR_Y - 46
    hover_layer.res_x, hover_layer.res_y = ui.w, RES_Y
    hover_layer.data = table.concat({
        shape(rr(x - bw / 2, y, bw, 28, 14), "120F0E", 40),
        text(x, y + 14, 5, 16, "FFFFFF", label, UI, true),
    }, "\n")
    hover_layer:update()
    if thumb and thumb.available and thumb.width then
        local px, py = ui.scale, ui.scale
        local tx = math.max(8 * px, math.min(ui.w * px - thumb.width - 8 * px, x * px - thumb.width / 2))
        mp.commandv("script-message-to", "thumbfast", "thumb", tostring(t),
            tostring(math.floor(tx)), tostring(math.floor((y - 8) * py - thumb.height)))
        ui.thumb_on = true
    end
end

redraw = function()
    if ui.opacity <= 0 then return end
    local wait = FRAME - (mp.get_time() - last_draw)
    if wait > 0 then
        -- A burst of mouse moves becomes one redraw.
        if not tick then
            tick = mp.add_timeout(wait, function()
                tick = nil
                redraw()
            end)
        end
        return
    end
    last_draw = mp.get_time()
    draw_front()
    draw_hover()
    keep_moving()
end

-- Showing and hiding ----------------------------------------------------

local fade_timer, hide_timer
local want = false

local function must_stay()
    return ui.menu or ui.drag_to or ui.slide or ui.menu_drag or mp.get_property_bool("pause")
        or (ui.mouse.hover ~= false and ui.mouse.y >= ZONE_Y)
end

local set_bindings -- defined below

local function fade()
    local target = want and 1 or 0
    if ui.opacity == target then
        if fade_timer then fade_timer:kill() fade_timer = nil end
        if target == 0 then
            back:remove()
            front:remove()
            hover_layer:remove()
        end
        set_bindings()
        return
    end
    ui.opacity = want and math.min(1, ui.opacity + FADE_STEP) or math.max(0, ui.opacity - FADE_STEP)
    draw_back()
    if ui.opacity > 0 then redraw() end
end

-- Subtitles step up out of the way while the controls show.
local SUB_LIFT = 15
local sub_home = nil

local function lift_subs(on)
    if on and not sub_home then
        sub_home = mp.get_property_number("sub-pos", 100)
        mp.set_property_number("sub-pos", math.min(sub_home, 100 - SUB_LIFT))
    elseif not on and sub_home then
        mp.set_property_number("sub-pos", sub_home)
        sub_home = nil
    end
end

local function set_visible(on)
    if want == on and (on or ui.opacity == 0) then return end
    want = on
    lift_subs(on)
    if not fade_timer then fade_timer = mp.add_periodic_timer(0.03, fade) end
end

local function poke()
    set_visible(true)
    if hide_timer then hide_timer:kill() end
    hide_timer = mp.add_timeout(HIDE_AFTER, function()
        hide_timer = nil
        if must_stay() then poke() else set_visible(false) end
    end)
end

-- The mouse -------------------------------------------------------------

local function seek_to(x, exact)
    local t = bar_time(x)
    if t then
        ui.drag_to = t
        mp.commandv("seek", tostring(t), "absolute+" .. (exact and "exact" or "keyframes"))
    end
end

local function set_level(x)
    local s = ui.slider
    local level = math.max(0, math.min(1, (x - s.x) / s.w))
    mp.set_property_number("volume", math.floor(level * (ui.top_volume or 100) + 0.5))
    mp.set_property_bool("mute", false)
end

local function on_press()
    local mx, my = ui.mouse.x, ui.mouse.y
    local m = ui.menu
    local function in_box(b) return b and mx >= b[1] and mx <= b[1] + b[3] and my >= b[2] and my <= b[2] + b[4] end
    if m and (in_box(m.box) or (m.child and in_box(m.child.box))) then
        for n = #ui.hit, 1, -1 do
            local h = ui.hit[n]
            if h.keep ~= nil and mx >= h.x and mx <= h.x + h.w and my >= h.y and my <= h.y + h.h then
                if h.drag then
                    ui.menu_drag = h.drag
                    h.drag(mx)
                else
                    h.run()
                end
                if not h.keep and ui.menu == m then ui.menu = nil end
                mp.add_timeout(0.05, redraw)
                return
            end
        end
        return
    end
    local b = ui.bar
    if b and my >= BAR_Y - 12 and my <= BAR_Y + BAR_H + 12 and mx >= b.x0 and mx <= b.x1 then
        ui.menu = nil
        seek_to(mx)
        ui.dragging = true
        redraw()
        return
    end
    local s = ui.slider
    if s and math.abs(my - ROW_Y) <= 22 and mx >= s.x - 10 and mx <= s.x + s.w + 10 then
        ui.slide = true
        set_level(mx)
        return
    end
    for i = #ui.hit, 1, -1 do
        local h = ui.hit[i]
        if h.keep == nil and mx >= h.x and mx <= h.x + h.w and my >= h.y and my <= h.y + h.h then
            local had = ui.menu
            h.run()
            if had and ui.menu == had then ui.menu = nil end
            redraw()
            return
        end
    end
    ui.menu = nil
    redraw()
end

local function on_click(event)
    if event.event == "down" or event.event == "press" then
        on_press()
        set_bindings() -- a menu may have opened: its keys work right away
    end
    if event.event == "up" or event.event == "press" then
        if ui.dragging then seek_to(ui.mouse.x, true) end
        ui.dragging, ui.drag_to, ui.slide, ui.menu_drag = false, nil, false, nil
        redraw()
    end
end

local function on_wheel(step)
    for n = #ui.hit, 1, -1 do
        local h = ui.hit[n]
        if h.wheel and inside(h.x, h.y, h.w, h.h) then
            h.wheel(step)
            redraw()
            return
        end
    end
    if ui.menu and ui.menu.kind ~= "context" then
        if step < 0 and not ui.menu.more then return end
        ui.menu.top = math.max(0, ui.menu.top - step)
        redraw()
    elseif ui.menu then
        return
    elseif ui.mouse.y >= ZONE_Y then
        mp.commandv("add", "volume", tostring(step * 5))
    end
end

-- The keyboard, while a menu is open.
local function holder()
    local m = ui.menu
    return m and m.in_child and m.child or m
end

local function can_focus(r) return r.run or r.picks or r.slider end

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
    local m = ui.menu
    for i, name in ipairs(TABS[m.kind] or {}) do
        if name == m.tab then
            m.tab = TABS[m.kind][(i - 1 + by) % #TABS[m.kind] + 1]
            m.top, m.focus, m.placed = 0, nil, false
            return
        end
    end
end

local function side(by)
    local m, c = ui.menu, holder()
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
        elseif by < 0 and m.in_child then
            m.in_child, m.open, m.child = false, nil, nil
        end
    else
        switch_tab(by)
    end
end

local MENU_KEYS = {
    { "UP", function() move_focus(-1) end },
    { "DOWN", function() move_focus(1) end },
    { "LEFT", function() side(-1) end },
    { "RIGHT", function() side(1) end },
    { "TAB", function() if ui.menu.kind ~= "context" then switch_tab(1) end end },
    { "ENTER", function()
        local m, c = ui.menu, holder()
        local r = c and c.live and c.focus and c.live[c.focus]
        if not r then return end
        if r.sub then return side(1) end
        if r.run then
            r.run()
            if ui.menu == m and not r.stay then ui.menu = nil end
            set_bindings()
        end
    end },
    { "ESC", function()
        local m = ui.menu
        if m.in_child then
            m.in_child, m.open, m.child = false, nil, nil
        else
            ui.menu = nil
        end
        set_bindings()
    end },
}

local bound = false
set_bindings = function()
    -- Only while the mouse is on the controls (or a menu is open), so a click
    -- on the picture still does what the user's input.conf says.
    local on = ui.opacity > 0 and (ui.menu ~= nil or ui.dragging or ui.slide or ui.mouse.y >= ZONE_Y)
    if on ~= bound then
    bound = on
    -- A held button on the controls must not grab the window instead: the
    -- compositor would then keep the mouse's moves from reaching us.
    if on then
        ui.dragging_was = mp.get_property_bool("window-dragging")
        mp.set_property_bool("window-dragging", false)
    elseif ui.dragging_was ~= nil then
        mp.set_property_bool("window-dragging", ui.dragging_was)
    end
    if on then
        mp.add_forced_key_binding("MBTN_LEFT", "hub-ui-click", on_click, { complex = true })
        mp.add_forced_key_binding("MBTN_LEFT_DBL", "hub-ui-double", function() end)
        mp.add_forced_key_binding("WHEEL_UP", "hub-ui-up", function() on_wheel(1) end)
        mp.add_forced_key_binding("WHEEL_DOWN", "hub-ui-down", function() on_wheel(-1) end)
        mp.add_forced_key_binding("MBTN_RIGHT", "hub-ui-right", function()
            if ui.menu then
                ui.menu = nil
                set_bindings()
                redraw()
            end
        end)
    else
        for _, name in ipairs({ "hub-ui-click", "hub-ui-double", "hub-ui-up", "hub-ui-down", "hub-ui-right" }) do
            mp.remove_key_binding(name)
        end
    end
    end
    local keys = ui.menu ~= nil
    if keys ~= ui.keys_bound then
        ui.keys_bound = keys
        for _, k in ipairs(MENU_KEYS) do
            if keys then
                mp.add_forced_key_binding(k[1], "hub-ui-key-" .. k[1], function()
                    if ui.menu then
                        k[2]()
                        redraw()
                    end
                end, { repeatable = true })
            else
                mp.remove_key_binding("hub-ui-key-" .. k[1])
            end
        end
    end
end

mp.observe_property("mouse-pos", "native", function(_, m)
    if type(m) ~= "table" or not m.x then return end
    local x, y = m.x / ui.scale, m.y / ui.scale
    local moved = x ~= ui.mouse.x or y ~= ui.mouse.y
    ui.mouse = { x = x, y = y, hover = m.hover }
    if m.hover == false and not must_stay() then
        set_visible(false)
    elseif moved then
        poke()
    end
    if ui.dragging then seek_to(x) end
    if ui.slide then set_level(x) end
    if ui.menu_drag then ui.menu_drag(x) end
    set_bindings()
    if moved and ui.menu then
        ui.menu.kbd, ui.menu.in_child = false, false
        if ui.menu.child then ui.menu.child.kbd = false end
    end
    if moved then redraw() end
end)

-- What mpv tells us -----------------------------------------------------

mp.observe_property("osd-dimensions", "native", function(_, d)
    if type(d) ~= "table" or not d.h or d.h <= 0 then return end
    ui.w, ui.scale = math.floor(RES_Y * d.w / d.h), d.h / RES_Y
    if ui.opacity > 0 then
        draw_back()
        redraw()
    end
end)

local last_second = nil
mp.observe_property("time-pos", "number", function(_, t)
    local second = t and math.floor(t)
    if second == last_second then return end
    last_second = second
    redraw()
end)

mp.observe_property("pause", "bool", function(_, paused)
    if paused then poke() end
    redraw()
end)

for _, name in ipairs({ "volume", "mute", "track-list", "chapter-list", "duration", "fullscreen" }) do
    mp.observe_property(name, "native", function() redraw() end)
end

-- The right-click menu. A plain binding: the user's input.conf still wins.
local function context_rows()
    local function list(tab) return function() ui.menu = { kind = "list", tab = tab, top = 0 } end end
    local function opens(i) return function() ui.menu.open, ui.menu.child = i, { top = 0 } end end
    local paused = mp.get_property_bool("pause")
    return {
        { icon = paused and "play" or "pause", label = paused and "Play" or "Pause", key = "Space", run = function() mp.commandv("cycle", "pause") end },
        { icon = "subs", label = "Subtitles", sub = CONTENT.Subtitles, run = opens(2), stay = true },
        { icon = "audio", label = "Audio", sub = CONTENT.Audio, run = opens(3), stay = true },
        { icon = "tune", label = "Speed and sleep", sub = CONTENT.Playback, run = opens(4), stay = true },
        { icon = "film", label = "Video", sub = CONTENT.Video, run = opens(5), stay = true },
        { icon = "list", label = "Episodes", run = list("Episodes") },
        { icon = "list", label = "Chapters", run = list("Chapters") },
        { icon = "next", label = "Finish episode", key = "Enter", run = function() mp.commandv("script-message", "anime-hub-finish") end },
        { icon = "full", label = "Fullscreen", key = "f", run = function() mp.commandv("cycle", "fullscreen") end },
    }
end

mp.add_key_binding("MBTN_RIGHT", "soft-context", function()
    poke()
    ui.menu = { kind = "context", x = ui.mouse.x, y = ui.mouse.y, rows = context_rows, top = 0 }
    set_bindings()
    redraw()
end)

mp.register_script_message("anime-hub-info", function(raw)
    local data = utils.parse_json(raw or "")
    if type(data) == "table" then
        info = data
        if type(data.sleep) == "table" and type(data.sleep.left) == "number" then
            ui.sleep_minutes, ui.sleep_end = data.sleep.minutes, mp.get_time() + data.sleep.left
        end
        redraw()
    end
end)

mp.register_script_message("anime-hub-segments", function(raw)
    local data = utils.parse_json(raw or "")
    segments = {}
    if type(data) ~= "table" or data.marks ~= true or type(data.segments) ~= "table" then return end
    for _, s in ipairs(data.segments) do
        if type(s) == "table" and type(s.start) == "number" and type(s["end"]) == "number" then
            segments[#segments + 1] = { kind = s.kind, start = s.start, stop = s["end"] }
        end
    end
    redraw()
end)

mp.observe_property("demuxer-cache-state", "native", function(_, c)
    ui.loaded = type(c) == "table" and c["seekable-ranges"] or nil
    local ahead = type(c) == "table" and c["cache-duration"] or nil
    local shown = ahead and math.floor(ahead)
    if shown ~= ui.ahead then
        ui.ahead = shown
        if mp.get_property_bool("pause") then redraw() end -- playing, the clock's tick shows it
    end
end)

mp.register_script_message("thumbfast-info", function(raw)
    thumb = utils.parse_json(raw or "")
end)

mp.register_event("start-file", function()
    info, segments, ui.menu = {}, {}, nil
end)

mp.register_event("file-loaded", function()
    -- Skins built on mpv's own OSC (ModernZ among them) step aside for us;
    -- thumbfast, if there, says what it can do.
    mp.commandv("script-message", "osc-visibility", "never", "no-osd")
    mp.commandv("script-message-to", "thumbfast", "thumbfast-info")
    poke()
end)

back = mp.create_osd_overlay("ass-events")
front = mp.create_osd_overlay("ass-events")
hover_layer = mp.create_osd_overlay("ass-events")
back.z, front.z, hover_layer.z = -10, 0, 10
