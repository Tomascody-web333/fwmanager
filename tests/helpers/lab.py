"""A two-host network lab made of namespaces: A runs the firewall, B is a neighbour.

        A (netns_prefix)                      B
   fwA 192.168.77.1/24  <-- veth -->  fwB 192.168.77.2/24    (the "local network")
       203.0.113.1/24                      203.0.113.9/24     (an address outside it)

B reaches A's two addresses from two different source addresses, so a rule that allows "the local
network only" can be told apart from one that allows everyone.
"""

import subprocess
import sys
import time

A_LAN, A_OUT = "192.168.77.1", "203.0.113.1"
B_LAN, B_OUT = "192.168.77.2", "203.0.113.9"

TCP_PROBE = """
import socket, sys
src, dst, port = sys.argv[1:4]
s = socket.socket(); s.settimeout(1.5); s.bind((src, 0))
try:
    s.connect((dst, int(port))); s.sendall(b"hi")
    print("ok" if s.recv(2) == b"hi" else "noreply")
except OSError:
    print("fail")
"""

LISTENER = """
import socket, sys, threading
port = int(sys.argv[1])
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("0.0.0.0", port)); srv.listen(8)
def echo(c):
    try:
        while (d := c.recv(64)):
            c.sendall(d)
    except OSError:
        pass
print("listening", flush=True)
while True:
    c, _ = srv.accept()
    threading.Thread(target=echo, args=(c,), daemon=True).start()
"""

LONG_CONNECTION = """
import socket, sys, time
src, dst, port = sys.argv[1:4]
s = socket.socket(); s.settimeout(5); s.bind((src, 0)); s.connect((dst, int(port)))
out = []
for i in range(2):
    s.sendall(b"%d" % i); out.append(s.recv(1) == b"%d" % i)
    print("round", i, "ok" if out[-1] else "bad", flush=True)
    if i == 0:
        time.sleep(float(sys.argv[4]))
"""


def run(prefix, argv, **kw):
    return subprocess.run([*prefix, *argv], capture_output=True, text=True, timeout=60, **kw)


def py(prefix, script, *args, timeout=30):
    return subprocess.run([*prefix, sys.executable, "-c", script, *map(str, args)],
                          capture_output=True, text=True, timeout=timeout)  # fmt: skip


class Lab:
    def __init__(self, a_prefix):
        self.a = list(a_prefix)
        self.holder_b = subprocess.Popen(["unshare", "-n", "sleep", "infinity"])
        time.sleep(0.3)
        self.b = ["nsenter", "-t", str(self.holder_b.pid), "-n"]
        self._build()
        self.listeners = []

    def _ip(self, prefix, *args):
        r = run(prefix, ["ip", *args])
        assert r.returncode == 0, f"ip {' '.join(args)}: {r.stderr}"

    def _build(self):
        self._ip(self.a, "link", "add", "fwA", "type", "veth", "peer", "name", "fwB")
        self._ip(self.a, "link", "set", "fwB", "netns", str(self.holder_b.pid))
        for prefix, dev, addrs in (
            (self.a, "fwA", (A_LAN, A_OUT)),
            (self.b, "fwB", (B_LAN, B_OUT)),
        ):
            for ip in addrs:
                self._ip(prefix, "addr", "add", f"{ip}/24", "dev", dev)
            self._ip(prefix, "link", "set", dev, "up")
            self._ip(prefix, "link", "set", "lo", "up")

    # -- things to do on the hosts ------------------------------------------------------------

    def listen_on_a(self, port):
        p = subprocess.Popen([*self.a, sys.executable, "-c", LISTENER, str(port)],
                             stdout=subprocess.PIPE, text=True)  # fmt: skip
        assert p.stdout.readline().strip() == "listening"
        self.listeners.append(p)
        return p

    def tcp(self, src, dst, port):
        """From B: does a TCP connection to A work? 'ok', 'fail' (refused/timeout)."""
        return py(self.b, TCP_PROBE, src, dst, port).stdout.strip()

    def ping(self, dst):
        """From B: does A answer an ICMP echo?"""
        script = (
            "from fwmanager.cli.dispatch import icmp_echo\n"
            "import sys; print(icmp_echo(sys.argv[1], 'fwB', 1.5))"
        )
        return py(
            self.b, f"import sys; sys.path.insert(0, {_src()!r})\n{script}", dst
        ).stdout.strip()

    def set_default_route_on_a(self):
        self._ip(self.a, "route", "replace", "default", "via", B_LAN)

    def clear_default_route_on_a(self):
        run(self.a, ["ip", "route", "del", "default"])

    def close(self):
        for p in self.listeners:
            p.kill()
            p.wait()
        self.holder_b.kill()
        self.holder_b.wait()
        run(self.a, ["ip", "link", "del", "fwA"])


def _src():
    from pathlib import Path

    return str(Path(__file__).resolve().parents[2] / "src")
