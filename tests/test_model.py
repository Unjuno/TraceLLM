import math

import pytest
import torch

from trace_llm.data import BOS, EOS, decode, encode, windows, collate
from trace_llm.model import Config, GPT, optimizer


def test_utf8_roundtrip_and_specials():
    text = "日記🙂\nA\x00"
    assert decode(encode(text)) == text
    assert encode(text)[0] == BOS and encode(text)[-1] == EOS


@pytest.mark.parametrize('text', ['あ', 'abc', '日記🙂' * 33])
def test_windows_cover_each_target_once(text):
    examples = windows(text, 16)
    assert [t for _, y in examples for t in y] == encode(text)[1:]
    assert sum(x.count(BOS) for x, _ in examples) == 1
    assert sum(y.count(EOS) for _, y in examples) == 1


def test_padding_ignored():
    x, y = collate(windows('あ', 16) + windows('a' * 20, 16))
    assert x.shape == y.shape
    assert int((y != -100).sum()) == len('あ'.encode()) + 1 + 21


def test_empty_rejected():
    with pytest.raises(ValueError):
        windows('', 16)


def test_count_and_causality():
    torch.manual_seed(1)
    model = GPT(Config()).eval()
    assert sum(p.numel() for p in model.parameters()) == 115136
    x = torch.randint(0, 256, (1, 16)); z = x.clone(); z[:, 8:] = 7
    assert torch.equal(model(x)[:, :8], model(z)[:, :8])
    assert model.embedding.weight is model.head.weight


def test_real_training_reduces_fitting_loss():
    torch.manual_seed(7); torch.set_num_threads(1)
    model = GPT(Config()); opt = optimizer(model)
    x, y = collate(windows('試験用の短い文章。' * 3, 32))
    before = model.loss(x, y).item()
    for _ in range(12):
        opt.zero_grad(); loss = model.loss(x, y); loss.backward(); opt.step()
    assert math.isfinite(loss.item()) and model.loss(x, y).item() < before


def test_optimizer_roundtrip_continuation(tmp_path):
    torch.manual_seed(3)
    config = Config(dropout=0.1)
    model = GPT(config); opt = optimizer(model)
    x, y = collate(windows('これは人工的な検証用の文章です。', 32))
    def step(m, o):
        o.zero_grad(); m.loss(x, y).backward(); o.step()
    step(model, opt)
    path = tmp_path / 'test.pt'
    torch.save({'model': model.state_dict(), 'optimizer': opt.state_dict(),
                'rng': torch.get_rng_state()}, path)
    step(model, opt)
    saved = torch.load(path, weights_only=True)
    other = GPT(config); other_opt = optimizer(other)
    other.load_state_dict(saved['model']); other_opt.load_state_dict(saved['optimizer'])
    torch.set_rng_state(saved['rng']); step(other, other_opt)
    assert all(torch.equal(a, b) for a, b in zip(model.parameters(), other.parameters()))


def test_short_window_input_target_lengths_match():
    x, y = windows('試験', 256)[0]
    assert len(x) == len(y)
    assert EOS not in x
