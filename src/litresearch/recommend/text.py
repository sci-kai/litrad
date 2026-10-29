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


def tfidf_matrix(texts: list[str], columns_from: int | None = None) -> np.ndarray:
    """Return L2-normalised TF-IDF rows (one per text) over a shared vocabulary.

    With ``columns_from``, only words of the first ``columns_from`` texts get a
    column. Rows are still normalised over all their words, so similarities
    that involve one of these first texts stay exact, while the matrix of a
    large library no longer needs a column for every word it contains.
    """
    docs = [Counter(tokens(text or "")) for text in texts]
    df = Counter(word for doc in docs for word in doc)
    n = len(docs)
    idf = {word: math.log((1 + n) / (1 + count)) + 1 for word, count in df.items()}
    kept = df if columns_from is None else {w for doc in docs[:columns_from] for w in doc}
    vocab = {word: i for i, word in enumerate(sorted(kept))}
    matrix = np.zeros((n, len(vocab)), dtype=np.float32)
    norms = np.ones((n, 1), dtype=np.float32)
    for row, doc in enumerate(docs):
        squares = 0.0
        for word, count in doc.items():
            value = (1 + math.log(count)) * idf[word]
            squares += value * value
            if (column := vocab.get(word)) is not None:
                matrix[row, column] = value
        if squares:
            norms[row] = math.sqrt(squares)
    matrix /= norms
    return matrix
