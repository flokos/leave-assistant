"""R-27: tanh is rewritten as 2·sigmoid(2x) − 1 so MKL's VML tanh kernel (SIGILL on AVX-only CPUs) is never reached."""

from __future__ import annotations

import math

import pytest

torch = pytest.importorskip("torch")
from torch import nn  # noqa: E402

from leave_assistant.infrastructure.ml.safe_tanh import SigmoidTanh, avoid_mkl_tanh, sigmoid_tanh  # noqa: E402


class XLMRobertaClassificationHead(nn.Module):  # same shape and forward as transformers'
    def __init__(self) -> None:
        super().__init__()
        self.dense = nn.Linear(8, 8)
        self.dropout = nn.Dropout(0.0)
        self.out_proj = nn.Linear(8, 1)

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        x = self.dense(self.dropout(features[:, 0, :]))
        return self.out_proj(self.dropout(torch.tanh(x)))


class Pooler(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.dense = nn.Linear(8, 8)
        self.activation = nn.Tanh()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.activation(self.dense(x))


def reference_tanh(t: torch.Tensor) -> torch.Tensor:
    """Scalar `math.tanh`: the test must not reach MKL's vector tanh itself (it crashed this very suite)."""
    return torch.tensor([math.tanh(v) for v in t.flatten().tolist()], dtype=t.dtype).reshape(t.shape)


def test_sigmoid_tanh_equals_tanh() -> None:
    x = torch.linspace(-20, 20, 4001)
    assert torch.allclose(sigmoid_tanh(x), reference_tanh(x), atol=1e-6)


def test_rewrites_both_sites_without_changing_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    torch.manual_seed(0)
    model = nn.ModuleDict({"pooler": Pooler(), "classifier": XLMRobertaClassificationHead()}).eval()
    features = torch.randn(3, 5, 8)
    with monkeypatch.context() as m:  # the "before" outputs use the scalar reference, not MKL
        m.setattr(torch, "tanh", reference_tanh)
        m.setattr(nn.Tanh, "forward", lambda self, x: reference_tanh(x))
        before = (model["pooler"](features), model["classifier"](features))

    assert avoid_mkl_tanh(model) == 2
    assert isinstance(model["pooler"].activation, SigmoidTanh)

    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("torch.tanh reached")

    monkeypatch.setattr(torch, "tanh", forbidden)
    after = (model["pooler"](features), model["classifier"](features))
    for old, new in zip(before, after, strict=True):
        assert torch.allclose(old, new, atol=1e-6)
