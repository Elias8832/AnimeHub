-- Anime Hub: skip openings, endings and credits (Enter), Decision 39.
-- Outside an opening/ending, Enter quits mpv and leaves a marker file
-- ($ANIME_HUB_SKIP_MARKER, else beside the IPC socket); the server treats that exit as "episode finished"
-- (marks it watched) and autoplays the next episode. A plain quit stays a
-- plain quit: without the marker, ending early just stops playback.
-- The hub sends this file's opening/ending ranges over IPC
-- (script-message anime-hub-segments {"segments": [...]}). Inside one,
-- Enter skips it; "auto" ranges skip themselves the first time playback
-- reaches them (rewinding into one lets you watch it). Skipping an ending
-- jumps to what follows it (a post-credits scene, a preview); only one
-- with nothing after it finishes the episode like Enter does.
-- Loaded via --script, so your own input.conf is untouched; a personal
-- ENTER binding would win over this.
local utils = require "mp.utils"

local NAMES = { op = "opening", ed = "ending" }
local segments = {}
local current = nil

-- The prompt: a dark rounded button in the bottom-right corner with the
-- app's ↵ key badge, drawn as ASS on an OSD overlay (mpv's plain OSD text
-- sits top-left and looks like a debug line). Clicking it does what Enter
-- does; it lights up under the mouse and stays while the mouse is on it.
-- Coordinates are in a 720-high space whose width follows the window's
-- aspect.
local RES_Y, FS, KFS = 720, 22, 16
local H, PAD, GAP, KW, KH, MARGIN, BOTTOM = 46, 22, 12, 30, 26, 36, 92
local pill = mp.create_osd_overlay("ass-events")
local ruler = mp.create_osd_overlay("ass-events")
ruler.compute_bounds, ruler.hidden = true, true
local shown, hide_timer = nil, nil
local skip, segment_at, show -- defined below

local function rounded(w, h, r)
    local c = r * 0.5523
    return string.format("m %.1f 0 l %.1f 0 b %.1f 0 %.1f %.1f %.1f %.1f l %.1f %.1f b %.1f %.1f %.1f %.1f %.1f %.1f "
        .. "l %.1f %.1f b %.1f %.1f 0 %.1f 0 %.1f l 0 %.1f b 0 %.1f %.1f 0 %.1f 0",
        r, w - r, w - r + c, w, r - c, w, r, w, h - r, w, h - r + c, w - r + c, h, w - r, h,
        r, h, r - c, h, h - r + c, h - r, r, r - c, r - c, r)
end

local function text_width(tags, text, res_x)
    ruler.res_x, ruler.res_y, ruler.data = res_x, RES_Y, "{\\an7\\pos(0,0)" .. tags .. "}" .. text
    local b = ruler:update()
    if b and b.x1 and b.x0 then return b.x1 - b.x0 end
    return #text * 0.55 * FS
end

-- A player look draws the button itself, as part of its controls: this script
-- only tells it what to say and when (Decision 56).
local look = false

local function tell_look(label, click, idle)
    mp.commandv("script-message", "anime-hub-skip-state", label or "", click and "1" or "0", idle and "1" or "0")
end

local function draw()
    if not shown or look then return end
    local dim = mp.get_property_native("osd-dimensions") or {}
    local res_x = (dim.w and dim.h and dim.h > 0) and math.floor(RES_Y * dim.w / dim.h) or 1280
    local label_tags = "\\fs" .. FS .. "\\b1\\bord0\\shad0"
    local key_tags = "\\fs" .. KFS .. "\\b0\\bord0\\shad0"
    local tw = text_width(label_tags, shown.label, res_x)
    local w = PAD + tw + (shown.key and (GAP + KW + 10) or PAD)
    local x, y = res_x - MARGIN - w, RES_Y - BOTTOM - H
    shown.rect, shown.scale = { x = x, y = y, w = w, h = H }, (dim.h and dim.h > 0) and dim.h / RES_Y or 1
    local fill, border = shown.hover and "\\1c&H45403C&\\1a&H00&" or "\\1c&H1A1614&\\1a&H28&",
        shown.hover and "\\3a&H30&" or "\\3a&HB0&"
    local ev = {
        string.format("{\\an7\\pos(%.1f,%.1f)\\bord1\\shad0%s\\3c&HFFFFFF&%s\\p1}%s{\\p0}",
            x, y, fill, border, rounded(w, H, H / 2)),
        string.format("{\\an4\\pos(%.1f,%.1f)%s\\1c&HFFFFFF&}%s", x + PAD, y + H / 2, label_tags, shown.label),
    }
    if shown.key then
        -- The app's key badge (.kb): a rounded square, 8 % white, ↵ at 60 %.
        local kx, ky = x + PAD + tw + GAP, y + (H - KH) / 2
        ev[#ev + 1] = string.format("{\\an7\\pos(%.1f,%.1f)\\bord0\\shad0\\1c&HFFFFFF&\\1a&HEA&\\p1}%s{\\p0}",
            kx, ky, rounded(KW, KH, 7))
        ev[#ev + 1] = string.format("{\\an5\\pos(%.1f,%.1f)%s\\1c&HF1F3F3&\\1a&H66&}↵", kx + KW / 2, ky + KH / 2, key_tags)
    end
    pill.res_x, pill.res_y, pill.data = res_x, RES_Y, table.concat(ev, "\n")
    pill:update()
end

-- `click`: a click is on the window by definition; for hovering, a mouse
-- that left the window doesn't count.
local function inside(m, click)
    if not (shown and shown.rect and type(m) == "table" and m.x and (click or m.hover ~= false)) then return false end
    local r, x, y = shown.rect, m.x / shown.scale, m.y / shown.scale
    return x >= r.x and x <= r.x + r.w and y >= r.y and y <= r.y + r.h
end

local function hide()
    if hide_timer then hide_timer:kill() hide_timer = nil end
    if shown and shown.key and not look then mp.remove_key_binding("anime-hub-skip-click") end
    shown = nil
    pill:remove()
    if look then tell_look() end
end

local function expire()
    hide_timer = nil
    if shown and shown.hover then
        hide_timer = mp.add_timeout(1, expire)
    else
        hide()
        if look and current then tell_look(nil, false, true) end -- the mouse can bring it back
    end
end

show = function(label, key, seconds)
    hide()
    shown = { label = label, key = key }
    if look then
        tell_look(label, key)
        hide_timer = mp.add_timeout(seconds, expire)
        return
    end
    if key then
        -- A plain (not forced) binding: a click binding in your own
        -- input.conf still wins. Clicks outside the button do nothing.
        mp.add_key_binding("MBTN_LEFT", "anime-hub-skip-click", function()
            -- Only ever skips the range it names; never finishes by accident.
            local seg = segment_at(mp.get_property_number("time-pos"))
            if seg and inside(mp.get_property_native("mouse-pos"), true) then skip(seg) end
        end)
    end
    draw()
    -- Lit at once if the mouse already rests where it appears.
    if key and inside(mp.get_property_native("mouse-pos")) then
        shown.hover = true
        draw()
    end
    hide_timer = mp.add_timeout(seconds, expire)
end

mp.observe_property("osd-dimensions", "native", function() draw() end)

mp.observe_property("mouse-pos", "native", function(_, m)
    if not (shown and shown.key) then return end
    local hover = inside(m)
    if hover ~= shown.hover then
        shown.hover = hover
        draw()
    end
end)

local function marker_path()
    -- The hub names the marker (a Windows pipe has no folder beside it);
    -- older hubs look beside the socket.
    local marker = os.getenv("ANIME_HUB_SKIP_MARKER")
    local sock = mp.get_property("input-ipc-server")
    if (not marker or marker == "") and sock and sock ~= "" then marker = sock .. ".skip" end
    return marker
end

local function finish()
    local marker = marker_path()
    if marker and marker ~= "" then
        local f = io.open(marker, "w")
        if f then f:close() end
    end
    mp.commandv("quit")
end

-- The right-click menu's Finish episode is Enter's "finished". A pick from the player look's script's Episodes list quits with the episode's id in
-- a file, and the hub starts it.
mp.register_script_message("anime-hub-finish", finish)
mp.register_script_message("anime-hub-skip-look", function()
    if look then return end
    -- A button already up (a look that spoke late) moves over to the look.
    local up = shown
    if up then
        if up.key then mp.remove_key_binding("anime-hub-skip-click") end
        pill:remove()
    end
    look = true
    if up then tell_look(up.label, up.key) end
end)
mp.register_script_message("anime-hub-skip-hover", function(on)
    if shown then shown.hover = on == "1" end
end)
mp.register_script_message("anime-hub-skip-click", function()
    local seg = segment_at(mp.get_property_number("time-pos"))
    if seg then skip(seg) end
end)
mp.register_script_message("anime-hub-skip-wake", function()
    local seg = segment_at(mp.get_property_number("time-pos"))
    if seg then show(seg.finish and "Finish episode" or ("Skip " .. NAMES[seg.kind]), true, 8) end
end)
mp.register_script_message("anime-hub-go", function(id)
    local marker = marker_path()
    local f = marker and marker ~= "" and io.open(marker .. ".go", "w")
    if not f then return end
    f:write(id)
    f:close()
    mp.commandv("quit")
end)

skip = function(seg)
    seg.done = true
    if seg.finish then
        finish()
        return
    end
    mp.commandv("seek", tostring(seg.stop), "absolute", "exact")
    show("Skipped " .. NAMES[seg.kind], false, 1.5)
end

segment_at = function(t)
    if type(t) ~= "number" then return nil end
    for _, seg in ipairs(segments) do
        if t >= seg.start and t < seg.stop - 0.5 then
            return seg
        end
    end
    return nil
end

-- Only well-formed ranges are kept: a known kind, numbers, start < end.
local function parse(raw)
    local out = {}
    local data = utils.parse_json(raw or "")
    if type(data) ~= "table" or type(data.segments) ~= "table" then return out end
    for _, s in ipairs(data.segments) do
        if type(s) == "table" and NAMES[s.kind] and type(s.start) == "number"
                and type(s["end"]) == "number" and s.start >= 0 and s["end"] > s.start then
            table.insert(out, { kind = s.kind, start = s.start, stop = s["end"],
                                auto = s.auto == true, finish = s.finish == true })
        end
    end
    return out
end

mp.observe_property("time-pos", "number", function(_, t)
    local seg = segment_at(t)
    if seg == current then return end
    current = seg
    if not seg then
        if shown and shown.key then hide() end
        return
    end
    if seg.auto and not seg.done then
        skip(seg)
    else
        show(seg.finish and "Finish episode" or ("Skip " .. NAMES[seg.kind]), true, 8)
    end
end)

-- Openings and endings on the timeline: chapters at their start and end,
-- so mpv's seek bar marks them and names them under the mouse. A spot that
-- already has a chapter (a file's own "Opening") keeps it.
local function mark_timeline()
    local list = mp.get_property_native("chapter-list") or {}
    local function add(time, title)
        for _, c in ipairs(list) do
            if math.abs(c.time - time) < 1 then return end
        end
        list[#list + 1] = { time = time, title = title }
    end
    for _, seg in ipairs(segments) do
        add(seg.start, seg.kind == "op" and "Opening" or "Ending")
        if not seg.finish then add(seg.stop, "Episode") end
    end
    table.sort(list, function(a, b) return a.time < b.time end)
    mp.set_property_native("chapter-list", list)
end

mp.register_script_message("anime-hub-segments", function(raw)
    segments = parse(raw)
    current = nil
    local data = utils.parse_json(raw or "")
    if type(data) == "table" and data.marks == true then mark_timeline() end
end)

mp.register_event("start-file", function()
    segments = {}
    current = nil
    hide()
end)

-- The player's Sub/Dub switch (TODO 153): a note at the top while the other
-- language loads (the episode is paused from the pick until the new player
-- shows), or why it can't. Drawn like the app's toast (#toast): a dark
-- rounded box with a soft shadow and a thin light edge, a round badge in the
-- show's colour with a turning ring, then the words; it springs in from
-- below. The hub sends anime-hub-switch {"state": "loading" | "failed" |
-- "off", "text"}; the looks send "loading" themselves the moment it's picked.
local note = mp.create_osd_overlay("ass-events")
note.z = 20
local noted, note_timer, note_hide = nil, nil, nil
local note_accent = "6FA4F2" -- the app's amber (#f2a46f), until the hub names the show's colour
local BAD = "8F8AEF"         -- the app's --bad (#ef8a8f)
local NOTE_H, NOTE_R, DISC = 46, 18, 28
local NOTE_FONT = "\\fnHanken Grotesk\\fs18\\b0\\bord0\\shad0"
local NOTE_IN, NOTE_OUT = 0.4, 0.2

local function spring(k)
    -- Settles with a small overshoot, like the app's --spring.
    local c1 = 1.6
    return 1 + (c1 + 1) * (k - 1) ^ 3 + c1 * (k - 1) ^ 2
end

local function ring_path(cx, cy, r, width, from, to)
    -- A band between two circles from one angle to another (degrees).
    local pts, steps = {}, math.max(8, math.floor(math.abs(to - from) / 6))
    for i = 0, steps do
        local a = math.rad(from + (to - from) * i / steps)
        pts[#pts + 1] = string.format("%.2f %.2f", cx + (r + width / 2) * math.cos(a), cy + (r + width / 2) * math.sin(a))
    end
    for i = steps, 0, -1 do
        local a = math.rad(from + (to - from) * i / steps)
        pts[#pts + 1] = string.format("%.2f %.2f", cx + (r - width / 2) * math.cos(a), cy + (r - width / 2) * math.sin(a))
    end
    return "m " .. pts[1] .. " l " .. table.concat(pts, " ", 2)
end

local function bar_path(x1, y1, x2, y2, width)
    local dx, dy = x2 - x1, y2 - y1
    local len = math.sqrt(dx * dx + dy * dy)
    local nx, ny = -dy / len * width / 2, dx / len * width / 2
    return string.format("m %.2f %.2f l %.2f %.2f l %.2f %.2f l %.2f %.2f",
        x1 + nx, y1 + ny, x2 + nx, y2 + ny, x2 - nx, y2 - ny, x1 - nx, y1 - ny)
end

local function shape(x, y, color, a, path, extra)
    return string.format("{\\an7\\pos(%.2f,%.2f)\\bord0\\shad0%s\\1c&H%s&\\1a&H%02X&\\p1}%s{\\p0}",
        x, y, extra or "", color, math.max(0, math.min(255, math.floor(a + 0.5))), path)
end

local function draw_note()
    if not noted then
        note:remove()
        return
    end
    local now = mp.get_time()
    local k_in = math.min(1, (now - noted.t0) / NOTE_IN)
    local k_out = noted.leaving and math.min(1, (now - noted.leaving) / NOTE_OUT) or 0
    if k_out >= 1 then
        noted = nil
        note:remove()
        return
    end
    local show = math.min(1, k_in * 2.5) * (1 - k_out)  -- opacity
    local lift = (1 - spring(k_in)) * 14 + k_out * 6
    local function fade(a) return 255 - (255 - a) * show end
    local dim = mp.get_property_native("osd-dimensions") or {}
    local res_x = (dim.w and dim.h and dim.h > 0) and math.floor(RES_Y * dim.w / dim.h) or 1280
    local busy = noted.state == "loading"
    local tw = text_width(NOTE_FONT, noted.text, res_x)
    local w = 9 + DISC + 11 + tw + 20
    local x, y = (res_x - w) / 2, 30 + lift
    local tint = busy and note_accent or BAD
    local cx, cy = x + 9 + DISC / 2, y + NOTE_H / 2
    local ev = {
        -- the shadow: 0 18px 44px -12px rgba(0,0,0,.65)
        shape(x + 10, y + 12, "000000", fade(0x70), rounded(w - 20, NOTE_H - 6, NOTE_R), "\\blur14"),
        -- the box with its thin light edge (--edge)
        string.format("{\\an7\\pos(%.2f,%.2f)\\bord1\\shad0\\1c&H221D1C&\\1a&H%02X&\\3c&HFFFFFF&\\3a&H%02X&\\p1}%s{\\p0}",
            x, y, math.floor(fade(0x2A)), math.floor(fade(0xE6)), rounded(w, NOTE_H, NOTE_R)),
        -- the badge: the colour at 16 %
        shape(cx - DISC / 2, cy - DISC / 2, tint, fade(0xD6), rounded(DISC, DISC, DISC / 2)),
    }
    if busy then
        local turn = (now * 450) % 360
        ev[#ev + 1] = shape(0, 0, "FFFFFF", fade(0xD6), ring_path(cx, cy, 6.5, 2.2, 0, 360))
        ev[#ev + 1] = shape(0, 0, tint, fade(0), ring_path(cx, cy, 6.5, 2.2, turn, turn + 100))
    else
        ev[#ev + 1] = shape(0, 0, tint, fade(0), bar_path(cx - 4, cy - 4, cx + 4, cy + 4, 2))
        ev[#ev + 1] = shape(0, 0, tint, fade(0), bar_path(cx + 4, cy - 4, cx - 4, cy + 4, 2))
    end
    ev[#ev + 1] = string.format("{\\an4\\pos(%.2f,%.2f)%s\\1c&HF1F3F3&\\1a&H%02X&}%s",
        x + 9 + DISC + 11, cy, NOTE_FONT, math.floor(fade(0)), noted.text)
    note.res_x, note.res_y, note.data = res_x, RES_Y, table.concat(ev, "\n")
    note:update()
end

local function note_tick()
    draw_note()
    local now = mp.get_time()
    local moving = noted and (noted.state == "loading" or noted.leaving or now - noted.t0 < NOTE_IN)
    if not moving and note_timer then
        note_timer:kill()
        note_timer = nil
    end
end

local function note_run()
    if not note_timer then note_timer = mp.add_periodic_timer(1 / 30, note_tick) end
end

local function set_note(state, text)
    if note_hide then note_hide:kill() note_hide = nil end
    if state == "loading" or state == "failed" then
        local again = noted and not noted.leaving
        noted = { state = state, text = text, t0 = again and noted.t0 or mp.get_time() }
        if state == "failed" then
            note_hide = mp.add_timeout(5, function()
                note_hide = nil
                if noted then noted.leaving = mp.get_time() note_run() end
            end)
        end
    elseif noted and not noted.leaving then
        noted.leaving = mp.get_time()
    end
    note_run()
    draw_note()
end

mp.register_script_message("anime-hub-switch", function(raw)
    local data = utils.parse_json(raw or "")
    if type(data) ~= "table" then return end
    local text = type(data.text) == "string" and data.text ~= "" and data.text or "Switching audio…"
    set_note(data.state, text)
end)
mp.register_script_message("anime-hub-info", function(raw)
    local data = utils.parse_json(raw or "")
    local hex = type(data) == "table" and type(data.accent) == "string" and data.accent:match("^#(%x%x%x%x%x%x)$")
    if hex then note_accent = hex:sub(5, 6) .. hex:sub(3, 4) .. hex:sub(1, 2) end
end)
mp.observe_property("osd-dimensions", "native", function() if noted then draw_note() end end)

mp.add_key_binding("ENTER", "anime-hub-skip-credits", function()
    local seg = segment_at(mp.get_property_number("time-pos"))
    if seg then
        skip(seg)
    else
        finish()
    end
end)
