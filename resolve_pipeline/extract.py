"""Multilingual mention extraction with GLiNER (urchade/gliner_multi-v2.1).

Labels are English semantic anchors from labels.yml; the mDeBERTa backbone
matches them zero-shot across languages. Install with `pip install ".[ner]"`.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import yaml

from .types import Mention

logger = logging.getLogger(__name__)

MODEL_ID = "urchade/gliner_multi-v2.1"
LABELS_PATH = Path(__file__).parent / "labels.yml"

# GLiNER splits text into words itself; whitespace splitting turns a Japanese
# or Chinese sentence into one word. One splitter per language, built once.
SPLITTER_BY_LANG = {"ja": "janome", "zh": "jieba"}
_SPLITTERS: dict[str, Any] = {}


def load_labels(path: Path = LABELS_PATH) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return list(yaml.safe_load(fh).get("labels", []))


def load_model(model_id: str = MODEL_ID) -> Any:
    from gliner import GLiNER
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("Loading GLiNER %s on %s", model_id, device)
    return GLiNER.from_pretrained(model_id, map_location=device)


_MODEL_LOCK = threading.Lock()


def _splitter_for(lang: str) -> Any:
    name = SPLITTER_BY_LANG.get(lang or "", "whitespace")
    if name not in _SPLITTERS:
        try:
            from gliner.data_processing.tokenizer import WordsSplitter
            _SPLITTERS[name] = WordsSplitter(name)
        except Exception as exc:  # noqa: BLE001 - optional tokenisers
            logger.warning("GLiNER splitter %r unavailable (%s); using whitespace", name, exc)
            _SPLITTERS[name] = None
    splitter = _SPLITTERS[name]
    if splitter is None and name != "whitespace":
        return _splitter_for("")
    return splitter


def extract(
    model: Any,
    text: str,
    labels: list[str] | None = None,
    threshold: float = 0.5,
    lang: str = "",
) -> list[Mention]:
    """Return mentions with character spans in `text`, highest-scoring span per overlap.

    The word splitter is chosen per language and set on the model's shared
    processor, so the splitter assignment and the prediction run under one
    lock: two threads sharing one model never predict with each other's
    splitter. Calls on one model are therefore serialised; for parallel
    extraction load one model per thread.
    """
    splitter = _splitter_for(lang)
    processor = getattr(model, "data_processor", None)
    with _MODEL_LOCK:
        if splitter is not None and processor is not None:
            processor.words_splitter = splitter
        spans = model.predict_entities(text, labels or load_labels(), threshold=threshold, flat_ner=True)
    return [
        Mention(s["text"], int(s["start"]), int(s["end"]), s["label"], round(float(s["score"]), 4))
        for s in spans
    ]
