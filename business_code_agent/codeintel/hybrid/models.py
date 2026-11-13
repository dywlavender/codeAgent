"""Local ONNX bi-encoder and Cross-Encoder; no source leaves the machine."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from threading import Timer
import time

import numpy as np

EMBEDDING_MODEL = "Xenova/paraphrase-multilingual-MiniLM-L12-v2"
RERANK_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"


class OnnxTextModel:
    def __init__(self, directory, *, pair=False, max_length=256):
        import onnxruntime as ort
        import json

        ort.disable_telemetry_events()
        from tokenizers import Tokenizer

        self.ort, self.pair = ort, pair
        self.tokenizer = Tokenizer.from_file(str(Path(directory) / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=max_length)
        config = json.loads(
            (Path(directory) / "config.json").read_text(encoding="utf-8")
        )
        pad_id = config.get("pad_token_id", 0)
        self.tokenizer.enable_padding(
            pad_id=pad_id, pad_token=self.tokenizer.id_to_token(pad_id) or "[PAD]"
        )
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(Path(directory) / "onnx/model_quantized.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.inputs = {i.name for i in self.session.get_inputs()}

    def run(self, texts, *, budget_ms=None):
        started = time.monotonic()
        options = self.ort.RunOptions()
        timer = (
            Timer(budget_ms / 1000, lambda: setattr(options, "terminate", True))
            if budget_ms
            else None
        )
        if timer:
            timer.start()
        try:
            encoded = self.tokenizer.encode_batch(texts)
            inputs = {
                "input_ids": np.asarray([x.ids for x in encoded], dtype=np.int64),
                "attention_mask": np.asarray(
                    [x.attention_mask for x in encoded], dtype=np.int64
                ),
                "token_type_ids": np.asarray(
                    [x.type_ids for x in encoded], dtype=np.int64
                ),
            }
            output = self.session.run(
                None, {k: v for k, v in inputs.items() if k in self.inputs}, options
            )[0]
            if self.pair:
                return output.reshape(len(texts), -1)[:, -1]
            mask = inputs["attention_mask"][..., None]
            vectors = (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
            return (
                vectors
                / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)
            ).astype(np.float32)
        finally:
            if timer:
                timer.cancel()
            self.last_seconds = time.monotonic() - started

    def encode(self, texts, batch_size=16):
        arrays = []
        for i in range(0, len(texts), batch_size):
            arrays.append(self.run(texts[i : i + batch_size]))
            if getattr(self, "progress", None) and (i // batch_size) % 32 == 0:
                self.progress(min(i + batch_size, len(texts)), len(texts))
        return (
            np.concatenate(arrays) if arrays else np.empty((0, 384), dtype=np.float32)
        )


@lru_cache(maxsize=4)
def load_model(directory, pair=False):
    return OnnxTextModel(directory, pair=pair)
