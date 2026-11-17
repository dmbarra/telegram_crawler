"""Automatic groups from stored vectors: 'look' (CLIP image vectors) and 'face' (face vectors).

Incremental: each new item joins the closest group if close enough, else starts a new group.
Afterwards, changed groups that came close to another group are merged. Only the group
centroids are compared, never all items. Rebuild regroups everything with the current tightness.
"""
import threading
import time

import numpy as np

KINDS = ("look", "face")
# Tightness 1 (fewer, bigger groups) .. 5 (more, smaller groups): minimum cosine to join.
LEVELS = {"look": (0.70, 0.75, 0.80, 0.85, 0.90), "face": (0.36, 0.40, 0.45, 0.50, 0.55)}
DEFAULT_LEVEL = 3


def _unit(v):
    return v / (np.linalg.norm(v) or 1.0)


class _State:
    """Centroids in one growing matrix, so each new item is a single matrix-vector product."""

    def __init__(self, dim):
        self.keys, self.sizes, self.vsums = [], [], []
        self.centroids = np.zeros((64, dim), np.float32)

    def add(self, key, vsum, size):
        if len(self.keys) == len(self.centroids):
            self.centroids = np.concatenate([self.centroids, np.zeros_like(self.centroids)])
        self.centroids[len(self.keys)] = _unit(vsum)
        self.keys.append(key)
        self.vsums.append(vsum.astype(np.float32))
        self.sizes.append(size)

    def closest(self, v):
        n = len(self.keys)
        if not n:
            return -1, -1.0
        sims = self.centroids[:n] @ v
        j = int(sims.argmax())
        return j, float(sims[j])

    def join(self, j, v):
        self.vsums[j] = self.vsums[j] + v
        self.sizes[j] += 1
        self.centroids[j] = _unit(self.vsums[j])

    def remove(self, j):
        """Swap-remove row j; returns nothing, indexes after the call change."""
        last = len(self.keys) - 1
        if j != last:
            self.centroids[j] = self.centroids[last]
            self.keys[j], self.sizes[j], self.vsums[j] = self.keys[last], self.sizes[last], self.vsums[last]
        self.centroids[last] = 0
        self.keys.pop(), self.sizes.pop(), self.vsums.pop()


def cluster(existing, items, threshold, progress=None) -> dict:
    """existing: [(id, vsum bytes, size)]; items: [(item, vector bytes)].
    Returns {"groups", "members", "merges", "created", "joined", "merged"} for Library.save_grouping."""
    vectors = [np.frombuffer(v, "<f4") for _, v in items]
    dim = len(vectors[0]) if vectors else (len(np.frombuffer(existing[0][1], "<f4")) if existing else 1)
    state = _State(dim)
    for gid, vsum, size in existing:
        state.add(gid, np.frombuffer(vsum, "<f4"), size)

    members, changed = [], set()
    created = joined = 0
    for n, ((item, _), v) in enumerate(zip(items, vectors)):
        j, score = state.closest(v)
        if j >= 0 and score >= threshold:
            state.join(j, v)
            key = state.keys[j]
            joined += 1
        else:
            key = f"new{n}"
            state.add(key, v, 1)
            score = 1.0
            created += 1
        members.append((item, key, score))
        changed.add(key)
        if progress and n % 200 == 0:
            progress(n, len(items))

    # Merge changed groups that are now close to another group.
    alias, merges, merged = {}, [], 0
    todo = list(changed)
    while todo:
        key = todo.pop()
        if key in alias or key not in state.keys:
            continue
        i = state.keys.index(key)
        sims = state.centroids[:len(state.keys)] @ state.centroids[i]
        sims[i] = -2
        j = int(sims.argmax())
        if sims[j] < threshold:
            continue
        a, b = state.keys[i], state.keys[j]
        # Keep existing groups (stable ids); else keep the bigger one.
        if isinstance(a, int) != isinstance(b, int):
            keep, drop = (a, b) if isinstance(a, int) else (b, a)
        else:
            keep, drop = (a, b) if state.sizes[i] >= state.sizes[j] else (b, a)
        ki, di = state.keys.index(keep), state.keys.index(drop)
        state.vsums[ki] = state.vsums[ki] + state.vsums[di]
        state.sizes[ki] += state.sizes[di]
        state.centroids[ki] = _unit(state.vsums[ki])
        state.remove(di)
        alias[drop] = keep
        if isinstance(drop, int):
            merges.append((drop, keep))
        changed.discard(drop)
        changed.add(keep)
        todo.append(keep)
        merged += 1

    def resolve(key):
        while key in alias:
            key = alias[key]
        return key

    merges = [(src, resolve(dst)) for src, dst in merges]
    groups = {}
    for key in changed:
        i = state.keys.index(key)
        groups[key] = (state.vsums[i].astype("<f4").tobytes(), state.sizes[i])
    return {
        "groups": groups,
        "members": [(item, resolve(key), score) for item, key, score in members],
        "merges": merges, "created": created, "joined": joined, "merged": merged,
    }


class Grouper:
    def __init__(self, library):
        self.library = library
        self._lock = threading.Lock()
        self.running = False
        self.mode = ""            # new | rebuild
        self.kind = ""
        self.done = self.total = 0
        self.started_at = self.finished_at = None
        self.results = {}
        self.error = ""

    def level(self, kind: str) -> int:
        try:
            level = int(self.library.setting(f"tightness_{kind}", DEFAULT_LEVEL))
        except ValueError:
            level = DEFAULT_LEVEL
        return level if 1 <= level <= len(LEVELS[kind]) else DEFAULT_LEVEL

    def set_level(self, kind: str, level: int) -> None:
        self.library.set_setting(f"tightness_{kind}", level)

    def snapshot(self) -> dict:
        with self._lock:
            state = {"running": self.running, "mode": self.mode, "kind": self.kind,
                     "done": self.done, "total": self.total, "started_at": self.started_at,
                     "finished_at": self.finished_at, "results": dict(self.results),
                     "error": self.error}
        state["levels"] = {k: self.level(k) for k in KINDS}
        state["summary"] = {k: self.library.group_summary(k) for k in KINDS}
        state["new"] = {k: self.library.group_items(k, new_only=True, count_only=True) for k in KINDS}
        return state

    def start(self, rebuild: bool):
        """Returns an error message, or None if the run started."""
        with self._lock:
            if self.running:
                return "Grouping is already running"
            self.running, self.mode, self.kind = True, "rebuild" if rebuild else "new", ""
            self.done = self.total = 0
            self.started_at, self.finished_at = time.time(), None
            self.results, self.error = {}, ""
        threading.Thread(target=self._run, args=(rebuild,), daemon=True, name="groups").start()
        return None

    def _progress(self, done, total):
        with self._lock:
            self.done, self.total = done, total

    def _run(self, rebuild):
        try:
            for kind in KINDS:
                items = self.library.group_items(kind, new_only=not rebuild)
                with self._lock:
                    self.kind, self.done, self.total = kind, 0, len(items)
                if not items:
                    self.results[kind] = {"items": 0, "created": 0, "joined": 0, "merged": 0}
                    continue
                existing = [] if rebuild else [tuple(r) for r in self.library.groups_state(kind)]
                out = cluster(existing, items, LEVELS[kind][self.level(kind) - 1], self._progress)
                self.library.save_grouping(kind, out["groups"], out["members"], out["merges"], rebuild)
                with self._lock:
                    self.done = len(items)
                    self.results[kind] = {"items": len(items), "created": out["created"],
                                          "joined": out["joined"], "merged": out["merged"]}
        except Exception as e:  # shown in the UI; the next run starts clean
            self.error = f"{type(e).__name__}: {e}"
        finally:
            with self._lock:
                self.running = False
                self.finished_at = time.time()
