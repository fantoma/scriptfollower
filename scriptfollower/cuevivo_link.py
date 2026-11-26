"""Collegamento con Cuevivo: Script Follower si presenta e segue il cambio di master.

Cuevivo può lavorare con istanze secondarie (slave / ridondanza); se il master cade, una secondaria viene
promossa. Script Follower però manda i comandi OSC a un indirizzo IP fisso, quello del vecchio master. Con
questo protocollo (tutto OSC su UDP) il nuovo master può dire a Script Follower dove spostarsi.

Script Follower → Cuevivo, sulla porta OSC di ogni profilo Cuevivo, all'avvio e poi ogni HELLO_EVERY secondi:
    /cuestage/client/hello  "scriptfollower" <versione> <nome del computer> <porta di ritorno:int>
                            <id del client> <id del profilo>
    /cuestage/client/bye    <id del client>                       (alla chiusura)

Cuevivo (master) → Script Follower, all'indirizzo da cui arriva l'hello e alla porta di ritorno:
    /cuestage/client/ack    <nome dell'istanza> <porta OSC:int> <istanze del gruppo: "ip1,ip2,…">
        Il master ricorda i client (e li passa alle secondarie con lo stato): servono a un nuovo master.

Il nuovo master → Script Follower, quando viene promosso (a tutti i client che il vecchio master conosceva):
    /cuestage/master        <nuovo ip> <porta OSC:int> <nome dell'istanza> [<vecchio ip>]
        Script Follower sposta lì i profili Cuevivo che puntavano al vecchio master (o a un'istanza del gruppo)
        e risponde:
    /cuestage/client/moved  <id del client> <id dei profili spostati, separati da virgole>

Gli indirizzi usano lo spazio dei nomi storico «/cuestage» (il programma si chiamava CueStage): lo capiscono sia
Cuevivo sia le versioni di CueStage che hanno questa funzione. Cuevivo accetta anche «/cuevivo/…» e risponde
nello spazio dei nomi usato dal programma; in arrivo Script Follower accetta tutti e due.
Il protocollo è descritto anche in OSC_CLIENTS.md, nel repository di Cuevivo.

Cue attive (pannello «Cue attive»), dopo un hello:
    /cuestage/client/active/subscribe    <id del client> <aggiornamenti/s:float> [<opzioni: "nogroups,media">]
    /cuestage/client/active/unsubscribe  <id del client>
    /cuestage/active                     <stato in JSON>          (Cuevivo → Script Follower)
    /cuestage/client/active/stop         <id cue> [<fade:float>]
    /cuestage/client/active/pause        <id cue>                 (pausa / ripresa)
Con le istanze secondarie: ci si abbona al master; se gli stati non arrivano più ci si riabbona da soli
(Cuevivo riavviato, rete, cambio di master). Se il master tace e il gruppo ha istanze in ridondanza, si mostrano
le cue di una di quelle finché il master risponde di nuovo o arriva il nuovo master.

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

DEFAULT_PORT = 53100              # porta di ritorno (UDP) su cui Script Follower ascolta Cuevivo
HELLO_EVERY = 5.0                 # secondi
LINK_DEFAULTS = {"on": True, "port": DEFAULT_PORT}

A_HELLO = "/cuestage/client/hello"
A_BYE = "/cuestage/client/bye"
A_MOVED = "/cuestage/client/moved"
A_ACK = "/cuestage/client/ack"
A_MASTER = "/cuestage/master"
A_SUB = "/cuestage/client/active/subscribe"
A_UNSUB = "/cuestage/client/active/unsubscribe"
A_STOP = "/cuestage/client/active/stop"
A_PAUSE = "/cuestage/client/active/pause"
A_ACTIVE = "/cuestage/active"
ACTIVE_STALE = 5.0                # secondi senza stati: «Cuevivo non collegato» (e si prova la ridondanza)
RESUBSCRIBE_AFTER = 3.0           # secondi senza stati: al prossimo hello ci si riabbona
ACTIVE_DEFAULTS = {"freq": 4.0, "nogroups": False, "media": False}
NAMESPACES = ("/cuestage", "/cuevivo")


def _canonical(address):
    """«/cuevivo/master» → «/cuestage/master»: i due spazi dei nomi sono lo stesso protocollo."""
    a = address.rstrip("/").lower()
    for ns in NAMESPACES[1:]:
        if a.startswith(ns + "/"):
            return NAMESPACES[0] + a[len(ns):]
    return a


def link_settings(d):
    s = dict(LINK_DEFAULTS)
    s.update({k: v for k, v in (d or {}).items() if k in LINK_DEFAULTS})
    s["on"] = s["on"] not in (False, "false", "0", 0, None)
    try:
        s["port"] = max(1024, min(65535, int(s["port"])))
    except (TypeError, ValueError):
        s["port"] = DEFAULT_PORT
    return s


def active_settings(d):
    s = dict(ACTIVE_DEFAULTS)
    s.update({k: v for k, v in (d or {}).items() if k in ACTIVE_DEFAULTS})
    try:
        s["freq"] = max(0.5, min(20.0, float(s["freq"])))
    except (TypeError, ValueError):
        s["freq"] = ACTIVE_DEFAULTS["freq"]
    for k in ("nogroups", "media"):
        s[k] = s[k] not in (False, "false", "0", 0, None)
    return s


def parse_active(text):
    """Stato JSON di /cuestage/active → dict pulito, oppure None se non è valido (si ignora senza errori)."""
    import json
    try:
        d = json.loads(text if isinstance(text, str) else bytes(text).decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("cues", []), list):
        return None

    def num(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    cues = []
    for c in d.get("cues") or []:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        cues.append({
            "id": str(c["id"]), "number": str(c.get("number") or ""), "name": str(c.get("name") or ""),
            "type": str(c.get("type") or ""), "list": str(c.get("list") or ""),
            "color": str(c.get("color") or "#5a5c64"), "group": bool(c.get("group")),
            "in_group": bool(c.get("in_group")),
            "state": c.get("state") if c.get("state") in ("wait", "run", "paused") else "run",
            "wait": num(c.get("wait")) or 0.0, "elapsed": num(c.get("elapsed")) or 0.0,
            "total": num(c.get("total")), "status": str(c.get("status") or ""), "fading": bool(c.get("fading"))})
    sb = d.get("standby") if isinstance(d.get("standby"), dict) else None
    standby = {"name": str(sb.get("name") or ""), "list": str(sb.get("list") or "")} if sb else None
    more = d.get("more")
    return {"instance": str(d.get("instance") or ""), "paused_all": bool(d.get("paused_all")),
            "standby": standby, "cues": cues, "more": int(more) if isinstance(more, int) and more > 0 else 0}


class Endpoint:
    """Un'istanza di Cuevivo da cui si ricevono le cue attive."""

    def __init__(self, host, port, names=(), fallback_for=None):
        self.host, self.port = host, int(port)
        self.names = list(names)          # nomi dei profili che puntano qui
        self.fallback_for = fallback_for  # (host, porta) del master che tace, per le istanze di riserva
        self.instance = ""                # nome dell'istanza (dagli stati o dall'ack)
        self.state = None                 # ultimo stato ricevuto (parse_active)
        self.t = -1e9                     # istante dell'ultimo stato
        self.sub_t = -1e9                 # istante dell'ultimo subscribe
        self.note = ""                    # es. «nuovo master»

    @property
    def key(self):
        return (self.host, self.port)

    def fresh(self, now=None):
        return ((now or time.monotonic()) - self.t) < ACTIVE_STALE


def plan_move(targets, new_ip, new_port, old_ip, known):
    """Profili Cuevivo da spostare: [(indice, id)].

    known = {id profilo: insieme degli IP del suo gruppo di istanze (dalle risposte all'hello)}.
    Si sposta un profilo Cuevivo se punta al vecchio master indicato, oppure a un'istanza del suo gruppo che
    contiene il nuovo master. Se il messaggio non dice nulla del vecchio master e non ci sono gruppi noti, si
    spostano tutti i profili Cuevivo (succede con un solo master)."""
    cs = [(i, t) for i, t in enumerate(targets) if t.get("plugin") == "cuevivo" and (t.get("host") or "").strip()]
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


class CuevivoLink(QObject):
    """Annuncia Script Follower ai profili Cuevivo e ascolta il nuovo master."""
    master = Signal(str, int, str, str, str, bool)   # nuovo ip, porta, nome, vecchio ip, mittente, istanza nota
    status = Signal(str)
    activeChanged = Signal()                         # stati delle cue attive o istanze cambiati
    _msg = Signal(str, str, list)                    # (dal thread del server) indirizzo OSC, mittente, argomenti

    def __init__(self, client_id=None, parent=None):
        super().__init__(parent)
        self.client_id = client_id or uuid.uuid4().hex[:12]
        self.name = socket.gethostname()
        self.targets = []
        self.cfg = link_settings(None)
        self.port = None                 # porta su cui si ascolta davvero
        self.known = {}                  # id profilo -> set(ip delle istanze del gruppo)
        self.names = {}                  # id profilo -> nome dell'istanza Cuevivo che ha risposto
        self.last_ack = {}               # id profilo -> istante dell'ultima risposta
        self.server = None
        self._clients = {}
        self.active_on = False           # pannello «Cue attive» visibile: abbonati
        self.active_cfg = active_settings(None)
        self.endpoints = {}              # (host, porta) -> Endpoint (i profili Cuevivo, senza doppioni)
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
        self._rebuild_endpoints()

    def _rebuild_endpoints(self):
        want = {}
        for t in self._cs_targets():
            key = (t["host"].strip(), int(t["port"]))
            want.setdefault(key, []).append(t["name"])
        for key, ep in list(self.endpoints.items()):
            if ep.fallback_for is not None:
                if ep.fallback_for not in want:
                    self._drop_endpoint(key)
            elif key not in want:
                self._drop_endpoint(key)
        for key, names in want.items():
            ep = self.endpoints.get(key)
            if ep is None:
                ep = self.endpoints[key] = Endpoint(key[0], key[1], names)
            ep.names = names
        self.activeChanged.emit()

    def _drop_endpoint(self, key):
        ep = self.endpoints.pop(key, None)
        if ep is not None and self.active_on and self.server is not None:
            self._send(ep.host, ep.port, A_UNSUB, [self.client_id])
        for k in [k for k, e in self.endpoints.items() if e.fallback_for == key]:
            self._drop_endpoint(k)

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
            self.status.emit(_("Cuevivo: porta di ritorno {0} non disponibile ({1})").format(self.cfg["port"], err))
            return False
        threading.Thread(target=self.server.serve_forever, daemon=True, name="cuevivo-link").start()
        self.timer.start(int(HELLO_EVERY * 1000))
        QTimer.singleShot(200, self.hello)
        return True

    def stop(self, bye=True):
        self.timer.stop()
        if self.active_on and self.server is not None:
            for ep in self.endpoints.values():
                self._send(ep.host, ep.port, A_UNSUB, [self.client_id])
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
        return [t for t in self.targets if t.get("plugin") == "cuevivo" and (t.get("host") or "").strip()]

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
        for ep in list(self.endpoints.values()):          # istanze di riserva: anche loro devono sentirci
            if ep.fallback_for is not None:
                self._hello_to(ep)
        if self.active_on:
            self._active_upkeep()

    def _hello_to(self, ep):
        self._send(ep.host, ep.port, A_HELLO, ["scriptfollower", __version__, self.name, int(self.port),
                                               self.client_id, ""])

    # ------------------------------------------------------------ cue attive
    def set_active(self, on, cfg=None):
        """Pannello «Cue attive» mostrato (abbonamento) o nascosto (disdetta). cfg: frequenza e opzioni."""
        cfg = active_settings(cfg if cfg is not None else self.active_cfg)
        changed = cfg != self.active_cfg
        self.active_cfg = cfg
        if bool(on) == self.active_on and not changed:
            return
        was = self.active_on
        self.active_on = bool(on)
        if self.server is None:
            return
        if self.active_on and not was:
            for ep in self.endpoints.values():
                ep.sub_t = -1e9
            self.hello()                  # prima l'hello (Cuevivo impara la porta di ritorno), poi il subscribe
        elif self.active_on:              # cambiate frequenza o opzioni: si rimanda il subscribe
            for ep in self.endpoints.values():
                self._subscribe(ep, force=True)
        elif was:
            for key, ep in list(self.endpoints.items()):
                self._send(ep.host, ep.port, A_UNSUB, [self.client_id])
                ep.state, ep.t = None, -1e9
                if ep.fallback_for is not None:
                    del self.endpoints[key]
        self.activeChanged.emit()

    def _subscribe(self, ep, force=False):
        now = time.monotonic()
        if not force and now - ep.sub_t < 1.0:
            return
        opts = ",".join(k for k in ("nogroups", "media") if self.active_cfg[k])
        args = [self.client_id, float(self.active_cfg["freq"])] + ([opts] if opts else [])
        self._send(ep.host, ep.port, A_SUB, args)
        ep.sub_t = now

    def _active_upkeep(self):
        """Dopo ogni hello: riabbonarsi dove gli stati non arrivano più; con il master che tace, provare le
        istanze in ridondanza del suo gruppo; toglierle appena il master risponde."""
        now = time.monotonic()
        for key, ep in list(self.endpoints.items()):
            if now - ep.t > RESUBSCRIBE_AFTER:
                self._subscribe(ep)
            if ep.fallback_for is not None:
                master = self.endpoints.get(ep.fallback_for)
                if master is None or master.fresh(now):
                    self._drop_endpoint(key)
                continue
            if not ep.fresh(now) and ep.t > -1e9:          # il master rispondeva e ora tace
                for m in self._group_of(ep):
                    k = (m, ep.port)
                    if k in self.endpoints:
                        continue
                    fb = self.endpoints[k] = Endpoint(m, ep.port, ep.names, fallback_for=key)
                    self._hello_to(fb)
                    QTimer.singleShot(150, lambda fb=fb: fb.fallback_for and self._subscribe(fb, force=True))
        self.activeChanged.emit()

    def _group_of(self, ep):
        """IP delle altre istanze del gruppo del master ep (dalle risposte all'hello)."""
        out = set()
        for t in self._cs_targets():
            if t["host"].strip() == ep.host and int(t["port"]) == ep.port:
                out |= self.known.get(t["id"]) or set()
        out.discard(ep.host)
        return sorted(out)

    def send_stop(self, key, cue_id, fade=None):
        ep = self.endpoints.get(key)
        if ep is not None:
            self._send(ep.host, ep.port, A_STOP, [str(cue_id)] + ([float(fade)] if fade is not None else []))

    def send_pause(self, key, cue_id):
        ep = self.endpoints.get(key)
        if ep is not None:
            self._send(ep.host, ep.port, A_PAUSE, [str(cue_id)])

    def _active(self, sender, args):
        if not args:
            return
        st = parse_active(args[0])
        if st is None:
            return                         # JSON non valido: si ignora
        cands = [ep for ep in self.endpoints.values() if ep.host == sender]
        if len(cands) > 1 and st["instance"]:
            by_name = [ep for ep in cands if ep.instance == st["instance"]]
            if not by_name:
                by_name = [ep for ep in cands if st["instance"] in {self.names.get(t["id"]) for t in self._cs_targets()
                                                                     if t["host"].strip() == ep.host
                                                                     and int(t["port"]) == ep.port}]
            cands = by_name or cands
        if not cands:
            return                         # da un'istanza che non conosciamo
        ep = max(cands, key=lambda e: e.sub_t) if len(cands) > 1 else cands[0]
        ep.state = st
        ep.t = time.monotonic()
        if st["instance"]:
            ep.instance = st["instance"]
        self.activeChanged.emit()

    def connected(self, pid):
        """La Cuevivo del profilo ha risposto da poco (sa che ci siamo)."""
        return time.monotonic() - self.last_ack.get(pid, -1e9) < 3 * HELLO_EVERY

    # ------------------------------------------------------------ ricezione
    def _recv(self, client_address, address, *args):
        self._msg.emit(address, client_address[0], list(args))      # al thread dell'interfaccia

    def _handle(self, address, sender, args):
        a = _canonical(address)
        if a == A_ACTIVE:
            self._active(sender, args)
        elif a == A_ACK:
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
                ep = self.endpoints.get((t["host"].strip(), int(t["port"])))
                if ep is not None and not ep.instance:
                    ep.instance = name

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

    def moved(self, new_ip, port, pids, name=""):
        """Dopo lo spostamento: lo si conferma al nuovo master e lo si saluta subito (con il nuovo abbonamento
        alle cue attive, che parte dal primo hello: il nuovo endpoint non ha ancora stati)."""
        self._send(new_ip, port, A_MOVED, [self.client_id, ",".join(pids)])
        ep = self.endpoints.get((new_ip, int(port)))
        if ep is not None:
            ep.note = "master"
            if name and not ep.instance:
                ep.instance = name
        for pid in pids:
            g = self.known.get(pid)
            if g is not None:
                g.add(new_ip)
        QTimer.singleShot(50, self.hello)
