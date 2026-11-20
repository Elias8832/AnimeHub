"""TODO 258: Settings → Phone says when the PC's firewall turns phones away
(ufw, firewalld, Windows Firewall), instead of pairing failing in silence."""
import ipaddress
import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import firewall, remote, server
from animehub.config import config

LAN = ipaddress.ip_network("192.168.2.0/24")
UFW_STOPPED = firewall._ufw_stopped  # the real one; Ufw's tests stub it

# Elias's rules after the fix (2026-11-18): KDE Connect, the phone remote
# from the home network and over Tailscale.
RULES = """*filter
:ufw-user-input - [0:0]
-A ufw-user-input -p udp -m multiport --dports 1714:1764 -j ACCEPT -m comment --comment 'dapp_KDE%20Connect'
-A ufw-user-input -p tcp -m multiport --dports 1714:1764 -j ACCEPT -m comment --comment 'dapp_KDE%20Connect'
-A ufw-user-input -p tcp --dport 8788 -s 192.168.2.0/24 -j ACCEPT
-A ufw-user-input -i tailscale0 -p tcp --dport 8789 -j ACCEPT
-A ufw-after-logging-input -j LOG --log-prefix "[UFW BLOCK] " -m limit --limit 3/min --limit-burst 10
COMMIT
"""


def setUpModule():
    support.use_library(support.case("aot"))


class Ufw(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "ufw").mkdir()
        self.conf("yes", "DROP", RULES)
        p = [patch.object(firewall, "UFW", self.dir / "ufw"), patch.object(firewall, "UFW_DEFAULT", self.dir / "default"),
             patch.object(firewall, "_ufw_stopped", return_value=False)]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def conf(self, enabled, policy, rules):
        (self.dir / "ufw" / "ufw.conf").write_text(f"# ufw\nENABLED={enabled}\nLOGLEVEL=low\n")
        (self.dir / "default").write_text(f'IPV6=yes\nDEFAULT_INPUT_POLICY="{policy}"\n')
        if rules is not None:
            (self.dir / "ufw" / "user.rules").write_text(rules)

    def test_the_rule_elias_added_opens_the_port(self):
        self.assertIsNone(firewall._ufw(8788, "enp12s0", LAN, True))

    def test_a_port_without_a_rule_is_blocked_with_the_fix(self):
        got = firewall._ufw(8790, "enp12s0", LAN, True)
        self.assertEqual(got["name"], "ufw")
        self.assertIs(got["blocks"], True)
        self.assertFalse(got["can_fix"])
        self.assertEqual(got["fix"], "sudo ufw allow from 192.168.2.0/24 to any port 8790 proto tcp"
                                     " && sudo ufw allow in on tailscale0 to any port 8790 proto tcp")
        self.assertEqual(firewall._ufw(8790, None, None, False)["fix"], "sudo ufw allow 8790/tcp")

    def test_a_tailscale_only_rule_leaves_the_home_network_out(self):
        self.assertIs(firewall._ufw(8789, "enp12s0", LAN, False)["blocks"], True)
        self.assertIsNone(firewall._ufw(8789, "tailscale0", None, False))

    def test_another_networks_rule_does_not_count(self):
        self.assertIs(firewall._ufw(8788, "wlan0", ipaddress.ip_network("10.0.0.0/24"), False)["blocks"], True)

    def test_off_or_open_by_default_means_no_warning(self):
        self.conf("no", "DROP", RULES)
        self.assertIsNone(firewall._ufw(8790, "enp12s0", LAN, False))
        self.conf("yes", "ACCEPT", RULES)
        self.assertIsNone(firewall._ufw(8790, "enp12s0", LAN, False))
        (self.dir / "ufw" / "ufw.conf").unlink()
        self.assertIsNone(firewall._ufw(8790, "enp12s0", LAN, False))

    def test_a_stopped_service_has_no_rules(self):
        with patch.object(firewall, "_ufw_stopped", return_value=True):
            self.assertIsNone(firewall._ufw(8790, "enp12s0", LAN, False))
        for out, stopped in (("inactive\n", True), ("failed\n", True), ("active\n", False)):
            with patch.object(firewall.shutil, "which", return_value="/usr/bin/systemctl"), \
                    patch.object(firewall.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, out, "")):
                self.assertIs(UFW_STOPPED(), stopped, out)
        with patch.object(firewall.shutil, "which", return_value=None):
            self.assertFalse(UFW_STOPPED())

    def test_unreadable_rules_say_maybe(self):
        (self.dir / "ufw" / "user.rules").unlink()
        got = firewall._ufw(8790, "enp12s0", LAN, False)
        self.assertIsNone(got["blocks"])
        self.assertIn("8790", got["fix"])

    def test_rule_order_ranges_and_portless_rules(self):
        v = firewall._ufw_verdict
        self.assertFalse(v("-A ufw-user-input -p tcp --dport 8790 -j DROP\n-A ufw-user-input -p tcp --dport 8790 -j ACCEPT", 8790, "e", LAN))
        self.assertTrue(v("-A ufw-user-input -p tcp -m multiport --dports 80,8000:9000 -j ACCEPT", 8790, "e", LAN))
        self.assertTrue(v("-A ufw-user-input -s 192.168.0.0/16 -j ACCEPT", 8790, "e", LAN))
        self.assertIsNone(v("-A ufw-user-input -p udp --dport 8790 -j ACCEPT", 8790, "e", LAN))
        self.assertTrue(v("-A ufw-user-input -p tcp --dport 8790 -m conntrack --ctstate NEW -m recent --set\n"
                          "-A ufw-user-input -p tcp --dport 8790 -j ufw-user-limit-accept", 8790, "e", LAN))


class Firewalld(unittest.TestCase):
    def run_with(self, answers):
        calls = []

        def fake(*args):
            calls.append(args)
            return answers.get(args[-1] if args[-1] != "--list-all" else "--list-all", (1, ""))
        with patch.object(firewall.shutil, "which", return_value="/usr/bin/firewall-cmd"), \
                patch.object(firewall, "_fwcmd", side_effect=fake):
            return firewall._firewalld(8790, "enp12s0"), calls

    LIST = "home (active)\n  target: default\n  interfaces: enp12s0\n  services: dhcpv6-client mdns ssh\n  ports: {ports}\n  rich rules: {rich}\n"

    def test_a_closed_port_gets_the_zone_command(self):
        got, calls = self.run_with({"--state": (0, "running"), "--get-zone-of-interface=enp12s0": (0, "home"),
                                    "--list-all": (0, self.LIST.format(ports="1714-1764/tcp", rich=""))})
        self.assertIs(got["blocks"], True)
        self.assertEqual(got["fix"], "sudo firewall-cmd --permanent --zone=home --add-port=8790/tcp && sudo firewall-cmd --reload")
        self.assertIn(("--zone=home", "--list-all"), calls)

    def test_open_port_trusted_zone_or_not_running(self):
        got, _ = self.run_with({"--state": (0, "running"), "--get-zone-of-interface=enp12s0": (0, "home"),
                                "--list-all": (0, self.LIST.format(ports="8700-8800/tcp", rich=""))})
        self.assertIsNone(got)
        self.assertTrue(firewall._firewalld_open("  target: ACCEPT\n", 8790))
        self.assertIsNone(firewall._firewalld_open("  ports: \n  rich rules: rule family=ipv4 accept\n", 8790))
        got, calls = self.run_with({"--state": (252, "not running")})
        self.assertIsNone(got)
        self.assertEqual(len(calls), 1)

    def test_falls_back_to_the_default_zone(self):
        got, _ = self.run_with({"--state": (0, "running"), "--get-default-zone": (0, "public"),
                                "--list-all": (0, self.LIST.format(ports="", rich=""))})
        self.assertIn("--zone=public", got["fix"])

    def test_not_installed(self):
        with patch.object(firewall.shutil, "which", return_value=None):
            self.assertIsNone(firewall._firewalld(8790, "enp12s0"))


class Windows(unittest.TestCase):
    def test_verdict(self):
        v = firewall._windows_verdict
        self.assertTrue(v({"on": False, "rules": []}, 8788))
        self.assertFalse(v({"on": True, "rules": []}, 8788))  # nothing lets it in: inbound is blocked by default
        self.assertTrue(v({"on": True, "rules": {"action": 1, "app": True, "ports": "*", "proto": 256}}, 8788))
        self.assertFalse(v({"on": True, "rules": [{"action": 1, "app": True, "ports": "*", "proto": 256},
                                                  {"action": 0, "app": True, "ports": "*", "proto": 6}]}, 8788))
        self.assertTrue(v({"on": True, "rules": [{"action": 1, "app": False, "ports": "80,8780-8790", "proto": 6}]}, 8788))
        self.assertFalse(v({"on": True, "rules": [{"action": 1, "app": False, "ports": "8789", "proto": 6}]}, 8788))

    def test_check_reads_powershells_answer(self):
        done = subprocess.CompletedProcess([], 0, '{"on":true,"rules":[]}', "")
        with patch.object(firewall.system, "WINDOWS", True), patch.object(firewall, "_ps", return_value=done):
            self.assertEqual(firewall.check(8788), {"name": "Windows Firewall", "blocks": True, "fix": None, "can_fix": True})
        broken = subprocess.CompletedProcess([], 1, "", "no COM")
        with patch.object(firewall.system, "WINDOWS", True), patch.object(firewall, "_ps", return_value=broken):
            self.assertIsNone(firewall.check(8788))

    def test_the_admin_script(self):
        s = firewall._win_allow_script(8788, r"C:\Users\O'Brien\AnimeHub\python\python.exe")
        self.assertIn(r"'C:\Users\O''Brien\AnimeHub\python\python.exe'", s)
        self.assertIn("$n.LocalPorts = '8788'", s)
        self.assertIn("LocalSubnet,100.64.0.0/10", s)
        self.assertIn("$r.Enabled = $false", s)  # switches block rules off, never deletes them

    def test_allow_answers(self):
        with patch.object(firewall.system, "WINDOWS", True):
            for rc, want in ((0, None), (1223, "Windows didn't get the OK, so nothing changed."), (5, "Windows Firewall said no (code 5).")):
                with patch.object(firewall, "_ps", return_value=subprocess.CompletedProcess([], rc, "", "")) as ps:
                    self.assertEqual(firewall.allow(8788), want)
                    self.assertIn("-Verb RunAs", ps.call_args[0][0])
        with patch.object(firewall.system, "WINDOWS", False):
            self.assertTrue(firewall.allow(8788))


class Cache(unittest.TestCase):
    def setUp(self):
        firewall.forget()
        self.addCleanup(firewall.forget)

    def test_answers_are_kept_then_refreshed(self):
        with patch.object(firewall, "check", return_value={"name": "ufw"}) as c:
            self.assertEqual(firewall.status(1, "e", LAN, False), {"name": "ufw"})
            self.assertEqual(firewall.status(1, "e", LAN, False), {"name": "ufw"})
            self.assertEqual(c.call_count, 1)
            firewall._cache["at"] -= firewall.TTL + 1
            c.return_value = None
            with patch.object(firewall.threading, "Thread") as worker:
                self.assertEqual(firewall.status(1, "e", LAN, False), {"name": "ufw"})  # the old answer while it asks again
                self.assertEqual(firewall.status(1, "e", LAN, False), {"name": "ufw"})  # one asking at a time
            self.assertEqual(worker.call_count, 1)
            worker.call_args.kwargs["target"](*worker.call_args.kwargs["args"])
            self.assertIsNone(firewall.status(1, "e", LAN, False))
            firewall.forget()
            firewall.status(1, "e", LAN, False)
            self.assertEqual(c.call_count, 3)

    def test_a_misread_never_breaks_settings(self):
        with patch.object(firewall.system, "WINDOWS", False), patch.object(firewall, "_ufw", side_effect=ValueError("odd")):
            self.assertIsNone(firewall.check(8788))


def forget():
    remote.stop()
    remote._code.update(code=None, until=0.0, tries=0)
    remote.settings.set_setting("phone_remote", "0")


class Payload(unittest.TestCase):
    def setUp(self):
        forget()
        self.addCleanup(forget)

    def test_firewall_and_vpn_in_the_payload(self):
        self.assertIsNone(remote.payload()["firewall"])
        blocked = {"name": "ufw", "blocks": True, "fix": "sudo ufw allow 8788/tcp", "can_fix": False}
        with patch.object(remote, "start", return_value=True), patch.object(remote, "lan_ip", return_value="192.168.1.20"), \
                patch.dict(remote._listener, server=object()), patch.object(remote, "firewall_status", return_value=blocked), \
                patch.object(remote.settings, "vpn_status", return_value={"active": True, "iface": "proton0", "known": True}):
            remote.set_enabled(True, server.RemoteHandler)
            p = remote.payload()
            self.assertEqual(p["firewall"], blocked)
            self.assertEqual(p["vpn"], "proton0")
            remote._code.update(code=None)
            self.assertIsNone(remote.payload()["vpn"])  # named only while a code is up

    def test_lan_link_finds_the_interface(self):
        with patch.object(remote.system, "WINDOWS", False), patch.object(remote.os.path, "isdir", return_value=True), \
                patch.object(remote.os, "listdir", return_value=["lo", "enp12s0"]), \
                patch.object(remote, "_iface_ip", side_effect=lambda n: {"lo": "127.0.0.1", "enp12s0": "192.168.2.31"}[n]), \
                patch.object(remote, "_iface_net", return_value=LAN):
            self.assertEqual(remote.lan_link("192.168.2.31"), ("enp12s0", LAN))
            self.assertEqual(remote.lan_link("10.0.0.2"), (None, None))
        self.assertEqual(remote.lan_link(None), (None, None))


class AllowButton(unittest.TestCase):
    """POST /api/remote {firewall_allow: true} on the PC's own server."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        forget()
        self.addCleanup(forget)

    def post(self, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_address[1]}/api/remote", data=json.dumps(body).encode(),
                                     method="POST", headers={"Content-Type": "application/json", "Host": f"127.0.0.1:{config.PORT}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_needs_the_remote_on(self):
        with patch.object(firewall, "allow") as allow:
            st, _ = self.post({"firewall_allow": True})
        self.assertEqual(st, 400)
        allow.assert_not_called()

    def test_reports_what_windows_said(self):
        remote.settings.set_setting("phone_remote", "1")
        with patch.object(firewall, "allow", return_value="Windows didn't get the OK, so nothing changed."):
            st, body = self.post({"firewall_allow": True})
        self.assertEqual((st, body["ok"], body["firewall_error"]), (200, False, "Windows didn't get the OK, so nothing changed."))
        with patch.object(firewall, "allow", return_value=None) as allow:
            st, body = self.post({"firewall_allow": True})
        self.assertEqual((st, body["ok"]), (200, True))
        allow.assert_called_once_with(config.REMOTE_PORT)

    def test_a_phone_cannot_press_it(self):
        self.assertFalse(remote.allowed("/api/remote", 1))


if __name__ == "__main__":
    unittest.main()
