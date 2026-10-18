-- Anime Hub: skip-credits hotkey (Enter).
-- Quits mpv and leaves a marker next to the hub's IPC socket; the server
-- treats that exit as "episode finished" (marks it watched) and autoplays
-- the next episode. A plain quit stays a
-- plain quit: without the marker, ending early just stops playback.
-- Loaded via --script, so your own input.conf is untouched; a personal
-- ENTER binding would win over this.
mp.add_key_binding("ENTER", "anime-hub-skip-credits", function()
    local sock = mp.get_property("input-ipc-server")
    if sock then
        local f = io.open(sock .. ".skip", "w")
        if f then f:close() end
    end
    mp.commandv("quit")
end)
