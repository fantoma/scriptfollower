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
    assert ctx.startswith("Dove ho messo"), ctx
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


print(f"\n{len(FAILED)} prove fallite" + (f": {', '.join(FAILED)}" if FAILED else ""))
sys.exit(len(FAILED))
