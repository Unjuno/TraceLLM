"""Artificial text only; no runtime diary directory is opened by these tests."""
import copy
import random

import pytest
import torch

from trace_llm import data, model


def test_training_policy_exists_and_is_validated():
    from trace_llm import training
    policy = training.TrainingConfig()
    assert policy.replay_ratio == 0.25
    assert policy.batch_mode == 'separate'
    for kw in [{'replay_ratio': 1}, {'replay_ratio': -0.1}, {'learning_rate': float('nan')},
               {'max_steps': 0}, {'duty_cycle': 0}, {'duty_cycle': 1.1},
               {'context_prefix': -1}, {'batch_mode': 'magic'}, {'retention_pages': -1}]:
        with pytest.raises(ValueError):
            training.TrainingConfig(**kw)


@pytest.mark.parametrize('text', ['あ', 'abcde', '日記🙂' * 21])
@pytest.mark.parametrize('prefix', [0, 3, 7])
def test_overlapping_windows_cover_targets_once(text, prefix):
    examples = data.windows(text, 8, prefix=prefix)
    targets = [t for _, y in examples for t in y if t != -100]
    assert targets == data.encode(text)[1:]
    assert sum(y.count(data.EOS) for _, y in examples) == 1
    assert all(len(x) == len(y) <= 8 for x, y in examples)
    for x, y in examples:
        for k in range(len(x)-1):
            if y[k] != -100:
                assert y[k] == x[k+1]


def test_replay_samples_beyond_window_prefix():
    rng = random.Random(9)
    seen = set()
    text = ''.join(chr(c) for c in range(33, 110))
    for _ in range(500):
        x, y = data.replay_window(text, 16, 1, rng, prefix=8)
        assert len(x) == len(y) <= 16
        seen.update(t for t in y if t != -100)
    assert set(data.encode(text)[1:]) <= seen


def test_separate_backward_matches_padded_token_weighting():
    from trace_llm.training import backward_examples
    torch.manual_seed(3); torch.set_num_threads(1)
    a = model.GPT(model.Config(context=32, dropout=0.0)); b = copy.deepcopy(a)
    examples = data.windows('長い人工テストの文章です。', 32)[:1] + data.windows('あ', 32)
    x, y = data.collate(examples)
    expected = a.loss(x, y); expected.backward()
    loss = backward_examples(b, examples, mode='separate')
    assert loss == pytest.approx(expected.item(), abs=1e-6)
    for p, q in zip(a.parameters(), b.parameters()):
        torch.testing.assert_close(p.grad, q.grad, atol=2e-7, rtol=2e-5)


def test_cached_rope_nonpersistent_and_matches_formula():
    attention = model.Attention(model.Config(context=32))
    assert hasattr(attention, 'cos_cache')
    assert 'cos_cache' not in attention.state_dict()
    x = torch.randn(2, 4, 17, 16)
    angle = torch.outer(torch.arange(17), attention.freq)
    a, b = x[..., 0::2], x[..., 1::2]
    expected = torch.stack((a*angle.cos()-b*angle.sin(), a*angle.sin()+b*angle.cos()), -1).flatten(-2)
    torch.testing.assert_close(attention.rotate(x), expected, rtol=0, atol=0)


@pytest.mark.parametrize('kw', [{'heads': 0}, {'width': 0}, {'heads': -1}, {'layers': True}])
def test_invalid_model_dimensions_raise_value_error(kw):
    with pytest.raises(ValueError):
        model.Config(**kw)


def test_model_presets_and_tokenizer_contract():
    from trace_llm.training import model_config, validate_tokenizer, TOKENIZER
    assert [sum(p.numel() for p in model.GPT(model_config(name, 256)).parameters())
            for name in ('seed', 'medium', 'small')] == [115136, 357216, 820608]
    validate_tokenizer(None)  # legacy format 1
    validate_tokenizer(TOKENIZER.copy())
    with pytest.raises(ValueError):
        validate_tokenizer({**TOKENIZER, 'bos': 0})


def test_ngram_baseline_is_normalised_and_does_not_learn_during_score():
    from trace_llm.evaluation import ByteNgram
    from math import log
    baseline = ByteNgram(order=3)
    assert baseline.score('人工') == pytest.approx(log(258))
    baseline.update('人工テストです。')
    before = copy.deepcopy(baseline.counts)
    assert baseline.score('人工テストです。') < log(258)
    baseline.score('未学習の人工テストです。')
    assert baseline.counts == before
    assert sum(baseline.probabilities([data.BOS])) == pytest.approx(1.0)
