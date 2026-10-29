"""Dati dello show dentro il PDF del copione.

I dati (pulsanti, note, profili OSC, tempi, pronunce, impostazioni dello show) stanno in un allegato
«scriptfollower.json»; ogni immagine dei pulsanti in un allegato «scriptfollower-img/<nome>». Gli allegati sono
il modo standard dei PDF per portarsi dietro dei file: i lettori li ignorano (Acrobat li mostra nel pannello
degli allegati), il copione si apre e si stampa come prima.

Ogni salvataggio riscrive il PDF da capo (niente salvataggi incrementali): i dati precedenti vengono sostituiti
e non resta una «storia» nel file. La scrittura è atomica (file temporaneo nella stessa cartella, poi
sostituzione), così un'interruzione non lascia mai un PDF rovinato. Prima di scrivere per la prima volta in un
PDF si crea una copia di riserva pulita «<nome>.originale.pdf», senza dati di Script Follower.
"""
import json
import os
import shutil

import pymupdf

from .i18n import _

DATA_NAME = "scriptfollower.json"
IMG_PREFIX = "scriptfollower-img/"
BACKUP_SUFFIX = ".originale.pdf"


class StoreError(Exception):
    """Il PDF non si può scrivere (firmato, protetto, bloccato, in sola lettura…): il messaggio dice perché."""


def backup_path(path):
    stem = path[:-4] if path.lower().endswith(".pdf") else path
    return stem + BACKUP_SUFFIX


def is_backup(path):
    return path.lower().endswith(BACKUP_SUFFIX)


def _ours(name):
    return name == DATA_NAME or name.startswith(IMG_PREFIX)


def read(path_or_doc):
    """(dati, {nome immagine: bytes}) dal PDF, oppure (None, {}) se non ci sono dati di Script Follower."""
    doc = path_or_doc if isinstance(path_or_doc, pymupdf.Document) else pymupdf.open(path_or_doc)
    try:
        names = doc.embfile_names()
        if DATA_NAME not in names:
            return None, {}
        try:
            data = json.loads(doc.embfile_get(DATA_NAME).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise ValueError(_("i dati di Script Follower nel PDF sono illeggibili: {0}").format(e)) from e
        images = {n[len(IMG_PREFIX):]: doc.embfile_get(n) for n in names if n.startswith(IMG_PREFIX)}
        return data, images
    finally:
        if doc is not path_or_doc:
            doc.close()


def has_data(path):
    try:
        with pymupdf.open(path) as doc:
            return DATA_NAME in doc.embfile_names()
    except Exception:  # noqa: BLE001
        return False


def _check_writable(doc):
    if doc.needs_pass or doc.is_encrypted or (doc.metadata or {}).get("encryption"):
        raise StoreError(_("il PDF è protetto da password: non si può scriverci dentro"))
    try:
        signed = doc.get_sigflags() > 0
    except Exception:  # noqa: BLE001
        signed = False
    if signed:
        raise StoreError(_("il PDF è firmato digitalmente: scriverci dentro renderebbe la firma non valida"))


def _strip(doc):
    for n in list(doc.embfile_names()):
        if _ours(n):
            doc.embfile_del(n)


def _clean_bytes(doc):
    """Il PDF senza i dati di Script Follower, riscritto pulito."""
    _strip(doc)
    return doc.tobytes(garbage=3, deflate=True)


def _write_atomic(path, data):
    folder = os.path.dirname(os.path.abspath(path)) or "."
    tmp = os.path.join(folder, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            shutil.copymode(path, tmp)
        except OSError:
            pass
        os.replace(tmp, path)
    except PermissionError as e:
        _rm(tmp)
        raise StoreError(_("il PDF non si può sovrascrivere (è aperto in un altro programma o la cartella è in "
                           "sola lettura): {0}").format(e)) from e
    except OSError as e:
        _rm(tmp)
        raise StoreError(_("scrittura del PDF non riuscita: {0}").format(e)) from e


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


def write(path, data, images=None, dest=None, backup=True):
    """Scrive dati e immagini nel PDF «path» (o in una copia «dest»), sostituendo quelli che c'erano.

    Ritorna il percorso della copia di riserva creata adesso (o None). Solleva StoreError se non si può."""
    with open(path, "rb") as f:
        src = f.read()
    doc = pymupdf.open(stream=src, filetype="pdf")
    try:
        _check_writable(doc)
        target = os.path.abspath(dest or path)
        made = None
        if backup and dest is None:
            bp = backup_path(target)
            if not os.path.exists(bp):
                had = DATA_NAME in doc.embfile_names()
                try:
                    if had:            # c'erano già dati: la copia di riserva li toglie
                        with pymupdf.open(stream=src, filetype="pdf") as d2:
                            clean = _clean_bytes(d2)
                        with open(bp, "wb") as f:
                            f.write(clean)
                    else:              # il PDF originale, identico byte per byte
                        shutil.copy2(path, bp)
                    made = bp
                except OSError as e:
                    raise StoreError(_("copia di riserva non riuscita ({0}): il PDF non è stato modificato").format(e)) from e
        _strip(doc)
        payload = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        doc.embfile_add(DATA_NAME, payload, filename=DATA_NAME, ufilename=DATA_NAME,
                        desc="Script Follower: pulsanti, note, profili OSC e impostazioni dello show")
        for name, blob in sorted((images or {}).items()):
            n = IMG_PREFIX + name
            doc.embfile_add(n, blob, filename=os.path.basename(name), ufilename=os.path.basename(name),
                            desc="Script Follower: immagine")
        out = doc.tobytes(garbage=3, deflate=True)     # da capo: i dati vecchi spariscono davvero
    finally:
        doc.close()
    _write_atomic(target, out)
    return made


def strip_file(path, dest):
    """Copia pulita del PDF (senza dati di Script Follower) in dest."""
    with pymupdf.open(path) as doc:
        clean = _clean_bytes(doc)
    _write_atomic(dest, clean)
