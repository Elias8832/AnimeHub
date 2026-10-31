"""The phone remote over the home Wi-Fi (Decision 43): a second listener on
every address, off by default, where every request needs a paired phone.
Pairing: Settings shows a QR code (or the address plus a 6-digit code to
type in); the phone gets a long-lived cookie and the database keeps only
its SHA-256. The main server stays on 127.0.0.1 (Decision 36)."""

import hashlib
import ipaddress
import os
import secrets
import socket
import threading
import time

from . import database, qr, settings, system, util
from .config import config

COOKIE = "animehub_remote"
CODE_TTL = 600
CODE_TRIES = 5
SEEN_EVERY = 60

# Routes a paired phone may not use: they change the PC itself (folders,
# the AniList account, opening files there) or the remote's own pairing.
BLOCKED = ("/api/browse", "/api/library-root", "/api/library-extra", "/api/adopt-root",
           "/api/open", "/api/open-folder", "/api/auth/start", "/api/auth/callback",
           "/api/auth/token", "/api/auth/logout", "/auth/callback", "/api/remote", "/api/anicli-get")
# What the pairing page itself needs before a phone is paired.
OPEN = ("/pair", "/api/remote/pair", "/style.css", "/manifest.webmanifest", "/icon.svg", "/icon-180.png", "/icon-192.png", "/icon-512.png")

_lock = threading.Lock()
_code = {"code": None, "until": 0.0, "tries": 0}
_listener = {"server": None, "error": None}
_seen = {}


def enabled():
    return settings.get_setting("phone_remote") == "1"


def _iface_ip(name):
    """An interface's IPv4 address (Linux SIOCGIFADDR), or None."""
    import fcntl
    import struct
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", name[:15].encode()))
        return socket.inet_ntoa(packed[20:24])
    except OSError:
        return None
    finally:
        s.close()


def _candidates():
    """Addresses this PC might have on the home network. Linux: every up
    interface that isn't a VPN, Tailscale, container or loopback (the VPN
    chip's own name rules — a VPN's address is no use to a phone).
    Elsewhere: the host's addresses, then the default route's."""
    out = []
    if not system.WINDOWS and os.path.isdir("/sys/class/net"):
        for n in sorted(os.listdir("/sys/class/net")):
            if settings.VPN_IFACE_SKIP.match(n) or settings.VPN_IFACE_RE.match(n):
                continue
            base = os.path.join("/sys/class/net", n)
            try:
                with open(os.path.join(base, "operstate")) as f:
                    state = f.read().strip()
                with open(os.path.join(base, "type")) as f:
                    kind = f.read().strip()
            except OSError:
                continue
            if state == "down" or kind == "65534":
                continue
            ip = _iface_ip(n)
            if ip:
                out.append(ip)
        return out
    try:
        out += [a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # a UDP connect sends nothing
        out.append(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    return out


def lan_ip():
    """The PC's address on the home network: a private address, the usual
    home-router ranges first (192.168.x, then 172.16–31.x, then 10.x).
    Tailscale's 100.64/10 and link-local addresses never count."""
    def rank(ip):
        a = ipaddress.ip_address(ip)
        if a.is_loopback or a.is_link_local or not a.is_private or a in ipaddress.ip_network("100.64.0.0/10"):
            return None
        return next(i for i, net in enumerate(("192.168.0.0/16", "172.16.0.0/12", "10.0.0.0/8", "0.0.0.0/0"))
                    if a in ipaddress.ip_network(net))
    good = [(r, ip) for ip in dict.fromkeys(_candidates()) if (r := rank(ip)) is not None]
    return min(good)[1] if good else None


def address():
    ip = lan_ip()
    return f"http://{ip}:{config.REMOTE_PORT}" if ip else None


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


# --- pairing --------------------------------------------------------------

def new_code():
    """A fresh 6-digit pairing code (the old one stops working)."""
    with _lock:
        _code.update(code="%06d" % secrets.randbelow(10 ** 6), until=time.time() + CODE_TTL, tries=0)
        return _code["code"]


def code_info():
    with _lock:
        if _code["code"] and time.time() < _code["until"] and _code["tries"] < CODE_TRIES:
            return {"code": _code["code"], "left": int(_code["until"] - time.time())}
    return None


def pair(code, name):
    """Trade a pairing code for a phone's token, or None. One use; a few
    wrong tries void the code (6 digits are guessable otherwise)."""
    with _lock:
        live = _code["code"] and time.time() < _code["until"] and _code["tries"] < CODE_TRIES
        if not live:
            return None
        if not isinstance(code, str) or not secrets.compare_digest(code.strip(), _code["code"]):
            _code["tries"] += 1
            return None
        _code.update(code=None, until=0.0, tries=0)
    token = secrets.token_urlsafe(32)
    now = util.utc_stamp()
    with database.db() as c:
        c.execute("INSERT INTO remote_devices(name, token_hash, created, last_seen) VALUES(?,?,?,?)",
                  (device_name(name), _hash(token), now, now))
    return token


def device_name(ua):
    """A readable name from the phone's User-Agent ("iPhone", "Android
    phone", …)."""
    ua = ua or ""
    for key, label in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android phone"),
                       ("Windows", "Windows PC"), ("Macintosh", "Mac"), ("Linux", "Linux device")):
        if key in ua:
            return label
    return "Phone"


def cookie_header(token):
    return f"{COOKIE}={token}; Max-Age=315360000; Path=/; HttpOnly; SameSite=Strict"


def device_of(cookie_header_value):
    """The paired phone a request's cookie names, or None."""
    token = None
    for part in (cookie_header_value or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            token = v
    if not token:
        return None
    h = _hash(token)
    with database.db() as c:
        r = c.execute("SELECT id FROM remote_devices WHERE token_hash=?", (h,)).fetchone()
    if not r:
        return None
    if time.time() - _seen.get(r[0], 0) > SEEN_EVERY:
        _seen[r[0]] = time.time()
        with database.db() as c:
            c.execute("UPDATE remote_devices SET last_seen=? WHERE id=?", (util.utc_stamp(), r[0]))
    return r[0]


def devices():
    with database.db() as c:
        rows = c.execute("SELECT id, name, created, last_seen FROM remote_devices ORDER BY id").fetchall()
    return [{"id": i, "name": n, "created": cr, "last_seen": ls} for i, n, cr, ls in rows]


def remove(device_id):
    with database.db() as c:
        return c.execute("DELETE FROM remote_devices WHERE id=?", (device_id,)).rowcount > 0


def allowed(route, device):
    """Whether the remote listener answers this route."""
    if route in OPEN or route.startswith("/fonts/"):
        return True
    return device is not None and not any(route == b or route.startswith(b + "/") for b in BLOCKED)


# --- the listener ---------------------------------------------------------

def start(handler):
    """Open the Wi-Fi listener (all addresses, config.REMOTE_PORT)."""
    with _lock:
        if _listener["server"]:
            return True
        try:
            srv = system.http_server_class()(("0.0.0.0", config.REMOTE_PORT), handler)
        except OSError as e:
            _listener["error"] = f"Port {config.REMOTE_PORT} is taken ({e.strerror or e}). Set another \"remote_port\" in the config file."
            util.log("phone remote: " + _listener["error"])
            return False
        srv.daemon_threads = True
        _listener.update(server=srv, error=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return True


def stop():
    with _lock:
        srv = _listener["server"]
        _listener["server"] = None
    if srv:
        srv.shutdown()
        srv.server_close()


def set_enabled(on, handler):
    settings.set_setting("phone_remote", "1" if on else "0")
    if on:
        start(handler)
        if not code_info() and not devices():
            new_code()
    else:
        stop()
        with _lock:
            _code.update(code=None, until=0.0, tries=0)


def payload():
    """Settings → Phone remote."""
    on = enabled()
    addr = address() if on else None
    code = code_info() if on else None
    out = {"on": on, "listening": bool(_listener["server"]), "error": _listener["error"] if on else None,
           "address": addr, "port": config.REMOTE_PORT, "devices": devices(), "code": None}
    if code and addr:
        link = f"{addr}/pair?c={code['code']}"
        out["code"] = {**code, "link": link, "qr": qr.svg(link)}
    return out
