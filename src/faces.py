"""Same-person search over stored face vectors (normalized float32, so cosine = dot product)."""
import numpy as np

# SFace cosine score: >= 0.363 means same person (OpenCV's published threshold).
SAME_PERSON = 0.363
MIN_SCORES = (0.30, 0.363, 0.45, 0.55, 0.65)


def same_person(query: bytes, faces, min_score: float) -> list:
    """[(path, score, face id)] for every image with a face close to `query`, best first.
    One entry per image: its closest face."""
    if not faces:
        return []
    vectors = np.stack([np.frombuffer(f["vector"], "<f4") for f in faces])
    scores = vectors @ np.frombuffer(query, "<f4")
    best = {}
    for f, score in zip(faces, scores.tolist()):
        if score >= min_score and score > best.get(f["path"], (-2,))[0]:
            best[f["path"]] = (score, f["id"])
    return sorted(((p, s, i) for p, (s, i) in best.items()), key=lambda r: -r[1])
