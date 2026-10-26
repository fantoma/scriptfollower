"""Ricerca in rete dei dispositivi che ricevono OSC, per configurare i profili in automatico.

L'OSC viaggia quasi sempre su UDP, e una porta UDP aperta di solito non risponde: non c'è un modo sicuro di
«vedere» chi ascolta. Si combinano allora più indizi, ognuno con il suo grado di certezza:

- **CueStage**: risponde alla ricerca delle istanze («CUESTAGE-MASTER?», UDP 53901) se ha le istanze secondarie
  attive; la sua interfaccia web (HTTP, di solito 8080) si presenta come «CueStage». OSC: porta 53000.
- **QLab**: accetta OSC anche su **TCP 53000**; alla richiesta /version (OSC su TCP con SLIP) risponde con la
  versione. Così è riconosciuto con certezza.
- **ChamSys MagicQ** e altri nodi luci: rispondono ad Art-Net ArtPoll (UDP 6454) con il loro nome. La porta
  OSC della MagicQ non si può leggere: si propone 8000 (quella consigliata).
- **Porte UDP su un singolo computer**: se l'host risponde «porta chiusa» (ICMP) su una porta di controllo,
  il silenzio su una porta OSC tipica (53000, 58000, 8000, 9000…) vuol dire che probabilmente è aperta.
  Indizio debole: va confermato.
"""
import ipaddress
import json
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor

OSC_UDP_PORTS = {53000: "cuestage", 58000: "scs", 8000: "chamsys_magicq", 9000: "generico", 7000: "generico"}
ICMP_GAP = 1.05                         # i sistemi limitano le risposte «porta chiusa» a circa una al secondo
CONTROL_PORT = 9                        # «discard»: quasi sempre chiusa, serve da confronto


def local_addresses():
    """Indirizzi IPv4 di questo computer (non loopback)."""
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out and not ip.startswith("127."):
                out.append(ip)
    except OSError:
        pass
    return out


def subnet_hosts(local_ips, limit=1024):
    """Gli altri indirizzi delle reti /24 di questo computer."""
    hosts = []
    for ip in local_ips:
        try:
            net = ipaddress.ip_network(f"{ip}/24", strict=False)
        except ValueError:
            continue
        for h in net.hosts():
            h = str(h)
            if h != ip and h not in hosts:
                hosts.append(h)
    return hosts[:limit]


def broadcast_addresses(local_ips):
    out = {"255.255.255.255"}
    for ip in local_ips:
        try:
            out.add(str(ipaddress.ip_network(f"{ip}/24", strict=False).broadcast_address))
        except ValueError:
            pass
    return sorted(out)


# ---------------------------------------------------------------------------- OSC minimo
def _osc_string(s):
    b = s.encode("utf-8") + b"\x00"
    return b + b"\x00" * (-len(b) % 4)


def osc_message(address, *args):
    tags = "," + "".join("i" if isinstance(a, int) else "f" if isinstance(a, float) else "s" for a in args)
    data = _osc_string(address) + _osc_string(tags)
    for a in args:
        if isinstance(a, int):
            data += struct.pack(">i", a)
        elif isinstance(a, float):
            data += struct.pack(">f", a)
        else:
            data += _osc_string(str(a))
    return data


def osc_parse(data):
    """(indirizzo, [argomenti]) di un messaggio OSC semplice (stringhe, interi, decimali)."""
    def rd_str(i):
        j = data.index(b"\x00", i)
        s = data[i:j].decode("utf-8", "replace")
        return s, j + 1 + (-(j + 1 - i) % 4)
    addr, i = rd_str(0)
    tags, i = rd_str(i) if i < len(data) else (",", i)
    args = []
    for t in tags[1:]:
        if t == "s":
            s, i = rd_str(i)
            args.append(s)
        elif t == "i":
            args.append(struct.unpack(">i", data[i:i + 4])[0])
            i += 4
        elif t == "f":
            args.append(struct.unpack(">f", data[i:i + 4])[0])
            i += 4
    return addr, args


SLIP_END, SLIP_ESC, SLIP_ESC_END, SLIP_ESC_ESC = 0xC0, 0xDB, 0xDC, 0xDD


def slip_encode(data):
    out = bytearray([SLIP_END])
    for b in data:
        if b == SLIP_END:
            out += bytes([SLIP_ESC, SLIP_ESC_END])
        elif b == SLIP_ESC:
            out += bytes([SLIP_ESC, SLIP_ESC_ESC])
        else:
            out.append(b)
    out.append(SLIP_END)
    return bytes(out)


def slip_decode(stream):
    """Pacchetti SLIP contenuti in stream (bytes)."""
    pkts, cur, esc = [], bytearray(), False
    for b in stream:
        if esc:
            cur.append(SLIP_END if b == SLIP_ESC_END else SLIP_ESC if b == SLIP_ESC_ESC else b)
            esc = False
        elif b == SLIP_ESC:
            esc = True
        elif b == SLIP_END:
            if cur:
                pkts.append(bytes(cur))
            cur = bytearray()
        else:
            cur.append(b)
    return pkts


# ---------------------------------------------------------------------------- sonde
def tcp_state(host, port, timeout=0.35):
    """'open', 'closed' (l'host risponde: è acceso) oppure None (nessuna risposta)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return "open"
    except ConnectionRefusedError:
        return "closed"
    except OSError:
        return None
    finally:
        s.close()


def qlab_version(host, port=53000, timeout=1.0):
    """Versione di QLab (OSC su TCP con SLIP) oppure None."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(slip_encode(osc_message("/version")))
            buf = b""
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                for pkt in slip_decode(buf):
                    try:
                        addr, args = osc_parse(pkt)
                    except (ValueError, struct.error, IndexError):
                        continue
                    if addr.startswith("/reply") and args:
                        try:
                            data = json.loads(args[0]).get("data")
                        except (ValueError, AttributeError):
                            data = args[0]
                        return str(data or "?")
    except OSError:
        return None
    return None


def http_server_name(host, port=8080, timeout=0.8):
    """Intestazione «Server» di un server HTTP (CueStage risponde «CueStage»)."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(f"GET /api/state HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
            data = b""
            while b"\r\n\r\n" not in data and len(data) < 8192:
                chunk = s.recv(2048)
                if not chunk:
                    break
                data += chunk
        for line in data.decode("latin-1", "replace").split("\r\n"):
            if line.lower().startswith("server:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def udp_port_state(host, port, timeout=0.25):
    """'closed' se l'host dice che la porta UDP è chiusa (ICMP), 'silent' se non risponde, None se errore."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.send(osc_message("/scriptfollower/ping"))
        try:
            s.recv(2048)
            return "silent"                         # ha risposto: aperta
        except socket.timeout:
            return "silent"
        except (ConnectionRefusedError, ConnectionResetError):
            return "closed"
    except OSError:
        return None
    finally:
        s.close()


def udp_open_ports(host, ports=tuple(OSC_UDP_PORTS), cancel=None, gap=None):
    """Porte UDP probabilmente aperte.

    Il silenzio conta solo se l'host risponde «porta chiusa» sulla porta di controllo, prima e subito dopo:
    i sistemi limitano queste risposte (circa una al secondo per destinazione), quindi le prove sono
    distanziate, altrimenti ogni porta sembrerebbe aperta."""
    gap = ICMP_GAP if gap is None else gap
    if udp_port_state(host, CONTROL_PORT) != "closed":
        return []
    out = []
    for p in ports:
        if cancel and cancel():
            break
        time.sleep(gap)
        if udp_port_state(host, p) != "silent":
            continue
        time.sleep(gap)
        if udp_port_state(host, CONTROL_PORT) == "closed":        # le risposte ICMP arrivano ancora
            out.append(p)
    return out


def cuestage_masters(local_ips, timeout=1.0):
    """Istanze di CueStage con le istanze secondarie attive (rispondono alla ricerca)."""
    found = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("0.0.0.0", 0))
        for b in broadcast_addresses(local_ips) + ["127.0.0.1"]:
            try:
                s.sendto(b"CUESTAGE-MASTER?", (b, 53901))
            except OSError:
                pass
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            s.settimeout(max(0.05, end - time.monotonic()))
            try:
                data, (ip, _p) = s.recvfrom(4096)
            except (socket.timeout, OSError):
                break
            try:
                info = json.loads(data.decode("utf-8"))
            except ValueError:
                continue
            if info.get("app") == "CueStage":
                found.append({"host": ip, "name": info.get("name") or "CueStage"})
    finally:
        s.close()
    return found


def artnet_nodes(local_ips, timeout=1.2):
    """Nodi Art-Net (console luci): [{host, name, long}]."""
    pkt = b"Art-Net\x00" + struct.pack("<H", 0x2000) + bytes([0, 14, 0x06, 0x00])
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", 6454))
    except OSError:
        s.bind(("0.0.0.0", 0))
    found = {}
    try:
        for b in broadcast_addresses(local_ips) + ["2.255.255.255", "10.255.255.255"]:
            try:
                s.sendto(pkt, (b, 6454))
            except OSError:
                pass
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            s.settimeout(max(0.05, end - time.monotonic()))
            try:
                data, (ip, _p) = s.recvfrom(2048)
            except (socket.timeout, OSError):
                break
            if len(data) < 108 or not data.startswith(b"Art-Net\x00") or struct.unpack("<H", data[8:10])[0] != 0x2100:
                continue
            node_ip = ".".join(str(x) for x in data[10:14])
            short = data[26:44].split(b"\x00")[0].decode("latin-1", "replace").strip()
            long = data[44:108].split(b"\x00")[0].decode("latin-1", "replace").strip()
            host = node_ip if node_ip != "0.0.0.0" else ip
            found[host] = {"host": host, "name": short or long or host, "long": long}
    finally:
        s.close()
    return list(found.values())


# ---------------------------------------------------------------------------- ricerca completa
def scan(progress=None, cancel=None, tcp_ports=(53000, 8080), sweep=True):
    """Cerca in rete. Ritorna [{host, port, plugin, name, how, sure}] (prima i risultati certi).

    progress(testo, frazione 0..1) viene chiamata durante la ricerca; cancel() -> True la interrompe."""
    def say(t, f):
        if progress:
            progress(t, f)

    def stop():
        return bool(cancel and cancel())

    ips = local_addresses()
    results = []
    lock = threading.Lock()

    def add(host, port, plugin, name, how, sure):
        with lock:
            for r in results:
                if r["host"] == host and r["port"] == port:
                    if sure and not r["sure"]:
                        r.update(plugin=plugin, name=name, how=how, sure=sure)
                    return
            results.append({"host": host, "port": port, "plugin": plugin, "name": name, "how": how,
                            "sure": sure})

    say("Ricerca di CueStage e delle console luci (Art-Net)…", 0.02)
    for m in cuestage_masters(ips):
        host = "127.0.0.1" if m["host"] in ips else m["host"]
        add(host, 53000, "cuestage", f"CueStage «{m['name']}»", "risponde alla ricerca delle istanze", True)
    if stop():
        return results
    for n in artnet_nodes(ips):
        txt = f"{n['name']} {n['long']}".lower()
        if "magicq" in txt or "chamsys" in txt:
            add(n["host"], 8000, "chamsys_magicq", f"ChamSys {n['name']}",
                "Art-Net; porta OSC da controllare in Setup › Network", True)
        else:
            add(n["host"], 8000, "generico", f"Nodo Art-Net «{n['name']}»",
                "Art-Net; supporto OSC e porta da verificare", False)
    if stop():
        return results

    hosts = ["127.0.0.1"] + (subnet_hosts(ips) if sweep else [])
    alive = {"127.0.0.1"}
    open_tcp = []
    jobs = [(h, p) for h in hosts for p in tcp_ports]
    done = 0
    say(f"Controllo {len(hosts)} indirizzi della rete…", 0.1)
    with ThreadPoolExecutor(max_workers=96) as ex:
        futs = {ex.submit(tcp_state, h, p): (h, p) for h, p in jobs}
        for f in futs:
            if stop():
                ex.shutdown(wait=False, cancel_futures=True)
                return results
            h, p = futs[f]
            st = f.result()
            done += 1
            if done % 64 == 0:
                say(f"Controllo la rete… {done}/{len(jobs)}", 0.1 + 0.6 * done / max(1, len(jobs)))
            if st:
                alive.add(h)
            if st == "open":
                open_tcp.append((h, p))

    say("Riconosco i programmi trovati…", 0.72)
    for h, p in open_tcp:
        if stop():
            return results
        if p == 53000:
            v = qlab_version(h)
            if v:
                add(h, 53000, "qlab", f"QLab {v}", "risponde a /version su TCP 53000", True)
            else:
                add(h, 53000, "qlab", "QLab (probabile)", "porta TCP 53000 aperta", False)
        elif p == 8080:
            srv = http_server_name(h) or ""
            if "cuestage" in srv.lower():
                add(h, 53000, "cuestage", "CueStage", "interfaccia web di CueStage sulla porta 8080", True)

    say("Controllo le porte OSC (UDP) dei computer accesi…", 0.8)
    alive = sorted(alive, key=lambda x: (x != "127.0.0.1", tuple(int(n) for n in x.split("."))))
    with ThreadPoolExecutor(max_workers=32) as ex:
        for h, ports in zip(alive, ex.map(lambda x: udp_open_ports(x, cancel=cancel), alive)):
            if stop():
                return results
            for p in ports:
                guess = OSC_UDP_PORTS.get(p, "generico")
                where = "questo computer" if h == "127.0.0.1" else h
                name = {53000: "CueStage o QLab", 58000: "SCS (porta di esempio)", 8000: "OSC (porta 8000)"}.get(
                    p, f"OSC (porta {p})")
                add(h, p, guess, name, f"porta UDP {p} probabilmente aperta su {where}", False)
    say("Ricerca finita", 1.0)
    results.sort(key=lambda r: (not r["sure"], r["host"] != "127.0.0.1", r["host"], r["port"]))
    return results
