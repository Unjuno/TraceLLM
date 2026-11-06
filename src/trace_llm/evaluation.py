"""Evaluation on explicit examples. Retention probes are NOT held-out validation."""
import hashlib
import random

import torch

from .data import collate, replay_window


def target_count(examples) -> int:
    return sum(sum(token != -100 for token in y) for _, y in examples)


def score_examples(model, examples) -> float | None:
    count = target_count(examples)
    if not count:
        return None
    training = model.training
    model.eval()
    total = 0.0
    try:
        with torch.inference_mode():
            for example in examples:
                x, y = collate([example])
                total += model.loss(x, y).item() * int((y != -100).sum())
    finally:
        model.train(training)
    return total / count


def retention_probes(rows, context: int, policy):
    """Deterministic bounded probes; no training RNG consumption or future data.

    Stable hash ordering spreads probes over page revisions. One random-start
    window/page, at most retention_pages. Sampling is explicitly NOT exhaustive.
    """
    ordered = sorted(rows, key=lambda r: hashlib.sha256(r['id'].encode()).digest())
    probes = []
    for row in ordered[:policy.retention_pages]:
        rng = random.Random(row['id'] + row['sha256'])
        ex = replay_window(row['body'], context, min(context, policy.retention_targets), rng)
        probes.append((row['page_id'], row['id'], ex))
    return probes


class ByteNgram:
    """Diagnostic longest-seen-context backoff with additive smoothing.

    Explicit update only; never used to replace/mix neural outputs. score() does
    not mutate counts. Vocabulary and EOS match neural evaluation units.
    """
    def __init__(self, order: int = 4, alpha: float = 0.1):
        import math
        if type(order) is not int or not 1 <= order <= 8 or not math.isfinite(alpha) or alpha <= 0:
            raise ValueError('Invalid n-gram settings')
        self.order, self.alpha = order, alpha
        self.counts = {}

    def update(self, text: str) -> None:
        from collections import Counter
        from .data import encode
        ids = encode(text)
        for i in range(1, len(ids)):
            for k in range(min(self.order-1, i)+1):
                context = tuple(ids[i-k:i])
                self.counts.setdefault(context, Counter())[ids[i]] += 1

    def probabilities(self, prefix: list[int]) -> list[float]:
        from .data import VOCAB_SIZE
        counts = {}
        for k in range(min(self.order-1, len(prefix)), -1, -1):
            context = tuple(prefix[-k:]) if k else ()
            if context in self.counts:
                counts = self.counts[context]; break
        total = sum(counts.values()) + self.alpha*VOCAB_SIZE
        return [(counts.get(i, 0)+self.alpha)/total for i in range(VOCAB_SIZE)]

    def score(self, text: str) -> float:
        import math
        from .data import encode
        ids = encode(text)
        return sum(-math.log(self.probabilities(ids[max(0, i-self.order+1):i])[ids[i]])
                   for i in range(1, len(ids))) / (len(ids)-1)
