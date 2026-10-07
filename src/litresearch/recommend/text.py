"""TF-IDF text vectors: a fallback when SPECTER2 embeddings are missing."""

import math
import re
from collections import Counter

import numpy as np

STOPWORDS = set(
    """
    about after also among analysis and are associated based between both can cancer
    cell cells data different during each from has have here however into its more
    most new not our patients results show shows such than that the their these this
    through using was were which while with within study studies human may two
    """.split()
)


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z0-9-]{2,}", text.lower())
    return [w for w in words if w not in STOPWORDS]


def tfidf_matrix(texts: list[str]) -> np.ndarray:
    """Return L2-normalised TF-IDF rows (one per text) over a shared vocabulary."""
    docs = [Counter(tokens(text or "")) for text in texts]
    df = Counter(word for doc in docs for word in doc)
    vocab = {word: i for i, word in enumerate(sorted(df))}
    matrix = np.zeros((len(docs), len(vocab)), dtype=np.float32)
    n = len(docs)
    for row, doc in enumerate(docs):
        for word, count in doc.items():
            matrix[row, vocab[word]] = (1 + math.log(count)) * (
                math.log((1 + n) / (1 + df[word])) + 1
            )
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return matrix / norms
