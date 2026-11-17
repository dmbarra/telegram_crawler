"""Standalone ML test on a folder of images. Local only, prints results, writes nothing.

    tags    DIR --labels "selfie,x-ray,skin,document"   best label per image (CLIP zero-shot)
    search  DIR "text"                                  images closest to a text (CLIP)
    similar DIR IMAGE                                   images closest to one image (CLIP)
    faces   DIR                                         faces per image + pairs that look like the same person
    match   IMAGE_A IMAGE_B                             compare the faces of two images

--limit and --top go before the command:  ml_test.py --top 50 search /images "x-ray"

Models: CLIP ViT-B-32 (open_clip, LAION-2B), YuNet face detection + SFace face recognition (OpenCV).
"""
import argparse
import sys
from pathlib import Path

import numpy as np

from models import SAME_PERSON, Clip, Faces, decode

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def list_images(folder: str, limit: int) -> list:
    files = sorted(p for p in Path(folder).rglob("*") if p.suffix.lower() in IMAGE_EXTS)
    if not files:
        sys.exit(f"No images found in {folder}")
    if len(files) > limit:
        print(f"{len(files)} images found, using the first {limit} (--limit)")
    return files[:limit]


def short(path: Path) -> str:
    return "/".join(path.parts[-2:])


def load(path: Path):
    """(PIL, BGR) or None if the file is not a readable image."""
    try:
        return decode(path.read_bytes())
    except (OSError, ValueError) as e:
        print(f"  skip {short(path)}: {e}")
        return None


def embed_all(clip: Clip, paths, batch=16) -> tuple:
    """(vectors, kept paths) for every readable image."""
    out, kept = [], []
    for i in range(0, len(paths), batch):
        pils = []
        for p in paths[i:i + batch]:
            img = load(p)
            if img:
                pils.append(img[0])
                kept.append(p)
        if pils:
            out.append(clip.embed(pils))
        print(f"  embedded {len(kept)}/{len(paths)}", end="\r")
    print()
    if not out:
        sys.exit("No readable images")
    return np.concatenate(out), kept


def cmd_tags(args):
    labels = [s.strip() for s in args.labels.split(",") if s.strip()]
    clip = Clip()
    emb, paths = embed_all(clip, list_images(args.dir, args.limit))
    # "a photo of ..." prompts work better than bare words with CLIP.
    scores = 100 * emb @ clip.texts([f"a photo of {label}" for label in labels]).T
    probs = np.exp(scores - scores.max(axis=1, keepdims=True))
    probs /= probs.sum(axis=1, keepdims=True)
    counts = dict.fromkeys(labels, 0)
    for path, row in zip(paths, probs):
        best = int(row.argmax())
        counts[labels[best]] += 1
        print(f"{row[best]:5.0%}  {labels[best]:<20} {short(path)}")
    print("\nPer label:", ", ".join(f"{k}: {v}" for k, v in counts.items()))


def cmd_search(args):
    clip = Clip()
    emb, paths = embed_all(clip, list_images(args.dir, args.limit))
    show_top(emb @ clip.texts([args.text])[0], paths, args.top)


def cmd_similar(args):
    clip = Clip()
    query, _ = embed_all(clip, [Path(args.image)])
    emb, paths = embed_all(clip, list_images(args.dir, args.limit))
    show_top(emb @ query[0], paths, args.top)


def show_top(scores, paths, top):
    print("score  image   (cosine similarity: higher = closer)")
    for i in np.argsort(-scores)[:top]:
        print(f"{scores[i]:.3f}  {short(paths[i])}")


# ─── Faces ──────────────────────────────────────────────────────
def find_faces(faces: Faces, path: Path) -> list:
    img = load(path)
    return faces.find(img[1]) if img else []


def cmd_faces(args):
    faces = Faces(args.min_score)
    paths = list_images(args.dir, args.limit)
    found = []
    with_face = 0
    for path in paths:
        hits = find_faces(faces, path)
        with_face += bool(hits)
        print(f"{len(hits)} face(s)  {short(path)}")
        found += [(path, i, f["vector"]) for i, f in enumerate(hits)]
    print(f"\n{with_face} of {len(paths)} images have a face · {len(found)} faces in total")
    if len(found) < 2:
        return

    # Vectors are normalized, so one matrix product gives every cosine score.
    vecs = np.stack([f[2] for f in found])
    sims = vecs @ vecs.T
    pairs = [(float(sims[i, j]), found[i], found[j])
             for i, j in zip(*np.triu_indices(len(found), k=1))
             if sims[i, j] >= SAME_PERSON and found[i][0] != found[j][0]]
    pairs.sort(key=lambda p: -p[0])
    print(f"{len(pairs)} pair(s) look like the same person (score >= {SAME_PERSON}):")
    for score, a, b in pairs[:args.top]:
        print(f"{score:.3f}  {short(a[0])}#{a[1]}  ~  {short(b[0])}#{b[1]}")


def cmd_match(args):
    faces = Faces(args.min_score)
    a, b = find_faces(faces, Path(args.image_a)), find_faces(faces, Path(args.image_b))
    print(f"A: {len(a)} face(s) · B: {len(b)} face(s)")
    if not a or not b:
        return
    for i, fa in enumerate(a):
        for j, fb in enumerate(b):
            score = float(fa["vector"] @ fb["vector"])
            verdict = "same person" if score >= SAME_PERSON else "different"
            print(f"A#{i} vs B#{j}: {score:.3f}  {verdict}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=500, help="max images to read (default 500)")
    ap.add_argument("--top", type=int, default=20, help="results to show (default 20)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("tags"); p.add_argument("dir"); p.add_argument("--labels", required=True)
    p.set_defaults(fn=cmd_tags)
    p = sub.add_parser("search"); p.add_argument("dir"); p.add_argument("text")
    p.set_defaults(fn=cmd_search)
    p = sub.add_parser("similar"); p.add_argument("dir"); p.add_argument("image")
    p.set_defaults(fn=cmd_similar)
    for name, fn in (("faces", cmd_faces), ("match", cmd_match)):
        p = sub.add_parser(name)
        if name == "faces":
            p.add_argument("dir")
        else:
            p.add_argument("image_a"); p.add_argument("image_b")
        p.add_argument("--min-score", type=float, default=0.8, help="face detection score (default 0.8)")
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
