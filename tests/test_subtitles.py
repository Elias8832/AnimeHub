"""Custom subtitles: storage, formats, ASS->VTT, batch/zip import, undo,
mpv IPC loading, drag-and-drop auto-capture, delay memory, phone player,
and HTTP API routes."""

import base64
import io
import json
import os
import tempfile
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tests import support  # noqa: F401
from animehub import database, library, pages, phoneplay, player, remote, server, subtitles, tracks
from animehub.config import config

SAMPLE_SRT = "1\n00:00:01,500 --> 00:00:04,250\nHello world\n\n2\n0:01:02,000 --> 0:01:05,500\nSecond line\n"
SAMPLE_ASS = """[Script Info]
Title: Fansub Test

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:02.50,0:00:05.75,Default,,0,0,0,,{\\an8\\i1}Top line\\NSecond line
Dialogue: 0,0:00:06.00,0:00:07.00,Sign,,0,0,0,,{\\p1}m 0 0 l 100 0 100 100
Dialogue: 0,0:00:08.00,0:00:10.00,Default,,0,0,0,,Plain\\hspace
"""


def _b64(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.b64encode(data).decode("ascii")


def _fresh_db():
    database.init_db()
    with database.db() as c:
        c.execute("DELETE FROM custom_subtitles")
        c.execute("DELETE FROM track_prefs")
    d = subtitles.sub_dir()
    if d.exists():
        for p in d.iterdir():
            p.unlink(missing_ok=True)


class FormatsAndConversion(unittest.TestCase):
    def test_guess_label_and_lang(self):
        lbl, lang = subtitles.guess_label_and_lang("Frieren - 01.de.ass")
        self.assertEqual(lang, "de")
        self.assertIn("German", lbl)
        lbl2, lang2 = subtitles.guess_label_and_lang("01.eng.srt")
        self.assertEqual(lang2, "en")
        self.assertEqual(lbl2, "English")
        lbl3, lang3 = subtitles.guess_label_and_lang("My_Fansub_Track.vtt")
        self.assertIsNone(lang3)
        self.assertEqual(lbl3, "My Fansub Track")

    def test_ass_to_vtt_strips_tags_and_drawings(self):
        vtt = subtitles.to_vtt(SAMPLE_ASS, ".ass")
        self.assertTrue(vtt.startswith("WEBVTT\n"))
        self.assertIn("00:00:02.500 --> 00:00:05.750\nTop line\nSecond line", vtt)
        self.assertNotIn("\\p1", vtt)
        self.assertNotIn("m 0 0", vtt)
        self.assertIn("00:00:08.000 --> 00:00:10.000\nPlain space", vtt)

    def test_srt_to_vtt_pads_single_digit_hours(self):
        vtt = subtitles.to_vtt(SAMPLE_SRT, ".srt")
        self.assertIn("00:00:01.500 --> 00:00:04.250", vtt)
        self.assertIn("00:01:02.000 --> 00:01:05.500", vtt)

    def test_decode_utf16_and_cp1252_and_rejects_binary(self):
        u16 = SAMPLE_SRT.encode("utf-16")
        self.assertIn("Hello world", subtitles.decode_subtitle_bytes(u16))
        cp = "1\n00:00:01,000 --> 00:00:02,000\nGr\xfc\xdfe\n".encode("cp1252")
        self.assertIn("Grüße", subtitles.decode_subtitle_bytes(cp))
        with self.assertRaises(subtitles.SubtitleError):
            subtitles.decode_subtitle_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        with self.assertRaises(subtitles.SubtitleError):
            subtitles.decode_subtitle_bytes(b"")


class StorageBatchAndUndo(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_add_dedup_update_remove_and_undo(self):
        slot = subtitles.aid_slot(154587, 1)
        s1 = subtitles.add_subtitle(slot, "Frieren - 01.de.ass", SAMPLE_ASS.encode(), delay=1.25)
        self.assertEqual(s1["slot"], "aid:154587:1")
        self.assertEqual(s1["lang"], "de")
        self.assertEqual(s1["delay"], 1.25)
        self.assertTrue(subtitles.file_path(s1["id"]).is_file())

        # Identical content deduplicates instead of creating a second copy
        s2 = subtitles.add_subtitle(slot, "other.ass", SAMPLE_ASS.encode())
        self.assertEqual(s2["id"], s1["id"])
        self.assertTrue(s2.get("deduped"))

        # Update label & delay (clamped)
        upd = subtitles.update_subtitle(s1["id"], label="German Fansub", delay=-2.5)
        self.assertEqual(upd["label"], "German Fansub")
        self.assertEqual(upd["delay"], -2.5)

        # VTT output works for phone playback
        vtt = subtitles.vtt_text(s1["id"])
        self.assertIn("Top line", vtt)

        # Remove + Undo restores exact id, label, delay, and file on disk
        removed, tok = subtitles.remove_ids([s1["id"]])
        self.assertEqual(len(removed), 1)
        self.assertIsNone(subtitles.get(s1["id"]))
        self.assertFalse((subtitles.sub_dir() / f"{s1['id']}.ass").exists())

        restored = subtitles.undo_remove(tok)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["id"], s1["id"])
        self.assertEqual(restored[0]["label"], "German Fansub")
        self.assertEqual(restored[0]["delay"], -2.5)
        self.assertTrue(subtitles.file_path(s1["id"]).is_file())

    def test_batch_zip_stage_commit_and_remove_season(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("[Group] Show - 01.de.ass", SAMPLE_ASS)
            zf.writestr("Show S01E02.srt", SAMPLE_SRT)
            zf.writestr("03.eng.vtt", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nEp3\n")
            zf.writestr("__MACOSX/._01.ass", "junk")
            zf.writestr("readme.txt", "ignore me")
        stage = subtitles.stage_batch([{"name": "season.zip", "data": _b64(buf.getvalue())}], valid_numbers=[1, 2, 3, 4])
        self.assertTrue(stage["ok"])
        by_name = {x["name"]: x for x in stage["items"]}
        self.assertEqual(len(by_name), 3)
        self.assertEqual(by_name["[Group] Show - 01.de.ass"]["suggested_ep"], 1)
        self.assertEqual(by_name["Show S01E02.srt"]["suggested_ep"], 2)
        self.assertEqual(by_name["03.eng.vtt"]["suggested_ep"], 3)

        picks = [{"idx": x["idx"], "slot": subtitles.aid_slot(999, x["suggested_ep"]), "label": x["label"]}
                 for x in stage["items"]]
        added = subtitles.commit_batch(stage["token"], picks)
        self.assertEqual(len(added), 3)
        listed = subtitles.list_for_entry(999)
        self.assertEqual([x["ep_num"] for x in listed], [1, 2, 3])

        # Remove entire season + Undo
        removed, tok = subtitles.remove_for_entry(999)
        self.assertEqual(len(removed), 3)
        self.assertEqual(subtitles.list_for_entry(999), [])
        subtitles.undo_remove(tok)
        self.assertEqual(len(subtitles.list_for_entry(999)), 3)


class MpvPlaybackAndDragDrop(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_load_custom_subs_and_track_prefs_priority(self):
        slot = subtitles.aid_slot(555, 2)
        s = subtitles.add_subtitle(slot, "ep2.de.ass", SAMPLE_ASS.encode(), delay=1.5)
        ep_id = "stream:555:2"
        cmds = []

        def fake_ipc(_sock, cmd):
            cmds.append(cmd)
            if cmd == ["get_property", "track-list"]:
                return {"data": [
                    {"id": 1, "type": "audio", "lang": "jpn", "selected": False},
                    {"id": 2, "type": "audio", "lang": "eng", "selected": True},
                    {"id": 1, "type": "sub", "lang": "eng", "title": "English", "selected": False},
                    {"id": 2, "type": "sub", "lang": "de", "title": s["label"], "selected": True,
                     "external": True, "external-filename": str(subtitles.file_path(s["id"]))},
                ]}
            return {"error": "success"}

        tracks.save(["media:555"], "audio", {"lang": "jpn", "title": None})
        tracks.save(["media:555"], "sub", {"lang": "eng", "title": "English"})

        state = {"map": {}, "seen_ext": set(), "delays": {}, "active": None}
        with mock.patch.object(player, "mpv_ipc", side_effect=fake_ipc):
            has_custom = player.load_custom_subs("/tmp/s", ep_id, state)
            self.assertTrue(has_custom)
            self.assertEqual(cmds[0][0], "sub-add")
            self.assertEqual(cmds[0][2], "select")
            self.assertIn(["set_property", "sub-delay", 1.5], cmds)

            # follow_tracks switches audio to jpn, but keeps the custom subtitle selected!
            tstate = {}
            tlist = fake_ipc(None, ["get_property", "track-list"])["data"]
            with mock.patch.object(player, "track_shows", return_value=["media:555"]):
                player.follow_tracks("/tmp/s", ep_id, tstate, tlist, has_custom_sub=True)
            self.assertIn(["set_property", "aid", 1], cmds)
            self.assertFalse(any(c[:2] == ["set_property", "sid"] for c in cmds))

    def test_drag_and_drop_auto_saves_and_persists_delay(self):
        ep_id = "stream:777:4"
        with tempfile.TemporaryDirectory() as td:
            dropped = Path(td) / "fansub_ep4.de.srt"
            dropped.write_text(SAMPLE_SRT, encoding="utf-8")
            sidecar_dir = Path(td) / "lib"
            sidecar_dir.mkdir()
            video = sidecar_dir / "Show - E04.mkv"
            video.write_bytes(b"")
            sidecar = sidecar_dir / "Show - E04.en.srt"
            sidecar.write_text(SAMPLE_SRT, encoding="utf-8")

            cmds = []
            state = {"map": {}, "seen_ext": set(), "delays": {}, "active": None}
            tlist = [
                # Stream URL sub from ani-cli: must be ignored
                {"id": 1, "type": "sub", "external": True, "external-filename": "https://cdn.example/sub.vtt", "selected": False},
                # Sidecar beside local video: must be ignored
                {"id": 2, "type": "sub", "external": True, "external-filename": str(sidecar), "selected": False},
                # User-dropped file: must be automatically kept!
                {"id": 3, "type": "sub", "external": True, "external-filename": str(dropped), "selected": True},
            ]
            with mock.patch.object(player, "mpv_ipc", side_effect=lambda _s, c: cmds.append(c) or {"error": "success"}):
                player.follow_custom_subs("/tmp/s", ep_id, state, tlist, 0.0, video_path=video)
                saved = subtitles.list_for_entry(777)
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0]["source"], "mpv")
                self.assertEqual(saved[0]["lang"], "de")
                self.assertTrue(any(c[0] == "show-text" and "Saved subtitle:" in c[1] for c in cmds))

                # Adjusting sub-delay in mpv persists onto that captured subtitle!
                player.follow_custom_subs("/tmp/s", ep_id, state, tlist, -1.75, video_path=video)
                self.assertEqual(subtitles.get(saved[0]["id"])["delay"], -1.75)


class ParityAndHttpApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _fresh_db()
        cls.old_port = config.PORT
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        config.PORT = cls.port
        import threading
        cls.t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        config.PORT = cls.old_port

    def setUp(self):
        _fresh_db()

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        req = Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=5) as r:
                raw = r.read()
                ctype = r.headers.get("Content-Type", "")
                return r.status, (json.loads(raw) if "json" in ctype else raw.decode("utf-8"))
        except HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_upload_list_vtt_update_remove_undo_http(self):
        st, r = self._req("POST", "/api/subtitles/upload", {
            "media_id": 12345, "number": 1, "name": "ep1.de.ass", "data": _b64(SAMPLE_ASS),
        })
        self.assertEqual(st, 200)
        self.assertTrue(r["ok"])
        sub_id = r["subtitle"]["id"]

        # List by entry AND by online episode id (parity)
        st1, r1 = self._req("GET", "/api/subtitles?media_id=12345")
        st2, r2 = self._req("GET", "/api/subtitles?ep=stream:12345:1")
        self.assertEqual(st1, 200)
        self.assertEqual(st2, 200)
        self.assertEqual(r1["subtitles"][0]["id"], sub_id)
        self.assertEqual(r2["subtitles"][0]["id"], sub_id)

        # Serve VTT for phone player
        st_v, vtt = self._req("GET", f"/api/subtitles/{sub_id}/vtt")
        self.assertEqual(st_v, 200)
        self.assertIn("WEBVTT", vtt)
        self.assertIn("Top line", vtt)

        # Update label & delay
        st_u, ru = self._req("POST", "/api/subtitles/update", {"id": sub_id, "label": "Deutsch", "delay": 0.75})
        self.assertEqual(st_u, 200)
        self.assertEqual(ru["subtitle"]["label"], "Deutsch")
        self.assertEqual(ru["subtitle"]["delay"], 0.75)

        # Phone player puts custom subtitle first with label & delay
        urls, trks = phoneplay._phone_sub_tracks("stream:12345:1", [
            {"url": "/api/phone/sub/tok/0", "label": "English", "lang": "en", "custom": False, "delay": 0.0}
        ])
        self.assertEqual(urls[0], f"/api/subtitles/{sub_id}/vtt")
        self.assertEqual(trks[0]["label"], "Deutsch")
        self.assertEqual(trks[0]["delay"], 0.75)
        self.assertTrue(trks[0]["custom"])

        # Remove + Undo via HTTP
        st_d, rd = self._req("POST", "/api/subtitles/remove", {"id": sub_id})
        self.assertEqual(st_d, 200)
        self.assertEqual(rd["count"], 1)
        self.assertTrue(rd["undo_token"])
        self.assertEqual(self._req("GET", "/api/subtitles?media_id=12345")[1]["subtitles"], [])

        st_un, run = self._req("POST", "/api/subtitles/undo", {"token": rd["undo_token"]})
        self.assertEqual(st_un, 200)
        self.assertEqual(run["count"], 1)
        self.assertEqual(self._req("GET", "/api/subtitles?media_id=12345")[1]["subtitles"][0]["id"], sub_id)

    def test_remote_blocks_pc_file_writes_but_allows_vtt_and_delay(self):
        self.assertIn("/api/subtitles/upload", remote.BLOCKED)
        self.assertIn("/api/subtitles/remove", remote.BLOCKED)
        self.assertNotIn("/api/subtitles/update", remote.BLOCKED)


if __name__ == "__main__":
    unittest.main()
