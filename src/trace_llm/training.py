"""CPU policies and token-weighted microbatches; no persistence or external data."""
from dataclasses import dataclass
import math

import torch

from .data import BOS, EOS, VOCAB_SIZE, collate
from .model import Config, GPT

TOKENIZER = {'kind': 'utf8-bytes', 'version': 1, 'vocab_size': VOCAB_SIZE,
             'bos': BOS, 'eos': EOS}


@dataclass(frozen=True)
class TrainingConfig:
    learning_rate: float = 3e-4
    replay_ratio: float = 0.25
    batch_mode: str = 'separate'
    context_prefix: int = 0
    max_steps: int = 64
    max_train_seconds: float = 5.0  # soft cap, checked between optimizer updates; 0 disables
    duty_cycle: float = 1.0       # cooperative sleeps, NOT an OS CPU quota
    retention_pages: int = 4
    retention_targets: int = 256

    def __post_init__(self):
        for name in ('context_prefix', 'max_steps', 'retention_pages', 'retention_targets'):
            value = getattr(self, name)
            if type(value) is not int or value < (1 if name in ('max_steps', 'retention_targets') else 0):
                raise ValueError(f'Invalid {name}')
        for name in ('learning_rate', 'replay_ratio', 'max_train_seconds', 'duty_cycle'):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f'Non-finite {name}')
        if not (0 < self.learning_rate <= 0.1 and 0 <= self.replay_ratio <= 0.5
                and 0 < self.duty_cycle <= 1 and self.max_train_seconds >= 0):
            raise ValueError('Invalid training policy')
        if self.batch_mode not in ('separate', 'padded'):
            raise ValueError('Invalid microbatch mode')
        if self.context_prefix > 1023 or self.retention_pages > 32 or self.retention_targets > 65536:
            raise ValueError('Evaluation or prefix budget too large')


def validate_tokenizer(saved: dict | None) -> None:
    """v0.1 omitted this metadata but used these exact token IDs."""
    if saved is not None and saved != TOKENIZER:
        raise ValueError('Checkpoint tokenizer differs; do not reinterpret saved token IDs')


def model_config(preset: str = 'seed', context: int = 256) -> Config:
    sizes = {'seed': (2, 64, 4), 'medium': (3, 96, 4), 'small': (4, 128, 4)}
    if preset not in sizes:
        raise ValueError('Unknown model preset')
    layers, width, heads = sizes[preset]
    return Config(layers=layers, width=width, heads=heads, context=context)


def backward_examples(model: GPT, examples: list, mode: str = 'separate') -> float:
    """Accumulate a mean over ALL unmasked targets, not a mean of sequence means.

    Caller zeros gradients and calls clip/step once. Ragged execution trades extra
    dispatch overhead for no padding work. With dropout, RNG consumption differs
    from padded mode; deterministic equivalence is tested with dropout disabled.
    """
    counts = [sum(t != -100 for t in y) for _, y in examples]
    total = sum(counts)
    if not total or any(n == 0 for n in counts) or mode not in ('separate', 'padded'):
        raise ValueError('Nonempty supervised examples and a valid mode are required')
    groups = [examples] if mode == 'padded' else [[ex] for ex in examples]
    value = 0.0
    for group in groups:
        x, y = collate(group)
        weight = int((y != -100).sum()) / total
        loss = model.loss(x, y)
        if not torch.isfinite(loss):
            raise RuntimeError('Non-finite loss; update not published')
        (loss * weight).backward()
        value += loss.detach().item() * weight
    return value
