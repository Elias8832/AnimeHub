"""Seekbar thumbnail preview tests (player-thumb.lua + Float/Dock integration).

Covers:
- LuaJIT unit scenarios for mpv/player-thumb.lua:
  * Online streams (preloaded stretch): uses `dump-cache` + idle local slice
    helper with 20 ms throttle and ZERO network helper spawns / ZERO Loading
    flash.
  * Online streams (unbuffered stretch): lazy-spawns a network helper mpv
    forwarding referrer, http-header-fields (Origin), user-agent,
    demuxer-lavf-format, tls-verify, and cookies with a 16MiB buffer cap,
    180 ms debounce, and shared 48-frame LRU cache (0 ms repeat hits).
  * mode="local" disables previews on network streams while working on disk.
  * Prepared runs (prepare.py's AHEAD_LUA with vo=null) stay dormant until a
    real VO appears.
  * Network helper startup failure before any frame stops loading cleanly.
  * Float (player-soft.lua) and Dock (player-deck.lua) route requests to
    player_thumb when anime-hub-thumb-info arrives, show Loading only when
    `loading=true`, honor prefs.thumb="off", and fall back to thumbfast only
    when player-thumb.lua is absent.
- End-to-end real mpv + real helper mpv tests against an extensionless HTTP
  HLS stream enforcing Referer + Origin headers (403 without them):
  * Preloaded stream (`--cache=yes`): hovering buffered timestamps decodes
    distinct BGRA frames via `dump-cache` with ZERO extra HTTP requests!
  * Unbuffered stream (`--cache=no`): hovering unbuffered timestamps forwards
    Referer + Origin + HLS format to the network helper and decodes distinct
    BGRA frames with zero 403s, cleaning up all temp files on exit.
"""

import hashlib
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from tests import support  # noqa: F401
from animehub import system

ROOT = Path(__file__).resolve().parent.parent
MPV_DIR = ROOT / "mpv"
LUA = shutil.which("luajit") or shutil.which("lua")

THUMB_HARNESS = r"""
local script_path = arg[1]
local scenario = arg[2]
local tmp_dir = arg[3]

local now = 0
local timers = {}
local next_tid = 1
local props = {
    platform = "linux",
    path = "/anime/ep01.mkv",
    ["stream-open-filename"] = "/anime/ep01.mkv",
    ["demuxer-via-network"] = false,
    ["demuxer-cache-state"] = {},
    ["video-out-params"] = { dw = 1920, dh = 1080 },
    ["video-params"] = { rotate = 0, primaries = "bt.709" },
    ["osd-dimensions"] = { w = 2560, h = 1440 },
    ["current-vo"] = "gpu-next",
    ["current-tracks/video"] = { image = false },
    vid = 1,
    edition = "auto",
    duration = 1440,
    ["time-pos"] = 10,
    ["media-controls"] = true,
    referrer = "",
    ["http-header-fields"] = {},
    ["user-agent"] = "",
    ["demuxer-lavf-format"] = "",
    ["tls-verify"] = true,
    cookies = false,
    ["cookies-file"] = "",
}
local opt_overrides = {}
local observers = {}
local events = {}
local messages = {}
local sent_messages = {}
local subprocesses = {}
local overlays = {}
local dumps = {}

local function clone_shallow(v)
    if type(v) ~= "table" then return v end
    local out = {}
    for k, val in pairs(v) do out[k] = val end
    return out
end

local function make_timer(delay, fn, periodic)
    local t = {
        id = next_tid,
        timeout = math.max(1e-6, delay or 0),
        at = now + math.max(1e-6, delay or 0),
        fn = fn,
        periodic = periodic,
        alive = true,
    }
    next_tid = next_tid + 1
    function t:kill() self.alive = false end
    function t:is_enabled() return self.alive end
    function t:resume()
        self.alive = true
        self.at = now + math.max(1e-6, self.timeout or 0)
    end
    timers[#timers + 1] = t
    return t
end

local function advance(dt)
    local target = now + dt
    while true do
        local best = nil
        for i = 1, #timers do
            local t = timers[i]
            if t.alive and t.at <= target + 1e-9 then
                if not best or t.at < best.at or (t.at == best.at and t.id < best.id) then
                    best = t
                end
            end
        end
        if not best then break end
        now = best.at
        if best.periodic then
            best.at = now + math.max(1e-6, best.timeout or 0)
        else
            best.alive = false
        end
        best.fn()
    end
    now = target
end

local function set_prop(name, val)
    props[name] = val
    for _, obs in ipairs(observers[name] or {}) do
        obs(name, clone_shallow(val))
    end
end

local function emit_event(name)
    for _, fn in ipairs(events[name] or {}) do fn() end
end

local function emit_msg(name, ...)
    for _, fn in ipairs(messages[name] or {}) do fn(...) end
end

_G.mp = {
    get_time = function() return now end,
    get_script_name = function() return "player_thumb" end,
    add_timeout = function(delay, fn) return make_timer(delay, fn, false) end,
    add_periodic_timer = function(delay, fn) return make_timer(delay, fn, true) end,
    observe_property = function(name, kind, fn)
        observers[name] = observers[name] or {}
        observers[name][#observers[name] + 1] = fn
    end,
    register_event = function(name, fn)
        events[name] = events[name] or {}
        events[name][#events[name] + 1] = fn
    end,
    register_script_message = function(name, fn)
        messages[name] = messages[name] or {}
        messages[name][#messages[name] + 1] = fn
    end,
    get_property = function(name, def)
        local v = props[name]
        if v == nil then return def end
        return tostring(v)
    end,
    get_property_bool = function(name, def)
        local v = props[name]
        if v == nil then return def or false end
        return v == true
    end,
    get_property_number = function(name, def)
        local v = props[name]
        if type(v) == "number" then return v end
        return def
    end,
    get_property_native = function(name, def)
        local v = props[name]
        if v == nil then return def end
        return clone_shallow(v)
    end,
    commandv = function(...)
        sent_messages[#sent_messages + 1] = { ... }
    end,
    command_native_async = function(cmd, cb)
        if type(cmd) == "table" and cmd.name == "subprocess" then
            subprocesses[#subprocesses + 1] = { args = cmd.args, cb = cb }
            return
        end
        if type(cmd) == "table" and cmd[1] == "dump-cache" then
            dumps[#dumps + 1] = { start = cmd[2], stop = cmd[3], path = cmd[4] }
            local f = io.open(cmd[4], "wb")
            if f then f:write("MKVSLICE"); f:close() end
            if cb then cb(true, {}) end
            return
        end
        if type(cmd) == "table" and (cmd[1] == "overlay-add" or cmd[1] == "overlay-remove") then
            overlays[#overlays + 1] = cmd
        end
        if cb then cb(true, {}) end
    end,
}

local function json_esc(s)
    return '"' .. tostring(s):gsub('\\', '\\\\'):gsub('"', '\\"') .. '"'
end
local function to_json(v)
    local tv = type(v)
    if tv == "table" then
        local is_arr = #v > 0
        if is_arr then
            local parts = {}
            for i = 1, #v do parts[i] = to_json(v[i]) end
            return "[" .. table.concat(parts, ",") .. "]"
        end
        local parts = {}
        for k, val in pairs(v) do
            parts[#parts + 1] = json_esc(k) .. ":" .. to_json(val)
        end
        return "{" .. table.concat(parts, ",") .. "}"
    elseif tv == "string" then return json_esc(v)
    elseif tv == "boolean" then return v and "true" or "false"
    elseif tv == "number" then return tostring(v)
    end
    return "null"
end

local function parse_json_simple(s)
    if type(s) ~= "string" then return nil end
    local out = {}
    for k, v in s:gmatch('"([^"]+)"%s*:%s*"([^"]*)"') do out[k] = v end
    for k, v in s:gmatch('"([^"]+)"%s*:%s*(true)') do out[k] = true end
    for k, v in s:gmatch('"([^"]+)"%s*:%s*(false)') do out[k] = false end
    for k, v in s:gmatch('"([^"]+)"%s*:%s*(%-?%d+%.?%d*)') do out[k] = tonumber(v) end
    local pref_block = s:match('"prefs"%s*:%s*(%b{})')
    if pref_block then out.prefs = parse_json_simple(pref_block) end
    return out
end

package.preload["mp.utils"] = function()
    return {
        getpid = function() return 424242 end,
        format_json = function(t) return to_json(t) end,
        parse_json = parse_json_simple,
        readdir = function(path, filter)
            local p = io.popen('ls -1 "' .. path .. '" 2>/dev/null')
            if not p then return {} end
            local out = {}
            for line in p:lines() do out[#out + 1] = line end
            p:close()
            return out
        end,
        file_info = function(path)
            local f = io.open(path, "rb")
            if not f then return nil end
            local sz = f:seek("end")
            f:close()
            return { size = sz, is_file = true }
        end,
    }
end

package.preload["mp.options"] = function()
    return {
        read_options = function(t, name)
            for k, v in pairs(opt_overrides) do t[k] = v end
        end,
    }
end

local orig_getenv = os.getenv
os.getenv = function(k)
    if k == "XDG_RUNTIME_DIR" then return tmp_dir end
    return orig_getenv(k)
end

if scenario == "local_mode_on_network" then
    opt_overrides.mode = "local"
end

dofile(script_path)

local function sync_props()
    for k, v in pairs(props) do set_prop(k, v) end
end

local function find_helper_paths(args)
    local out_p, helper_p
    for _, a in ipairs(args) do
        if a:sub(1, 4) == "--o=" then out_p = a:sub(5) end
        if a:sub(1, 9) == "--script=" then helper_p = a:sub(10) end
    end
    local base = helper_p and helper_p:gsub("%-helper%.lua$", "")
    local root = base and base:gsub("%-[a-z]+%-%d+$", "")
    return base and (base .. "-cmd.txt"), base and (base .. "-done.txt"), out_p, root
end

local function count_net_spawns()
    local n = 0
    for _, sp in ipairs(subprocesses) do
        for _, a in ipairs(sp.args) do
            if a == "--demuxer-max-bytes=16MiB" then n = n + 1; break end
        end
    end
    return n
end

local function find_net_spawn()
    for _, sp in ipairs(subprocesses) do
        for _, a in ipairs(sp.args) do
            if a == "--demuxer-max-bytes=16MiB" then return sp end
        end
    end
    return nil
end

if scenario == "online_headers_debounce_cache" then
    props.path = "https://cdn.example/stream/master"
    props["stream-open-filename"] = "https://cdn.example/stream/master"
    props["demuxer-via-network"] = true
    props.referrer = "https://miruro.example/"
    props["http-header-fields"] = { "Origin: https://miruro.example", "X-Test: 1" }
    props["user-agent"] = "TestAgent/1.0"
    props["demuxer-lavf-format"] = "hls"
    props["tls-verify"] = false
    props.cookies = true
    props["cookies-file"] = "/tmp/cookies.txt"
    sync_props()
    emit_event("file-loaded")

    -- Idle local slice helper pre-spawns (zero network), network helper stays lazy.
    local net_before_hover = count_net_spawns()

    -- Rapid scrub across 3 unbuffered timestamps within debounce window: only the last spawns net helper.
    emit_msg("thumb", "120", "300", "900")
    advance(0.05)
    emit_msg("thumb", "240", "400", "900")
    advance(0.05)
    emit_msg("thumb", "480", "500", "900")
    local net_mid_debounce = count_net_spawns()
    advance(0.25)
    local net_after_debounce = count_net_spawns()

    local nsp = find_net_spawn()
    local cmd_p, done_p, _, root = find_helper_paths(nsp.args)
    local dest480 = root .. "-b480.bgra"
    local df = io.open(dest480, "wb"); df:write("FRAME480"); df:close()
    local dn = io.open(done_p, "wb"); dn:write("1\t480\t" .. dest480 .. "\n"); dn:close()
    advance(0.05)

    local overlays_after_first = #overlays

    -- Move to another uncached timestamp 720 -> old overlay removed immediately (shows Loading).
    emit_msg("thumb", "720", "700", "900")
    local last_ov_on_miss = overlays[#overlays] and overlays[#overlays][1]
    advance(0.25)
    local cf = io.open(cmd_p, "rb"); local cmd_text = cf and cf:read("*a") or ""; if cf then cf:close() end

    -- Move back to cached bucket 480 -> instant 0 ms overlay-add, no new seek.
    local ov_before_hit = #overlays
    emit_msg("thumb", "481", "505", "900")
    local hit_ov = overlays[#overlays]
    emit_event("shutdown")

    print(to_json({
        net_before_hover = net_before_hover,
        net_mid_debounce = net_mid_debounce,
        net_after_debounce = net_after_debounce,
        args = nsp.args,
        overlays_after_first = overlays_after_first,
        last_ov_on_miss = last_ov_on_miss,
        cmd_has_seek_720 = cmd_text:find("^seek\t%d+\t720%.000\t720\t") ~= nil,
        cache_hit_immediate = (#overlays > ov_before_hit) and hit_ov[1] == "overlay-add" and hit_ov[5] == dest480,
        cleaned_cache = io.open(dest480, "rb") == nil,
    }))

elseif scenario == "online_preloaded_uses_dump_cache_instantly" then
    props.path = "https://cdn.example/stream/master"
    props["demuxer-via-network"] = true
    props["demuxer-cache-state"] = { ["seekable-ranges"] = { { start = 0, ["end"] = 600 } } }
    sync_props()
    emit_event("file-loaded")
    advance(0.3)

    -- Hover inside preloaded range (t=120): 20 ms throttle (NOT 180 ms!), uses dump-cache, zero net spawns, loading=false.
    emit_msg("thumb", "120", "300", "900")
    advance(0.04)

    local slice_cmd_p, slice_done_p, _, root = find_helper_paths(subprocesses[1].args)
    local cf = io.open(slice_cmd_p, "rb"); local cmd_text = cf and cf:read("*a") or ""; if cf then cf:close() end

    local saw_loading_true = false
    for _, m in ipairs(sent_messages) do
        if m[1] == "script-message" and m[2] == "anime-hub-thumb-info" and m[3]:find('"loading":true') then
            saw_loading_true = true
        end
    end

    -- Simulate slice helper finishing bucket 120.
    local dest120 = root .. "-b120.bgra"
    local df = io.open(dest120, "wb"); df:write("FRAME120"); df:close()
    local dn = io.open(slice_done_p, "wb"); dn:write("2\t120\t" .. dest120 .. "\n"); dn:close()
    advance(0.05)
    local drew_overlay = (#overlays > 0) and overlays[#overlays][1] == "overlay-add" and overlays[#overlays][5] == dest120
    emit_event("shutdown")

    print(to_json({
        net_spawns = count_net_spawns(),
        dump_count = #dumps,
        cmd_is_load = cmd_text:find("^load\t%d+\t120\t") ~= nil,
        saw_loading_true = saw_loading_true,
        drew_overlay = drew_overlay,
    }))

elseif scenario == "local_mode_on_network" then
    props.path = "https://cdn.example/stream/master"
    props["demuxer-via-network"] = true
    sync_props()
    emit_event("file-loaded")
    emit_msg("thumb", "120", "300", "900")
    advance(0.3)
    local last_info = nil
    for _, m in ipairs(sent_messages) do
        if m[1] == "script-message" and m[2] == "anime-hub-thumb-info" then last_info = m[3] end
    end
    print(to_json({
        spawned = #subprocesses,
        info = last_info or "",
    }))

elseif scenario == "ahead_vo_null_waits" then
    props["current-vo"] = "null"
    props["osd-dimensions"] = { w = 0, h = 0 }
    sync_props()
    emit_event("file-loaded")
    advance(0.3)
    local spawned_while_null = #subprocesses
    -- Simulate rapid startup window->fullscreen resizes: should coalesce into 1 spawn.
    set_prop("current-vo", "gpu-next")
    set_prop("osd-dimensions", { w = 1280, h = 720 })
    advance(0.05)
    set_prop("osd-dimensions", { w = 2560, h = 1440 })
    advance(0.3)
    local spawned_after_vo = #subprocesses
    emit_event("shutdown")
    print(to_json({
        spawned_while_null = spawned_while_null,
        spawned_after_vo = spawned_after_vo,
    }))

elseif scenario == "spawn_failure_disables" then
    props.path = "https://cdn.example/dead"
    props["demuxer-via-network"] = true
    sync_props()
    emit_event("file-loaded")
    emit_msg("thumb", "60", "300", "900")
    advance(0.25)
    -- Net helper fails with status 2 before producing any frame.
    local nsp = find_net_spawn()
    nsp.cb(true, { status = 2 })
    local last_info = ""
    for _, m in ipairs(sent_messages) do
        if m[1] == "script-message" and m[2] == "anime-hub-thumb-info" then last_info = m[3] end
    end
    -- Subsequent unbuffered hovers do not keep respawning net helper.
    emit_msg("thumb", "120", "400", "900")
    advance(0.25)
    print(to_json({
        net_spawn_count = count_net_spawns(),
        loading_cleared = last_info:find('"loading":false') ~= nil,
    }))

elseif scenario == "online_resumed_uses_disk_cache" then
    local jpg120 = tmp_dir .. "/t120.jpg"
    local jf = io.open(jpg120, "wb"); jf:write("FAKEJPG120"); jf:close()
    props.path = "https://cdn.example/stream/master"
    props["demuxer-via-network"] = true
    -- Resumed at minute 5 (300s): 0..299 is NOT in mpv's RAM cache.
    props["demuxer-cache-state"] = { ["seekable-ranges"] = { { start = 300, ["end"] = 1400 } } }
    sync_props()
    emit_event("file-loaded")
    emit_msg("anime-hub-info", '{"prefs":{"thumb":"all"},"thumb_cache":"' .. tmp_dir .. '"}')
    advance(0.3)

    -- Hover minute 2 (122s) -> hits t120.jpg on disk, zero network helper, zero Loading flash.
    emit_msg("thumb", "122", "300", "900")
    advance(0.04)

    local slice_cmd_p, slice_done_p, _, root = find_helper_paths(subprocesses[1].args)
    local cf = io.open(slice_cmd_p, "rb"); local cmd_text = cf and cf:read("*a") or ""; if cf then cf:close() end
    local saw_loading_true = false
    for _, m in ipairs(sent_messages) do
        if m[1] == "script-message" and m[2] == "anime-hub-thumb-info" and m[3]:find('"loading":true') then
            saw_loading_true = true
        end
    end
    local dest120 = root .. "-b120.bgra"
    local df = io.open(dest120, "wb"); df:write("DECODED120"); df:close()
    local dn = io.open(slice_done_p, "wb"); dn:write("2\t120\t" .. dest120 .. "\n"); dn:close()
    advance(0.05)
    local drew = (#overlays > 0) and overlays[#overlays][1] == "overlay-add" and overlays[#overlays][5] == dest120
    emit_event("shutdown")

    print(to_json({
        net_spawns = count_net_spawns(),
        dump_count = #dumps,
        cmd_loads_jpg = cmd_text:find("^load\t%d+\t120\t[^\t]+\t" .. jpg120:gsub("([%-%.%+%[%]%(%)%$%^%%%?%*])", "%%%1")) ~= nil,
        saw_loading_true = saw_loading_true,
        drew_overlay = drew,
        jpg_kept = io.open(jpg120, "rb") ~= nil,
    }))

elseif scenario == "background_snapshot_while_playing" then
    props.path = "https://cdn.example/stream/master"
    props["demuxer-via-network"] = true
    props["demuxer-cache-state"] = { ["seekable-ranges"] = { { start = 0, ["end"] = 600 } } }
    props["time-pos"] = 25
    props.pause = true
    sync_props()
    emit_event("file-loaded")
    emit_msg("anime-hub-info", '{"prefs":{"thumb":"all"},"thumb_cache":"' .. tmp_dir .. '"}')
    advance(6)
    local dumps_while_paused = #dumps

    props.pause = false
    advance(6)
    local dumps_while_playing = #dumps
    -- Find the MJPEG encoder subprocess and simulate it writing t20.part.jpg.
    local enc_sp, part_out = nil, nil
    for _, sp in ipairs(subprocesses) do
        for _, a in ipairs(sp.args) do
            if a == "--ovc=mjpeg" then enc_sp = sp end
            if a:sub(1, 4) == "--o=" and a:find("%.part%.jpg$") then part_out = a:sub(5) end
        end
    end
    if enc_sp and part_out then
        local pf = io.open(part_out, "wb"); pf:write("REALJPG20"); pf:close()
        enc_sp.cb(true, { status = 0 })
    end
    emit_event("shutdown")

    print(to_json({
        dumps_while_paused = dumps_while_paused,
        dumps_while_playing = dumps_while_playing,
        had_mjpeg_encode = enc_sp ~= nil,
        saved_t20 = io.open(tmp_dir .. "/t20.jpg", "rb") ~= nil,
    }))
end
"""

LOOK_THUMB_HARNESS = r"""
local script_path = arg[1]
local scenario = arg[2]

local now = 0
local timers = {}
local next_tid = 1
local props = {
    pause = false, fullscreen = true, volume = 100, ["volume-max"] = 130, mute = false,
    duration = 1440, ["time-pos"] = 10, ["sub-pos"] = 100, ["window-dragging"] = true,
    ["osd-dimensions"] = { w = 2560, h = 1440 },
    ["mouse-pos"] = { x = 0, y = 0, hover = false },
    ["track-list"] = {}, ["chapter-list"] = {}, ["media-title"] = "Ep 1",
    speed = 1, ["sub-delay"] = 0, ["audio-delay"] = 0, ["video-aspect-override"] = -1,
    panscan = 0, ["demuxer-cache-state"] = {},
}
local observers, events, messages, sent_to = {}, {}, {}, {}

local function make_timer(delay, fn, periodic)
    local t = { id = next_tid, at = now + math.max(1e-6, delay or 0), delay = math.max(1e-6, delay or 0), fn = fn, periodic = periodic, alive = true }
    next_tid = next_tid + 1
    function t:kill() self.alive = false end
    timers[#timers + 1] = t
    return t
end

local function advance(dt)
    local target = now + dt
    while true do
        local best = nil
        for i = 1, #timers do
            local t = timers[i]
            if t.alive and t.at <= target + 1e-9 then
                if not best or t.at < best.at or (t.at == best.at and t.id < best.id) then best = t end
            end
        end
        if not best then break end
        now = best.at
        if best.periodic then best.at = now + best.delay else best.alive = false end
        best.fn()
    end
    now = target
end

local function set_prop(name, val)
    props[name] = val
    for _, obs in ipairs(observers[name] or {}) do obs(name, val) end
end

local function emit_event(name) for _, fn in ipairs(events[name] or {}) do fn() end end
local function emit_msg(name, ...) for _, fn in ipairs(messages[name] or {}) do fn(...) end end

local last_ov_data = ""
_G.mp = {
    get_time = function() return now end,
    add_timeout = function(d, fn) return make_timer(d, fn, false) end,
    add_periodic_timer = function(d, fn) return make_timer(d, fn, true) end,
    observe_property = function(n, k, fn) observers[n] = observers[n] or {}; observers[n][#observers[n] + 1] = fn end,
    register_event = function(n, fn) events[n] = events[n] or {}; events[n][#events[n] + 1] = fn end,
    register_script_message = function(n, fn) messages[n] = messages[n] or {}; messages[n][#messages[n] + 1] = fn end,
    get_property = function(n, d) return props[n] ~= nil and tostring(props[n]) or d end,
    get_property_bool = function(n, d) return props[n] ~= nil and props[n] == true or (d or false) end,
    get_property_number = function(n, d) return type(props[n]) == "number" and props[n] or d end,
    get_property_native = function(n, d) return props[n] ~= nil and props[n] or d end,
    set_property = function(n, v) props[n] = v end,
    set_property_bool = function(n, v) props[n] = v and true or false end,
    set_property_number = function(n, v) props[n] = v end,
    set_property_native = function(n, v) props[n] = v end,
    add_key_binding = function() end,
    add_forced_key_binding = function() end,
    remove_key_binding = function() end,
    command = function() end,
    commandv = function(...)
        local a = { ... }
        if a[1] == "script-message-to" then
            sent_to[#sent_to + 1] = { target = a[2], cmd = a[3], arg1 = a[4] }
        end
    end,
    create_osd_overlay = function()
        local ov = { data = "", z = 0 }
        function ov:update() last_ov_data = self.data or ""; return { x0 = 0, y0 = 0, x1 = 80, y1 = 20 } end
        function ov:remove() end
        return ov
    end,
}

local function parse_json_simple(s)
    if type(s) ~= "string" then return nil end
    local out = {}
    for k, v in s:gmatch('"([^"]+)"%s*:%s*"([^"]*)"') do out[k] = v end
    for k, v in s:gmatch('"([^"]+)"%s*:%s*(true)') do out[k] = true end
    for k, v in s:gmatch('"([^"]+)"%s*:%s*(false)') do out[k] = false end
    for k, v in s:gmatch('"([^"]+)"%s*:%s*(%-?%d+%.?%d*)') do out[k] = tonumber(v) end
    local pref_block = s:match('"prefs"%s*:%s*(%b{})')
    if pref_block then out.prefs = parse_json_simple(pref_block) end
    return out
end

package.preload["mp.utils"] = function()
    return { parse_json = parse_json_simple, format_json = function() return "{}" end }
end

dofile(script_path)

set_prop("osd-dimensions", { w = 2560, h = 1440 })
emit_event("file-loaded")
local is_deck = script_path:find("player%-deck%.lua") ~= nil
local bar_y = is_deck and 1396 or 1244
local bar_x = 1280

if scenario == "hub_thumb_overrides_thumbfast" then
    emit_msg("thumbfast-info", '{"available":true,"disabled":false,"width":200,"height":112}')
    emit_msg("anime-hub-thumb-info", '{"available":true,"disabled":false,"width":280,"height":158,"network":true,"ready":false,"loading":true,"target":"player_thumb"}')
elseif scenario == "hub_thumb_disabled_blocks_fallback" then
    emit_msg("thumbfast-info", '{"available":true,"disabled":false,"width":200,"height":112}')
    emit_msg("anime-hub-thumb-info", '{"available":true,"disabled":true,"width":280,"height":158,"network":true,"ready":false,"loading":false,"target":"player_thumb"}')
elseif scenario == "prefs_off_blocks_all" then
    emit_msg("anime-hub-info", '{"prefs":{"thumb":"off"}}')
    emit_msg("thumbfast-info", '{"available":true,"disabled":false,"width":200,"height":112}')
    emit_msg("anime-hub-thumb-info", '{"available":true,"disabled":false,"width":280,"height":158,"network":false,"ready":false,"loading":false,"target":"player_thumb"}')
elseif scenario == "thumbfast_fallback_when_no_hub" then
    emit_msg("thumbfast-info", '{"available":true,"disabled":false,"width":200,"height":112}')
end

set_prop("mouse-pos", { x = 100, y = 100, hover = true })
advance(0.6)
set_prop("mouse-pos", { x = bar_x, y = bar_y, hover = true })
advance(0.6)

local targets = {}
for _, m in ipairs(sent_to) do
    if m.cmd == "thumb" then targets[m.target] = (targets[m.target] or 0) + 1 end
end
local has_loading = last_ov_data:find("Loading") ~= nil
print(string.format('{"player_thumb":%d,"thumbfast":%d,"has_loading":%s}',
    targets.player_thumb or 0, targets.thumbfast or 0, has_loading and "true" or "false"))
"""


@unittest.skipUnless(LUA, "needs luajit or lua to run player Lua scripts")
class PlayerThumbLuaTests(unittest.TestCase):
    def run_thumb(self, scenario):
        with tempfile.TemporaryDirectory() as d:
            harness = Path(d) / "harness.lua"
            harness.write_text(THUMB_HARNESS, encoding="utf-8")
            run = subprocess.run(
                [LUA, str(harness), str(MPV_DIR / "player-thumb.lua"), scenario, d],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(run.returncode, 0, f"{scenario} failed:\n{run.stderr}\n{run.stdout}")
            return json.loads(run.stdout.strip().splitlines()[-1])

    def run_look(self, script_name, scenario):
        with tempfile.TemporaryDirectory() as d:
            harness = Path(d) / "harness.lua"
            harness.write_text(LOOK_THUMB_HARNESS, encoding="utf-8")
            run = subprocess.run(
                [LUA, str(harness), str(MPV_DIR / script_name), scenario],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(run.returncode, 0, f"{script_name}/{scenario} failed:\n{run.stderr}\n{run.stdout}")
            return json.loads(run.stdout.strip().splitlines()[-1])

    def test_online_stream_forwards_headers_debounces_and_caches(self):
        res = self.run_thumb("online_headers_debounce_cache")
        self.assertEqual(res["net_before_hover"], 0)
        self.assertEqual(res["net_mid_debounce"], 0)
        self.assertEqual(res["net_after_debounce"], 1)
        args = res["args"]
        self.assertIn("--referrer=https://miruro.example/", args)
        self.assertIn("--http-header-fields-append=Origin: https://miruro.example", args)
        self.assertIn("--http-header-fields-append=X-Test: 1", args)
        self.assertIn("--user-agent=TestAgent/1.0", args)
        self.assertIn("--demuxer-lavf-format=hls", args)
        self.assertIn("--tls-verify=no", args)
        self.assertIn("--cookies=yes", args)
        self.assertIn("--cookies-file=/tmp/cookies.txt", args)
        self.assertIn("--demuxer-max-bytes=16MiB", args)
        self.assertIn("--start=480.000", args)
        self.assertGreaterEqual(res["overlays_after_first"], 1)
        self.assertEqual(res["last_ov_on_miss"], "overlay-remove")
        self.assertTrue(res["cmd_has_seek_720"])
        self.assertTrue(res["cache_hit_immediate"])
        self.assertTrue(res["cleaned_cache"])

    def test_online_preloaded_uses_dump_cache_instantly(self):
        res = self.run_thumb("online_preloaded_uses_dump_cache_instantly")
        self.assertEqual(res["net_spawns"], 0)
        self.assertEqual(res["dump_count"], 1)
        self.assertTrue(res["cmd_is_load"])
        self.assertFalse(res["saw_loading_true"])
        self.assertTrue(res["drew_overlay"])

    def test_local_mode_disables_network_streams(self):
        res = self.run_thumb("local_mode_on_network")
        self.assertEqual(res["spawned"], 0)
        self.assertIn('"disabled":true', res["info"])

    def test_prepared_vo_null_waits_for_real_vo(self):
        res = self.run_thumb("ahead_vo_null_waits")
        self.assertEqual(res["spawned_while_null"], 0)
        self.assertEqual(res["spawned_after_vo"], 1)

    def test_spawn_failure_disables_cleanly(self):
        res = self.run_thumb("spawn_failure_disables")
        self.assertEqual(res["net_spawn_count"], 1)
        self.assertTrue(res["loading_cleared"])

    def test_online_resumed_uses_disk_cache_instantly(self):
        res = self.run_thumb("online_resumed_uses_disk_cache")
        self.assertEqual(res["net_spawns"], 0)
        self.assertEqual(res["dump_count"], 0)
        self.assertTrue(res["cmd_loads_jpg"])
        self.assertFalse(res["saw_loading_true"])
        self.assertTrue(res["drew_overlay"])
        self.assertTrue(res["jpg_kept"])

    def test_background_snapshot_saves_watched_frame(self):
        res = self.run_thumb("background_snapshot_while_playing")
        self.assertEqual(res["dumps_while_paused"], 0)
        self.assertEqual(res["dumps_while_playing"], 1)
        self.assertTrue(res["had_mjpeg_encode"])
        self.assertTrue(res["saved_t20"])

    def test_looks_prefer_player_thumb_and_respect_settings(self):
        for script in ("player-soft.lua", "player-deck.lua"):
            with self.subTest(script=script):
                r1 = self.run_look(script, "hub_thumb_overrides_thumbfast")
                self.assertGreater(r1["player_thumb"], 0)
                self.assertEqual(r1["thumbfast"], 0)
                self.assertTrue(r1["has_loading"])

                r2 = self.run_look(script, "hub_thumb_disabled_blocks_fallback")
                self.assertEqual(r2["player_thumb"], 0)
                self.assertEqual(r2["thumbfast"], 0)

                r3 = self.run_look(script, "prefs_off_blocks_all")
                self.assertEqual(r3["player_thumb"], 0)
                self.assertEqual(r3["thumbfast"], 0)

                r4 = self.run_look(script, "thumbfast_fallback_when_no_hub")
                self.assertEqual(r4["player_thumb"], 0)
                self.assertGreater(r4["thumbfast"], 0)


class RealPlayerThumbEndToEnd(unittest.TestCase):
    """Spins up real mpv + player-thumb.lua against an extensionless HTTP HLS
    stream that rejects requests without Referer + Origin (403) and verifies:
    1) Preloaded stream (`--cache=yes`): hovering buffered timestamps decodes
       distinct BGRA frames via `dump-cache` with ZERO extra HTTP requests.
    2) Unbuffered stream (`--cache=no`): hovering unbuffered timestamps forwards
       Referer + Origin + HLS format and decodes distinct BGRA frames cleanly.
    """

    def _run_hls_case(self, preloaded):
        mpv = system.find_program("mpv")
        ffmpeg = shutil.which("ffmpeg")
        if not (mpv and ffmpeg):
            self.skipTest("needs mpv and ffmpeg")

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            hls_dir = d / "hls"
            hls_dir.mkdir()
            subprocess.run([
                ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc2=d=12:s=320x180:r=24",
                "-c:v", "libx264", "-g", "24", "-keyint_min", "24", "-sc_threshold", "0",
                "-pix_fmt", "yuv420p", "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
                "-hls_segment_filename", str(hls_dir / "seg%d.bin"), str(hls_dir / "playlist"),
            ], check=True, capture_output=True, timeout=20)

            bad_reqs = []
            good_reqs = []
            hover_mark = [None]

            class AuthHLS(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    ref = self.headers.get("Referer", "")
                    org = self.headers.get("Origin", "")
                    if ref != "https://cdn.example/" or org != "https://cdn.example":
                        bad_reqs.append((self.path, ref, org))
                        self.send_response(403)
                        self.end_headers()
                        return
                    good_reqs.append((self.path, hover_mark[0] is not None))
                    f = hls_dir / self.path.lstrip("/")
                    if not f.is_file():
                        self.send_response(404)
                        self.end_headers()
                        return
                    data = f.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

                def log_message(self, *a):
                    pass

            srv = socketserver.TCPServer(("127.0.0.1", 0), AuthHLS)
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.addCleanup(srv.shutdown)
            self.addCleanup(srv.server_close)

            out_frames = d / "voframes"
            out_frames.mkdir()
            thumb_tmp = d / "thumbtmp"
            thumb_tmp.mkdir()
            result_file = d / "result.json"
            snap1 = d / "snap1.bgra"
            snap2 = d / "snap2.bgra"
            mark_file = d / "hovered.flag"
            driver = d / "driver.lua"
            wait_buf = "true" if preloaded else "false"
            t1, t2 = (2, 8) if preloaded else (0, 8)
            driver.write_text(f"""
local utils = require "mp.utils"
local info = nil
local stage = 0
local saved = {{}}
local WAIT_BUF = {wait_buf}

local function find_bgra(bucket)
    local files = utils.readdir({str(thumb_tmp)!r}, "files") or {{}}
    local suf = "-b" .. tostring(bucket) .. ".bgra"
    for _, name in ipairs(files) do
        if name:sub(-#suf) == suf then
            return {str(thumb_tmp)!r} .. "/" .. name
        end
    end
    return nil
end

local function copy_file(src, dst)
    local f = io.open(src, "rb")
    if not f then return false end
    local data = f:read("*a"); f:close()
    local g = io.open(dst, "wb")
    if not g then return false end
    g:write(data); g:close()
    return #data
end

local function buffered_enough()
    local c = mp.get_property_native("demuxer-cache-state") or {{}}
    for _, r in ipairs(c["seekable-ranges"] or {{}}) do
        if r.start and r["end"] and r.start <= 0.5 and r["end"] >= 10 then return true end
    end
    return false
end

local function finish(ok, note)
    local f = io.open({str(result_file)!r}, "wb")
    if f then
        f:write(utils.format_json({{
            ok = ok, note = note or "",
            info = info,
            size1 = saved.s1 or 0, size2 = saved.s2 or 0,
        }}))
        f:close()
    end
    mp.command("quit")
end

mp.register_event("file-loaded", function()
    mp.commandv("script-message", "anime-hub-thumb-query")
end)

mp.register_script_message("anime-hub-thumb-info", function(raw)
    local parsed = utils.parse_json(raw or "")
    if parsed and parsed.available and not parsed.disabled and parsed.width and parsed.width > 0 then
        info = parsed
    end
end)

mp.add_periodic_timer(0.1, function()
    if not info then return end
    if stage == 0 then
        if WAIT_BUF and not buffered_enough() then return end
        local mf = io.open({str(mark_file)!r}, "wb"); if mf then mf:write("1"); mf:close() end
        stage = 1
        mp.commandv("script-message-to", info.target or "player_thumb", "thumb", "{t1}", "10", "10")
    elseif stage == 1 then
        local p = find_bgra({t1})
        if p then
            saved.s1 = copy_file(p, {str(snap1)!r}) or 0
            stage = 2
            mp.commandv("script-message-to", info.target or "player_thumb", "thumb", "{t2}", "50", "10")
        end
    elseif stage == 2 then
        local p = find_bgra({t2})
        if p then
            saved.s2 = copy_file(p, {str(snap2)!r}) or 0
            stage = 3
            finish(true, "both")
        end
    end
end)

mp.add_timeout(12, function() finish(false, "timeout at stage " .. tostring(stage)) end)
""", encoding="utf-8")

            # Watch mark_file in a tiny background thread so we know which HTTP
            # requests arrived AFTER buffering completed and hovering started.
            stop_watch = threading.Event()
            def watch_mark():
                while not stop_watch.is_set():
                    if mark_file.exists():
                        hover_mark[0] = True
                        return
                    stop_watch.wait(0.02)
            threading.Thread(target=watch_mark, daemon=True).start()

            cache_flags = ["--cache=yes", "--demuxer-readahead-secs=60"] if preloaded else ["--cache=no"]
            env = {**os.environ, "XDG_RUNTIME_DIR": str(thumb_tmp), "TEMP": str(thumb_tmp)}
            run = subprocess.run([
                mpv, "--no-config", "--load-scripts=no", "--osc=no", "--ao=null",
                "--vo=image", f"--vo-image-outdir={out_frames}", "--vo-image-format=png",
                "--pause", "--keep-open=always", "--msg-level=all=warn",
                *cache_flags,
                "--referrer=https://cdn.example/",
                "--http-header-fields=Origin: https://cdn.example",
                "--demuxer-lavf-format=hls",
                f"--script={MPV_DIR / 'player-thumb.lua'}",
                f"--script={driver}",
                "--script-opts-append=animehub_thumb-mode=all",
                "--", f"http://127.0.0.1:{port}/playlist",
            ], env=env, capture_output=True, text=True, timeout=20)
            stop_watch.set()

            self.assertNotIn("Lua error", run.stdout + run.stderr)
            self.assertTrue(result_file.exists(), f"no result file; stderr={run.stderr}")
            res = json.loads(result_file.read_text(encoding="utf-8"))
            self.assertTrue(res["ok"], f"driver failed: {res}; stderr={run.stderr}")
            self.assertEqual(bad_reqs, [], "helper mpv sent request without Referer/Origin")
            if preloaded:
                after_hover = [p for p, after in good_reqs if after]
                self.assertEqual(after_hover, [], f"preloaded hover should make zero HTTP requests, got {after_hover}")

            expected = res["info"]["width"] * res["info"]["height"] * 4
            self.assertEqual(res["size1"], expected)
            self.assertEqual(res["size2"], expected)
            h1 = hashlib.sha1(snap1.read_bytes()).hexdigest()
            h2 = hashlib.sha1(snap2.read_bytes()).hexdigest()
            self.assertNotEqual(h1, h2, "preview frames at two timestamps should differ")
            self.assertEqual(list(thumb_tmp.glob("ah-thumb-*")), [])

    def test_real_mpv_online_hls_preloaded_zero_network(self):
        self._run_hls_case(preloaded=True)

    def test_real_mpv_online_hls_unbuffered_forwards_headers(self):
        self._run_hls_case(preloaded=False)

    def test_real_mpv_online_watched_cache_across_sessions(self):
        """Session 1 watches an online HLS stream and snapshots t0.jpg to disk.
        Session 2 resumes at t=8 with --cache=no (0:00 unbuffered) and hovers
        t=0 -> decoded instantly from t0.jpg with ZERO extra HTTP requests."""
        mpv = system.find_program("mpv")
        ffmpeg = shutil.which("ffmpeg")
        if not (mpv and ffmpeg):
            self.skipTest("needs mpv and ffmpeg")

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            hls_dir = d / "hls"
            hls_dir.mkdir()
            subprocess.run([
                ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc2=d=12:s=320x180:r=24",
                "-c:v", "libx264", "-g", "24", "-keyint_min", "24", "-sc_threshold", "0",
                "-pix_fmt", "yuv420p", "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
                "-hls_segment_filename", str(hls_dir / "seg%d.bin"), str(hls_dir / "playlist"),
            ], check=True, capture_output=True, timeout=20)

            reqs_after_hover = []
            hover_flag = [False]

            class AuthHLS(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    if hover_flag[0]:
                        reqs_after_hover.append(self.path)
                    f = hls_dir / self.path.lstrip("/")
                    if not f.is_file():
                        self.send_response(404)
                        self.end_headers()
                        return
                    data = f.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

                def log_message(self, *a):
                    pass

            srv = socketserver.TCPServer(("127.0.0.1", 0), AuthHLS)
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.addCleanup(srv.shutdown)
            self.addCleanup(srv.server_close)

            disk_cache = d / "disk_thumbs"
            disk_cache.mkdir()
            thumb_tmp = d / "thumbtmp"
            thumb_tmp.mkdir()
            out1, out2 = d / "vo1", d / "vo2"
            out1.mkdir(); out2.mkdir()
            env = {**os.environ, "XDG_RUNTIME_DIR": str(thumb_tmp), "TEMP": str(thumb_tmp)}

            # Session 1: watch from t=0 until t0.jpg is saved on disk.
            drv1 = d / "drv1.lua"
            drv1.write_text(f"""
local utils = require "mp.utils"
mp.add_periodic_timer(0.1, function()
    local fi = utils.file_info({str(disk_cache / "t0.jpg")!r})
    if fi and fi.size > 0 then mp.command("quit") end
end)
mp.add_timeout(10, function() mp.command("quit") end)
""", encoding="utf-8")
            subprocess.run([
                mpv, "--no-config", "--load-scripts=no", "--osc=no", "--ao=null",
                "--vo=image", f"--vo-image-outdir={out1}", "--vo-image-format=png",
                "--loop-file=inf", "--cache=yes", "--demuxer-readahead-secs=30", "--demuxer-lavf-format=hls",
                f"--script={MPV_DIR / 'player-thumb.lua'}", f"--script={drv1}",
                "--script-opts-append=animehub_thumb-mode=all",
                "--script-opts-append=animehub_thumb-snap_period=0.2",
                f"--script-opts-append=animehub_thumb-disk_dir={disk_cache}",
                "--", f"http://127.0.0.1:{port}/playlist",
            ], env=env, capture_output=True, text=True, timeout=20)
            jpg0 = disk_cache / "t0.jpg"
            self.assertTrue(jpg0.exists() and jpg0.stat().st_size > 500, "Session 1 did not snapshot t0.jpg")

            # Session 2: resume at t=8 with --cache=no (t=0 NOT in RAM), hover t=0.
            res_file = d / "res2.json"
            mark_file = d / "hover2.flag"
            drv2 = d / "drv2.lua"
            drv2.write_text(f"""
local utils = require "mp.utils"
local info, stage = nil, 0
local function find_bgra(b)
    local files = utils.readdir({str(thumb_tmp)!r}, "files") or {{}}
    local suf = "-b" .. tostring(b) .. ".bgra"
    for _, n in ipairs(files) do
        if n:sub(-#suf) == suf then return {str(thumb_tmp)!r} .. "/" .. n end
    end
end
mp.register_event("file-loaded", function() mp.commandv("script-message", "anime-hub-thumb-query") end)
mp.register_script_message("anime-hub-thumb-info", function(raw)
    local p = utils.parse_json(raw or "")
    if p and p.available and not p.disabled and p.width and p.width > 0 then info = p end
end)
mp.add_periodic_timer(0.1, function()
    if not info then return end
    if stage == 0 then
        local mf = io.open({str(mark_file)!r}, "wb"); if mf then mf:write("1"); mf:close() end
        stage = 1
        mp.commandv("script-message-to", info.target or "player_thumb", "thumb", "0", "10", "10")
    elseif stage == 1 then
        local p = find_bgra(0)
        if p then
            local fi = utils.file_info(p)
            local f = io.open({str(res_file)!r}, "wb")
            if f then f:write(utils.format_json({{ ok = true, size = fi and fi.size or 0, info = info }})); f:close() end
            mp.command("quit")
        end
    end
end)
mp.add_timeout(10, function() mp.command("quit") end)
""", encoding="utf-8")
            stop_w = threading.Event()
            def watch_m():
                while not stop_w.is_set():
                    if mark_file.exists():
                        hover_flag[0] = True
                        return
                    stop_w.wait(0.02)
            threading.Thread(target=watch_m, daemon=True).start()

            run2 = subprocess.run([
                mpv, "--no-config", "--load-scripts=no", "--osc=no", "--ao=null",
                "--vo=image", f"--vo-image-outdir={out2}", "--vo-image-format=png",
                "--pause", "--keep-open=always", "--cache=no", "--start=8",
                "--demuxer-lavf-format=hls",
                f"--script={MPV_DIR / 'player-thumb.lua'}", f"--script={drv2}",
                "--script-opts-append=animehub_thumb-mode=all",
                f"--script-opts-append=animehub_thumb-disk_dir={disk_cache}",
                "--", f"http://127.0.0.1:{port}/playlist",
            ], env=env, capture_output=True, text=True, timeout=20)
            stop_w.set()
            self.assertNotIn("Lua error", run2.stdout + run2.stderr)
            self.assertTrue(res_file.exists(), f"Session 2 did not decode t0.jpg; stderr={run2.stderr}")
            r2 = json.loads(res_file.read_text(encoding="utf-8"))
            self.assertEqual(r2["size"], r2["info"]["width"] * r2["info"]["height"] * 4)
            self.assertEqual(reqs_after_hover, [], f"Session 2 hit network despite t0.jpg on disk: {reqs_after_hover}")


class ThumbCachePythonTests(unittest.TestCase):
    def test_dir_clear_and_prune(self):
        from animehub import player
        from animehub.config import config

        self.assertIsNone(player.thumb_cache_dir("local123", create=True))
        d1 = player.thumb_cache_dir("stream:100:1", create=True)
        self.assertTrue(d1 and d1.is_dir() and d1.parent == config.THUMBS)
        (d1 / "t0.jpg").write_bytes(b"x" * 200)

        # Clear deletes the finished episode's folder.
        player.clear_thumb_cache("stream:100:1")
        self.assertFalse(d1.exists())

        # Age + size + count pruning.
        old = player.thumb_cache_dir("stream:100:2", create=True)
        (old / "t0.jpg").write_bytes(b"a" * 500)
        past = os.path.getmtime(old) - 20 * 86400
        os.utime(old / "t0.jpg", (past, past))
        os.utime(old, (past, past))

        mid = player.thumb_cache_dir("stream:100:3", create=True)
        (mid / "t0.jpg").write_bytes(b"b" * 500)
        now = os.path.getmtime(mid)
        os.utime(mid / "t0.jpg", (now - 10, now - 10))
        os.utime(mid, (now - 10, now - 10))

        fresh = player.thumb_cache_dir("stream:100:4", create=True)
        (fresh / "t0.jpg").write_bytes(b"c" * 500)

        player.prune_thumb_cache(max_age_days=14, max_bytes=600, max_dirs=5)
        self.assertFalse(old.exists())
        self.assertFalse(mid.exists())
        self.assertTrue(fresh.exists())
        player.clear_thumb_cache("stream:100:4")


if __name__ == "__main__":
    unittest.main()
