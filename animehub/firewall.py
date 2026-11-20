"""Does the PC's firewall let phones reach the phone remote (TODO 258)?

Without this, pairing just failed when a firewall dropped the phone: the
listener was up, Settings said all was fine, the phone saw nothing. Read
without admin rights:
- ufw: /etc/ufw/ufw.conf, /etc/default/ufw and /etc/ufw/user.rules (the
  rules ufw writes; world-readable on Arch, root-only on some distros, then
  the answer is "may block").
- firewalld: firewall-cmd's read-only queries (no password asked).
- Windows: the firewall's rules for this Python and for the port, through
  HNetCfg.FwPolicy2 (language-neutral numbers, unlike netsh's text).

check() answers None when nothing in the way was found, else
{"name", "blocks": True | None (can't tell), "fix": command | None,
"can_fix": the hub can open the port itself (Windows, one UAC prompt)}.
status() keeps the answer for a little while: Settings re-reads
/api/remote every few seconds while a pairing code is up."""

import base64
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import system, util

UFW = Path("/etc/ufw")
UFW_DEFAULT = Path("/etc/default/ufw")
TTL = 15
RULE_NAME = "Anime Hub phone remote"
# Phones reach the PC over the home network and, away from home, Tailscale
# (Decision 36): 100.64.0.0/10.
TAILNET = "100.64.0.0/10"

_lock = threading.Lock()
_cache = {"key": None, "at": 0.0, "value": None, "busy": False}


# --- ufw ------------------------------------------------------------------

def _conf(path):
    out = {}
    try:
        for line in Path(path).read_text().splitlines():
            m = re.match(r"\s*([A-Z_]+)\s*=\s*\"?([^\"#]*)\"?", line)
            if m:
                out[m.group(1)] = m.group(2).strip()
    except OSError:
        return None
    return out


def _ports(rule):
    """The ports a user.rules line names, as (lo, hi) pairs; None = all."""
    m = re.search(r"--dports? (\S+)", rule)
    if not m:
        return None
    out = []
    for part in m.group(1).split(","):
        lo, _, hi = part.partition(":")
        if lo.isdigit():
            out.append((int(lo), int(hi) if hi.isdigit() else int(lo)))
    return out


def _ufw_verdict(rules, port, iface, net):
    """The first ufw-user-input rule that a phone on `net` (via `iface`)
    connecting to TCP `port` meets: True = let in, False = turned away,
    None = no rule (the default policy decides)."""
    for line in rules.splitlines():
        if not line.startswith("-A ufw-user-input "):
            continue
        proto = re.search(r"-p (\S+)", line)
        if proto and proto.group(1) not in ("tcp", "all"):
            continue
        ports = _ports(line)
        if ports is not None and not any(lo <= port <= hi for lo, hi in ports):
            continue
        dev = re.search(r"-i (\S+)", line)
        if dev and dev.group(1) != iface:
            continue
        src = re.search(r"-s (\S+)", line)
        if src and net is not None:
            try:
                if not ipaddress.ip_network(src.group(1), strict=False).overlaps(net):
                    continue
            except ValueError:
                continue
        target = re.search(r"-j (\S+)", line)
        target = target.group(1) if target else ""
        if target in ("ACCEPT", "ufw-user-limit-accept"):
            return True
        if target in ("DROP", "REJECT", "ufw-user-limit"):
            return False
        # ufw's "limit" rules: a counting line, then the jump that decides.
    return None


def _ufw_fix(port, net, tailscale):
    cmd = f"sudo ufw allow from {net} to any port {port} proto tcp" if net else f"sudo ufw allow {port}/tcp"
    if tailscale:
        cmd += f" && sudo ufw allow in on tailscale0 to any port {port} proto tcp"
    return cmd


def _ufw_stopped():
    """systemd says ufw's service is stopped (its rules are gone even when
    ufw.conf still says ENABLED=yes). No systemd = can't tell = False."""
    if not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(["systemctl", "is-active", "ufw"], capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.stdout.strip() in ("inactive", "failed")


def _ufw(port, iface, net, tailscale):
    conf = _conf(UFW / "ufw.conf")
    if not conf or conf.get("ENABLED", "").lower() != "yes" or _ufw_stopped():
        return None
    policy = (_conf(UFW_DEFAULT) or {}).get("DEFAULT_INPUT_POLICY", "DROP").upper()
    try:
        rules = (UFW / "user.rules").read_text()
    except OSError:
        rules = None  # root-only here: can't tell
    verdict = _ufw_verdict(rules, port, iface, net) if rules is not None else None
    if verdict is None:
        if policy == "ACCEPT":
            return None
        verdict = False if rules is not None else None
    if verdict:
        return None
    return {"name": "ufw", "blocks": True if verdict is False else None,
            "fix": _ufw_fix(port, net, tailscale), "can_fix": False}


# --- firewalld ------------------------------------------------------------

def _fwcmd(*args):
    try:
        r = subprocess.run(["firewall-cmd", *args], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return r.returncode, r.stdout.strip()


def _firewalld_open(listing, port):
    """`firewall-cmd --list-all` text: does the zone let TCP `port` in?
    True / False; None when the zone opens it some other way we can't read
    (rich rules, services) — then we don't warn."""
    fields = {}
    for line in listing.splitlines():
        k, sep, v = line.strip().partition(":")
        if sep:
            fields[k.strip()] = v.strip()
    if fields.get("target", "").upper() in ("ACCEPT", "TRUE"):
        return True
    for p in fields.get("ports", "").split():
        num, _, proto = p.partition("/")
        lo, _, hi = num.partition("-")
        if proto == "tcp" and lo.isdigit() and int(lo) <= port <= int(hi or lo):
            return True
    if fields.get("rich rules"):
        return None
    return False


def _firewalld(port, iface):
    if not shutil.which("firewall-cmd"):
        return None
    rc, out = _fwcmd("--state")
    if rc != 0 or out != "running":
        return None
    zone = None
    if iface:
        rc, out = _fwcmd(f"--get-zone-of-interface={iface}")
        zone = out if rc == 0 and out else None
    if not zone:
        rc, out = _fwcmd("--get-default-zone")
        zone = out if rc == 0 and out else None
    if not zone:
        return {"name": "firewalld", "blocks": None, "can_fix": False,
                "fix": f"sudo firewall-cmd --permanent --add-port={port}/tcp && sudo firewall-cmd --reload"}
    rc, out = _fwcmd(f"--zone={zone}", "--list-all")
    verdict = _firewalld_open(out, port) if rc == 0 else None
    if verdict is True or (rc == 0 and verdict is None):
        return None
    return {"name": "firewalld", "blocks": True if verdict is False else None, "can_fix": False,
            "fix": f"sudo firewall-cmd --permanent --zone={zone} --add-port={port}/tcp && sudo firewall-cmd --reload"}


# --- Windows --------------------------------------------------------------

# Windows: the rules that could touch the phone remote, as JSON. Only
# enabled inbound rules for the network type in use (domain 1, private 2,
# public 4), either for this Python or for a TCP port with no program.
_WIN_READ = r"""
$f = New-Object -ComObject HNetCfg.FwPolicy2
$cur = $f.CurrentProfileTypes
$on = $false
foreach ($t in 1, 2, 4) { if (($cur -band $t) -and $f.FirewallEnabled($t)) { $on = $true } }
$exe = $env:ANIME_HUB_EXE
$rules = @($f.Rules | Where-Object {
  $_.Enabled -and $_.Direction -eq 1 -and ($_.Profiles -band $cur) -and
  (($_.ApplicationName -and $_.ApplicationName -ieq $exe) -or
   (-not $_.ApplicationName -and ($_.Protocol -eq 6 -or $_.Protocol -eq 256) -and $_.LocalPorts -and $_.LocalPorts -ne '*'))
} | ForEach-Object { @{ action = [int]$_.Action; app = [bool]$_.ApplicationName; ports = [string]$_.LocalPorts; proto = [int]$_.Protocol } })
@{ on = $on; rules = $rules } | ConvertTo-Json -Compress -Depth 3
"""


def _win_port_in(ports, port):
    if not ports or ports == "*":
        return True
    for part in ports.split(","):
        lo, _, hi = part.strip().partition("-")
        if lo.isdigit() and int(lo) <= port <= int(hi if hi.isdigit() else lo):
            return True
    return False


def _windows_verdict(state, port):
    """From _WIN_READ's answer: True = phones get in, False = blocked.
    Windows lets a block rule win over any allow rule."""
    if not state.get("on"):
        return True
    rules = state.get("rules") or []
    if isinstance(rules, dict):  # ConvertTo-Json writes a one-item list as the item
        rules = [rules]
    hits = [r for r in rules if _win_port_in(r.get("ports"), port)]
    if any(r.get("action") == 0 for r in hits):
        return False
    return any(r.get("action") == 1 for r in hits)


def _ps(script, env=None, timeout=60):
    shell = system.powershell()
    if not shell:
        return None
    try:
        r = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                           env={**os.environ, **(env or {})}, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout, **system.quiet())
    except (OSError, subprocess.SubprocessError):
        return None
    return r


def _windows(port):
    r = _ps(_WIN_READ, {"ANIME_HUB_EXE": sys.executable})
    if r is None or r.returncode != 0:
        return None
    try:
        state = json.loads(r.stdout.strip() or "{}")
    except ValueError:
        return None
    if _windows_verdict(state, port):
        return None
    return {"name": "Windows Firewall", "blocks": True, "fix": None, "can_fix": True}


def _quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def _win_allow_script(port, exe):
    """The admin part: switch off this Python's inbound block rules (Windows
    made them when its "Allow access?" was dismissed; block beats allow),
    then one rule for the port, only from the home network and Tailscale."""
    return f"""$ErrorActionPreference = 'Stop'
$f = New-Object -ComObject HNetCfg.FwPolicy2
$exe = {_quote(exe)}
foreach ($r in @($f.Rules)) {{ if ($r.Direction -eq 1 -and $r.Action -eq 0 -and $r.ApplicationName -and $r.ApplicationName -ieq $exe) {{ $r.Enabled = $false }} }}
foreach ($r in @($f.Rules)) {{ if ($r.Name -eq {_quote(RULE_NAME)}) {{ $f.Rules.Remove($r.Name) }} }}
$n = New-Object -ComObject HNetCfg.FWRule
$n.Name = {_quote(RULE_NAME)}
$n.Description = 'Lets paired phones reach Anime Hub (Settings > Phone).'
$n.Protocol = 6
$n.LocalPorts = '{int(port)}'
$n.RemoteAddresses = 'LocalSubnet,{TAILNET}'
$n.Direction = 1
$n.Action = 1
$n.Profiles = 2147483647
$n.Enabled = $true
$f.Rules.Add($n)
"""


def allow(port):
    """Windows: open the port through one UAC prompt. Returns None when
    done, else what went wrong (in words for Settings)."""
    if not system.WINDOWS:
        return "Run the command shown in Settings."
    inner = base64.b64encode(_win_allow_script(port, sys.executable).encode("utf-16-le")).decode()
    outer = ("try { $p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
             f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{inner}'; exit $p.ExitCode }} "
             "catch { exit 1223 }")
    r = _ps(outer, timeout=300)
    forget()
    if r is None:
        return "Windows PowerShell didn't start."
    if r.returncode == 1223:
        return "Windows didn't get the OK, so nothing changed."
    if r.returncode != 0:
        return f"Windows Firewall said no (code {r.returncode})."
    return None


# --- the answer -----------------------------------------------------------

def check(port, iface=None, net=None, tailscale=False):
    """What stands between phones and `port`, or None (see the top)."""
    if system.ANDROID:
        return None
    try:
        if system.WINDOWS:
            return _windows(port)
        return _ufw(port, iface, net, tailscale) or _firewalld(port, iface)
    except Exception as e:  # a firewall we misread must never break Settings
        util.log("firewall check: %s" % e)
        return None


def _refresh(key):
    value = check(*key)
    with _lock:
        _cache.update(key=key, at=time.monotonic(), value=value, busy=False)
    return value


def status(port, iface=None, net=None, tailscale=False):
    """check(), kept for TTL seconds. The first ask waits for the answer;
    later ones get the last answer while a fresh one is worked out."""
    key = (port, iface, net, tailscale)
    with _lock:
        fresh = _cache["key"] == key and time.monotonic() - _cache["at"] < TTL
        known = _cache["key"] == key
        if fresh or (known and _cache["busy"]):
            return _cache["value"]
        if known:
            _cache["busy"] = True
            value = _cache["value"]
    if not known:
        return _refresh(key)
    threading.Thread(target=_refresh, args=(key,), daemon=True).start()
    return value


def forget():
    """Ask again next time (a new code, after the Windows button)."""
    with _lock:
        _cache.update(key=None, at=0.0, value=None, busy=False)
