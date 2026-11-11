"""Starts the hub copied out of the APK (Decision 88). Its folders go in the
app's own storage; everything else is the same hub as on the PC."""
import os
import socket
import sys
import threading


def _port(files_dir):
    """The app's own port: the one it had before when it is free (the page's
    saved state belongs to its address), else a new free one. Never the PC
    default 8787, which another app on the phone may hold."""
    keep = os.path.join(files_dir, "hub.port")
    try:
        with open(keep) as f:
            want = int(f.read().strip())
    except (OSError, ValueError):
        want = 0
    for port in ([want] if want else []) + [0]:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            port = s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
        with open(keep, "w") as f:
            f.write(str(port))
        return port
    raise OSError("no free port")


def run(hub_dir, files_dir):
    """Start the hub; returns its port."""
    for name in ("DATA", "CONFIG", "CACHE"):
        os.environ["XDG_%s_HOME" % name] = os.path.join(files_dir, name.lower())
    os.environ.setdefault("HOME", files_dir)
    sys.argv = ["hub.py", "--no-browser"]
    sys.path.insert(0, hub_dir)
    from animehub.config import config
    config.PORT = _port(files_dir)

    def serve():
        try:
            from animehub.server import main
            main()
        except BaseException:
            import traceback
            traceback.print_exc()

    threading.Thread(target=serve, name="hub", daemon=True).start()
    return config.PORT
