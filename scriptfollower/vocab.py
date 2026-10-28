"""Vocabolario del copione rispetto a quello del modello vocale.

Il modello «small» italiano di Vosk riconosce solo le parole del suo vocabolario: una parola del copione che
il modello non conosce (un nome proprio, un neologismo, un numero scritto in cifre) non verrà mai «sentita».
Aggiungere davvero parole nuove al modello richiede di ricompilarne il grafo (pacchetto di compilazione di
Alphacephei per l'italiano, Kaldi, SRILM, Phonetisaurus, un server Linux con 32 GB di RAM): non si può fare
dall'app. Si può invece:

1. scoprire quali parole del copione il modello non conosce (Model.vosk_model_find_word);
2. dare a ognuna una «pronuncia» fatta di parole che il modello conosce («Gesualdi» → «gesù aldi»,
   «118» → «centodiciotto»): il riconoscitore le può sentire e l'allineamento le aggancia alla parola;
3. le parole sconosciute senza pronuncia vengono saltate senza penalità dall'allineamento.
"""
import unicodedata

_UNITS = ["zero", "uno", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove", "dieci", "undici",
          "dodici", "tredici", "quattordici", "quindici", "sedici", "diciassette", "diciotto", "diciannove"]
_TENS = ["", "", "venti", "trenta", "quaranta", "cinquanta", "sessanta", "settanta", "ottanta", "novanta"]


def _below_100(n):
    if n < 20:
        return _UNITS[n]
    t, u = divmod(n, 10)
    word = _TENS[t]
    if u in (1, 8):                       # ventuno, ventotto: la vocale finale cade
        word = word[:-1]
    return word + (_UNITS[u] if u else "")


def _below_1000(n):
    h, r = divmod(n, 100)
    head = "" if h == 0 else ("cento" if h == 1 else _UNITS[h] + "cento")
    if r == 0:
        return head or "zero"
    tail = _below_100(r)
    if head and tail.startswith("ott"):  # centotto (più comune di centootto)
        head = head[:-1]
    return head + tail


def number_words(n):
    """Numero in lettere, come si scrive in italiano: 118 -> 'centodiciotto', 2024 -> 'duemilaventiquattro'.

    Il tre finale prende l'accento (ventitré). Fino a 999 999; oltre restituisce le cifre."""
    n = int(n)
    if n < 0 or n > 999_999:
        return str(n)
    if n < 1000:
        w = _below_1000(n)
    else:
        th, r = divmod(n, 1000)
        w = ("mille" if th == 1 else _below_1000(th) + "mila") + (_below_1000(r) if r else "")
    if w.endswith("tre") and n > 3:
        w = w[:-3] + "tré"
    return w


_EN_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
             "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words_en(n):
    """118 -> 'one hundred eighteen' (parole separate, come le scrive il modello inglese di Vosk)."""
    n = int(n)
    if n < 0 or n > 999_999:
        return str(n)

    def below_1000(k):
        out = []
        h, r = divmod(k, 100)
        if h:
            out += [_EN_UNITS[h], "hundred"]
        if r >= 20:
            t, u = divmod(r, 10)
            out += [_EN_TENS[t]] + ([_EN_UNITS[u]] if u else [])
        elif r or not out:
            out.append(_EN_UNITS[r])
        return out
    if n < 1000:
        return " ".join(below_1000(n))
    th, r = divmod(n, 1000)
    return " ".join(below_1000(th) + ["thousand"] + (below_1000(r) if r else []))


def default_alias(token, lang="it"):
    """Pronuncia automatica di un token: i numeri in cifre, per le lingue riconosciute con Vosk.
    (Whisper, usato per lo sloveno, scrive di solito i numeri in cifre: lì restano come sono.)"""
    if token.isdigit() and len(token) <= 6:
        if lang == "it":
            return number_words(int(token))
        if lang == "en":
            return number_words_en(int(token))
    return None


# ---------------------------------------------------------------------------- proposte
_ACCENTED = {"a": "à", "e": "è", "i": "ì", "o": "ò", "u": "ù"}


def _strip(w):
    return "".join(ch for ch in unicodedata.normalize("NFD", w) if unicodedata.category(ch) != "Mn")


def suggest(word, known, lang="it"):
    """Proposta di pronuncia per una parola sconosciuta, usando solo parole note al modello.

    known(parola) -> bool. Prova: numeri in lettere, accento finale aggiunto/tolto/cambiato, parola divisa in
    due o tre parti note («aspirapolvere» → «aspira polvere»). Ritorna il testo o None."""
    w = word.lower()
    d = default_alias(w, lang)
    if d and all(known(t) for t in d.replace("-", " ").split()):
        return d
    cands = []
    base = _strip(w)
    if base != w:
        cands.append(base)
    if base and base[-1] in _ACCENTED:
        cands.append(base[:-1] + _ACCENTED[base[-1]])
        if base[-1] == "e":
            cands.append(base[:-1] + "é")
    for c in cands:
        if c != w and known(c):
            return c
    # divisione in parti note (le più lunghe possibile)
    n = len(w)
    for i in range(n - 2, 1, -1):
        a, b = w[:i], w[i:]
        if len(a) >= 2 and len(b) >= 2 and known(a) and known(b):
            return f"{a} {b}"
    for i in range(2, n - 3):
        for j in range(i + 2, n - 1):
            a, b, c = w[:i], w[i:j], w[j:]
            if len(c) >= 2 and known(a) and known(b) and known(c):
                return f"{a} {b} {c}"
    return None
