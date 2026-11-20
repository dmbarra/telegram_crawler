"""Content fingerprints (SHA-256) and the duplicates report, from the library."""
import hashlib
from pathlib import Path


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicates(library, downloads_dir: Path) -> list:
    """[{group, sha, keep, dups: [rel...], size}] — keep = oldest file (first downloaded).
    A group is one channel (its id, as text)."""
    by_key = {}
    seen = set()
    for r in library.kept_files():
        rel = r["path"]
        if not r["sha256"] or r["channel_id"] is None or rel in seen \
                or not (downloads_dir / rel).is_file():
            continue
        seen.add(rel)
        by_key.setdefault((str(r["channel_id"]), r["sha256"]), []).append((r["downloaded_at"], rel, r["size"] or 0))
    out = []
    for (group, sha), files in by_key.items():
        if len(files) < 2:
            continue
        files.sort()
        out.append({
            "group": group,
            "sha": sha,
            "keep": files[0][1],
            "dups": [r for _, r, _ in files[1:]],
            "size": files[0][2],
        })
    out.sort(key=lambda d: (d["group"].lower(), d["keep"]))
    return out


def find_visual_duplicates(candidates: list, max_hamming: int = 4, min_clip_sim: float = 0.96, scope: str = "channel") -> dict:
    """Detect perceptual and semantic duplicate images using hybrid pHash + CLIP embeddings.

    Groups matches using disjoint set union. Inside each cluster, the highest resolution
    (or largest/oldest) image is chosen as the 'keeper', and remaining copies are marked as duplicates.
    Returns: {"count", "size", "duplicates": [...], "clusters_count"}
    """
    try:
        import numpy as np
    except ImportError:
        np = None

    # Group candidates by scope
    grouped = {}
    for c in candidates:
        key = str(c.get("channel_id") or "all") if scope == "channel" else "global"
        grouped.setdefault(key, []).append(c)

    all_duplicates = []
    total_clusters = 0

    for group_key, items in grouped.items():
        if len(items) < 2:
            continue

        # Pre-parse pHash and CLIP embeddings
        for item in items:
            raw_hash = item.get("phash")
            try:
                item["_hash"] = int(raw_hash, 16) if raw_hash and len(raw_hash) == 16 else None
            except ValueError:
                item["_hash"] = None

            raw_clip = item.get("clip")
            if raw_clip and isinstance(raw_clip, (bytes, memoryview)) and np is not None:
                try:
                    vec = np.frombuffer(raw_clip, dtype="<f4")
                    norm = np.linalg.norm(vec)
                    item["_clip"] = (vec / norm) if norm > 0 else None
                except Exception:
                    item["_clip"] = None
            else:
                item["_clip"] = None

        n = len(items)
        parent = list(range(n))

        def find(i):
            path = []
            while parent[i] != i:
                path.append(i)
                i = parent[i]
            for node in path:
                parent[node] = i
            return i

        def union(i, j):
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        # Compare pairs
        for i in range(n):
            item_i = items[i]
            h_i = item_i["_hash"]
            c_i = item_i["_clip"]
            for j in range(i + 1, n):
                item_j = items[j]
                h_j = item_j["_hash"]
                c_j = item_j["_clip"]

                is_dup = False
                # 1. Check pHash difference
                if h_i is not None and h_j is not None:
                    dist = (h_i ^ h_j).bit_count()
                    if dist <= max_hamming:
                        is_dup = True

                # 2. Check CLIP cosine similarity if not already matched
                if not is_dup and c_i is not None and c_j is not None:
                    sim = float(np.dot(c_i, c_j))
                    if sim >= min_clip_sim:
                        is_dup = True

                if is_dup:
                    union(i, j)

        # Build clusters
        clusters = {}
        for idx in range(n):
            root = find(idx)
            clusters.setdefault(root, []).append(items[idx])

        for root, cluster in clusters.items():
            if len(cluster) < 2:
                continue
            total_clusters += 1
            # Sort keeper first: highest resolution (w*h), largest size, earliest date
            def _rank(x):
                w = x.get("width") or 0
                h = x.get("height") or 0
                sz = x.get("size") or 0
                dt = str(x.get("at") or "")
                return (-(w * h), -sz, dt)

            cluster.sort(key=_rank)
            keeper = cluster[0]
            keeper_res = f"{keeper.get('width') or '?'}x{keeper.get('height') or '?'}"

            for dup in cluster[1:]:
                dup_res = f"{dup.get('width') or '?'}x{dup.get('height') or '?'}"
                all_duplicates.append({
                    "path": dup["path"],
                    "size": dup.get("size") or 0,
                    "keeper": keeper["path"],
                    "keeper_res": keeper_res,
                    "dup_res": dup_res,
                    "channel_id": dup.get("channel_id"),
                })

    return {
        "count": len(all_duplicates),
        "size": sum(d["size"] for d in all_duplicates),
        "duplicates": all_duplicates,
        "clusters_count": total_clusters,
    }

