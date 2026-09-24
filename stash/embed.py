"""Local embedding. The only learned model in stash; inference only."""

import hashlib
from typing import Protocol


class Embedder(Protocol):
    @property
    def dim(self) -> int: ...
    @property
    def name(self) -> str: ...
    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbedder:
    """Deterministic, dependency-free embedder for plumbing tests.

    Not for retrieval-quality tests; those use SentenceTransformerEmbedder.
    """

    def __init__(self, dim: int = 384) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return f"fake-{self._dim}"

    def embed(self, text: str) -> list[float]:
        # Deterministic finite floats in [0, 1); never NaN/inf, so the vector
        # index accepts them. Distinct text -> distinct vector.
        out: list[float] = []
        counter = 0
        while len(out) < self._dim:
            h = hashlib.sha256(f"{counter}:{text}".encode()).digest()  # 32 bytes
            out.extend(b / 255.0 for b in h)
            counter += 1
        return out[:self._dim]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._name = model_name
        self._model = None  # lazy: avoid loading the model at import time

    def _ensure(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._name)
        return self._model

    @property
    def dim(self) -> int:
        return int(self._ensure().get_sentence_embedding_dimension())

    @property
    def name(self) -> str:
        return self._name

    def embed(self, text: str) -> list[float]:
        return [float(x) for x in self._ensure().encode(text)]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in row] for row in self._ensure().encode(texts)]
