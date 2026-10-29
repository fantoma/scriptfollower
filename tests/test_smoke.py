"""Test di regressione rapidi di Script Follower (senza schede audio, senza modello vocale, senza rete esterna).

    python3 tests/test_smoke.py          (dalla cartella del progetto)

Ogni prova stampa ✔ o ✘; il codice di uscita è il numero di prove fallite.
"""
import os
import random
import socket
import sys
import tempfile
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])
QSettings("ScriptFollower", "ScriptFollower").clear()
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)

import scriptfollower.audio_input as ai  # noqa: E402
ai.sd = None                                   # nessuna scheda richiesta
from scriptfollower.align import Aligner, normalize  # noqa: E402
from scriptfollower.osc import parse_osc_args, plugins, render  # noqa: E402
from scriptfollower.project import Cueing, Project, new_point  # noqa: E402
from scriptfollower.script_doc import ScriptDoc, pymupdf  # noqa: E402

TMP = tempfile.mkdtemp(prefix="scriptfollower-test-")
FAILED = []
TEXT = ("Sì mamma, sì, dai, abbastanza bene, ma sì che vengo a trovarti, solo non devi mettermi fretta! "
        "Lo so che sono tre mesi che sto nel vecchio appartamento dei nonni. Cosa faccio tutto il giorno? "
        "Senti, è meglio che la finiamo qua, ok? Ci sentiamo domani, ciao ciao. Uff, che rottura, ma perché "
        "non mi lascia in pace? Che figata questa nuova versione! Mi avevano detto che era incredibile ma qui "
        "sembra proprio di essere dentro. Fatti sotto brutto bastardo! Tieni, beccati questo, questo e questo! "
        "No! Che cosa è successo! Chi ha tolto la corrente? Proprio adesso che avevo superato il terzo livello. "
        "Dove ho messo il cellulare? Ah, eccolo! È tutto buio anche fuori, dev'esserci stato un black out. ")


def run(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.003)


def test(fn):
    try:
        fn()
        print(f"  ✔ {fn.__name__}")
    except Exception:  # noqa: BLE001
        FAILED.append(fn.__name__)
        print(f"  ✘ {fn.__name__}")
        traceback.print_exc()
    return fn


def make_pdf():
    path = os.path.join(TMP, "copione.pdf")
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    page.insert_text((72, y), "1. PROLOGO", fontname="hebo", fontsize=14)
    y += 30
    page.insert_text((72, y), "(entra dal fondo, LUCI 2)", fontname="heit", fontsize=11)
    y += 22
    words = TEXT.split()
    line = []
    for w in words:
        line.append(w)
        if len(" ".join(line)) > 70:
            page.insert_text((72, y), " ".join(line), fontname="helv", fontsize=11)
            y += 16
            line = []
    if line:
        page.insert_text((72, y), " ".join(line), fontname="helv", fontsize=11)
        y += 16
    page.insert_text((72, y + 10), "MUSICA: 001_Bloom", fontname="helv", fontsize=11)
    page.insert_text((72, y + 30), "Fine della prova (esce).", fontname="helv", fontsize=11)
    doc.save(path)
    return path


PDF = make_pdf()


@test
def normalizzazione():
    assert normalize("Cos’è,") == ["cos", "è"]
    assert normalize("«Sì!»") == ["sì"]
    assert normalize("dell'ospedale...") == ["dell", "ospedale"]
    assert normalize("— ok?") == ["ok"]


@test
def copione_parole_escluse():
    d = ScriptDoc(PDF)
    spoken = " ".join(w.text for w in d.words if w.spoken)
    excluded = " ".join(w.text for w in d.words if not w.spoken)
    assert "PROLOGO" in excluded, excluded           # grassetto
    assert "fondo," in excluded                      # corsivo + parentesi
    assert "001_Bloom" in excluded and "MUSICA:" in excluded   # riga «ETICHETTA:»
    assert "(esce)." in excluded and "Fine" in spoken          # parentesi in una riga normale
    assert d.tokens[:3] == ["sì", "mamma", "sì"], d.tokens[:5]
    # inclusione a mano
    i = next(w.i for w in d.words if w.text == "PROLOGO")
    d.toggle_spoken([i], True)
    assert d.words[i].spoken and "prologo" in d.tokens
    d.toggle_spoken([i], False)
    assert "prologo" not in d.tokens and not d.overrides
    # frasi prima di una parola
    k = next(w.i for w in d.words if w.text == "eccolo!")
    ctx = " ".join(d.words[j].text for j in d.sentences_before(k))
    # due frasi, ma almeno 18 parole: si aggiungono frasi intere precedenti
    assert ctx.startswith("Chi ha tolto la corrente?"), ctx
    assert " ".join(d.words[j].text for j in d.sentences_before(k, min_words=0)).startswith("Dove ho messo")
    # i puntini di sospensione non chiudono la frase
    from scriptfollower.script_doc import ends_sentence
    assert ends_sentence("me?") and ends_sentence("ciao.»") and not ends_sentence("…") and not ends_sentence("no...")
    assert d.word_at(0, d.words[k].x0 + 1, d.words[k].y0 + 2) == k
    # anteprima del riquadro: frasi, righe, parole
    assert d.context_before(k, "sentences", 2) == d.sentences_before(k, n=2, max_words=120)
    ws = d.context_before(k, "words", 5)
    assert len(ws) == 5 and ws[-1] == k and all(d.words[i].spoken for i in ws)
    ls = d.context_before(k, "lines", 3)
    assert ls[-1] == k and len({d.words[i].line for i in ls}) == 3
    assert d.context_before(k, "lines", 1) == [i for i in d.lines[d.words[k].line].words if i <= k]


def long_script(n=12):
    """Copione lungo senza ripetizioni esatte: il testo di prova e tante sue permutazioni."""
    base = normalize(TEXT)
    rnd = random.Random(3)
    out = list(base)
    for _ in range(n):
        b = list(base)
        rnd.shuffle(b)
        out += b
    return out


@test
def allineamento_con_errori():
    toks = long_script(6)
    al = Aligner(toks)
    rnd = random.Random(7)
    heard, lag = [], []
    for i, t in enumerate(toks):
        r = rnd.random()
        if r < 0.15:
            continue
        heard.append(rnd.choice(toks) if r < 0.3 else t)
        al.update(heard[-12:])
        if i > 20:
            lag.append(i - al.pos)
    assert float(np.median(lag)) <= 1, np.median(lag)
    assert max(abs(x) for x in lag[-50:]) <= 6


@test
def allineamento_salto():
    toks = long_script(12)
    al = Aligner(toks)
    for i in range(0, 40):
        al.update(toks[max(0, i - 9):i + 1])
    # l'attore salta avanti di molto (oltre la finestra di ricerca)
    al.ahead = 30
    start = len(toks) - 300
    for i in range(start, start + 14):
        al.update(toks[start:i + 1][-10:])
    assert abs(al.pos - (start + 13)) <= 2, (al.pos, start + 13)
    # e indietro (si ripete una scena)
    back = 400
    for i in range(back, back + 14):
        al.update(toks[back:i + 1][-10:])
    assert abs(al.pos - (back + 13)) <= 2, (al.pos, back + 13)
    # nessuno spostamento con parole a caso che non c'entrano
    p = al.pos
    for _ in range(10):
        al.update(["zebra", "ornitorinco", "astronave", "bicicletta"])
    assert al.pos == p


@test
def parti_improvvisate():
    # documento: la parte segnata esce dal riconoscimento e crea un punto d'attesa
    d = ScriptDoc(PDF)
    a = next(w.i for w in d.words if w.text == "Uff,")
    b = next(w.i for w in d.words if w.text == "versione!")
    n = len(d.tokens)
    d.set_improv([(a, b), (a + 2, a + 4)])                   # sovrapposte: una sola
    assert d.improv == [(a, b)] and not d.words[a + 3].spoken and len(d.tokens) < n
    hold = d.last_token_at_or_before(a - 1)
    assert d.hold_tokens() == {hold} and d.improv_at(a + 1) == (a, b)
    # allineamento: durante l'improvvisazione (parole del copione a caso) la posizione resta ferma,
    # poi si riaggancia al testo che segue
    toks = long_script(8)
    cut = 300
    al = Aligner(toks, holds={cut})
    for i in range(cut - 20, cut + 1):
        al.update(toks[max(0, i - 9):i + 1])
    assert al.pos == cut, al.pos
    rnd = random.Random(5)
    heard = list(toks[cut - 9:cut + 1])
    for _ in range(80):
        heard.append(rnd.choice(toks))
        al.update(heard[-10:])
        assert al.pos == cut, ("si è mosso durante l'improvvisazione", al.pos)
    for i in range(cut + 1, cut + 15):
        heard.append(toks[i])
        al.update(heard[-10:])
    assert abs(al.pos - (cut + 14)) <= 1, al.pos
    assert al.pos not in al.holds
    # se il riconoscimento è un po' indietro quando inizia l'improvvisazione, non salta oltre per sbaglio
    al = Aligner(toks, holds={cut})
    for i in range(cut - 20, cut - 2):
        al.update(toks[max(0, i - 9):i + 1])
    heard = list(toks[cut - 12:cut - 2])
    for _ in range(80):
        heard.append(rnd.choice(toks))
        al.update(heard[-10:])
        assert al.pos <= cut, al.pos


@test
def ricerca_solo_nella_parte_rimanente():
    toks = long_script(8)
    al = Aligner(toks)
    for i in range(0, 500):
        al.update(toks[max(0, i - 9):i + 1])
    al.set_floor(480)
    # l'attore «ripete» una frase già detta prima del limite: non si torna indietro
    for i in range(100, 115):
        al.update(toks[100:i + 1][-10:])
    assert al.pos >= 480, al.pos
    # un riposizionamento a mano prima del limite lo abbassa
    al.reset(50)
    assert al.floor == 50
    d = ScriptDoc(PDF)
    n_all = len(d.vocabulary())
    assert len(d.vocabulary(len(d.tokens) - 10)) < n_all


@test
def preascolto_ingresso():
    m = ai.Monitor()
    m.stream = object()                     # finta uscita aperta
    m.gain = 0.5
    m.cols = [0, 1]
    x = np.full(1000, 0.8, np.float32)
    m.push(x)
    out = np.zeros((512, 2), np.float32)
    m._cb(out, 512, None, None)
    assert np.allclose(out[:, 0], 0.4) and np.allclose(out[:, 1], 0.4)
    m.cols = [1]
    out = np.zeros((300, 2), np.float32)
    m._cb(out, 300, None, None)
    assert not out[:, 0].any() and np.allclose(out[:, 1], 0.4)
    # l'ingresso passa al preascolto prima o dopo il gate
    a = ai.AudioInput()
    a.cfg = ai.audio_settings({"threshold_db": -30, "hold_ms": 0, "mon_post": True})
    a._rs = ai.Resampler(48000)
    a.monitor.stream = object()
    a.process(np.full(480, 0.001, np.float32), 48000)       # sotto soglia: silenzio
    assert not a.monitor.buf.any()
    a.cfg["mon_post"] = False
    a.process(np.full(480, 0.001, np.float32), 48000)
    assert a.monitor.buf[-480:].max() > 0
    assert ai.parse_channels("3-4") == [3, 4] and ai.parse_channels("2") == [2] and ai.parse_channels("x") == [1, 2]
    up = ai.Resampler(44100, 48000)
    n = sum(len(up(np.zeros(441, np.float32))) for _ in range(100))
    assert abs(n - 48000) <= 2, n


@test
def vocabolario_e_pronunce():
    from scriptfollower.vocab import number_words, suggest
    assert [number_words(n) for n in (1, 8, 15, 21, 23, 28, 100, 108, 118, 180, 1000, 2024, 1003)] == [
        "uno", "otto", "quindici", "ventuno", "ventitré", "ventotto", "cento", "centotto", "centodiciotto",
        "centottanta", "mille", "duemilaventiquattro", "milletré"]
    vocab = {"aspira", "polvere", "perché", "gesù", "aldi", "centodiciotto", "mamma", "ciao"}
    known = vocab.__contains__
    assert suggest("aspirapolvere", known) == "aspira polvere"
    assert suggest("perche", known) == "perché"
    assert suggest("118", known) == "centodiciotto"
    assert suggest("zxqw", known) is None
    # nel copione: i numeri si leggono in lettere, le pronunce sostituiscono la parola
    d = ScriptDoc(PDF)
    k = next(w.i for w in d.words if w.text == "mamma,")
    d.set_aliases({"mamma": "ma ma"})
    assert d.words[k].tokens == ["ma", "ma"] and d.token_word[d.words[k].t0 + 1] == k
    assert d.word_counts()["mamma"] >= 1
    # con un modello finto: le parole sconosciute vengono segnalate e saltate dall'allineamento
    from scriptfollower.ui_vocab import VocabDialog

    class FakeModel:
        def vosk_model_find_word(self, w):
            return -1 if w in ("mamma", "figata", "dev") else 1

    from scriptfollower.main_window import MainWindow
    w = MainWindow()
    w.open_pdf(PDF)
    w.rec.model = FakeModel()
    dlg = VocabDialog(w)
    rows = {wd: (say, st) for wd, say, st in ((dlg.table.item(r, 0).text(), dlg.table.item(r, 2).text(),
                                               dlg.table.item(r, 3).text()) for r in range(dlg.table.rowCount()))}
    assert set(rows) == {"mamma", "figata", "dev"}, rows
    assert rows["mamma"][1].startswith("✘")
    r = next(r for r in range(dlg.table.rowCount()) if dlg.table.item(r, 0).text() == "figata")
    dlg.table.item(r, 2).setText("fi gata")
    assert dlg.table.item(r, 3).text().startswith("✔")
    assert dlg.aliases() == {"figata": "fi gata"}
    dlg.reject()
    w._model_unknown({"mamma"})
    assert any(w.aligner.gcum[i + 1] == w.aligner.gcum[i] for i in range(len(w.doc.tokens))
               if w.doc.tokens[i] == "mamma")              # «mamma» si salta senza penalità
    w.project.dirty = False
    w.close()


@test
def ricerca_in_rete():
    """QLab finto (OSC su TCP con SLIP) e una porta UDP aperta su questo computer."""
    import json
    import scriptfollower.netscan as ns
    assert ns.osc_parse(ns.osc_message("/a/b", 1, 2.5, "x")) == ("/a/b", [1, 2.5, "x"])
    assert ns.slip_decode(ns.slip_encode(b"\xc0\xdbabc")) == [b"\xc0\xdbabc"]
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    qport = srv.getsockname()[1]

    def qlab():
        c, _ = srv.accept()
        for pkt in ns.slip_decode(c.recv(4096)):
            if ns.osc_parse(pkt)[0] == "/version":
                c.sendall(ns.slip_encode(ns.osc_message("/reply/version", json.dumps({"data": "5.4"}))))
        time.sleep(0.2)
        c.close()
    threading.Thread(target=qlab, daemon=True).start()
    assert ns.qlab_version("127.0.0.1", qport) == "5.4"
    assert ns.tcp_state("127.0.0.1", qport) == "open"
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.bind(("127.0.0.1", 0))
    uport = u.getsockname()[1]
    try:
        found = ns.udp_open_ports("127.0.0.1", ports=(uport, 9), gap=0.3)
        assert uport in found and 9 not in found, found
    finally:
        u.close()
        srv.close()


@test
def profili_osc_e_comandi():
    from scriptfollower.osc import clean_targets, find_target, migrate_commands, new_target
    old = [{"name": "A", "host": "1.1.1.1", "port": 1, "plugin": "qlab"}, {"name": "B", "host": "2.2.2.2"}]
    ts = clean_targets(old)
    assert [t["id"] for t in ts] == ["p1", "p2"]
    pts = [{"commands": [{"target": 1, "cmd": ""}]}]
    migrate_commands(pts, ts)
    assert pts[0]["commands"][0]["target_id"] == "p2"
    ts.append(new_target(ts, "scs", "3.3.3.3"))
    assert ts[2]["id"] == "p3" and ts[2]["port"] == 58000
    del ts[0]                                         # eliminato il primo: il comando resta sul suo profilo
    assert find_target(pts[0]["commands"][0], ts)["name"] == "B"
    del ts[0]
    try:
        find_target(pts[0]["commands"][0], ts)
        raise AssertionError("profilo eliminato non segnalato")
    except KeyError:
        pass
    assert render({"target_id": "p3", "cmd": "go"}, ts)[:3] == ("3.3.3.3", 58000, "/ctrl/go")


@test
def navigazione_riattivazione_zone():
    from scriptfollower.main_window import MainWindow
    from scriptfollower.ui_settings import SettingsDialog
    w = MainWindow()
    w.resize(1300, 800)
    w.show()
    w.open_pdf(PDF)
    run(0.2)
    d = w.doc
    ks = [next(x.i for x in d.words if x.text == t) for t in ("questo!", "corrente?", "eccolo!")]
    for k, lab in zip(ks, "ABC"):
        p = new_point(k, "button")
        p["label"] = lab
        w.project.add_point(p)
    w.refresh_points()
    A, B, C = w.project.sorted_points()
    # comando precedente / successivo: si vedono solo se c'è dove andare
    w._update_nav()
    assert not w.nav_prev_btn.isVisibleTo(w) and w.nav_next_btn.isVisibleTo(w) and "A" in w.nav_next_btn.text()
    w.nav_command(1)
    assert w.nav_word == A["word"] and "B" in w.nav_next_btn.text()
    w.nav_command(1)
    assert w.nav_word == B["word"] and "A" in w.nav_prev_btn.text()
    w.nav_command(-1)
    assert w.nav_word == A["word"] and w.cueing.pos == -1          # la posizione non cambia
    w.go_current()
    assert w.nav_word is None
    # riattivazione di un pulsante passato e disattivato
    w.set_mode(False)
    w.jump_to(ks[1] + 1)
    w.press_point(C["id"])                                         # disattiva A e B
    assert w.point_state(B)[1] is False
    w.reactivate(B["id"])
    assert w.point_state(B) == ("armed", True)
    w.press_point(B["id"])
    assert B["id"] in w.cueing.sent and w.point_state(B)[0] in ("sent", "passed")
    # zona azzurra allargata e ristretta trascinando i bordi
    w.set_mode(True)
    runs = d.excluded_runs()
    a, b = next((x, y) for x, y in runs if any(d.words[i].text.startswith("(entra") for i in range(x, y + 1)))
    w._zone_resized("excluded", a, b, a, b + 2)
    assert not d.words[b + 2].spoken and (a, b + 2) in d.excluded_runs()
    w._zone_resized("excluded", a, b + 2, a + 1, b + 2)
    assert d.words[a].spoken and not d.words[a + 1].spoken
    # profili OSC: aggiungi, configura da un risultato della ricerca, elimina
    dlg = SettingsDialog(w, 1)
    n = len(dlg.t_widgets)
    row = dlg.use_found({"host": "10.0.0.9", "port": 53000, "plugin": "qlab", "name": "QLab 5", "how": "test"})
    assert len(dlg.t_widgets) == n + 1 and row["plugin"].currentData() == "qlab"
    dlg._remove_target(dlg.t_widgets[0])
    ts = dlg.targets()
    assert len(ts) == n and ts[-1]["host"] == "10.0.0.9" and ts[0]["id"] == "p2"
    dlg.reject()
    w.project.dirty = False
    w.close()


@test
def immagini_dei_pulsanti():
    from PySide6.QtGui import QColor, QImage
    from scriptfollower.main_window import MainWindow
    from scriptfollower.ui_images import ImageViewer, abs_path, media_dir
    from scriptfollower.ui_point import PointDialog
    src = []
    for i, col in enumerate(("red", "blue")):
        im = QImage(320, 200, QImage.Format_RGB32)
        im.fill(QColor(col))
        path = os.path.join(TMP, f"foto{i}.png")
        im.save(path)
        src.append(path)
    w = MainWindow()
    w.open_pdf(PDF)
    k = next(x.i for x in w.doc.words if x.text == "questo!")
    p = new_point(k, "button")
    p["label"] = "Luci"
    dlg = PointDialog(p, w.project.targets, "", w, project=w.project)
    dlg._import(src + [src[0]])                      # la stessa due volte: una sola
    assert dlg.p["images"] == ["copione_immagini/foto0.png", "copione_immagini/foto1.png"], dlg.p["images"]
    assert os.path.isfile(os.path.join(media_dir(w.project), "foto1.png"))
    dlg._move_image(-1) if dlg.imgs.currentRow() == 1 else None
    assert dlg.p["images"][0].endswith("foto1.png")
    w.project.add_point(dlg.result_point())
    dlg.reject()
    w.set_mode(False)
    w.refresh_points()
    run(0.1)
    assert len(w.panel.btn_imgs.paths) == 2 and w.panel.btn_imgs.isVisibleTo(w.panel)
    v = ImageViewer(w.panel.btn_imgs.paths, 1, "Luci", w)
    v.show()
    run(0.1)
    assert not v._pm.isNull() and v._pm.width() == 320
    v.step(1)
    assert v.i == 0
    v.close()
    # salvato e riaperto: i percorsi restano relativi al PDF
    w.project.path = os.path.join(TMP, "immagini.sfollow")
    w.save()
    q = Project.load(w.project.path)
    pt = next(x for x in q.points if x["label"] == "Luci")
    assert os.path.isfile(abs_path(q, pt["images"][0]))
    w.project.dirty = False
    w.close()


@test
def osc_argomenti_e_plugin():
    assert parse_osc_args('1 2.5 ciao "due parole" "12" true') == [1, 2.5, "ciao", "due parole", "12", True]
    ps = plugins(reload=True)
    for pid in ("cuestage", "qlab", "scs", "chamsys_magicq", "generico"):
        assert pid in ps, pid
    targets = [{"name": "A", "host": "127.0.0.1", "port": 53000, "plugin": "qlab"},
               {"name": "B", "host": "10.0.0.2", "port": 58000, "plugin": "scs"}]
    assert render({"target": 0, "cmd": "cue_start", "values": {"cue": "12"}}, targets) == \
        ("127.0.0.1", 53000, "/cue/12/start", "")
    h, p, a, args = render({"target": 1, "cmd": "cue_go", "values": {"cue": "Q10"}}, targets)
    assert (a, parse_osc_args(args)) == ("/cue/go", ["Q10"])
    h, p, a, args = render({"target": 1, "cmd": "master", "values": {"db": -12}}, targets)
    assert parse_osc_args(args) == [-12.0] and isinstance(parse_osc_args(args)[0], float)
    assert render({"target": 1, "cmd": "", "address": "fx/1", "args": "1 0.5"}, targets)[2:] == ("/fx/1", "1 0.5")


@test
def regole_dei_pulsanti():
    pr = Project()
    b1, b2, b3 = new_point(10, "button"), new_point(20, "button"), new_point(30, "button")
    n1 = new_point(15, "note")
    for x in (b1, b2, b3, n1):
        pr.add_point(x)
    cu = Cueing(pr)
    assert cu.next_button() is b1 and cu.next_note() is n1
    cu.set_pos(12)                                   # b1 passato senza pressione: resta attivo
    assert cu.passed(b1) and cu.enabled(b1) and cu.next_button() is b2
    assert cu.last_passed_button() is b1
    cu.press(b2)                                     # premendo il corrente il precedente si disattiva
    assert not cu.enabled(b1) and cu.next_button() is b3
    cu.set_pos(22)                                   # b2 passato (già inviato): resta l'ultimo attivo
    assert cu.enabled(b2) and not cu.enabled(b1) and not cu.passed(n1) is False
    cu.press(b3)
    assert not cu.enabled(b2)
    cu.set_pos(5)                                    # tornando indietro tutto si riattiva
    assert cu.enabled(b1) and cu.enabled(b2) and cu.enabled(b3) and cu.next_button() is b1


@test
def tempi_medi_tra_eventi():
    from scriptfollower.timing import START, Timing, fmt
    pr = Project()
    a, n, b = new_point(10, "button"), new_point(20, "note"), new_point(30, "button")
    for x in (a, n, b):
        pr.add_point(x)
    now = [0.0]
    t = Timing(pr, clock=lambda: now[0])
    t.recording = True
    for run, (da, dn, db) in enumerate(((10, 5, 20), (14, 7, 24), (12, 6, 22))):
        t.restart()
        now[0] = 1000.0 * (run + 1)
        t.begin()
        for pid, dt in ((a["id"], da), (n["id"], dn), (b["id"], db)):
            now[0] += dt
            t.event(pid)
    assert pr.timing["runs"] == 3
    assert t.estimate(a["id"]) == 12 and t.estimate(n["id"]) == 6 and t.estimate(b["id"]) == 22
    # nuova replica: dopo l'evento «a», il prossimo pulsante «b» passa per la nota «n» (6 + 22 s)
    t.restart()
    now[0] = 9000.0
    t.begin()
    now[0] += 11
    t.event(a["id"])
    now[0] += 4
    est, left = t.eta(b["id"])
    assert est == 28 and left == 24, (est, left)
    # un salto a mano interrompe la catena: niente misura
    t.jumped()
    now[0] += 100
    assert t.event(n["id"]) is None and len(pr.timing["intervals"][f"{a['id']}>{n['id']}"]) == 3
    # registrazione spenta: nessuna misura
    t.recording = False
    now[0] += 5
    assert t.event(b["id"]) is None
    # un punto aggiunto in mezzo non eredita le misure di un'altra coppia
    c = new_point(25, "button")
    pr.add_point(c)
    assert t.estimate(b["id"]) is None and t.estimate(a["id"]) == 11.75     # 10, 14, 12 e 11 della 4ª replica
    assert f"{START}>{a['id']}" in pr.timing["intervals"]
    assert fmt(75) == "1:15" and fmt(3725) == "1:02:05" and fmt(-12, sign=True) == "−0:12"
    # salvataggio e riapertura
    path = os.path.join(TMP, "tempi")
    pr.save(path)
    q = Project.load(path + ".sfollow")
    assert q.timing["runs"] == 4 and Timing(q).estimate(n["id"]) == 6


@test
def progetto_salva_e_apri():
    pr = Project()
    pr.pdf = PDF
    p = new_point(7, "button")
    p["label"] = "LUCI 4"
    p["commands"] = [{"target": 1, "cmd": "go", "values": {"pb": 2}}]
    pr.add_point(p)
    pr.overrides = {3: True}
    path = os.path.join(TMP, "prova")
    pr.save(path)
    q = Project.load(path + ".sfollow")
    assert q.pdf == PDF and q.points[0]["label"] == "LUCI 4" and q.overrides == {3: True}
    assert q.points[0]["commands"][0]["values"]["pb"] == 2 and len(q.targets) == 2


@test
def gate_e_ricampionamento():
    a = ai.AudioInput()
    a.cfg = ai.audio_settings({"threshold_db": -30, "hold_ms": 100, "gate_on": True})
    a._rs = ai.Resampler(48000)
    got = []
    a.consumer = got.append
    quiet = np.full(480, 0.001, np.float32)                 # -60 dB
    loud = (0.5 * np.sin(np.arange(480) / 5)).astype(np.float32)
    a.process(quiet, 48000)
    assert not a.gate and a.meter_post == 0.0
    a.process(loud, 48000)
    assert a.gate and a.meter_post > 0.1
    for _ in range(20):                                    # 200 ms di silenzio: si chiude dopo l'hold
        a.process(quiet, 48000)
    assert not a.gate
    pcm = np.frombuffer(b"".join(got), np.int16)
    assert len(pcm) == 22 * 160, len(pcm)                  # 48 kHz -> 16 kHz
    assert np.abs(pcm[-800:]).max() == 0                   # gate chiuso = silenzio
    for sr in (44100, 16000, 22050):
        r = ai.Resampler(sr)
        n = sum(len(r(np.zeros(511, np.float32))) for _ in range(100))
        assert abs(n - 511 * 100 * 16000 / sr) <= 2, (sr, n)


@test
def riconoscitore_con_vosk_finto():
    """Il thread del riconoscitore con un Vosk finto: grammatica passata, parziali e risultati definitivi."""
    import json
    import scriptfollower.recognizer as rz

    class FakeRec:
        def __init__(self, model, rate, grammar=None):
            self.grammar = json.loads(grammar) if grammar else None
            FakeRec.last = self
            self.n = 0

        def AcceptWaveform(self, data):
            self.n += 1
            return self.n % 4 == 0

        def Result(self):
            return json.dumps({"text": "ciao [unk] mamma"})

        def PartialResult(self):
            return json.dumps({"partial": "sì sì"})

    class FakeModel:
        def vosk_model_find_word(self, w):
            return -1 if w == "mamma" else 3

    class FakeVosk:
        Model = staticmethod(lambda path: FakeModel())
        KaldiRecognizer = FakeRec

    old = rz.vosk
    rz.vosk = FakeVosk
    try:
        r = rz.Recognizer()
        got = []
        r.hypothesis.connect(lambda w, t: got.append(list(w)))
        unk = []
        r.unknown.connect(unk.append)
        r.start("/finto", ["ciao", "mamma", "sì"])
        run(0.2)
        for _ in range(12):
            r.feed(b"\x00" * 3200)
            time.sleep(0.05)
            run(0.01)
        run(0.3)
        r.stop()
        assert FakeRec.last.grammar == ["ciao", "mamma", "sì", "[unk]"]
        assert unk == [{"mamma"}], unk
        assert ["ciao", "mamma"] in got, got           # «[unk]» scartato
        assert any(g[-2:] == ["sì", "sì"] for g in got), got
    finally:
        rz.vosk = old


@test
def finestra_e_invio_osc():
    # ricevitore OSC locale
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import ThreadingOSCUDPServer
    got = []
    disp = Dispatcher()
    disp.set_default_handler(lambda addr, *args: got.append((addr, list(args))))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = ThreadingOSCUDPServer(("127.0.0.1", port), disp)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        from scriptfollower.main_window import MainWindow
        w = MainWindow()
        w.resize(1300, 800)
        w.show()
        run(0.2)
        w.open_pdf(PDF)
        run(0.2)
        assert w.doc is not None and w.aligner is not None
        w.project.targets[0].update(host="127.0.0.1", port=port, plugin="cuestage")
        d = w.doc
        k1 = next(x.i for x in d.words if x.text == "questo!")
        k2 = next(x.i for x in d.words if x.text == "corrente?")
        k3 = next(x.i for x in d.words if x.text == "eccolo!")
        for k, lab, cue in ((k1, "A", "1"), (k2, "B", "2"), (k3, "C", "3")):
            p = new_point(k, "button")
            p["label"] = lab
            p["commands"] = [{"target": 0, "cmd": "cue_start", "values": {"cue": cue}},
                             {"target": 0, "cmd": "go"}]
            w.project.add_point(p)
        n = new_point(k2 + 2, "note")
        n["text"] = "Torcia"
        w.project.add_point(n)
        w.refresh_points()
        w.set_mode(False)
        run(0.1)
        A, B, C = [w.project.sorted_points()[i] for i in (0, 1, 3)]
        assert w.cueing.next_button() is A
        # parlato simulato fino a «corrente?»
        heard = []
        for t in range(0, d.words[k2].t0 + 1):
            heard.append(d.tokens[t])
            w.on_hypothesis(heard[-12:], "")
        run(0.1)
        assert abs(w.cueing.pos - k2) <= 1, (w.cueing.pos, k2)
        assert w.point_state(A) == ("passed", False) or w.point_state(A) == ("armed", True)
        assert w.cueing.next_button() is C
        w.fire_next()                                 # Spazio: invia C, disattiva A e B
        run(0.3)
        assert w.aligner.floor == w.aligner.pos       # premuto in anticipo: si cerca da dove si è arrivati
        assert ("/cue/3/start", []) in got and ("/go", []) in got, got
        assert w.point_state(B)[1] is False and w.point_state(C) == ("sent", True)
        assert w.panel.pid is None                    # nessun altro pulsante
        # ascolto sospeso (modo manuale): il parlato non sposta più la posizione
        w.listening = True
        w.set_suspended(True)
        before = w.cueing.pos
        for t in range(d.words[k2].t0 + 1, d.words[k3].t0 + 1):
            heard.append(d.tokens[t])
            w.on_hypothesis(heard[-12:], "")
        assert w.cueing.pos == before and w.audio.consumer is None
        w.jump_to(k3)                                 # a mano sì
        assert w.cueing.pos == k3
        w.set_suspended(False)
        assert w.aligner.pos == d.words[k3].t0 + len(d.words[k3].tokens) - 1
        w.listening = False
        # riposizionamento all'inizio: tutto di nuovo attivo
        w.restart()
        assert w.point_state(A) == ("next", True) and w.point_state(C) == ("future", True)
        # salva il progetto accanto al PDF
        w.project.path = os.path.join(TMP, "copione.sfollow")
        assert w.save() and os.path.isfile(w.project.path)
        w.close()
    finally:
        server.shutdown()
        server.server_close()


@test
def lingue_e_modelli():
    """Lingue del copione: modelli trovati per lingua, nome → lingua, numeri inglesi, lettere slovene."""
    import scriptfollower.languages as L
    from scriptfollower.vocab import default_alias, number_words_en
    assert L.ORDER == ["it", "en", "sl"] and L.LANGS["sl"]["engine"] == "whisper"
    assert L.model_lang("vosk-model-small-it-0.22") == "it"
    assert L.model_lang("vosk-model-small-en-us-0.15") == "en"
    assert L.model_lang("faster-whisper-small") == "sl"
    root = os.path.join(TMP, "modelli")
    for name, files in (("vosk-model-small-en-us-0.15", ["am/final.mdl"]),
                        ("faster-whisper-small", ["model.bin", "tokenizer.json"])):
        for f in files:
            os.makedirs(os.path.dirname(os.path.join(root, name, f)), exist_ok=True)
            open(os.path.join(root, name, f), "w").close()
    old = os.environ.get("SCRIPTFOLLOWER_MODEL")
    os.environ["SCRIPTFOLLOWER_MODEL"] = root
    try:
        assert L.find_model("en").endswith("vosk-model-small-en-us-0.15")
        assert L.find_model("sl").endswith("faster-whisper-small")
        assert L.installed("sl")
    finally:
        if old is None:
            os.environ.pop("SCRIPTFOLLOWER_MODEL")
        else:
            os.environ["SCRIPTFOLLOWER_MODEL"] = old
    assert number_words_en(118) == "one hundred eighteen", number_words_en(118)
    assert number_words_en(2024) == "two thousand twenty four", number_words_en(2024)
    assert default_alias("21", "en") == "twenty one" and default_alias("21", "it") == "ventuno"
    assert normalize("Čez šest žab, Đuro!") == ["čez", "šest", "žab", "đuro"]


@test
def interfaccia_tradotta():
    """Cataloghi en/sl completi (stessi segnaposto) e finestre costruite in sloveno."""
    import json
    import re
    import string
    from scriptfollower import i18n
    from scriptfollower.ui_languages import LanguagesDialog
    from scriptfollower.ui_settings import SettingsDialog
    here = os.path.dirname(i18n.__file__)
    cats = {c: json.load(open(os.path.join(here, f"{c}.json"), encoding="utf-8")) for c in ("en", "sl")}
    assert set(cats["en"]) == set(cats["sl"]), set(cats["en"]) ^ set(cats["sl"])
    ph = re.compile(r"\{[^}]*\}")
    for c, cat in cats.items():
        for k, v in cat.items():
            assert v and sorted(ph.findall(k)) == sorted(ph.findall(v)), (c, k, v)
            list(string.Formatter().parse(v))
    # ogni _("…") del codice ha la traduzione
    src = ""
    for f in os.listdir(os.path.join(ROOT, "scriptfollower")):
        if f.endswith(".py"):
            src += open(os.path.join(ROOT, "scriptfollower", f), encoding="utf-8").read()
    used = set(re.findall(r"""\b_\(\s*(?:'([^'\\\n]+)'|"([^"\\\n]+)")\s*\)""", src))
    missing = [a or b for a, b in used if (a or b) not in cats["en"]]
    assert not missing, missing[:10]
    try:
        assert i18n.set_language("sl") == "sl"
        assert i18n._("Impostazioni") == "Nastavitve"
        assert i18n._("testo che non esiste") == "testo che non esiste"
        d = LanguagesDialog()
        assert d.table.rowCount() == 3 and d.table.horizontalHeaderItem(0).text() == "Jezik"
        d.close()
        from scriptfollower.main_window import MainWindow
        w = MainWindow()
        assert w.menuBar().actions()[0].text() == "&Datoteka"
        for page in range(4):
            SettingsDialog(w, page).close()
        w.project.dirty = False
        w.close()
        from PySide6.QtCore import QCoreApplication
        fb = i18n.QtFallback("sl", app)
        app.installTranslator(fb)
        try:
            assert QCoreApplication.translate("QPlatformTheme", "Cancel") == "Prekliči"
            assert QCoreApplication.translate("QLineEdit", "&Copy") == "&Copy"     # gli altri restano com'erano
        finally:
            app.removeTranslator(fb)
        i18n.set_language("en")
        assert i18n._("Profilo {0}").format(3) == "Profile 3"
    finally:
        i18n.set_language("it")
    assert i18n.set_language("xx") == "it"


@test
def progetto_lingua_salvata():
    p = Project()
    p.language = "sl"
    path = os.path.join(TMP, "lingua.sfollow")
    p.save(path)
    assert Project.load(path).language == "sl"
    import json
    data = json.load(open(path, encoding="utf-8"))
    data["language"] = "xx"
    json.dump(data, open(path, "w", encoding="utf-8"))
    assert Project.load(path).language == "it"


@test
def whisper_con_modello_minuscolo():
    """Motore Whisper vero (CTranslate2) con un modello a pesi casuali: spettrogramma, prompt, thread."""
    from scriptfollower import whisper_engine as we
    m = we.log_mel(np.zeros(16000, np.float32))
    assert m.shape == (80, 3000) and m.flags["C_CONTIGUOUS"] and m.dtype == np.float32
    if not we.available():
        print("    (ctranslate2/tokenizers non installati: salto la parte con il modello)")
        return
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import tiny_whisper
    path = tiny_whisper.make(os.path.join(TMP, "tiny-whisper"))
    assert we.is_whisper_model(path)
    eng = we.WhisperEngine(path, threads=1)
    audio = (np.random.default_rng(1).standard_normal(16000 * 2) * 0.1).astype(np.float32)
    out = eng.transcribe(audio, "sl", prompt="dober dan")
    assert isinstance(out, str) and len(out.split()) <= 16, out
    try:
        eng.transcribe(audio, "xx")
        raise AssertionError("lingua inesistente accettata")
    except ValueError:
        pass
    import scriptfollower.recognizer as rz
    old = rz.WHISPER_STEP
    rz.WHISPER_STEP = 0.2
    try:
        r = rz.Recognizer()
        got, ready = [], []
        r.hypothesis.connect(lambda w, t: got.append(t))
        r.ready.connect(ready.append)
        r.set_prompt("dober dan kako si")
        r.start(path, engine="whisper", language="sl")
        run(0.5)
        chunk = (np.random.default_rng(2).standard_normal(1600) * 3000).astype(np.int16).tobytes()
        for _ in range(40):
            r.feed(chunk)
            time.sleep(0.02)
            run(0.01)
        run(1.0)
        r.stop()
        assert ready == [True], ready
        assert got, "nessuna trascrizione"
    finally:
        rz.WHISPER_STEP = old


@test
def prova_con_file_audio():
    """File audio al posto della scheda: lettura (libsndfile e ffmpeg), canali, salti, gate e invio a 16 kHz."""
    import shutil
    import subprocess
    import soundfile as sf
    from scriptfollower.audio_file import AudioFile, FilePlayer, fmt_time, is_audio
    from scriptfollower.audio_input import AudioInput
    sr = 44100
    t = np.arange(int(sr * 2.0)) / sr
    burst = (0.5 * np.sin(2 * np.pi * 440 * t) * ((t % 1.0) < 0.5)).astype(np.float32)   # 0,5 s sì, 0,5 s no
    st = np.stack([burst, np.zeros_like(burst)], axis=1)                                     # voci sul canale 1
    path = os.path.join(TMP, "prova.wav")
    sf.write(path, st, sr)
    assert is_audio(path) and is_audio("x.M4A") and not is_audio("x.pdf")
    assert fmt_time(3725) == "1:02:05" and fmt_time(75) == "1:15"
    f = AudioFile(path)
    assert (f.sr, f.channels, f.backend) == (sr, 2, "libsndfile") and abs(f.duration - 2.0) < 1e-3
    assert abs(float(np.abs(f.read(sr // 4)).max()) - 0.25) < 0.01          # mix: metà del canale 1
    f.channel = 2
    assert float(np.abs(f.read(1000)).max()) == 0.0
    f.seek(1.5)
    assert f.pos == int(1.5 * sr)
    f.close()
    # in AudioInput: gate, meter e riconoscitore a 16 kHz, 3× più veloce del tempo reale
    a = AudioInput()
    a.configure({"device": "", "gate_on": True, "threshold_db": -30, "hold_ms": 0})
    got = []
    a.consumer = got.append
    pl = FilePlayer(a)
    pl.load(path)
    assert a.file_mode and a.listening and a.sr == sr
    pl.set_channel(1)
    pl.set_speed(3.0)
    ends = []
    pl.state.connect(ends.append)
    t0 = time.monotonic()
    pl.play()
    while pl.playing and time.monotonic() - t0 < 5:
        run(0.05)
    run(0.1)
    took = time.monotonic() - t0
    assert ends[-1] == "end" and 0.4 < took < 1.6, (ends, took)
    pcm = np.frombuffer(b"".join(got), np.int16)
    assert abs(len(pcm) - 32000) < 400, len(pcm)
    first, second = pcm[1000:7000], pcm[9000:15000]                       # suono, poi silenzio del gate
    assert np.abs(first).max() > 10000 and np.abs(second).max() == 0
    # salto e ripresa; il cambio di velocità vale per i tempi tra gli eventi
    got.clear()
    pl.seek(1.9)
    pl.play()
    run(0.4)
    assert not pl.playing and 0 < len(b"".join(got)) // 2 < 2400
    pl.unload()
    a.leave_file()
    assert not a.file_mode
    from scriptfollower.timing import Timing
    clk = [100.0]
    tm = Timing(Project(), clock=lambda: clk[0])
    assert tm.clock() == 100.0
    tm.set_speed(2.0)
    clk[0] += 10
    assert tm.clock() == 120.0
    # altri formati con ffmpeg (M4A/AAC), se c'è
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        m4a = os.path.join(TMP, "prova.m4a")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-c:a", "aac", m4a], check=True)
        g = AudioFile(m4a)
        assert g.backend == "ffmpeg" and g.channels == 2 and abs(g.duration - 2.0) < 0.1
        g.seek(0.1)
        x = g.read(4410)
        assert len(x) == 4410 and np.abs(x).max() > 0.1
        g.seek(0.75)                                                       # pausa del segnale
        assert np.abs(g.read(2000)).max() < 0.05
        g.close()
    else:
        print("    (ffmpeg non installato: salto la prova M4A)")
    try:
        AudioFile(os.path.join(TMP, "non-esiste.wav"))
        raise AssertionError("file inesistente accettato")
    except RuntimeError:
        pass


@test
def finestra_prova_file_audio():
    import soundfile as sf
    from scriptfollower.main_window import MainWindow
    path = os.path.join(TMP, "replica.flac")
    sf.write(path, (0.3 * np.sin(np.arange(16000 * 3) / 8)).astype(np.float32), 16000)
    w = MainWindow()
    w.open_pdf(PDF)
    w.audio_test_dialog(path)
    d = w.audiotest
    assert d is not None and d.player.file is not None and w.audio.file_mode
    assert d.slider.maximum() == 30 and d.chan.count() == 1
    d.auto.setChecked(False)                         # niente modello vocale nei test
    d.speed.setCurrentIndex(3)
    assert w.timing.speed == 3.0
    d.play()
    run(0.4)
    assert d.slider.value() > 0
    d.skip(10)
    run(1.5)
    assert not d.player.playing
    d.close()
    run(0.1)
    assert w.audiotest is None and not w.audio.file_mode and w.timing.speed == 1.0
    w.project.dirty = False
    w.close()


print(f"\n{len(FAILED)} prove fallite" + (f": {', '.join(FAILED)}" if FAILED else ""))
sys.exit(len(FAILED))
