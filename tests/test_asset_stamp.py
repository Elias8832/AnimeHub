"""Decision 72: the hub stamps the page's scripts with a code made from their
content, so web/index.html never needs a hand-bumped ?v= number (two
threads bumping the same line kept clashing on merge)."""

import hashlib
import re
import unittest
from unittest import mock

from tests import support

from animehub import server

WEB = support.ROOT / "web"


def code(name):
    return hashlib.sha1((WEB / name).read_bytes()).hexdigest()[:10]


class AssetStamp(unittest.TestCase):
    def test_index_html_has_no_numbers_to_bump(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/style.css?v=auto"', html)
        self.assertIn('src="/app.js?v=auto"', html)
        self.assertIsNone(re.search(r"/(app\.js|style\.css)\?v=\d", html))

    def test_served_page_carries_each_files_code(self):
        page = server.index_html().decode("utf-8")
        self.assertIn(f'/style.css?v={code("style.css")}', page)
        self.assertIn(f'/app.js?v={code("app.js")}', page)
        self.assertNotIn("?v=auto", page)

    def test_a_changed_file_gets_a_new_code(self):
        real = server.system.read_bytes

        def fake(path):
            data = real(path)
            return data + b"\n/* changed */" if str(path).endswith("app.js") else data
        before = server.index_html()
        with mock.patch.object(server.system, "read_bytes", side_effect=fake):
            after = server.index_html()
        self.assertNotEqual(before, after)
        self.assertIn(f'/style.css?v={code("style.css")}'.encode(), after)  # the other file keeps its code

    def test_a_missing_file_leaves_the_tag_as_is(self):
        real = server.system.read_bytes

        def fake(path):
            if str(path).endswith("style.css"):
                raise OSError("gone")
            return real(path)
        with mock.patch.object(server.system, "read_bytes", side_effect=fake):
            page = server.index_html().decode("utf-8")
        self.assertIn("/style.css?v=auto", page)
        self.assertIn(f'/app.js?v={code("app.js")}', page)


if __name__ == "__main__":
    unittest.main()
