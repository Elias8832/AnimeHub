"""mpv IPC sockets live in the user's private runtime dir, not shared /tmp
(TODO 52h)."""

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import support  # noqa: F401
from animehub import player, system


@unittest.skipIf(system.WINDOWS, "POSIX only (TODO 74)")
class SocketPath(unittest.TestCase):
    def test_runtime_dir(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            sock = player.ipc_socket_path()
            self.assertEqual(sock.parent, Path(d))
            self.assertRegex(sock.name, r"^anime-hub-[0-9a-f]{8}\.sock$")
            self.assertNotEqual(sock, player.ipc_socket_path())

    def test_fallback(self):
        for env in ({"XDG_RUNTIME_DIR": ""}, {"XDG_RUNTIME_DIR": "/nonexistent/anime-hub"}):
            with mock.patch.dict(os.environ, env):
                self.assertEqual(player.ipc_socket_path().parent, Path("/tmp"))


@unittest.skipIf(system.WINDOWS, "POSIX only (TODO 74)")
class EventBeforeReply(unittest.TestCase):
    """mpv tells every client about a seek; an event line arriving before the
    reply used to be taken for it, so the call returned None (TODO 149)."""

    def serve(self, *chunks):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = Path(d.name) / "mpv.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(path))
        srv.listen(1)
        self.addCleanup(srv.close)

        def run():
            conn, _ = srv.accept()
            with conn:
                conn.recv(4096)
                for chunk in chunks:
                    conn.sendall(chunk)
                    time.sleep(0.02)  # separate reads, like a real seek
        threading.Thread(target=run, daemon=True).start()
        return path

    def test_reply_after_events(self):
        sock = self.serve(b'{"event":"seek"}\n', b'{"event":"playback-restart"}\n',
                          json.dumps({"data": 42.0, "error": "success", "request_id": 0}).encode() + b"\n")
        self.assertEqual(player.mpv_ipc(sock, ["get_property", "time-pos"])["data"], 42.0)

    def test_reply_alone(self):
        sock = self.serve(b'{"data":7,"error":"success"}\n')
        self.assertEqual(player.mpv_ipc(sock, ["get_property", "volume"])["data"], 7)

    def test_mpv_gone_after_an_event(self):
        sock = self.serve(b'{"event":"end-file","reason":"error"}\n')
        self.assertIsNone(player.mpv_ipc(sock, ["get_property", "time-pos"]))


if __name__ == "__main__":
    unittest.main()
