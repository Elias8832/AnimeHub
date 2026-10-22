"""mpv IPC sockets live in the user's private runtime dir, not shared /tmp
(TODO 52h)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import support  # noqa: F401
from animehub import player


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


if __name__ == "__main__":
    unittest.main()
