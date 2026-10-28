"""OSC in uscita: i due profili di destinazione e i «plugin» con i comandi già pronti.

Plugin (stesso formato dei profili OSC di CueStage): file JSON in scriptfollower/osc_profiles/ o nella cartella
dei dati (osc_profiles/), con
  id, name, port (porta predefinita), notes,
  commands: [{id, label, address, args, params: [{id, label, type int|float|enum|text, …}], help}]
Indirizzo e argomenti usano i parametri tra graffe: "/cue/{cue}/start".

Profilo di destinazione (quanti se ne vuole, almeno uno): {id, name, host, port, note, plugin}.
Comando di un pulsante: {target_id: id del profilo, target: indice (vecchi progetti),
                         cmd: id del comando del plugin oppure "" (personalizzato), values: {parametro: valore},
                         address, args}.
"""
import json
import os
import re
import threading

from .system import data_dir
from .i18n import _

try:
    from pythonosc.udp_client import SimpleUDPClient
except Exception:  # pragma: no cover
    SimpleUDPClient = None

BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "osc_profiles")
USER_DIR = data_dir("osc_profiles")
N_TARGETS = 2                    # profili in un progetto nuovo (se ne possono aggiungere altri)
MAX_TARGETS = 16
CUSTOM = ""                      # comando «indirizzo personalizzato»

_cache = None


def plugins(reload=False):
    """{id: plugin}"""
    global _cache
    if _cache is not None and not reload:
        return _cache
    out = {}
    for d in (BUILTIN_DIR, USER_DIR):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(d, fn), encoding="utf-8") as f:
                        p = json.load(f)
                    if p.get("id") and p.get("commands"):
                        out[p["id"]] = p
                except (OSError, ValueError) as e:
                    print(f"Plugin OSC {fn} non valido: {e}")
    _cache = out
    return out


def plugin_order():
    """Ordine nel menu: Generico, poi i software/console in ordine alfabetico."""
    ps = plugins()
    return sorted(ps, key=lambda k: (k != "generico", ps[k]["name"].lower()))


def default_targets():
    return [{"id": "p1", "name": "CueStage", "host": "127.0.0.1", "port": 53000, "note": "", "plugin": "cuestage"},
            {"id": "p2", "name": "Luci", "host": "192.168.1.50", "port": 8000, "note": "", "plugin": "chamsys_magicq"}]


def new_target(targets, plugin="generico", host="", port=None, name=None):
    """Un profilo nuovo con un id che non c'è ancora tra targets."""
    ids = {t.get("id") for t in targets or []}
    k = len(targets or []) + 1
    while f"p{k}" in ids:
        k += 1
    p = plugins().get(plugin) or {}
    return {"id": f"p{k}", "name": name or (p.get("name", "").split(" (")[0] if plugin != "generico"
                                            else _("Profilo {0}").format(k)),
            "host": host, "port": int(port or p.get("port") or 8000), "note": "", "plugin": plugin}


def clean_targets(targets):
    """Profili completi (almeno uno). I vecchi progetti senza id ricevono «p1», «p2»… per posizione."""
    if not targets:
        return default_targets()
    out, ids = [], set()
    for i, src in enumerate(targets[:MAX_TARGETS]):
        if not isinstance(src, dict):
            continue
        t = {"id": f"p{i + 1}", "name": f"Profilo {i + 1}", "host": "", "port": 8000, "note": "",
             "plugin": "generico"}
        t.update({k: v for k, v in src.items() if k in t})
        try:
            t["port"] = max(1, min(65535, int(t["port"])))
        except (TypeError, ValueError):
            t["port"] = 8000
        t["id"] = str(t["id"] or f"p{i + 1}")
        while t["id"] in ids:
            t["id"] += "b"
        ids.add(t["id"])
        t["name"] = str(t["name"] or f"Profilo {i + 1}")
        t["host"] = str(t["host"] or "").strip()
        t["note"] = str(t["note"] or "")
        if t["plugin"] not in plugins():
            t["plugin"] = "generico"
        out.append(t)
    return out or default_targets()


def find_target(command, targets):
    """Profilo a cui va il comando: per id (o per posizione nei vecchi progetti). KeyError se non c'è più."""
    tid = command.get("target_id")
    if tid:
        for t in targets:
            if t["id"] == tid:
                return t
        raise KeyError(_('il profilo OSC di questo comando è stato eliminato'))
    i = int(command.get("target") or 0)
    if 0 <= i < len(targets):
        return targets[i]
    raise KeyError(_('il profilo OSC n. {0} non esiste più').format(i + 1))


def migrate_commands(points, targets):
    """Dà a ogni comando l'id del suo profilo (i vecchi progetti avevano solo la posizione)."""
    targets = clean_targets(targets)
    for p in points or []:
        for c in p.get("commands") or []:
            if not c.get("target_id"):
                i = int(c.get("target") or 0)
                if 0 <= i < len(targets):
                    c["target_id"] = targets[i]["id"]


def commands_using(points, target_id):
    return sum(1 for p in points or [] for c in p.get("commands") or [] if c.get("target_id") == target_id)


# ---------------------------------------------------------------------------- argomenti
_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'|(\S+)')


def parse_osc_args(text):
    """'1 2.5 ciao "due parole" "12" true' -> [1, 2.5, 'ciao', 'due parole', '12', True]

    Il testo tra virgolette resta sempre una stringa (serve per le etichette di cue di SCS: "Q10", "12")."""
    out = []
    for m in _TOKEN.finditer(text or ""):
        if m.group(1) is not None:
            out.append(m.group(1).replace('\\"', '"'))
            continue
        if m.group(2) is not None:
            out.append(m.group(2))
            continue
        t = m.group(3)
        low = t.lower()
        if low in ("true", "false"):
            out.append(low == "true")
            continue
        try:
            out.append(int(t))
            continue
        except ValueError:
            pass
        try:
            out.append(float(t))
            continue
        except ValueError:
            pass
        out.append(t)
    return out


def _fmt(v, param):
    t = param.get("type")
    try:
        if t == "int":
            return str(int(float(v)))
        if t == "float":
            return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(param.get("default", 0))
    return str(v)


def find_command(plugin_id, cmd_id):
    p = plugins().get(plugin_id)
    if not p:
        return None
    for c in p["commands"]:
        if c["id"] == cmd_id:
            return c
    return None


def param_values(cmd, values):
    vals = {}
    for prm in cmd.get("params") or []:
        v = (values or {}).get(prm["id"], prm.get("default", ""))
        vals[prm["id"]] = _fmt(v, prm)
    return vals


def render(command, targets):
    """(host, port, indirizzo, testo degli argomenti) di un comando di un pulsante."""
    targets = clean_targets(targets)
    t = find_target(command, targets)
    cid = command.get("cmd") or CUSTOM
    if cid == CUSTOM:
        address, args = command.get("address") or "/", command.get("args") or ""
    else:
        c = find_command(t["plugin"], cid)
        if c is None:
            raise KeyError(_('comando «{0}» non presente nel plugin «{1}» del profilo «{2}»').format(cid, t['plugin'], t['name']))
        vals = param_values(c, command.get("values"))
        address = c["address"].format(**vals)
        args = " ".join(a.format(**vals) for a in c.get("args") or []).strip()
    address = address.strip() or "/"
    if not address.startswith("/"):
        address = "/" + address
    return t["host"], t["port"], address, args


def describe(command, targets):
    """Testo breve: «CueStage › Avvia una cue · Cue 12»."""
    targets = clean_targets(targets)
    try:
        t = find_target(command, targets)
    except KeyError as e:
        return f"⚠ {e}"
    cid = command.get("cmd") or CUSTOM
    if cid == CUSTOM:
        return f"{t['name']} › {command.get('address') or '/'} {command.get('args') or ''}".strip()
    c = find_command(t["plugin"], cid)
    if c is None:
        return f"{t['name']} › " + _("comando «{0}» non disponibile").format(cid)
    vals = param_values(c, command.get("values"))
    parts = []
    for prm in c.get("params") or []:
        v = vals[prm["id"]]
        if prm.get("type") == "enum":
            v = _(dict((str(a), b) for a, b in prm.get("items") or []).get(str(v), v))
        parts.append(f"{_(prm['label'])} {v}")
    return f"{t['name']} › {_(c['label'])}" + (" · " + ", ".join(parts) if parts else "")


# ---------------------------------------------------------------------------- invio
_clients = {}
_lock = threading.Lock()


def osc_send(host, port, address, args_text):
    if SimpleUDPClient is None:
        raise RuntimeError(_('python-osc non installato'))
    if not host:
        raise ValueError(_('indirizzo IP del profilo non impostato'))
    key = (host, int(port))
    with _lock:
        cl = _clients.get(key)
        if cl is None:
            cl = _clients[key] = SimpleUDPClient(host, int(port), allow_broadcast=True)
    args = parse_osc_args(args_text)
    cl.send_message(address, args if args else [])


def send_commands(commands, targets):
    """Invia tutti i comandi di un pulsante, nell'ordine. Ritorna ([righe inviate], [errori])."""
    sent, errors = [], []
    for c in commands or []:
        try:
            host, port, address, args = render(c, targets)
            osc_send(host, port, address, args)
            sent.append(f"{host}:{port} {address} {args}".strip())
        except Exception as e:  # noqa: BLE001
            errors.append(f"{describe(c, targets)}: {e}")
    return sent, errors
