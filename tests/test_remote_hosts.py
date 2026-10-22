"""Config `remote_hosts` (Decision 36): the hub answers a proxied name such
as a Tailscale `serve` address, and still refuses every other Host."""

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from tests import support
from animehub import server
from animehub.config import config, read_config


class Config(unittest.TestCase):
    def read(self, raw):
        with tempfile.TemporaryDirectory(dir=support.TMP) as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps(raw))
            cfg, warnings, _ = read_config(p)
        return cfg, warnings

    def test_names_are_normalised(self):
        cfg, warnings = self.read({"remote_hosts": [" Ryoku.tail.ts.net. "]})
        self.assertEqual(cfg["remote_hosts"], ["ryoku.tail.ts.net"])
        self.assertEqual(warnings, [])

    def test_default_and_bad_values(self):
        self.assertEqual(self.read({})[0]["remote_hosts"], [])
        for bad in ("ryoku.ts.net", [1], [""]):
            with self.subTest(bad=bad):
                cfg, warnings = self.read({"remote_hosts": bad})
                self.assertEqual(cfg["remote_hosts"], [])
                self.assertIn("remote_hosts", warnings[0])


class HostCheck(unittest.TestCase):
    def setUp(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.port = httpd.server_address[1]
        for name, value in (("PORT", self.port), ("REMOTE_HOSTS", ("ryoku.tail.ts.net",))):
            p = mock.patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)

    def status(self, host):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        self.addCleanup(c.close)
        c.putrequest("GET", "/api/status", skip_host=True)
        c.putheader("Host", host)
        c.endheaders()
        r = c.getresponse()
        r.read()
        return r.status

    def test_hosts(self):
        for host, ok in ((f"127.0.0.1:{self.port}", True), (f"localhost:{self.port}", True),
                         ("ryoku.tail.ts.net", True), ("RYOKU.tail.ts.net:443", True),
                         ("evil.example", False), ("ryoku.tail.ts.net.evil.example", False),
                         ("localhost:1", False), ("", False)):
            with self.subTest(host=host):
                self.assertEqual(self.status(host) != 403, ok)


if __name__ == "__main__":
    unittest.main()
