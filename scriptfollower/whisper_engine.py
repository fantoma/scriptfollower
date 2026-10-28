"""Whisper (CTranslate2) per le lingue che Vosk non ha (lo sloveno).

Usa direttamente CTranslate2 e il tokenizer di Hugging Face, senza faster-whisper (che si porterebbe dietro
PyAV, onnxruntime e huggingface_hub). Il calcolo dello spettrogramma log-mel è quello di Whisper, ripreso da
faster-whisper (licenza MIT).

Whisper non è un riconoscitore «in streaming»: ogni ~0,8 s si trascrivono gli ultimi secondi di audio
(finestra mobile) e l'allineamento usa le parole della finestra. Il ritardo è di circa 1–2 s più di Vosk.
"""
import json
import os

import numpy as np
from .i18n import _

try:
    import ctranslate2
except Exception:  # pragma: no cover
    ctranslate2 = None
try:
    from tokenizers import Tokenizer
except Exception:  # pragma: no cover
    Tokenizer = None

SR = 16000
N_FFT, HOP, N_SAMPLES = 400, 160, 30 * 16000
_MEL = {}


def available():
    return ctranslate2 is not None and Tokenizer is not None


def is_whisper_model(path):
    return os.path.isfile(os.path.join(path, "model.bin")) and os.path.isfile(os.path.join(path, "tokenizer.json"))


def mel_filters(n_mels):
    """Banco di filtri mel «slaney» (come librosa), per n_fft = 400 a 16 kHz."""
    m = _MEL.get(n_mels)
    if m is not None:
        return m
    fftfreqs = np.fft.rfftfreq(n=N_FFT, d=1.0 / SR)
    mels = np.linspace(0.0, 45.245640471924965, n_mels + 2)
    f_sp = 200.0 / 3
    freqs = f_sp * mels
    min_log_hz, min_log_mel, logstep = 1000.0, 1000.0 / f_sp, np.log(6.4) / 27.0
    log_t = mels >= min_log_mel
    freqs[log_t] = min_log_hz * np.exp(logstep * (mels[log_t] - min_log_mel))
    fdiff = np.diff(freqs)
    ramps = freqs.reshape(-1, 1) - fftfreqs.reshape(1, -1)
    lower = -ramps[:-2] / fdiff[:-1, None]
    upper = ramps[2:] / fdiff[1:, None]
    w = np.maximum(0, np.minimum(lower, upper))
    w *= (2.0 / (freqs[2:n_mels + 2] - freqs[:n_mels]))[:, None]
    m = _MEL[n_mels] = w.astype(np.float32)
    return m


def log_mel(audio, n_mels=80):
    """Spettrogramma log-mel di Whisper per audio a 16 kHz, completato a 30 s: (n_mels, 3000)."""
    x = np.asarray(audio, np.float32)[:N_SAMPLES]
    x = np.pad(x, (0, N_SAMPLES - len(x) + HOP))
    x = np.pad(x, (N_FFT // 2, N_FFT // 2), mode="reflect")
    n_frames = 1 + (len(x) - N_FFT) // HOP
    frames = np.lib.stride_tricks.as_strided(x, (n_frames, N_FFT), (HOP * x.strides[0], x.strides[0]))
    window = np.hanning(N_FFT + 1)[:-1].astype(np.float32)
    spec = np.fft.rfft(frames * window, axis=-1)
    mag = (np.abs(spec[:-1]) ** 2).T.astype(np.float32)          # (201, 3000)
    mel = mel_filters(n_mels) @ mag
    log_spec = np.log10(np.clip(mel, 1e-10, None))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    return np.ascontiguousarray(((log_spec + 4.0) / 4.0)[:, :3000], dtype=np.float32)


class WhisperEngine:
    def __init__(self, path, threads=0):
        if not available():
            raise RuntimeError(_('motore Whisper non incluso in questa versione (ctranslate2/tokenizers)'))
        self.path = path
        self.model = ctranslate2.models.Whisper(path, device="cpu", compute_type="int8",
                                                intra_threads=threads or max(1, (os.cpu_count() or 4) - 1))
        self.tok = Tokenizer.from_file(os.path.join(path, "tokenizer.json"))
        self.n_mels = 80
        pc = os.path.join(path, "preprocessor_config.json")
        if os.path.isfile(pc):
            try:
                with open(pc, encoding="utf-8") as f:
                    self.n_mels = int(json.load(f).get("feature_size", 80))
            except (OSError, ValueError):
                pass
        t = self.tok.token_to_id
        self.sot, self.prev, self.eot = t("<|startoftranscript|>"), t("<|startofprev|>"), t("<|endoftext|>")
        self.transcribe_id, self.notimestamps = t("<|transcribe|>"), t("<|notimestamps|>")

    def transcribe(self, audio, language, prompt=""):
        """Testo dell'audio (float32, 16 kHz, al massimo 30 s). prompt = testo detto prima (aiuta lo stile)."""
        lang_id = self.tok.token_to_id(f"<|{language}|>")
        if lang_id is None:
            raise ValueError(_('lingua «{0}» non supportata da Whisper').format(language))
        seq = []
        if prompt:
            ids = self.tok.encode(" " + prompt.strip(), add_special_tokens=False).ids
            seq = [self.prev] + ids[-200:]
        seq += [self.sot, lang_id, self.transcribe_id, self.notimestamps]
        feats = ctranslate2.StorageView.from_array(np.ascontiguousarray(log_mel(audio, self.n_mels)[np.newaxis]))
        # al massimo ~8 token al secondo di audio: evita le ripetizioni senza fine («allucinazioni»)
        max_new = max(16, int(len(audio) / SR * 8))
        res = self.model.generate(feats, [seq], beam_size=1, max_length=min(448, len(seq) + max_new),
                                  return_no_speech_prob=True,
                                  suppress_blank=True, suppress_tokens=[-1])[0]
        if getattr(res, "no_speech_prob", 0.0) > 0.6:
            return ""
        ids = [i for i in res.sequences_ids[0] if i < self.eot]
        return self.tok.decode(ids).strip()
