"""Small nanoGPT-style causal decoder. Independent implementation; no downloads."""
from dataclasses import dataclass

import torch
from torch import nn
from torch.nn import functional as F

from .data import VOCAB_SIZE


@dataclass(frozen=True)
class Config:
    layers: int = 2
    width: int = 64
    heads: int = 4
    context: int = 256
    dropout: float = 0.1

    def __post_init__(self):
        if (self.layers < 1 or self.width % self.heads or
                (self.width // self.heads) % 2 or not 1 <= self.context <= 1024):
            raise ValueError('Invalid model dimensions')
        if not 0 <= self.dropout < 1:
            raise ValueError('Invalid dropout')


class Attention(nn.Module):
    def __init__(self, config: Config):
        super().__init__()
        self.heads = config.heads
        self.dropout = config.dropout
        self.qkv = nn.Linear(config.width, 3 * config.width, bias=False)
        self.proj = nn.Linear(config.width, config.width, bias=False)
        size = config.width // config.heads
        self.register_buffer('freq', 1.0 / (10000 ** (torch.arange(0, size, 2) / size)),
                             persistent=False)

    def rotate(self, x):
        angle = torch.outer(torch.arange(x.size(-2), device=x.device), self.freq)
        cos, sin = angle.cos(), angle.sin()
        a, b = x[..., 0::2], x[..., 1::2]
        return torch.stack((a * cos - b * sin, a * sin + b * cos), dim=-1).flatten(-2)

    def forward(self, x):
        batch, length, width = x.shape
        q, k, v = (t.view(batch, length, self.heads, width // self.heads).transpose(1, 2)
                   for t in self.qkv(x).chunk(3, dim=-1))
        z = F.scaled_dot_product_attention(
            self.rotate(q), self.rotate(k), v, is_causal=True,
            dropout_p=self.dropout if self.training else 0.0)
        return self.proj(z.transpose(1, 2).contiguous().view(batch, length, width))


class Block(nn.Module):
    def __init__(self, config):
        super().__init__()
        d = config.width
        self.norm1 = nn.LayerNorm(d, bias=False)
        self.norm2 = nn.LayerNorm(d, bias=False)
        self.attn = Attention(config)
        self.mlp = nn.Sequential(nn.Linear(d, 4*d, bias=False), nn.GELU(),
                                 nn.Linear(4*d, d, bias=False), nn.Dropout(config.dropout))

    def forward(self, x):
        x = x + self.attn(self.norm1(x))
        return x + self.mlp(self.norm2(x))


class GPT(nn.Module):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.embedding = nn.Embedding(VOCAB_SIZE, config.width)
        self.blocks = nn.Sequential(*(Block(config) for _ in range(config.layers)))
        self.norm = nn.LayerNorm(config.width, bias=False)
        self.head = nn.Linear(config.width, VOCAB_SIZE, bias=False)
        self.head.weight = self.embedding.weight
        self.apply(self._init)

    @staticmethod
    def _init(module):
        if isinstance(module, (nn.Linear, nn.Embedding)):
            nn.init.normal_(module.weight, std=0.02)

    def forward(self, ids):
        if ids.size(1) > self.config.context:
            raise ValueError('Context exceeded')
        return self.head(self.norm(self.blocks(self.embedding(ids))))

    def loss(self, x, y):
        return F.cross_entropy(self(x).reshape(-1, VOCAB_SIZE), y.reshape(-1),
                               ignore_index=-100)


def optimizer(model: GPT):
    return torch.optim.AdamW([
        {'params': [p for p in model.parameters() if p.ndim >= 2], 'weight_decay': 0.01},
        {'params': [p for p in model.parameters() if p.ndim < 2], 'weight_decay': 0.0},
    ], lr=3e-4, betas=(0.9, 0.95))
