"""Corpus-free UTF-8 tokens; page-local targets, no invented boundary tokens."""
import torch

BOS, EOS, VOCAB_SIZE = 256, 257, 258


def encode(text: str) -> list[int]:
    return [BOS, *text.encode('utf-8'), EOS]


def decode(tokens: list[int]) -> str:
    return bytes(t for t in tokens if 0 <= t < 256).decode('utf-8', errors='replace')


def windows(text: str, length: int, prefix: int = 0) -> list[tuple[list[int], list[int]]]:
    """One pass over all byte/EOS targets with optional masked left context.

    Only the real page start includes BOS. Prefix targets are -100; no duplicate
    target exposure is hidden by overlap. Default prefix=0 preserves v0.1 windows.
    """
    if (not text.strip() or type(length) is not int or length < 1
            or type(prefix) is not int or not 0 <= prefix < length):
        raise ValueError('Nonempty page, positive length and 0 <= prefix < length required')
    ids = encode(text)
    result = []
    for target_start in range(0, len(ids) - 1, length - prefix):
        left = max(0, target_start - prefix)
        end = min(len(ids) - 1, target_start + length - prefix)
        y = ids[left + 1:end + 1]
        y[:target_start - left] = [-100] * (target_start - left)
        result.append((ids[left:end], y))
    return result


def replay_window(text: str, length: int, targets: int, rng, prefix: int = 0):
    """Uniform target START within one page, not always a chunk prefix.

    Page selection is caller policy. This is not token-uniform sampling across a
    corpus; short suffixes can return fewer than requested targets.
    """
    if not text.strip() or targets < 1 or not 0 <= prefix < length:
        raise ValueError('Invalid replay sample')
    ids = encode(text)
    start = rng.randrange(len(ids) - 1)
    end = min(len(ids) - 1, start + min(targets, length - prefix))
    left = max(0, start - prefix)
    y = ids[left + 1:end + 1]
    y[:start - left] = [-100] * (start - left)
    return ids[left:end], y


def collate(examples):
    if not examples:
        raise ValueError('Empty batch')
    n = max(len(y) for _, y in examples)
    x = torch.zeros((len(examples), n), dtype=torch.long)
    y = torch.full_like(x, -100)
    for row, (a, b) in enumerate(examples):
        x[row, :len(a)] = torch.tensor(a)
        y[row, :len(b)] = torch.tensor(b)
    return x, y
