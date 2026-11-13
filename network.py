"""Red local: varias laptops en la misma Wi-Fi tocan con un mismo reloj.

Una laptop crea la SALA (anfitrión) y las demás se UNEN: la ven sola en la lista (aviso por UDP broadcast) o
escriben su IP. No se necesita internet, solo estar en la misma red.

Qué se comparte
  - Empezar / Detener: cuando alguien da Empezar, el anfitrión fija un momento ~1 s en el futuro (en SU reloj)
    y se lo manda a todos; cada laptop convierte ese momento a su propio reloj y parte ahí, con la misma cuenta,
    el mismo compás de inicio y el mismo tempo. Detener detiene a todos.
  - El tema: los invitados abren el tema del anfitrión si lo tienen.
  - Quién está y qué toca, y los cambios de patrón de la batería (para mostrarlos).

Reloj: cada invitado mide la diferencia entre su reloj y el del anfitrión con pings (como NTP: se queda con la
medición de menor ida y vuelta), así que un mensaje que tarda en llegar no corre el inicio.

Mensajes: JSON, uno por línea, por TCP (puerto 47810). Avisos de sala: UDP broadcast al puerto 47811.
Todo lo que llega queda en `inbox` (una cola) y la ventana lo lee desde el hilo de Tk.
"""
import json
import queue
import socket
import threading
import time
import uuid

APP = "orquesta-laptops"
TCP_PORT = 47810
DISC_PORT = 47811
LEAD = 1.0         # segundos entre "Empezar" y el inicio de la cuenta: tiempo para que el aviso llegue a todos
PING_EVERY = 2.0


def now():
    return time.perf_counter()


def local_ips():
    """This laptop's IPv4 addresses on the network (no packet is sent)."""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def _broadcast_targets():
    out = {"255.255.255.255", "127.0.0.1"}
    for ip in local_ips():
        out.add(".".join(ip.split(".")[:3] + ["255"]))  # most home / phone networks are /24
    return sorted(out)


class _Conn:
    """One TCP connection that sends and receives JSON lines."""

    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()
        self.peer = None  # member id, once it says hello
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self.file = sock.makefile("r", encoding="utf-8", newline="\n")

    def send(self, msg):
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
        with self.lock:
            self.sock.sendall(data)

    def lines(self):
        for line in self.file:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue

    def close(self):
        for fn in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                fn()
            except OSError:
                pass


class Link:
    """This laptop in the network: alone (role None), anfitrión ("host") or invitado ("guest").

    Everything that arrives is put in `inbox` as dicts; the app reads them on its own thread:
        {"type": "go", "sec0", "countin", "speed", "bpm", "theme", "at", "by"}  start at host time `at`
        {"type": "halt", "by"}                    stop
        {"type": "roster", "members": [...], "host_only": bool}
        {"type": "pattern", "cmd": {...}, "by", "instrument"}
        {"type": "note", "text"}                  something to show the user
        {"type": "lost", "text"}                  the connection ended
    """

    def __init__(self, name="Laptop"):
        self.id = uuid.uuid4().hex[:10]
        self.name = name
        self.role = None
        self.inbox = queue.Queue()
        self.info = {}          # what I tell the others: theme, instruments...
        self.host_only = False  # (host) only the host starts and stops
        self.members = {}       # (host) id -> member dict
        self.roster = []        # last roster (everyone)
        self.offset = 0.0       # host clock - my clock
        self.rtt = None
        self._samples = []      # [(rtt, offset)]
        self._server = None
        self._conns = []
        self._conn = None       # (guest) connection to the host
        self._stop = threading.Event()
        self.host_addr = None
        self.port = None

    # ---- state ----
    @property
    def active(self):
        return self.role is not None

    def host_time(self):
        return now() + self.offset

    def to_local(self, host_t):
        return host_t - self.offset

    def count(self):
        return len(self.roster) if self.active else 0

    def synced(self):
        return self.role == "host" or self.rtt is not None

    # ---- hosting ----
    def host(self):
        self.leave()
        self._stop = threading.Event()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        for port in range(TCP_PORT, TCP_PORT + 10):  # another copy of the app may already use it
            try:
                srv.bind(("", port))
                break
            except OSError:
                continue
        else:
            srv.close()
            raise OSError("no hay un puerto libre para la sala")
        srv.listen(16)
        srv.settimeout(0.5)
        self._server, self.port, self.role = srv, port, "host"
        self.offset, self.rtt = 0.0, 0.0
        self.members = {self.id: self._me()}
        self._send_roster()
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._beacon_loop, daemon=True).start()
        return port

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                sock, _addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn = _Conn(sock)
            self._conns.append(conn)
            threading.Thread(target=self._client_loop, args=(conn,), daemon=True).start()

    def _client_loop(self, conn):
        try:
            for msg in conn.lines():
                if msg.get("type") == "ping":  # answered right here: the reply time matters
                    conn.send({"type": "pong", "t0": msg.get("t0"), "th": now()})
                    continue
                if msg.get("type") == "hello":
                    conn.peer = msg.get("id")
                self._host_handle(msg)
        except (OSError, ValueError):
            pass
        conn.close()
        if conn in self._conns:
            self._conns.remove(conn)
        if conn.peer and self.members.pop(conn.peer, None) is not None and self.role == "host":
            self.inbox.put({"type": "note", "text": "Se desconectó una laptop."})
            self._send_roster()

    def _host_handle(self, msg):
        """The host decides: every message from anybody (including itself) passes here."""
        kind = msg.get("type")
        who = msg.get("id")
        if kind in ("hello", "info"):
            m = self.members.get(who, {})
            m.update({"id": who, "name": msg.get("name", "?"), **msg.get("info", {})})
            self.members[who] = m
            self._send_roster()
        elif kind == "start":
            if self.host_only and who != self.id:
                return self._tell(who, "Solo el anfitrión puede empezar.")
            go = {k: msg.get(k) for k in ("sec0", "countin", "speed", "bpm", "theme")}
            go.update({"type": "go", "at": now() + LEAD, "by": msg.get("name", "?")})
            self._broadcast(go)
        elif kind == "stop":
            if self.host_only and who != self.id:
                return self._tell(who, "Solo el anfitrión puede detener.")
            self._broadcast({"type": "halt", "by": msg.get("name", "?")})
        elif kind == "pattern":
            self._broadcast(msg)

    def _tell(self, who, text):
        if who == self.id:
            self.inbox.put({"type": "note", "text": text})
            return
        for c in list(self._conns):
            if c.peer == who:
                try:
                    c.send({"type": "note", "text": text})
                except OSError:
                    pass

    def set_host_only(self, on):
        self.host_only = bool(on)
        if self.role == "host":
            self._send_roster()

    def _send_roster(self):
        host = self.members.get(self.id, {})
        members = [host] + sorted((m for k, m in self.members.items() if k != self.id),
                                  key=lambda m: m.get("name", ""))
        self._broadcast({"type": "roster", "members": members, "host_only": self.host_only})

    def _broadcast(self, msg):
        for c in list(self._conns):
            try:
                c.send(msg)
            except OSError:
                pass
        self._receive(msg)

    def _beacon_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while not self._stop.is_set() and self.role == "host":
            beacon = {"app": APP, "id": self.id, "name": self.name, "port": self.port,
                      "theme": self.info.get("theme_name") or "", "count": len(self.members)}
            data = json.dumps(beacon).encode("utf-8")
            for target in _broadcast_targets():
                try:
                    s.sendto(data, (target, DISC_PORT))
                except OSError:
                    pass
            self._stop.wait(1.0)
        s.close()

    # ---- joining ----
    def join(self, addr, port=TCP_PORT):
        self.leave()
        self._stop = threading.Event()
        sock = socket.create_connection((addr, int(port)), timeout=4)
        sock.settimeout(None)
        self._conn = _Conn(sock)
        self.role, self.host_addr, self.port = "guest", addr, int(port)
        self.offset, self.rtt, self._samples = 0.0, None, []
        self._conn.send({"type": "hello", "id": self.id, "name": self.name, "info": self.info})
        threading.Thread(target=self._guest_loop, args=(self._conn,), daemon=True).start()
        threading.Thread(target=self._ping_loop, args=(self._conn,), daemon=True).start()

    def _guest_loop(self, conn):
        try:
            for msg in conn.lines():
                if msg.get("type") == "pong":
                    self._clock_sample(msg)
                else:
                    self._receive(msg)
        except (OSError, ValueError):
            pass
        conn.close()
        if self._conn is conn:  # not a leave() of ours
            self._conn, self.role, self.roster = None, None, []
            self.inbox.put({"type": "lost", "text": "Se perdió la conexión con el anfitrión."})

    def _ping_loop(self, conn):
        n = 0
        while not self._stop.is_set() and self._conn is conn:
            try:
                conn.send({"type": "ping", "t0": now()})
            except OSError:
                break
            n += 1
            self._stop.wait(0.05 if n < 20 else PING_EVERY)  # quick burst first, then keep it fresh

    def _clock_sample(self, msg):
        t1 = now()
        t0, th = msg.get("t0"), msg.get("th")
        if t0 is None or th is None:
            return
        rtt = t1 - t0
        self._samples.append((rtt, th - (t0 + t1) / 2))
        self._samples = self._samples[-30:]
        best = min(self._samples)  # the quickest round trip has the least doubt
        self.rtt, self.offset = best

    # ---- both ----
    def _me(self):
        return {"id": self.id, "name": self.name, "host": self.role == "host", **self.info}

    def update_info(self, **info):
        """Tell the others what I'm doing (theme, instruments...)."""
        self.info.update(info)
        self._send({"type": "info", "id": self.id, "name": self.name, "info": self.info})

    def rename(self, name):
        self.name = name or self.name
        self.update_info()

    def request_start(self, sec0, countin, speed, bpm, theme):
        self._send({"type": "start", "id": self.id, "name": self.name, "sec0": sec0, "countin": countin,
                    "speed": speed, "bpm": bpm, "theme": theme})

    def request_stop(self):
        self._send({"type": "stop", "id": self.id, "name": self.name})

    def send_pattern(self, cmd, instrument):
        self._send({"type": "pattern", "id": self.id, "name": self.name, "cmd": cmd, "instrument": instrument})

    def _send(self, msg):
        if self.role == "host":
            if msg.get("type") in ("info", "hello"):
                self.members[self.id] = self._me()
                return self._send_roster()
            return self._host_handle(msg)
        if self.role == "guest" and self._conn is not None:
            try:
                self._conn.send(msg)
            except OSError:
                pass

    def _receive(self, msg):
        if msg.get("type") == "roster":
            self.roster = msg.get("members", [])
        self.inbox.put(msg)

    def leave(self):
        self._stop.set()
        role, self.role = self.role, None
        if self._conn is not None:
            conn, self._conn = self._conn, None
            conn.close()
        for c in list(self._conns):
            c.close()
        self._conns = []
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        self.members, self.roster = {}, []
        self.offset, self.rtt = 0.0, None
        return role


class Finder:
    """Listens for the salas announced on the network: {key: {"addr", "port", "name", "theme", "count", "seen"}}."""

    def __init__(self):
        self.found = {}
        self._stop = threading.Event()
        self.error = None
        self._sock = None

    def start(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            s.bind(("", DISC_PORT))
            s.settimeout(0.5)
        except OSError as exc:
            self.error = str(exc)
            return False
        self._sock = s
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    def _loop(self):
        while not self._stop.is_set():
            try:
                data, (addr, _p) = self._sock.recvfrom(2048)
                msg = json.loads(data.decode("utf-8"))
            except socket.timeout:
                continue
            except (OSError, ValueError):
                if self._stop.is_set():
                    break
                continue
            if msg.get("app") != APP:
                continue
            if addr.startswith("127.") and any(f["id"] == msg.get("id") for f in self.found.values()):
                continue  # the same sala, seen through its real address too
            key = msg.get("id")
            self.found[key] = {"id": key, "addr": addr, "port": msg.get("port", TCP_PORT),
                               "name": msg.get("name", "?"), "theme": msg.get("theme", ""),
                               "count": msg.get("count", 1), "seen": now()}

    def salas(self, max_age=3.5):
        t = now()
        return [f for f in self.found.values() if t - f["seen"] < max_age]

    def stop(self):
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
