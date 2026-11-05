"""Collegamento con CueStage: Script Follower si presenta e segue il cambio di master.

CueStage può lavorare con istanze secondarie (slave / ridondanza); se il master cade, una secondaria viene
promossa. Script Follower però manda i comandi OSC a un indirizzo IP fisso, quello del vecchio master. Con
questo protocollo (tutto OSC su UDP) il nuovo master può dire a Script Follower dove spostarsi.

Script Follower → CueStage, sulla porta OSC di ogni profilo CueStage, all'avvio e poi ogni HELLO_EVERY secondi:
    /cuestage/client/hello  "scriptfollower" <versione> <nome del computer> <porta di ritorno:int>
                            <id del client> <id del profilo>
    /cuestage/client/bye    <id del client>                       (alla chiusura)

CueStage (master) → Script Follower, all'indirizzo da cui arriva l'hello e alla porta di ritorno:
    /cuestage/client/ack    <nome dell'istanza> <porta OSC:int> <istanze del gruppo: "ip1,ip2,…">
        Il master ricorda i client (e li passa alle secondarie con lo stato): servono a un nuovo master.

Il nuovo master → Script Follower, quando viene promosso (a tutti i client che il vecchio master conosceva):
    /cuestage/master        <nuovo ip> <porta OSC:int> <nome dell'istanza> [<vecchio ip>]
        Script Follower sposta lì i profili CueStage che puntavano al vecchio master (o a un'istanza del gruppo)
        e risponde:
    /cuestage/client/moved  <id del client> <id dei profili spostati, separati da virgole>

Il protocollo è descritto anche in OSC_CLIENTS.md, nel repository di CueStage.

Sicurezza: un messaggio «master» viene applicato da solo solo se arriva da un'istanza del gruppo conosciuta
(dalle risposte all'hello) o dall'indirizzo di un profilo; altrimenti si chiede conferma all'operatore.
"""
import socket
import threading
import time
import uuid

from PySide6.QtCore import QObject, QTimer, Signal

from . import __version__
from .i18n import _

try:
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import ThreadingOSCUDPServer
    from pythonosc.udp_client import SimpleUDPClient
except Exception:  # pragma: no cover
    Dispatcher = ThreadingOSCUDPServer = SimpleUDPClient = None

DEFAULT_PORT = 53100              # porta di ritorno (UDP) su cui Script Follower ascolta CueStage
HELLO_EVERY = 5.0                 # secondi
LINK_DEFAULTS = {"on": True, "port": DEFAULT_PORT}

A_HELLO = "/cuestage/client/hello"
A_BYE = "/cuestage/client/bye"
A_MOVED = "/cuestage/client/moved"
A_ACK = "/cuestage/client/ack"
A_MASTER = "/cuestage/master"


def link_settings(d):
    s = dict(LINK_DEFAULTS)
    s.update({k: v for k, v in (d or {}).items() if k in LINK_DEFAULTS})
    s["on"] = s["on"] not in (False, "false", "0", 0, None)
    try:
        s["port"] = max(1024, min(65535, int(s["port"])))
    except (TypeError, ValueError):
        s["port"] = DEFAULT_PORT
    return s


def plan_move(targets, new_ip, new_port, old_ip, known):
    """Profili CueStage da spostare: [(indice, id)].

    known = {id profilo: insieme degli IP del suo gruppo di istanze (dalle risposte all'hello)}.
    Si sposta un profilo CueStage se punta al vecchio master indicato, oppure a un'istanza del suo gruppo che
    contiene il nuovo master. Se il messaggio non dice nulla del vecchio master e non ci sono gruppi noti, si
    spostano tutti i profili CueStage (succede con un solo master)."""
    cs = [(i, t) for i, t in enumerate(targets) if t.get("plugin") == "cuestage" and (t.get("host") or "").strip()]
    there = lambda t: t["host"].strip() == new_ip and int(t.get("port") or 0) == int(new_port)  # noqa: E731
    out = []
    for i, t in cs:
        if there(t):
            continue                                        # è già lì
        host = t["host"].strip()
        if (old_ip and host == old_ip) or new_ip in (known.get(t["id"]) or set()):
            out.append((i, t["id"]))
    if not out and not old_ip and not any(known.values()):
        out = [(i, t["id"]) for i, t in cs if not there(t)]
    return out


class CueStageLink(QObject):
    """Annuncia Script Follower ai profili CueStage e ascolta il nuovo master."""
    master = Signal(str, int, str, str, str, bool)   # nuovo ip, porta, nome, vecchio ip, mittente, istanza nota
    status = Signal(str)
    _msg = Signal(str, str, list)                    # (dal thread del server) indirizzo OSC, mittente, argomenti

    def __init__(self, client_id=None, parent=None):
        super().__init__(parent)
        self.client_id = client_id or uuid.uuid4().hex[:12]
        self.name = socket.gethostname()
        self.targets = []
        self.cfg = link_settings(None)
        self.port = None                 # porta su cui si ascolta davvero
        self.known = {}                  # id profilo -> set(ip delle istanze del gruppo)
        self.names = {}                  # id profilo -> nome dell'istanza CueStage che ha risposto
        self.last_ack = {}               # id profilo -> istante dell'ultima risposta
        self.server = None
        self._clients = {}
        self._msg.connect(self._handle)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.hello)

    # ------------------------------------------------------------ avvio e configurazione
    def configure(self, cfg, targets):
        cfg = link_settings(cfg)
        restart = cfg != self.cfg or (self.server is None and cfg["on"])
        self.cfg = cfg
        self.set_targets(targets)
        if restart:
            self.stop(bye=False)
            if cfg["on"]:
                self.start()

    def set_targets(self, targets):
        ids = {t["id"] for t in targets}
        self.targets = [dict(t) for t in targets]
        for d in (self.known, self.names, self.last_ack):
            for k in [k for k in d if k not in ids]:
                del d[k]

    def start(self):
        if ThreadingOSCUDPServer is None:
            self.status.emit(_("python-osc non installato"))
            return False
        disp = Dispatcher()
        disp.set_default_handler(self._recv, needs_reply_address=True)
        err = None
        for port in range(self.cfg["port"], self.cfg["port"] + 10):     # occupata: la successiva
            try:
                self.server = ThreadingOSCUDPServer(("0.0.0.0", port), disp)
                self.port = port
                break
            except OSError as e:
                err = e
        if self.server is None:
            self.status.emit(_("CueStage: porta di ritorno {0} non disponibile ({1})").format(self.cfg["port"], err))
            return False
        threading.Thread(target=self.server.serve_forever, daemon=True, name="cuestage-link").start()
        self.timer.start(int(HELLO_EVERY * 1000))
        QTimer.singleShot(200, self.hello)
        return True

    def stop(self, bye=True):
        self.timer.stop()
        if bye and self.server is not None:
            for t in self._cs_targets():
                self._send(t["host"], t["port"], A_BYE, [self.client_id])
        s, self.server = self.server, None
        self.port = None
        if s is not None:
            try:
                s.shutdown()
                s.server_close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ invio
    def _cs_targets(self):
        return [t for t in self.targets if t.get("plugin") == "cuestage" and (t.get("host") or "").strip()]

    def _send(self, host, port, address, args):
        if SimpleUDPClient is None:
            return
        try:
            key = (host.strip(), int(port))
            cl = self._clients.get(key)
            if cl is None:
                cl = self._clients[key] = SimpleUDPClient(key[0], key[1], allow_broadcast=True)
            cl.send_message(address, args)
        except (OSError, ValueError):
            pass                          # rete assente o indirizzo sbagliato: si riprova al prossimo giro

    def hello(self):
        if self.server is None or self.port is None:
            return
        for t in self._cs_targets():
            self._send(t["host"], t["port"], A_HELLO,
                       ["scriptfollower", __version__, self.name, int(self.port), self.client_id, t["id"]])

    def connected(self, pid):
        """La CueStage del profilo ha risposto da poco (sa che ci siamo)."""
        return time.monotonic() - self.last_ack.get(pid, -1e9) < 3 * HELLO_EVERY

    # ------------------------------------------------------------ ricezione
    def _recv(self, client_address, address, *args):
        self._msg.emit(address, client_address[0], list(args))      # al thread dell'interfaccia

    def _handle(self, address, sender, args):
        a = address.rstrip("/").lower()
        if a == A_ACK:
            self._ack(sender, args)
        elif a == A_MASTER:
            self._master(sender, args)

    def _ack(self, sender, args):
        name = str(args[0]) if len(args) > 0 else ""
        members = {m.strip() for m in str(args[2]).split(",") if m.strip()} if len(args) > 2 else set()
        members.add(sender)
        for t in self._cs_targets():
            if t["host"].strip() == sender or t["host"].strip() in members:
                self.known[t["id"]] = set(members) | {t["host"].strip()}
                self.names[t["id"]] = name
                self.last_ack[t["id"]] = time.monotonic()

    def _master(self, sender, args):
        new_ip = str(args[0]).strip() if args and str(args[0]).strip() else sender
        try:
            port = int(args[1]) if len(args) > 1 else 0
        except (TypeError, ValueError):
            port = 0
        name = str(args[2]) if len(args) > 2 else ""
        old_ip = str(args[3]).strip() if len(args) > 3 else ""
        if not port:                       # senza porta: la stessa del profilo
            port = next((int(t["port"]) for t in self._cs_targets()), 53000)
        trusted = any(sender in g or new_ip in g for g in self.known.values()) or \
            any(t["host"].strip() in (sender, old_ip) for t in self._cs_targets())
        self.master.emit(new_ip, port, name, old_ip, sender, trusted)

    def moved(self, new_ip, port, pids):
        """Dopo lo spostamento: lo si conferma al nuovo master e lo si saluta subito."""
        self._send(new_ip, port, A_MOVED, [self.client_id, ",".join(pids)])
        for pid in pids:
            g = self.known.get(pid)
            if g is not None:
                g.add(new_ip)
        QTimer.singleShot(50, self.hello)
