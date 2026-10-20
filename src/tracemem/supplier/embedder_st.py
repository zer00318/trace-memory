"""OWNS: the real Embedder adapter — sentence-transformers, CPU (WO-009).

Env law: ollama's embedding endpoint is broken on this machine; embeddings go
through sentence-transformers, full stop. Lazy model load (first call pays);
CPU device pinned so the embedder NEVER contends for the GPU with capture or
the reasoner (one-model law applies to the metal, not just to ollama).
"""
from __future__ import annotations

from typing import Optional, Sequence

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, device: str = "cpu") -> None:
        self._model_name = model_name
        self._device = device
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            # The production runtime already seals this model locally. A
            # network HEAD on every daemon start turned offline inference
            # into repeated 10-second stalls on the answering thread.
            self._model = SentenceTransformer(
                self._model_name, device=self._device, local_files_only=True)
        return self._model

    def embed(self, text: str) -> Sequence[float]:
        return self.embed_many([text])[0]

    # HOW MANY TEXTS GO THROUGH THE MODEL AT ONCE. Big enough that the
    # per-call overhead stops dominating, small enough that a corpus pass
    # does not allocate a batch nobody can hold. Not tuned against anything;
    # UNCALIBRATED.
    BATCH = 256

    def embed_many(self, texts: Sequence[str]) -> "list":
        """Embed a whole list in batches — same vectors, one forward pass per
        BATCH instead of one per text.

        WHY THIS EXISTS. `embed` called `encode([text])`, one text per forward
        pass, and that is fine for a question and fatal for a corpus.
        `evalx/baseline_rag.answer` embeds EVERY claim in the store on EVERY
        question; on the founder's real store that is 633,761 single-text
        forward passes per question, which is the mechanical reason the
        falsification harness had only ever been run against a 7-question
        synthetic fixture. Batched, the same work is one pass per 256 texts.

        The vectors are identical — this changes how many texts share a
        forward pass, not the model, the normalisation, or the output.

        MEASURED, and the honest number is the smaller one: 2,436 texts/s on
        short synthetic strings, and about 130/s on the founder's real claims,
        which are longer and far more varied. A full pass over his 633,805
        claims is therefore roughly 80 minutes — once, and cacheable — not the
        four the synthetic figure suggested. A benchmark on made-up data is a
        benchmark of the made-up data.
        """
        texts = list(texts)
        if not texts:
            return []
        out = []
        model = self._load()
        for i in range(0, len(texts), self.BATCH):
            chunk = texts[i:i + self.BATCH]
            for vec in model.encode(chunk, normalize_embeddings=True,
                                    batch_size=self.BATCH,
                                    show_progress_bar=False):
                out.append([float(x) for x in vec])
        return out
