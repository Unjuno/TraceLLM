"""Corpus-free UTF-8 tokens; page-local targets, no invented boundary tokens."""
import torch

BOS, EOS, VOCAB_SIZE = 256, 257, 258


def encode(text: str) -> list[int]:
    return [BOS, *text.encode('utf-8'), EOS]


def decode(tokens: list[int]) -> str:
    return bytes(t for t in tokens if 0 <= t < 256).decode('utf-8', errors='replace')


def windows(text: str, length: int) -> list[tuple[list[int], list[int]]]:
    if not text.strip() or length < 1:
        raise ValueError('A non-empty page and a positive sequence length are required.')
    ids = encode(text)
    return [(ids[:-1][i:i + length], ids[i + 1:i + length + 1])
            for i in range(0, len(ids) - 1, length)]


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
