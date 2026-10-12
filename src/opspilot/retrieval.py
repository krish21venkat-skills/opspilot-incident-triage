"""Runbook retrieval with TF-IDF and cosine similarity, in pure Python.

Why not a vector database? The runbook set is small, a managed vector store would
leave the AWS free tier, and keyword retrieval is easy to explain and test.
ADR 0004 records the trade-off and the upgrade path.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and are as at be by for from has have in is it of on or that the this to was were with".split())


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


@dataclass(frozen=True)
class Runbook:
    id: str
    title: str
    keywords: str
    body: str


@dataclass(frozen=True)
class RunbookHit:
    id: str
    title: str
    score: float
    body: str


def parse_runbook(raw: str, fallback_id: str) -> Runbook:
    """Parse a markdown file with a small `key: value` header between --- lines."""
    meta: dict[str, str] = {}
    body = raw
    if raw.startswith("---"):
        _, header, body = raw.split("---", 2)
        for line in header.strip().splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip().lower()] = value.strip()
    return Runbook(
        id=meta.get("id", fallback_id),
        title=meta.get("title", fallback_id),
        keywords=meta.get("keywords", ""),
        body=body.strip(),
    )


class RunbookIndex:
    def __init__(self, runbooks: list[Runbook]):
        self._runbooks = runbooks
        docs = [self._doc_tokens(r) for r in runbooks]
        n = len(docs)
        df: Counter[str] = Counter()
        for tokens in docs:
            df.update(set(tokens))
        self._idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
        self._vectors = [self._vector(tokens) for tokens in docs]

    @staticmethod
    def _doc_tokens(runbook: Runbook) -> list[str]:
        # Title and keywords count triple so they outweigh incidental words in the body.
        weighted = f"{runbook.title} {runbook.keywords} " * 3 + runbook.body
        return _tokens(weighted)

    def _vector(self, tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        vec = {t: (1 + math.log(c)) * self._idf.get(t, 1.0) for t, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    @classmethod
    def from_directory(cls, path: str | Path) -> RunbookIndex:
        runbooks = [parse_runbook(p.read_text(encoding="utf-8"), p.stem) for p in sorted(Path(path).glob("*.md"))]
        return cls(runbooks)

    def search(self, query: str, k: int = 2, min_score: float = 0.05) -> list[RunbookHit]:
        query_vec = self._vector(_tokens(query))
        hits = []
        for runbook, vec in zip(self._runbooks, self._vectors, strict=True):
            score = sum(w * vec.get(t, 0.0) for t, w in query_vec.items())
            if score >= min_score:
                hits.append(RunbookHit(runbook.id, runbook.title, round(score, 4), runbook.body))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]
