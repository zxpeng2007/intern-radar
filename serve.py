"""Serve the Intern Radar page.

    python serve.py               bind according to "expose" in config.json
    python serve.py --local-only  loopback only, whatever the config says
    python serve.py --port 9000

expose = "local"     -> 127.0.0.1 only
expose = "tailscale" -> 127.0.0.1 + this computer's Tailscale address, so your
                        own phone can reach it and nobody else can. The address
                        is re-checked every 30 s, because at login Tailscale
                        usually comes up after this script does.
expose = "lan"       -> every interface (0.0.0.0). Fine at home behind a router;
                        on campus or on a machine with a public IP this puts the
                        page on the open internet, so the script warns.

Only four paths are served: the page, the widget payload, the CSV and a health
check. The scrape caches in this folder are never exposed.
"""
import datetime
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWED = {
    "/": "index.html",
    "/index.html": "index.html",
    "/widget.json": "widget.json",
    "/listings.csv": "listings.csv",
}


def load_config():
    cfg = {"expose": "tailscale", "port": 8765}
    path = os.path.join(HERE, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:  # -sig: tolerate the BOM Windows editors add
            cfg.update({k: v for k, v in json.load(f).items() if k in cfg})
    return cfg


def _is_cgnat(ip):
    """True for 100.64.0.0/10, the range every Tailscale address comes from."""
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
    except ValueError:
        return False
    return a == 100 and 64 <= b <= 127


def tailscale_ip():
    """This computer's Tailscale address, or None.

    Asked of the network stack rather than of the tailscale CLI: this runs
    every 30 seconds from a windowless background process on Windows, and
    spawning a console program from one flashes a window each time. The CLI
    is kept as a hidden fallback.
    """
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if _is_cgnat(info[4][0]):
                return info[4][0]
    except OSError:
        pass
    si, flags = None, 0
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        flags = subprocess.CREATE_NO_WINDOW
    for exe in ["tailscale", r"C:\Program Files\Tailscale\tailscale.exe", "/Applications/Tailscale.app/Contents/MacOS/Tailscale"]:
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5, startupinfo=si, creationflags=flags)
            lines = (out.stdout or "").strip().splitlines()
            if out.returncode == 0 and lines and _is_cgnat(lines[0].strip()):
                return lines[0].strip()
        except (OSError, subprocess.SubprocessError):
            continue
    return None


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def _route(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return body
        if path not in ALLOWED:
            self.send_error(404, "Not served")
            return False
        self.path = "/" + ALLOWED[path]
        return True

    def do_GET(self):
        r = self._route()
        if r is True:
            super().do_GET()
        elif r:
            self.wfile.write(r)

    def do_HEAD(self):
        if self._route() is True:
            super().do_HEAD()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()


def serve(bind, port):
    srv = http.server.ThreadingHTTPServer((bind, port), Handler)
    srv.daemon_threads = True
    print(f"ready on http://{bind}:{port}", flush=True)
    srv.serve_forever()


def watch_tailscale(port, poll_seconds=30):
    """Bind the Tailscale address as soon as it exists, and re-bind if it changes."""
    bound = None
    said_missing = False
    while True:
        ts = tailscale_ip()
        if ts and ts != bound:
            try:
                threading.Thread(target=serve, args=(ts, port), daemon=True).start()
                bound, said_missing = ts, False
            except OSError as e:
                print(f"could not bind {ts}:{port}: {e}", flush=True)
        elif not ts and not said_missing:
            print(f"Tailscale address not found yet; serving on loopback only and retrying every {poll_seconds}s.", flush=True)
            said_missing = True
        time.sleep(poll_seconds)


def main():
    if sys.stdout is None or sys.stderr is None:  # windowless (pythonw / scheduled task): log to a file
        sys.stdout = sys.stderr = open(os.path.join(HERE, "serve.log"), "a", encoding="utf-8")
    cfg = load_config()
    port = int(cfg["port"])
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    expose = "local" if "--local-only" in sys.argv else str(cfg["expose"]).lower()
    if not os.path.exists(os.path.join(HERE, "index.html")):
        print("index.html is missing - run  python refresh.py  first.", flush=True)
    print(f"serve.py starting {datetime.datetime.now():%Y-%m-%d %H:%M:%S} (expose={expose})", flush=True)

    threads = []
    if expose == "lan":
        print("WARNING: expose=lan binds every interface. If this machine has a public IP (common on campus), "
              "the page is reachable from the internet. Prefer expose=tailscale.", flush=True)
        threads.append(threading.Thread(target=serve, args=("0.0.0.0", port), daemon=True))
    else:
        threads.append(threading.Thread(target=serve, args=("127.0.0.1", port), daemon=True))
        if expose == "tailscale":
            threads.append(threading.Thread(target=watch_tailscale, args=(port,), daemon=True))
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
