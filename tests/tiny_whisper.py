"""Modello Whisper minuscolo (pesi casuali, ~200 kB) per provare il motore senza scaricare nulla.

    python3 tests/tiny_whisper.py CARTELLA
"""
import os
import sys

import numpy as np
import ctranslate2.specs as S
from tokenizers import Tokenizer, models, pre_tokenizers, decoders



def make(out):
    """Scrive in «out» un modello CTranslate2 Whisper (model.bin, config.json, tokenizer.json)."""
    os.makedirs(out, exist_ok=True)
    words = ["dober", "dan", "kako", "si", "hvala", "lepo", "ja", "ne", "[UNK]"]
    special = ["<|endoftext|>", "<|startoftranscript|>", "<|startofprev|>", "<|transcribe|>", "<|sl|>", "<|it|>",
               "<|en|>", "<|nocaptions|>", "<|notimestamps|>"]
    vocab = {w: i for i, w in enumerate(words + special)}
    tok = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.decoder = decoders.WordPiece()
    tok.add_special_tokens(special)
    tok.save(os.path.join(out, "tokenizer.json"))
    V, d, rng = len(vocab), 64, np.random.default_rng(0)
    r = lambda *s: (rng.standard_normal(s) * 0.05).astype(np.float32)
    z = lambda *s: np.zeros(s, np.float32)
    o = lambda *s: np.ones(s, np.float32)
    sp = S.WhisperSpec(1, 2, 1, 2)
    e = sp.encoder
    e.conv1.weight, e.conv1.bias = r(d, 80, 3), z(d)
    e.conv2.weight, e.conv2.bias = r(d, d, 3), z(d)
    e.position_encodings.encodings = r(1500, d)
    e.layer_norm.gamma, e.layer_norm.beta = o(d), z(d)
    def ln(x): x.gamma, x.beta = o(d), z(d)
    def lin(x, a, b): x.weight, x.bias = r(a, b), z(a)
    for L in e.layer:
        ln(L.self_attention.layer_norm); lin(L.self_attention.linear[0], 3 * d, d); lin(L.self_attention.linear[1], d, d)
        ln(L.ffn.layer_norm); lin(L.ffn.linear_0, 4 * d, d); lin(L.ffn.linear_1, d, 4 * d)
    dd = sp.decoder
    dd.embeddings.weight = r(V, d)
    dd.position_encodings.encodings = r(448, d)
    ln(dd.layer_norm)
    dd.projection.weight = r(V, d)
    for L in dd.layer:
        ln(L.self_attention.layer_norm); lin(L.self_attention.linear[0], 3 * d, d); lin(L.self_attention.linear[1], d, d)
        ln(L.attention.layer_norm); lin(L.attention.linear[0], d, d); lin(L.attention.linear[1], 2 * d, d)
        lin(L.attention.linear[2], d, d)
        ln(L.ffn.layer_norm); lin(L.ffn.linear_0, 4 * d, d); lin(L.ffn.linear_1, d, 4 * d)
    inv = sorted(vocab, key=vocab.get)
    sp.register_vocabulary(inv)
    sp.config.suppress_ids = []
    sp.config.suppress_ids_begin = []
    sp.config.lang_ids = [vocab["<|sl|>"], vocab["<|it|>"], vocab["<|en|>"]]
    sp.config.alignment_heads = [(0, 0)]
    sp.validate()
    sp.save(out)
    return out


if __name__ == "__main__":
    print(make(sys.argv[1]))
