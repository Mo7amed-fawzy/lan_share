"""LAN relay discovery.

The relay server answers UDP broadcast probes, so clients on the same network
can find it automatically instead of hard-coding an IP. Works on any PC that
runs the Lan-Share client; the relay must be running on the same LAN.
"""

import json
import socket
import struct
import threading
import time

DISCOVERY_PORT = 8502
PROBE = b"LANSHARE_DISCOVER"
REPLY = b"LANSHARE_REPLY"


def _local_ipv4():
    """Return IPv4 addresses of this machine (primary interface first)."""
    ips = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            ips.append(sock.getsockname()[0])
        finally:
            sock.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except socket.gaierror:
        pass
    return ips


def _interfaces():
    """Return ``[(name, ip, netmask)]`` for every up IPv4 interface.

    Netmasks are read from the kernel via ``SIOCGIFNETMASK`` so the per-subnet
    broadcast is correct even for non-/24 networks. Falls back to /24 if the
    ioctl is unavailable.
    """
    interfaces = []
    try:
        import fcntl
    except ImportError:
        fcntl = None
    if fcntl is not None:
        try:
            for _, name in socket.if_nameindex():
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    addr = fcntl.ioctl(
                        sock.fileno(), 0x8915, struct.pack("256s", name[:15].encode())
                    )
                    mask = fcntl.ioctl(
                        sock.fileno(), 0x891B, struct.pack("256s", name[:15].encode())
                    )
                    ip = socket.inet_ntoa(addr[20:24])
                    netmask = socket.inet_ntoa(mask[20:24])
                except OSError:
                    continue
                finally:
                    sock.close()
                if not ip.startswith("127."):
                    interfaces.append((name, ip, netmask))
            if interfaces:
                return interfaces
        except (OSError, AttributeError):
            pass
    # Fallback: assume /24 on the first non-loopback address.
    for ip in _local_ipv4():
        if not ip.startswith("127."):
            interfaces.append(("default", ip, "255.255.255.0"))
            break
    return interfaces


def _broadcast(ip, netmask):
    """Return the directed broadcast address for an interface."""
    addr = struct.unpack(">I", socket.inet_aton(ip))[0]
    mask = struct.unpack(">I", socket.inet_aton(netmask))[0]
    return socket.inet_ntoa(struct.pack(">I", addr | (~mask & 0xFFFFFFFF)))


def _probe_targets():
    targets = [("255.255.255.255", DISCOVERY_PORT)]
    for _, ip, netmask in _interfaces():
        targets.append((_broadcast(ip, netmask), DISCOVERY_PORT))
    targets.append(("127.0.0.1", DISCOVERY_PORT))
    return targets


def discover(timeout=2.0):
    """Broadcast a probe and return ``(ip, port)`` of the relay, or ``None``.

    Probes the global broadcast, each local subnet broadcast (computed from the
    real interface netmask, so non-/24 networks work) and localhost. A real LAN
    address is preferred over loopback; loopback is only reported when the only
    relay answering is on this machine.
    """
    found = discover_detail(timeout=timeout)
    if found is None:
        return None
    return found["ip"], found["port"]


def discover_detail(timeout=2.0):
    """Like :func:`discover` but returns a dict or ``None``.

    Returns ``{"ip": ..., "port": ..., "via": "lan" | "localhost"}``. The
    ``via`` field lets callers distinguish a relay found on the LAN from the
    local machine's own relay, so they never silently assume loopback.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    best_lan = None
    best_local = None
    try:
        sock.bind(("", 0))
        sock.settimeout(timeout)
        for target in _probe_targets():
            try:
                sock.sendto(PROBE, target)
            except OSError:
                continue
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                break
            if not data.startswith(REPLY):
                continue
            try:
                info = json.loads(data[len(REPLY):].decode("utf-8", "replace"))
            except ValueError:
                continue
            if info.get("type") != "lan-share-relay":
                continue
            ip, port = addr[0], int(info.get("port", 8501))
            if ip.startswith("127."):
                if best_local is None:
                    best_local = {"ip": ip, "port": port, "via": "localhost"}
            else:
                return {"ip": ip, "port": port, "via": "lan"}
    finally:
        sock.close()
    return best_local


def probe(ip, port=DISCOVERY_PORT, timeout=2.0):
    """Send a unicast probe to a specific host and return ``(ip, port)`` or ``None``.

    Unlike :func:`discover` this targets a single address - used by the LAN
    diagnosis script to test one host without relying on broadcast.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", 0))
        sock.settimeout(timeout)
        sock.sendto(PROBE, (ip, port))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                break
            if not data.startswith(REPLY):
                continue
            try:
                info = json.loads(data[len(REPLY):].decode("utf-8", "replace"))
            except ValueError:
                continue
            if info.get("type") != "lan-share-relay":
                continue
            return addr[0], int(info.get("port", 8501))
    finally:
        sock.close()
    return None


class DiscoveryResponder:
    """Listens on the discovery UDP port and answers probes."""

    def __init__(self, relay_port=8501):
        self.relay_port = relay_port
        self._running = False
        self._sock = None
        self._thread = None

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind(("", DISCOVERY_PORT))
        except OSError:
            self._sock.close()
            self._sock = None
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        payload = REPLY + json.dumps(
            {"type": "lan-share-relay", "port": self.relay_port}
        ).encode()
        while self._running:
            try:
                data, addr = self._sock.recvfrom(1024)
            except OSError:
                break
            if data.startswith(PROBE):
                try:
                    self._sock.sendto(payload, addr)
                except OSError:
                    pass

    def stop(self):
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

