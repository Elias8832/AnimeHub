-- Anime Hub seekbar thumbnail preview (local files and online streams).
-- Runs inside mpv alongside player-soft.lua / player-deck.lua.
-- * Local files: pre-spawns a lightweight helper mpv for instant scrubbing.
-- * Online streams (preloaded stretch): uses mpv's `dump-cache` to extract a
--   tiny local slice from RAM cache (~2 ms, zero network traffic) and decodes
--   it in a persistent idle helper mpv (~10 ms), so buffered parts preview
--   instantly just like files on disk.
-- * Online streams (unbuffered stretch): lazy-spawns a network helper mpv that
--   forwards referrer, http-header-fields, user-agent, demuxer-lavf-format,
--   tls-verify, and cookies with a 16MiB buffer cap, 180 ms debounce, and 4 s
--   bucketing. All decoded frames share a 48-frame LRU cache in temp storage.
local utils = require "mp.utils"
local mp_options = require "mp.options"

local opts = {
    mode = "all",          -- "all" | "local" | "off"
    max_w = 140,           -- logical width at 720p (~210px on 1080p, ~280px on 1440p)
    max_h = 140,
    overlay_id = 43,       -- distinct from thumbfast's 42
    cache_max = 48,        -- max cached BGRA frames per episode
    net_debounce = 0.18,   -- settle delay on unbuffered online timestamps (seconds)
    local_debounce = 0.02, -- throttle on local files & preloaded cache slices (seconds)
    net_bucket = 4,        -- cache/seek granularity on unbuffered online timestamps (seconds)
    local_bucket = 1,      -- cache/seek granularity on local files & preloaded slices (seconds)
    idle_quit = 90,        -- helper auto-exit after inactivity (seconds)
    disk_step = 10,        -- snapshot interval for persistent watched online cache (seconds)
    snap_period = 5,       -- how often to check for a missing watched snapshot (seconds)
    disk_dir = "",         -- optional override; normally delivered via anime-hub-info
}
mp_options.read_options(opts, "animehub_thumb")

local function detect_os()
    local p = mp.get_property("platform")
    if type(p) == "string" and p ~= "" then return p:lower() end
    if jit and jit.os then return jit.os:lower() end
    if package.config:sub(1, 1) == "\\" then return "windows" end
    return "linux"
end

local OS = detect_os()
local IS_WIN = OS == "windows"
local SEP = IS_WIN and "\\" or "/"
local PID = tostring(utils.getpid and utils.getpid() or math.floor(mp.get_time() * 1000))
local SCRIPT_NAME = (mp.get_script_name and mp.get_script_name()) or "player_thumb"
local SUPPORT_MEDIA_CONTROL = mp.get_property_native("media-controls") ~= nil

local function temp_dir()
    if IS_WIN then
        return os.getenv("TEMP") or os.getenv("TMP") or "."
    end
    local xdg = os.getenv("XDG_RUNTIME_DIR")
    if xdg and xdg ~= "" then return xdg end
    return "/tmp"
end

local TMP = temp_dir():gsub("[/\\]+$", "")

local props = {}
local file_loaded = false
local gen = 0
local base_prefix = nil

local effective_w, effective_h = 0, 0
local disabled = true
local net_spawn_failed = false
local cache_spawn_failed = false
local allow_tonemap = true

local seq = 0

-- Two optional helper processes:
-- * `local_h`: handles local file seeks OR online preloaded `dump-cache` slices (zero network).
-- * `net_h`: lazy-spawned only for unbuffered online timestamps (forwards stream headers).
local local_h = nil
local net_h = nil

local showing = false
local show_x, show_y = 0, 0
local last_draw_x, last_draw_y = nil, nil
local overlay_active = false
local loading_remote = false
local shown_path = nil
local shown_bucket = nil

local want_bucket = nil
local want_time = nil
local want_cached_range = nil -- { start, stop } when hovered time is in mpv's RAM cache
local want_disk_jpg = nil     -- saved JPEG path from a prior session (Tier 2)
local inflight_bucket = nil

local dump_busy = false
local dump_pending = false
local dump_slot = 0

-- Persistent watched-part cache (online episodes only, ~5-8 KB JPEG every 10 s).
local disk_dir = nil
local disk_slots = {} -- integer second -> full path to t<sec>.jpg
local snap_busy = false
local snap_handle = nil
local snap_timer = nil

-- Cache: bucket -> path, plus LRU order (oldest first).
local cache = {}
local cache_order = {}
local created_files = {}

local function track_file(p)
    if p and p ~= "" then created_files[p] = true end
end

local function move_file(from, to)
    if IS_WIN then os.remove(to) end
    return os.rename(from, to) ~= nil
end

local function write_atomic(path, content)
    local tmp = path .. ".tmp"
    track_file(tmp)
    track_file(path)
    local f = io.open(tmp, "wb")
    if not f then return false end
    f:write(content)
    f:close()
    return move_file(tmp, path)
end

local function is_network()
    local v = props["demuxer-via-network"]
    if v == nil then v = mp.get_property_bool("demuxer-via-network", false) end
    if v then return true end
    local path = props["path"]
    if type(path) ~= "string" then path = mp.get_property("path") or "" end
    return path:match("^https?://") ~= nil
end

local function find_cache_range(t)
    local c = props["demuxer-cache-state"]
    if type(c) ~= "table" then c = mp.get_property_native("demuxer-cache-state") end
    local ranges = type(c) == "table" and c["seekable-ranges"]
    if type(ranges) ~= "table" then return nil end
    local dur = props["duration"] or 0
    for _, r in ipairs(ranges) do
        if type(r) == "table" and r.start and r["end"] then
            local lo = (r.start <= 0.5) and 0 or r.start
            local hi = (dur > 0 and r["end"] >= dur - 1.5) and r["end"] or (r["end"] - 0.5)
            if t >= lo and t <= hi then
                return { start = r.start, stop = r["end"] }
            end
        end
    end
    return nil
end

local function index_disk_cache(dir)
    disk_slots = {}
    if type(dir) ~= "string" or dir == "" then
        disk_dir = nil
        return
    end
    disk_dir = dir:gsub("[/\\]+$", "")
    if not utils.readdir then return end
    local files = utils.readdir(disk_dir, "files") or {}
    for _, name in ipairs(files) do
        local sec = name:match("^t(%d+)%.jpg$")
        if sec then
            disk_slots[tonumber(sec)] = disk_dir .. SEP .. name
        end
    end
end

local function find_disk_thumb(t)
    if not disk_dir then return nil, nil end
    local best_s, best_p, best_d = nil, nil, opts.disk_step + 0.5
    for sec, path in pairs(disk_slots) do
        local d = math.abs(sec - t)
        if d < best_d then
            best_d, best_s, best_p = d, sec, path
        end
    end
    if best_p and utils.file_info(best_p) then
        return best_s, best_p
    end
    return nil, nil
end

local broadcast_info

local function set_loading(on)
    if loading_remote == on then return end
    loading_remote = on
    broadcast_info()
end

local function remove_overlay()
    if not overlay_active then return end
    overlay_active = false
    shown_path = nil
    shown_bucket = nil
    last_draw_x, last_draw_y = nil, nil
    mp.command_native_async({ "overlay-remove", opts.overlay_id }, function() end)
    broadcast_info()
end

local function draw_overlay(path, x, y)
    if not showing or not path or effective_w <= 0 or effective_h <= 0 then return end
    if overlay_active and shown_path == path and last_draw_x == x and last_draw_y == y then return end
    local changed = (not overlay_active) or loading_remote
    overlay_active = true
    loading_remote = false
    shown_path = path
    last_draw_x, last_draw_y = x, y
    mp.command_native_async({
        "overlay-add", opts.overlay_id, x, y, path, 0, "bgra",
        effective_w, effective_h, effective_w * 4
    }, function() end)
    if changed then broadcast_info() end
end

local function cache_touch(bucket)
    for i = #cache_order, 1, -1 do
        if cache_order[i] == bucket then
            table.remove(cache_order, i)
            break
        end
    end
    cache_order[#cache_order + 1] = bucket
end

local function cache_put(bucket, path)
    track_file(path)
    cache[bucket] = path
    cache_touch(bucket)
    while #cache_order > opts.cache_max do
        local old_b = table.remove(cache_order, 1)
        if old_b == shown_bucket then
            cache_order[#cache_order + 1] = old_b
            break
        end
        local old_p = cache[old_b]
        cache[old_b] = nil
        if old_p then
            os.remove(old_p)
            created_files[old_p] = nil
        end
    end
end

local function clean_files()
    for p, _ in pairs(created_files) do
        os.remove(p)
    end
    created_files = {}
    cache = {}
    cache_order = {}
end

broadcast_info = function()
    local payload = utils.format_json({
        available = true,
        disabled = disabled,
        width = effective_w,
        height = effective_h,
        network = is_network(),
        ready = overlay_active,
        loading = loading_remote,
        target = SCRIPT_NAME,
    })
    if payload then
        mp.commandv("script-message", "anime-hub-thumb-info", payload)
    end
end

local function compute_disabled()
    if opts.mode == "off" then return true end
    if is_network() and net_spawn_failed and cache_spawn_failed then return true end
    if (not is_network()) and cache_spawn_failed then return true end
    if effective_w <= 0 or effective_h <= 0 then return true end
    local vid = props["vid"]
    if not vid or vid == false or vid == 0 or vid == "no" then return true end
    local track = props["current-tracks/video"]
    if type(track) == "table" and track.image then return true end
    if is_network() and opts.mode ~= "all" then return true end
    local vo = props["current-vo"]
    if vo == nil then vo = mp.get_property("current-vo") end
    if not vo or vo == "" or vo == "null" then return true end
    local path = props["path"]
    if type(path) ~= "string" or path == "" then return true end
    return false
end

local function calc_dimensions()
    local vp = props["video-out-params"]
    local dw = type(vp) == "table" and vp.dw or nil
    local dh = type(vp) == "table" and vp.dh or nil
    if not dw or not dh or dw <= 0 or dh <= 0 then return 0, 0 end

    local osd = props["osd-dimensions"]
    local osd_h = type(osd) == "table" and osd.h or 0
    -- Match player-soft.lua / player-deck.lua 720p logical scale (~210px on 1080p, ~280px on 1440p).
    local scale = osd_h > 0 and math.max(0.75, math.min(3.0, osd_h / 720)) or 1
    local max_w = math.floor(opts.max_w * scale + 0.5)
    local max_h = math.floor(opts.max_h * scale + 0.5)

    local w, h
    if dw / dh > max_w / max_h then
        w = max_w
        h = math.floor(dh / dw * w + 0.5)
    else
        h = max_h
        w = math.floor(dw / dh * h + 0.5)
    end
    w = math.max(32, math.floor(w / 2 + 0.5) * 2)
    h = math.max(32, math.floor(h / 2 + 0.5) * 2)
    return w, h
end

local HELPER_HEAD = [[
local utils = require "mp.utils"
local CMD = %q
local DONE = %q
local OUT = %q
local BYTES = %d
local IS_WIN = %s
local IDLE = %d
local SEEK_MODE = %q
local cur_seq = %d
local cur_bucket = %d
local cur_dest = %q
]]

local HELPER_BODY = [[
local last_cmd = "init\n"
local last_act = mp.get_time()
local quitting = false
local loaded = false
local queued = nil
local awaiting_seek = false

local function move_file(from, to)
    if IS_WIN then os.remove(to) end
    return os.rename(from, to) ~= nil
end

local function write_done(line)
    local tmp = DONE .. ".tmp"
    local f = io.open(tmp, "wb")
    if not f then return end
    f:write(line)
    f:close()
    move_file(tmp, DONE)
end

local function run_queued()
    if not queued or quitting then return end
    local q = queued
    queued = nil
    cur_seq = q.seq
    cur_bucket = q.bucket
    cur_dest = q.dest
    if q.kind == "load" then
        awaiting_seek = false
        mp.commandv("loadfile", q.src, "replace")
    else
        awaiting_seek = true
        mp.commandv("seek", q.t, SEEK_MODE)
    end
end

local function emit_frame(retry)
    if quitting or cur_dest == "" or awaiting_seek then return end
    if mp.get_property_bool("seeking", false) then return end
    local fi = utils.file_info(OUT)
    if fi and fi.size == BYTES then
        if move_file(OUT, cur_dest) then
            write_done(string.format("%d\t%d\t%s\n", cur_seq, cur_bucket, cur_dest))
            cur_dest = ""
        end
    elseif retry then
        mp.add_timeout(0.03, function() emit_frame(false) end)
    end
end

local function check_cmd()
    if quitting then return end
    local fi = utils.file_info(CMD)
    if not fi then
        quitting = true
        mp.command("quit")
        return
    end
    local f = io.open(CMD, "rb")
    if not f then return end
    local raw = f:read("*a")
    f:close()
    if not raw or raw == "" or raw == last_cmd then
        if mp.get_time() - last_act > IDLE then
            quitting = true
            mp.command("quit")
        end
        return
    end
    last_cmd = raw
    last_act = mp.get_time()
    if raw:sub(1, 4) == "quit" then
        quitting = true
        mp.command("quit")
        return
    end
    local ls, lb, ldest, lsrc = raw:match("^load\t(%d+)\t(%d+)\t([^\t\r\n]+)\t([^\r\n]+)")
    if ls and lb and ldest and lsrc then
        queued = { kind = "load", seq = tonumber(ls), bucket = tonumber(lb), dest = ldest, src = lsrc }
        run_queued()
        return
    end
    local s, t, b, dest = raw:match("^seek\t(%d+)\t([%d%.]+)\t(%d+)\t([^\r\n]+)")
    if s and t and b and dest then
        queued = { kind = "seek", seq = tonumber(s), t = t, bucket = tonumber(b), dest = dest }
        if loaded then run_queued() end
    end
end

mp.register_event("file-loaded", function()
    loaded = true
    last_act = mp.get_time()
    if queued and queued.kind == "seek" then run_queued() end
end)

mp.register_event("seek", function()
    awaiting_seek = false
end)

mp.register_event("playback-restart", function()
    last_act = mp.get_time()
    emit_frame(true)
    if queued and (queued.kind == "load" or loaded) then run_queued() end
end)

mp.add_periodic_timer(0.015, check_cmd)
]]

local done_timer = nil
local debounce_timer = nil
local prespawn_timer = nil

local function remove_helper_files(h)
    if not h then return end
    for _, p in ipairs({ h.cmd_path, h.cmd_path .. ".tmp", h.done_path, h.done_path .. ".tmp", h.helper_path, h.out_path }) do
        if p then
            os.remove(p)
            created_files[p] = nil
        end
    end
end

local function quit_helper(h)
    if not h then return end
    if h.spawned and h.cmd_path then
        write_atomic(h.cmd_path, "quit\n")
        h.spawned = false
    end
    if h.handle and mp.abort_async_command then
        mp.abort_async_command(h.handle)
        h.handle = nil
    end
end

local function stop_all_helpers()
    if done_timer then done_timer:kill() end
    if debounce_timer then debounce_timer:kill() end
    if prespawn_timer then prespawn_timer:kill() end
    if snap_timer then snap_timer:kill() end
    if snap_handle and mp.abort_async_command then
        mp.abort_async_command(snap_handle)
        snap_handle = nil
    end
    snap_busy = false
    quit_helper(local_h)
    quit_helper(net_h)
    local_h = nil
    net_h = nil
    inflight_bucket = nil
    dump_busy = false
    dump_pending = false
    loading_remote = false
    remove_overlay()
    clean_files()
end

local function find_mpv()
    local p = mp.get_property_native("user-data/frontend/process-path")
    if type(p) == "string" and p ~= "" then return p end
    return "mpv"
end

local function build_vf()
    local rot = (type(props["video-params"]) == "table" and props["video-params"].rotate or 0) or 0
    local sw, sh = effective_w, effective_h
    if rot % 180 == 90 then sw, sh = sh, sw end

    local parts = {}
    local tonemapped = false
    if allow_tonemap and type(props["video-params"]) == "table" and props["video-params"].primaries == "bt.2020" then
        parts[#parts + 1] = "zscale=transfer=linear,format=gbrpf32le,tonemap=hable,zscale=transfer=bt709"
        tonemapped = true
    end
    parts[#parts + 1] = string.format(
        "scale=w=%d:h=%d:force_original_aspect_ratio=decrease,pad=w=%d:h=%d:x=-1:y=-1,format=bgra",
        sw, sh, sw, sh
    )
    return table.concat(parts, ","), rot, tonemapped
end

local function ensure_base_prefix()
    if not base_prefix then
        gen = gen + 1
        base_prefix = TMP .. SEP .. "ah-thumb-" .. PID .. "-" .. tostring(gen)
    end
    return base_prefix
end

local function bucket_path(bucket)
    return ensure_base_prefix() .. "-b" .. tostring(bucket) .. ".bgra"
end

-- Spawns a helper mpv:
-- * kind = "file": local video file (seeks inside path).
-- * kind = "slice": idle local decoder for online `dump-cache` slices (zero network).
-- * kind = "net": network stream helper (forwards headers & HLS format).
local function spawn_helper(kind, start_t, init_bucket)
    if disabled or effective_w <= 0 or effective_h <= 0 then return nil end

    local path = nil
    if kind ~= "slice" then
        path = props["path"]
        if type(path) ~= "string" or path == "" then return nil end
        local open_name = props["stream-open-filename"]
        if kind == "net" and type(open_name) == "string" and open_name ~= "" and open_name ~= path then
            path = open_name
        end
    end

    local tag = kind == "net" and "net" or "loc"
    local pfx = ensure_base_prefix() .. "-" .. tag .. "-" .. tostring(seq + 1)
    local h = {
        kind = kind,
        gen = gen,
        cmd_path = pfx .. "-cmd.txt",
        done_path = pfx .. "-done.txt",
        helper_path = pfx .. "-helper.lua",
        out_path = pfx .. "-out",
        last_done = nil,
        spawned = false,
        produced = false,
    }
    track_file(h.cmd_path)
    track_file(h.cmd_path .. ".tmp")
    track_file(h.done_path)
    track_file(h.done_path .. ".tmp")
    track_file(h.helper_path)
    track_file(h.out_path)

    seq = seq + 1
    local init_seq = seq
    local init_dest = (kind ~= "slice" and init_bucket) and bucket_path(init_bucket) or ""
    if init_dest ~= "" then track_file(init_dest) end
    local dur = props["duration"] or 60
    local exact = (kind == "file") and dur < 30
    local seek_mode = exact and "absolute+exact" or "absolute+keyframes"

    local script_src = string.format(
        HELPER_HEAD,
        h.cmd_path, h.done_path, h.out_path,
        effective_w * effective_h * 4,
        IS_WIN and "true" or "false",
        opts.idle_quit,
        seek_mode,
        init_seq, init_bucket or 0, init_dest
    ) .. HELPER_BODY

    local hf = io.open(h.helper_path, "wb")
    if not hf then return nil end
    hf:write(script_src)
    hf:close()
    if not write_atomic(h.cmd_path, "init\n") then return nil end

    local vf, rot, tonemapped = build_vf()
    local vid = props["vid"]
    local edition = props["edition"] or "auto"
    local args = {
        find_mpv(), "--no-config", "--msg-level=all=no", "--idle=yes", "--pause",
        "--keep-open=always", "--really-quiet", "--no-terminal",
        "--load-scripts=no", "--osc=no", "--ytdl=no",
        "--load-stats-overlay=no", "--load-osd-console=no", "--load-auto-profiles=no",
        "--no-sub", "--no-audio",
        exact and "--hr-seek=yes" or "--hr-seek=no",
        "--demuxer-readahead-secs=0",
        "--demuxer-max-back-bytes=0",
        "--demuxer-max-bytes=" .. (kind == "net" and "16MiB" or "512KiB"),
        "--vd-lavc-skiploopfilter=all", "--vd-lavc-software-fallback=1",
        "--vd-lavc-fast", "--vd-lavc-threads=2", "--hwdec=no",
        "--vf=" .. vf,
        "--sws-scaler=fast-bilinear",
        "--sws-allow-zimg=no",
        "--video-rotate=" .. tostring(rot),
        "--ovc=rawvideo", "--of=image2", "--ofopts=update=1",
        "--o=" .. h.out_path,
        "--script=" .. h.helper_path,
    }
    if kind ~= "slice" then
        args[#args + 1] = "--edition=" .. tostring(edition)
        args[#args + 1] = "--vid=" .. tostring(vid or "auto")
        args[#args + 1] = "--start=" .. string.format("%.3f", start_t or 0)
    end
    if SUPPORT_MEDIA_CONTROL then
        args[#args + 1] = "--media-controls=no"
    end
    if OS == "darwin" and props["macos-app-activation-policy"] then
        args[#args + 1] = "--macos-app-activation-policy=accessory"
    end
    if kind == "net" then
        args[#args + 1] = "--network-timeout=10"
        local ref = mp.get_property("referrer")
        if ref and ref ~= "" then
            args[#args + 1] = "--referrer=" .. ref
        end
        local hdrs = mp.get_property_native("http-header-fields")
        if type(hdrs) == "table" then
            for _, hdr in ipairs(hdrs) do
                if type(hdr) == "string" and hdr ~= "" then
                    args[#args + 1] = "--http-header-fields-append=" .. hdr
                end
            end
        elseif type(hdrs) == "string" and hdrs ~= "" then
            args[#args + 1] = "--http-header-fields=" .. hdrs
        end
        local ua = mp.get_property("user-agent")
        if ua and ua ~= "" then
            args[#args + 1] = "--user-agent=" .. ua
        end
        local fmt = mp.get_property("demuxer-lavf-format")
        if fmt and fmt ~= "" then
            args[#args + 1] = "--demuxer-lavf-format=" .. fmt
        end
        if mp.get_property_bool("tls-verify", true) == false then
            args[#args + 1] = "--tls-verify=no"
        end
        if mp.get_property_bool("cookies", false) then
            args[#args + 1] = "--cookies=yes"
            local cf = mp.get_property("cookies-file")
            if cf and cf ~= "" then
                args[#args + 1] = "--cookies-file=" .. cf
            end
        end
    end
    if path then
        args[#args + 1] = "--"
        args[#args + 1] = path
    end

    h.spawned = true
    if kind == "net" then net_h = h else local_h = h end
    if init_bucket then inflight_bucket = init_bucket end
    local my_gen = gen

    h.handle = mp.command_native_async({
        name = "subprocess",
        playback_only = true,
        args = args,
    }, function(ok, res)
        h.spawned = false
        h.handle = nil
        -- Remove this helper's own temp files now that it has actually exited
        -- (avoids leaving stray `-out` files if it was mid-write on shutdown/resize).
        remove_helper_files(h)
        if my_gen ~= gen then return end
        if kind == "net" and net_h == h then net_h = nil end
        if kind ~= "net" and local_h == h then local_h = nil end
        local status = type(res) == "table" and res.status or -1
        if (not ok or (status ~= 0 and status ~= -2)) and not h.produced then
            if tonemapped and allow_tonemap then
                allow_tonemap = false
                if showing and want_time then
                    spawn_helper(kind, want_time, want_bucket)
                end
                return
            end
            if kind == "net" then
                net_spawn_failed = true
                set_loading(false)
            else
                cache_spawn_failed = true
            end
            disabled = compute_disabled()
            if disabled then remove_overlay() end
            broadcast_info()
        end
    end)

    if init_bucket and done_timer and not done_timer:is_enabled() then
        done_timer:resume()
    end
    return h
end

local dispatch_seek

local function request_net_seek(t, bucket)
    if net_spawn_failed then
        set_loading(false)
        return
    end
    set_loading(not overlay_active)
    if not (net_h and net_h.spawned) then
        spawn_helper("net", t, bucket)
        return
    end
    if inflight_bucket == bucket then return end
    seq = seq + 1
    inflight_bucket = bucket
    local dest = bucket_path(bucket)
    track_file(dest)
    write_atomic(net_h.cmd_path, string.format("seek\t%d\t%.3f\t%d\t%s\n", seq, t, bucket, dest))
    if done_timer and not done_timer:is_enabled() then
        done_timer:resume()
    end
end

local function request_cache_slice(t, bucket, range)
    if cache_spawn_failed then
        request_net_seek(t, bucket)
        return
    end
    if dump_busy then
        dump_pending = true
        return
    end
    if not (local_h and local_h.spawned) then
        spawn_helper("slice", 0, nil)
    end
    if not (local_h and local_h.spawned) then
        request_net_seek(t, bucket)
        return
    end

    dump_busy = true
    dump_pending = false
    dump_slot = 1 - dump_slot
    local slice_path = ensure_base_prefix() .. "-s" .. tostring(dump_slot) .. ".mkv"
    track_file(slice_path)
    local d_start = math.max(range.start, t - 0.05)
    local d_stop = math.min(range.stop, t + 0.5)
    if d_stop <= d_start + 0.05 then
        d_start = math.max(range.start, d_stop - 0.5)
    end
    local my_gen = gen
    local h = local_h

    mp.command_native_async({ "dump-cache", d_start, d_stop, slice_path }, function(ok)
        dump_busy = false
        if my_gen ~= gen then return end
        local fi = ok and utils.file_info(slice_path) or nil
        if fi and fi.size and fi.size > 0 and h and h.spawned then
            seq = seq + 1
            inflight_bucket = bucket
            local dest = bucket_path(bucket)
            track_file(dest)
            write_atomic(h.cmd_path, string.format("load\t%d\t%d\t%s\t%s\n", seq, bucket, dest, slice_path))
            if done_timer and not done_timer:is_enabled() then
                done_timer:resume()
            end
        elseif showing and want_bucket == bucket then
            -- Fallback to network helper if dump-cache couldn't write this range.
            request_net_seek(t, bucket)
        end
        if dump_pending and showing then
            dump_pending = false
            dispatch_seek()
        end
    end)
end

local function request_disk_jpg(jpg_path, bucket, t)
    if cache_spawn_failed then
        request_net_seek(t, bucket)
        return
    end
    if not (local_h and local_h.spawned) then
        spawn_helper("slice", 0, nil)
    end
    if not (local_h and local_h.spawned) then
        request_net_seek(t, bucket)
        return
    end
    if inflight_bucket == bucket then return end
    seq = seq + 1
    inflight_bucket = bucket
    local dest = bucket_path(bucket)
    track_file(dest)
    write_atomic(local_h.cmd_path, string.format("load\t%d\t%d\t%s\t%s\n", seq, bucket, dest, jpg_path))
    if done_timer and not done_timer:is_enabled() then
        done_timer:resume()
    end
end

dispatch_seek = function()
    if disabled or not showing or want_bucket == nil or want_time == nil then return end
    if cache[want_bucket] and utils.file_info(cache[want_bucket]) then
        cache_touch(want_bucket)
        shown_bucket = want_bucket
        draw_overlay(cache[want_bucket], show_x, show_y)
        return
    end
    if is_network() then
        local range = want_cached_range or find_cache_range(want_time)
        if range then
            request_cache_slice(want_time, want_bucket, range)
        elseif want_disk_jpg and utils.file_info(want_disk_jpg) then
            request_disk_jpg(want_disk_jpg, want_bucket, want_time)
        else
            request_net_seek(want_time, want_bucket)
        end
        return
    end
    if not (local_h and local_h.spawned) then
        spawn_helper("file", want_time, want_bucket)
        return
    end
    if inflight_bucket == want_bucket then return end
    seq = seq + 1
    inflight_bucket = want_bucket
    local dest = bucket_path(want_bucket)
    track_file(dest)
    write_atomic(local_h.cmd_path, string.format("seek\t%d\t%.3f\t%d\t%s\n", seq, want_time, want_bucket, dest))
    if done_timer and not done_timer:is_enabled() then
        done_timer:resume()
    end
end

debounce_timer = mp.add_timeout(0.02, dispatch_seek)
debounce_timer:kill()

local function check_helper_done(h)
    if not (h and h.done_path) then return false end
    local f = io.open(h.done_path, "rb")
    if not f then return false end
    local raw = f:read("*a")
    f:close()
    if not raw or raw == "" or raw == h.last_done then return false end
    h.last_done = raw
    local _, b_str, dest = raw:match("^(%d+)\t(%d+)\t([^\r\n]+)")
    local b = tonumber(b_str)
    if b and dest and utils.file_info(dest) then
        h.produced = true
        if inflight_bucket == b then inflight_bucket = nil end
        cache_put(b, dest)
        if showing then
            local fast = (not is_network()) or (h.kind == "slice")
            if fast or b == want_bucket then
                shown_bucket = b
                draw_overlay(dest, show_x, show_y)
            end
        end
        return true
    end
    return false
end

local function poll_done()
    check_helper_done(local_h)
    check_helper_done(net_h)
    local any_spawned = (local_h and local_h.spawned) or (net_h and net_h.spawned)
    if not showing and inflight_bucket == nil then
        done_timer:kill()
    elseif not any_spawned and not showing then
        done_timer:kill()
    end
end

done_timer = mp.add_periodic_timer(0.016, poll_done)
done_timer:kill()

local function on_thumb(time_str, rx, ry)
    if disabled or effective_w <= 0 or effective_h <= 0 then return end
    local t = tonumber(time_str)
    local x, y = tonumber(rx), tonumber(ry)
    if not t or not x or not y then return end

    local dur = props["duration"]
    if type(dur) == "number" and dur > 0 then
        t = math.max(0, math.min(math.max(0, dur - 0.5), t))
    else
        t = math.max(0, t)
    end

    local net = is_network()
    local cached_range = net and find_cache_range(t) or nil
    local disk_sec, disk_jpg = nil, nil
    if net and not cached_range and not cache_spawn_failed then
        disk_sec, disk_jpg = find_disk_thumb(t)
    end
    local fast_local = (not net) or ((cached_range ~= nil or disk_jpg ~= nil) and not cache_spawn_failed)
    local bucket
    if disk_sec ~= nil then
        bucket = disk_sec
    else
        local step = fast_local and opts.local_bucket or opts.net_bucket
        bucket = math.floor(t / step + 0.5) * step
        if type(dur) == "number" and dur > 0 then
            bucket = math.max(0, math.min(math.max(0, math.floor(dur - 0.5)), bucket))
        else
            bucket = math.max(0, bucket)
        end
    end

    show_x = math.floor(x + 0.5)
    show_y = math.floor(y + 0.5)
    showing = true
    want_bucket = bucket
    want_time = fast_local and t or bucket
    want_cached_range = cached_range
    want_disk_jpg = disk_jpg

    local cached = cache[bucket]
    if cached and utils.file_info(cached) then
        debounce_timer:kill()
        cache_touch(bucket)
        shown_bucket = bucket
        draw_overlay(cached, show_x, show_y)
        return
    end

    if fast_local then
        set_loading(false)
        if overlay_active and shown_path then
            draw_overlay(shown_path, show_x, show_y)
        end
        if not debounce_timer:is_enabled() then
            debounce_timer.timeout = opts.local_debounce
            debounce_timer:resume()
        end
    else
        -- Unbuffered online spot: hide stale frame so the Loading box shows.
        if shown_bucket ~= bucket and overlay_active then
            remove_overlay()
        end
        set_loading(true)
        debounce_timer:kill()
        debounce_timer.timeout = opts.net_debounce
        debounce_timer:resume()
    end
end

local function on_clear()
    showing = false
    want_bucket = nil
    want_time = nil
    want_cached_range = nil
    want_disk_jpg = nil
    debounce_timer:kill()
    loading_remote = false
    remove_overlay()
    if inflight_bucket == nil then
        done_timer:kill()
    end
end

-- Background watched-part snapshots (online episodes only): every `disk_step` s
-- of played video, extracts a tiny slice from mpv's RAM cache (zero network) and
-- saves a ~6 KB JPEG to `disk_dir` so resuming in a future session has instant
-- previews for everything you already watched.
local function snap_tick()
    if disabled or not file_loaded or not is_network() or not disk_dir then return end
    if showing or dump_busy or snap_busy then return end
    if mp.get_property_bool("pause", false) then return end
    local pos = mp.get_property_number("time-pos")
    if not pos or pos < 0 then return end

    local step = math.max(2, opts.disk_step)
    local cur = math.floor(pos / step) * step
    local target = nil
    if not disk_slots[cur] then
        target = cur
    elseif cur >= step and not disk_slots[cur - step] then
        target = cur - step
    end
    if target == nil then return end

    local range = find_cache_range(target)
    if not range then return end

    snap_busy = true
    local my_gen = gen
    local dir = disk_dir
    local slice_path = ensure_base_prefix() .. "-snap.mkv"
    local part_jpg = dir .. SEP .. "t" .. tostring(target) .. ".part.jpg"
    local final_jpg = dir .. SEP .. "t" .. tostring(target) .. ".jpg"
    track_file(slice_path)
    track_file(part_jpg)

    local d_start = math.max(range.start, target - 0.05)
    local d_stop = math.min(range.stop, target + 0.5)
    if d_stop <= d_start + 0.05 then
        d_start = math.max(range.start, d_stop - 0.5)
    end

    mp.command_native_async({ "dump-cache", d_start, d_stop, slice_path }, function(ok)
        if my_gen ~= gen then
            os.remove(slice_path)
            snap_busy = false
            return
        end
        local fi = ok and utils.file_info(slice_path) or nil
        if not (fi and fi.size and fi.size > 0) then
            os.remove(slice_path)
            snap_busy = false
            return
        end
        local rot = (type(props["video-params"]) == "table" and props["video-params"].rotate or 0) or 0
        local args = {
            find_mpv(), "--no-config", "--no-terminal", "--really-quiet", "--msg-level=all=no",
            "--load-scripts=no", "--osc=no", "--ytdl=no",
            "--load-stats-overlay=no", "--load-osd-console=no", "--load-auto-profiles=no",
            "--no-sub", "--no-audio", "--frames=1",
            "--vd-lavc-skiploopfilter=all", "--vd-lavc-software-fallback=1",
            "--vd-lavc-fast", "--vd-lavc-threads=1", "--hwdec=no",
            "--video-rotate=" .. tostring(rot),
            "--vf=lavfi=[scale=320:-2:out_range=pc:out_color_matrix=bt601,format=yuvj420p]",
            "--ovc=mjpeg", "--ovcopts=qmin=4,qmax=6", "--ofopts=update=1",
            "--o=" .. part_jpg, "--", slice_path,
        }
        if SUPPORT_MEDIA_CONTROL then args[#args + 1] = "--media-controls=no" end
        snap_handle = mp.command_native_async({
            name = "subprocess",
            playback_only = true,
            args = args,
        }, function(enc_ok, res)
            snap_handle = nil
            snap_busy = false
            os.remove(slice_path)
            created_files[slice_path] = nil
            created_files[part_jpg] = nil
            local jfi = enc_ok and utils.file_info(part_jpg) or nil
            if my_gen == gen and jfi and jfi.size and jfi.size > 0 then
                if move_file(part_jpg, final_jpg) then
                    disk_slots[target] = final_jpg
                else
                    os.remove(part_jpg)
                end
            else
                os.remove(part_jpg)
            end
        end)
    end)
end

snap_timer = mp.add_periodic_timer(opts.snap_period, snap_tick)
snap_timer:kill()

prespawn_timer = mp.add_timeout(0.25, function()
    if disabled or not file_loaded or cache_spawn_failed then return end
    if local_h and local_h.spawned then return end
    if is_network() then
        -- Pre-spawn an idle local slice decoder (zero network traffic) so
        -- hovering already-preloaded parts of the stream is instant.
        spawn_helper("slice", 0, nil)
    else
        spawn_helper("file", mp.get_property_number("time-pos", 0), nil)
    end
end)
prespawn_timer:kill()

local function refresh_state(force_broadcast)
    local nw, nh = calc_dimensions()
    local resized = (nw ~= effective_w or nh ~= effective_h) and (nw > 0 and nh > 0)
    if nw > 0 and nh > 0 then
        effective_w, effective_h = nw, nh
    end
    local was_disabled = disabled
    disabled = compute_disabled()

    if disabled then
        if local_h or net_h then stop_all_helpers() end
        if force_broadcast or not was_disabled then broadcast_info() end
        return
    end

    if resized and (local_h or net_h) then
        stop_all_helpers()
        base_prefix = nil
    end

    if force_broadcast or resized or was_disabled then
        broadcast_info()
    end

    -- Wait 250 ms for startup window->fullscreen resizes to settle so we spawn
    -- one helper instead of three.
    if file_loaded and not (local_h and local_h.spawned) and not cache_spawn_failed then
        prespawn_timer:kill()
        prespawn_timer:resume()
    end
    if file_loaded and is_network() and disk_dir and snap_timer and not snap_timer:is_enabled() then
        snap_timer:resume()
    end
end

local function observe(name, kind, trigger)
    mp.observe_property(name, kind, function(_, v)
        props[name] = v
        if trigger then refresh_state(false) end
    end)
end

observe("path", "native", true)
observe("stream-open-filename", "native", false)
observe("demuxer-via-network", "bool", true)
observe("demuxer-cache-state", "native", false)
observe("video-out-params", "native", true)
observe("video-params", "native", true)
observe("osd-dimensions", "native", true)
observe("current-vo", "native", true)
observe("current-tracks/video", "native", true)
observe("vid", "native", true)
observe("edition", "native", false)
observe("duration", "number", false)
observe("macos-app-activation-policy", "native", false)

mp.register_script_message("thumb", on_thumb)
mp.register_script_message("clear", on_clear)
mp.register_script_message("anime-hub-thumb-query", function()
    refresh_state(true)
end)
mp.register_script_message("anime-hub-info", function(raw)
    local parsed = utils.parse_json(raw or "")
    if type(parsed) ~= "table" then return end
    local changed = false
    if type(parsed.prefs) == "table" then
        local m = parsed.prefs.thumb
        if m == "all" or m == "local" or m == "off" then
            opts.mode = m
            changed = true
        end
    end
    if type(parsed.thumb_cache) == "string" and parsed.thumb_cache ~= "" and parsed.thumb_cache ~= disk_dir then
        index_disk_cache(parsed.thumb_cache)
        changed = true
    end
    if changed then refresh_state(true) end
end)

mp.register_event("start-file", function()
    file_loaded = false
    stop_all_helpers()
    base_prefix = nil
    net_spawn_failed = false
    cache_spawn_failed = false
    allow_tonemap = true
    effective_w, effective_h = 0, 0
    disabled = true
    if opts.disk_dir ~= "" then
        index_disk_cache(opts.disk_dir)
    else
        disk_dir = nil
        disk_slots = {}
    end
end)

if opts.disk_dir ~= "" then
    index_disk_cache(opts.disk_dir)
end

mp.register_event("file-loaded", function()
    file_loaded = true
    refresh_state(true)
end)

mp.register_event("shutdown", function()
    stop_all_helpers()
end)
