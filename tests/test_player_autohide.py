"""Auto-hide and coordinate-scaling tests for mpv player scripts.

Runs the real Lua scripts (player-soft.lua, player-deck.lua, player-mouse.lua,
skip-credits.lua) under LuaJIT/Lua with a deterministic mock `mp` API so we
can drive exact Wayland/startup property orderings (`mouse-pos` arriving before
`osd-dimensions`, stationary cursor resting in the bottom control zone on a new
mpv instance, fullscreen scale transitions, prepared `AHEAD_LUA` unpause, and
active hover after real movement).
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from tests import support  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
MPV_DIR = ROOT / "mpv"
LUA = shutil.which("luajit") or shutil.which("lua")

HARNESS = r"""
local script_path = arg[1]
local scenario = arg[2]

local now = 0
local timers = {}
local next_tid = 1
local props = {
    pause = false,
    fullscreen = true,
    volume = 100,
    ["volume-max"] = 130,
    mute = false,
    duration = 1440,
    ["time-pos"] = 10,
    ["sub-pos"] = 100,
    ["window-dragging"] = true,
    ["input-builtin-dragging"] = true,
    ["input-dragging-deadzone"] = 3,
    ["osd-dimensions"] = { w = 0, h = 0 },
    ["mouse-pos"] = { x = 0, y = 0, hover = false },
    ["track-list"] = {},
    ["chapter-list"] = {},
    ["media-title"] = "Episode 1",
    speed = 1,
    ["sub-delay"] = 0,
    ["audio-delay"] = 0,
    ["video-aspect-override"] = -1,
    panscan = 0,
    ["demuxer-cache-state"] = {},
}
local observers = {}
local events = {}
local messages = {}
local overlays = {}
local bindings = {}
local sent_messages = {}

local function clone_shallow(v)
    if type(v) ~= "table" then return v end
    local out = {}
    for k, val in pairs(v) do out[k] = val end
    return out
end

local function make_timer(delay, fn, periodic)
    local d = math.max(1e-6, delay or 0)
    local t = {
        id = next_tid,
        at = now + d,
        delay = d,
        fn = fn,
        periodic = periodic,
        alive = true,
    }
    next_tid = next_tid + 1
    function t:kill() self.alive = false end
    timers[#timers + 1] = t
    return t
end

local function advance(dt)
    local target = now + dt
    while true do
        local best, best_i = nil, nil
        for i = 1, #timers do
            local t = timers[i]
            if t.alive and t.at <= target + 1e-9 then
                if not best or t.at < best.at or (t.at == best.at and t.id < best.id) then
                    best, best_i = t, i
                end
            end
        end
        if not best then break end
        now = best.at
        if best.periodic then
            best.at = now + best.delay
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

local function any_overlay_visible()
    for _, ov in ipairs(overlays) do
        if ov.visible then return true end
    end
    return false
end

local function any_binding(prefix)
    for name, _ in pairs(bindings) do
        if name:sub(1, #prefix) == prefix then return true end
    end
    return false
end

_G.mp = {
    get_time = function() return now end,
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
    set_property = function(name, val) props[name] = val end,
    set_property_bool = function(name, val) props[name] = val and true or false end,
    set_property_number = function(name, val) props[name] = val end,
    set_property_native = function(name, val) props[name] = val end,
    add_key_binding = function(key, name, fn) bindings[name] = fn end,
    add_forced_key_binding = function(key, name, fn) bindings[name] = fn end,
    remove_key_binding = function(name) bindings[name] = nil end,
    commandv = function(...)
        local a = { ... }
        if a[1] == "script-message" then
            sent_messages[#sent_messages + 1] = a
        end
    end,
    command = function() end,
    create_osd_overlay = function()
        local ov = { visible = false, data = "", z = 0, hidden = false }
        function ov:update()
            if not self.hidden then self.visible = (self.data or "") ~= "" end
            return { x0 = 0, y0 = 0, x1 = 80, y1 = 20 }
        end
        function ov:remove()
            self.visible = false
        end
        overlays[#overlays + 1] = ov
        return ov
    end,
}

package.preload["mp.utils"] = function()
    return {
        parse_json = function(raw)
            if type(raw) == "string" and raw:find('"kind":"op"', 1, true) then
                return { segments = { { kind = "op", start = 5, ["end"] = 95 } } }
            elseif type(raw) == "string" and raw:find('"click_windowed":"own"', 1, true) then
                return { prefs = { click = "pause", click_windowed = "own", priority = "settings" } }
            elseif type(raw) == "string" and raw:find('"click_windowed":"menu"', 1, true) then
                return { prefs = { click = "pause", click_windowed = "menu", priority = "settings" } }
            elseif type(raw) == "string" and raw:find('"click":"pause"', 1, true) then
                return { prefs = { click = "pause", priority = "settings" } }
            end
            return nil
        end,
        file_info = function() return nil end,
    }
end

dofile(script_path)

-- Initial property dispatch like real mpv startup.
set_prop("osd-dimensions", { w = 0, h = 0 })
set_prop("mouse-pos", { x = 0, y = 0, hover = false })
set_prop("pause", false)

local function emit_result(tbl)
    local parts = {}
    for k, v in pairs(tbl) do
        local s
        if type(v) == "boolean" then s = v and "true" or "false"
        elseif type(v) == "number" then s = tostring(v)
        else s = string.format("%q", tostring(v)) end
        parts[#parts + 1] = string.format("%q:%s", k, s)
    end
    print("{" .. table.concat(parts, ",") .. "}")
end

if scenario == "mouse_before_1440p_scale" then
    -- New mpv on 2560x1440: mouse-pos arrives in the middle of the screen
    -- (y=800 px = 400 in 720-space, well above controls) while scale is still 1.
    emit_event("start-file")
    emit_event("file-loaded")
    set_prop("mouse-pos", { x = 1280, y = 800, hover = true })
    set_prop("osd-dimensions", { w = 2560, h = 1440 })
    advance(0.5)
    local visible_soon = any_overlay_visible()
    advance(4.0)
    emit_result({
        visible_soon = visible_soon,
        visible_after = any_overlay_visible(),
        sub_pos = props["sub-pos"],
        bound = any_binding("hub-ui-") or any_binding("deck-"),
        window_dragging = props["window-dragging"],
    })
elseif scenario == "mouse_moved_before_1440p_scale" then
    -- Mouse even jiggles slightly before osd-dimensions reaches 2560x1440,
    -- still physically in the middle of the screen (y=800 -> 400 in 720-space).
    emit_event("start-file")
    emit_event("file-loaded")
    set_prop("mouse-pos", { x = 1280, y = 800, hover = true })
    set_prop("mouse-pos", { x = 1282, y = 800, hover = true })
    set_prop("osd-dimensions", { w = 2560, h = 1440 })
    advance(0.5)
    local visible_soon = any_overlay_visible()
    advance(4.0)
    emit_result({
        visible_soon = visible_soon,
        visible_after = any_overlay_visible(),
        sub_pos = props["sub-pos"],
        bound = any_binding("hub-ui-") or any_binding("deck-"),
        window_dragging = props["window-dragging"],
    })
elseif scenario == "resting_cursor_in_bottom_zone_on_startup" then
    -- New mpv opens while cursor is still resting at bottom-right (e.g. from
    -- clicking Skip / Finish / an episode tile) and user does not touch mouse.
    emit_event("start-file")
    emit_event("file-loaded")
    set_prop("osd-dimensions", { w = 2560, h = 1440 })
    set_prop("mouse-pos", { x = 2300, y = 1380, hover = true })
    advance(0.5)
    local visible_soon = any_overlay_visible()
    advance(4.0)
    local visible_after_rest = any_overlay_visible()
    local bound_after_rest = any_binding("hub-ui-") or any_binding("deck-")
    local dragging_after_rest = props["window-dragging"]
    -- Now user actively moves mouse inside the bottom bar: must appear and stay.
    set_prop("mouse-pos", { x = 2304, y = 1380, hover = true })
    advance(4.0)
    local visible_while_active_hover = any_overlay_visible()
    -- Moving mouse out of bottom zone hides it again.
    set_prop("mouse-pos", { x = 1280, y = 400, hover = true })
    advance(4.0)
    emit_result({
        visible_soon = visible_soon,
        visible_after_rest = visible_after_rest,
        bound_after_rest = bound_after_rest,
        dragging_after_rest = dragging_after_rest,
        visible_while_active_hover = visible_while_active_hover,
        visible_after_leave_zone = any_overlay_visible(),
    })
elseif scenario == "ahead_unpause_autohide" then
    -- Prepared online episode (AHEAD_LUA): starts paused with vo=null (0x0),
    -- file loads, then transitions to real 1440p window and unpauses.
    set_prop("pause", true)
    emit_event("start-file")
    emit_event("file-loaded")
    advance(3.0)
    local visible_while_paused = any_overlay_visible()
    set_prop("mouse-pos", { x = 1280, y = 900, hover = true })
    set_prop("osd-dimensions", { w = 2560, h = 1440 })
    set_prop("pause", false)
    advance(4.0)
    emit_result({
        visible_while_paused = visible_while_paused,
        visible_after_unpause = any_overlay_visible(),
        sub_pos = props["sub-pos"],
    })
elseif scenario == "close_button_before_1440p_scale" then
    -- player-mouse.lua: cursor in top-middle of 1440p screen (x=1300, y=60)
    -- arrives before osd-dimensions updates scale from 1 to 2.
    set_prop("fullscreen", true)
    set_prop("mouse-pos", { x = 1300, y = 60, hover = true })
    set_prop("osd-dimensions", { w = 2560, h = 1440 })
    advance(1.0)
    emit_result({
        visible_after = any_overlay_visible(),
        close_bound = bindings["hub-close"] ~= nil,
    })
elseif scenario == "leave_and_reenter_bottom_bar" then
    -- Mouse starts inside window, moves away to another monitor, then returns
    -- directly onto the bottom bar: controls must reappear and stay open.
    emit_event("start-file")
    emit_event("file-loaded")
    set_prop("osd-dimensions", { w = 2560, h = 1440 })
    set_prop("mouse-pos", { x = 1280, y = 400, hover = true })
    advance(4.0)
    set_prop("mouse-pos", { x = 1280, y = 400, hover = false })
    advance(0.5)
    set_prop("mouse-pos", { x = 1280, y = 1380, hover = true })
    advance(4.0)
    emit_result({
        visible_after_reenter = any_overlay_visible(),
    })
elseif scenario == "skip_pill_before_1440p_scale" then
    -- Standalone skip-credits.lua: pill shown while scale=1, mouse at (1200, 620)
    -- looks hovered at scale=1 (in bottom-right corner), then osd-dimensions
    -- jumps to 2560x1440 (mouse is really at 600, 310 — middle of screen).
    -- Pill must un-hover and expire after 8 s instead of staying stuck forever.
    emit_event("start-file")
    emit_msg("anime-hub-segments", '{"segments":[{"kind":"op","start":5,"end":95}]}')
    set_prop("mouse-pos", { x = 1200, y = 605, hover = true })
    set_prop("time-pos", 10)
    local visible_soon = any_overlay_visible()
    set_prop("osd-dimensions", { w = 2560, h = 1440 })
    advance(10.0)
    emit_result({
        visible_soon = visible_soon,
        visible_after = any_overlay_visible(),
    })
elseif scenario == "dragging_toggles_with_fullscreen" then
    -- Starts fullscreen: built-in dragging must be off so moving the mouse
    -- while clicking never cancels MBTN_LEFT / MBTN_LEFT_DBL.
    set_prop("osd-dimensions", { w = 2560, h = 1440 })
    local fs_drag = props["input-builtin-dragging"]
    local fs_deadzone = props["input-dragging-deadzone"]
    -- Leave fullscreen: built-in dragging returns, with scaled deadzone (24 px at 1440p).
    set_prop("fullscreen", false)
    local win_drag = props["input-builtin-dragging"]
    local win_deadzone = props["input-dragging-deadzone"]
    -- Re-enter fullscreen: built-in dragging turns back off.
    set_prop("fullscreen", true)
    local fs_drag_again = props["input-builtin-dragging"]
    -- Windowed at 1080p rescales deadzone to 18 px.
    set_prop("fullscreen", false)
    set_prop("osd-dimensions", { w = 1920, h = 1080 })
    local win_1080_deadzone = props["input-dragging-deadzone"]
    emit_result({
        fs_drag = fs_drag,
        fs_deadzone = fs_deadzone,
        win_drag = win_drag,
        win_deadzone = win_deadzone,
        fs_drag_again = fs_drag_again,
        win_1080_deadzone = win_1080_deadzone,
    })
elseif scenario == "dragging_respects_user_mpv_conf" then
    -- User already disabled built-in dragging and set a custom deadzone before
    -- script load; toggling fullscreen must not overwrite either.
    -- Reset and re-run with custom initial properties.
    props["input-builtin-dragging"] = false
    props["input-dragging-deadzone"] = 40
    props["fullscreen"] = true
    observers = {}
    dofile(script_path)
    set_prop("osd-dimensions", { w = 2560, h = 1440 })
    local fs_drag = props["input-builtin-dragging"]
    set_prop("fullscreen", false)
    local win_drag = props["input-builtin-dragging"]
    local win_deadzone = props["input-dragging-deadzone"]
    emit_result({
        fs_drag = fs_drag,
        win_drag = win_drag,
        win_deadzone = win_deadzone,
    })
elseif scenario == "dragging_safe_on_older_mpv" then
    -- mpv < 0.39 lacks input-builtin-dragging / input-dragging-deadzone.
    props["input-builtin-dragging"] = nil
    props["input-dragging-deadzone"] = nil
    props["fullscreen"] = true
    observers = {}
    dofile(script_path)
    set_prop("osd-dimensions", { w = 2560, h = 1440 })
    set_prop("fullscreen", false)
    emit_result({
        drag_is_nil = props["input-builtin-dragging"] == nil,
        deadzone_is_nil = props["input-dragging-deadzone"] == nil,
    })
elseif scenario == "click_splits_fullscreen_and_windowed" then
    -- Separate actions: Pause in fullscreen, My config (unbound) when windowed.
    set_prop("fullscreen", true)
    emit_msg("anime-hub-info", '{"prefs":{"click":"pause","click_windowed":"own"}}')
    local fs_bound = bindings["hub-click-fs"] ~= nil and bindings["hub-click-win"] == nil
    local fs_drag = props["input-builtin-dragging"]
    set_prop("fullscreen", false)
    local win_unbound = bindings["hub-click-fs"] == nil and bindings["hub-click-win"] == nil and bindings["hub-click"] == nil
    local win_own_drag = props["input-builtin-dragging"]
    -- Separate actions: Pause in fullscreen, Menu when windowed -> dragging off in windowed too.
    emit_msg("anime-hub-info", '{"prefs":{"click":"pause","click_windowed":"menu"}}')
    local win_menu_bound = bindings["hub-click-win"] ~= nil and bindings["hub-click-fs"] == nil
    local win_menu_drag = props["input-builtin-dragging"]
    bindings["hub-click-win"]()
    local menu_sent = #sent_messages == 1 and sent_messages[1][2] == "anime-hub-menu"
    -- Omitting click_windowed falls back to click (backwards compatible) and disables dragging too.
    emit_msg("anime-hub-info", '{"prefs":{"click":"pause"}}')
    local fallback_shared = bindings["hub-click"] ~= nil and bindings["hub-click-win"] == nil
    local fallback_drag = props["input-builtin-dragging"]
    emit_result({
        fs_bound = fs_bound,
        fs_drag = fs_drag,
        win_unbound = win_unbound,
        win_own_drag = win_own_drag,
        win_menu_bound = win_menu_bound,
        win_menu_drag = win_menu_drag,
        menu_sent = menu_sent,
        fallback_shared = fallback_shared,
        fallback_drag = fallback_drag,
    })
else
    error("unknown scenario: " .. tostring(scenario))
end
"""


@unittest.skipUnless(LUA, "needs luajit or lua on PATH")
class PlayerAutoHideTests(unittest.TestCase):
    def run_scenario(self, script_name, scenario):
        proc = subprocess.run(
            [LUA, "-", str(MPV_DIR / script_name), scenario],
            input=HARNESS,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_controls_hide_when_mouse_pos_arrives_before_1440p_osd_dimensions(self):
        """A 1440p cursor at y=800 (middle of screen) looked like y=800 >= 606
        when scale was still 1, locking the controls open until a mouse nudge."""
        for script in ("player-soft.lua", "player-deck.lua"):
            with self.subTest(script):
                res = self.run_scenario(script, "mouse_before_1440p_scale")
                self.assertTrue(res["visible_soon"])
                self.assertFalse(res["visible_after"])
                self.assertEqual(res["sub_pos"], 100)
                self.assertFalse(res["bound"])
                self.assertTrue(res["window_dragging"])

    def test_controls_hide_when_mouse_moves_before_scale_updates(self):
        for script in ("player-soft.lua", "player-deck.lua"):
            with self.subTest(script):
                res = self.run_scenario(script, "mouse_moved_before_1440p_scale")
                self.assertTrue(res["visible_soon"])
                self.assertFalse(res["visible_after"])
                self.assertEqual(res["sub_pos"], 100)
                self.assertFalse(res["bound"])
                self.assertTrue(res["window_dragging"])

    def test_resting_cursor_in_bottom_zone_hides_until_user_moves_mouse(self):
        """Opening the next episode with the cursor still parked where Skip or
        Episodes was clicked must not pin the controls open forever."""
        for script in ("player-soft.lua", "player-deck.lua"):
            with self.subTest(script):
                res = self.run_scenario(script, "resting_cursor_in_bottom_zone_on_startup")
                self.assertTrue(res["visible_soon"])
                self.assertFalse(res["visible_after_rest"])
                self.assertFalse(res["bound_after_rest"])
                self.assertTrue(res["dragging_after_rest"])
                self.assertTrue(res["visible_while_active_hover"])
                self.assertFalse(res["visible_after_leave_zone"])

    def test_prepared_ahead_episode_hides_after_unpause(self):
        for script in ("player-soft.lua", "player-deck.lua"):
            with self.subTest(script):
                res = self.run_scenario(script, "ahead_unpause_autohide")
                self.assertTrue(res["visible_while_paused"])
                self.assertFalse(res["visible_after_unpause"])
                self.assertEqual(res["sub_pos"], 100)

    def test_reentering_window_onto_bottom_bar_keeps_controls_open(self):
        for script in ("player-soft.lua", "player-deck.lua"):
            with self.subTest(script):
                res = self.run_scenario(script, "leave_and_reenter_bottom_bar")
                self.assertTrue(res["visible_after_reenter"])

    def test_fullscreen_close_button_rescales_with_osd_dimensions(self):
        res = self.run_scenario("player-mouse.lua", "close_button_before_1440p_scale")
        self.assertFalse(res["visible_after"])
        self.assertFalse(res["close_bound"])

    def test_standalone_skip_pill_rescales_hover_with_osd_dimensions(self):
        res = self.run_scenario("skip-credits.lua", "skip_pill_before_1440p_scale")
        self.assertTrue(res["visible_soon"])
        self.assertFalse(res["visible_after"])

    def test_fullscreen_disables_builtin_dragging_and_windowed_scales_deadzone(self):
        res = self.run_scenario("player-mouse.lua", "dragging_toggles_with_fullscreen")
        self.assertFalse(res["fs_drag"])
        self.assertEqual(res["fs_deadzone"], 24)
        self.assertTrue(res["win_drag"])
        self.assertEqual(res["win_deadzone"], 24)
        self.assertFalse(res["fs_drag_again"])
        self.assertEqual(res["win_1080_deadzone"], 18)

    def test_dragging_sync_respects_user_mpv_conf(self):
        res = self.run_scenario("player-mouse.lua", "dragging_respects_user_mpv_conf")
        self.assertFalse(res["fs_drag"])
        self.assertFalse(res["win_drag"])
        self.assertEqual(res["win_deadzone"], 40)

    def test_dragging_sync_noop_on_older_mpv(self):
        res = self.run_scenario("player-mouse.lua", "dragging_safe_on_older_mpv")
        self.assertTrue(res["drag_is_nil"])
        self.assertTrue(res["deadzone_is_nil"])

    def test_left_click_splits_fullscreen_and_windowed(self):
        res = self.run_scenario("player-mouse.lua", "click_splits_fullscreen_and_windowed")
        self.assertTrue(res["fs_bound"])
        self.assertFalse(res["fs_drag"])
        self.assertTrue(res["win_unbound"])
        self.assertTrue(res["win_own_drag"])
        self.assertTrue(res["win_menu_bound"])
        self.assertFalse(res["win_menu_drag"])
        self.assertTrue(res["menu_sent"])
        self.assertTrue(res["fallback_shared"])
        self.assertFalse(res["fallback_drag"])


if __name__ == "__main__":
    unittest.main()
