"""Keep `tanh` off MKL's vector-math kernel (doc 13 R-27).

On AVX-only CPUs (no AVX2; the reference host is an Ivy Bridge) `mkl_vml_kernel_sTanh_Z0HAynn` in PyTorch's CPU build
intermittently dies with SIGILL. Pinning MKL with `MKL_CBWR` or `MKL_ENABLE_INSTRUCTIONS` did not prevent it. The
transformer stacks we load need `tanh` in exactly two places: BERT's pooler (`nn.Tanh`) and the RoBERTa/XLM-R
classification head (a direct `torch.tanh` call). Both are rewritten as `2·sigmoid(2x) − 1`. That is the same function,
and PyTorch computes sigmoid with its own vectorised kernel, not MKL's VML.
"""

from __future__ import annotations

import types
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import torch
from torch import nn


def sigmoid_tanh(x: torch.Tensor) -> torch.Tensor:
    return torch.sigmoid(x * 2).mul_(2).sub_(1)


class SigmoidTanh(nn.Module):
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return sigmoid_tanh(x)


def _classification_head_forward(self: Any, features: torch.Tensor, **_: Any) -> torch.Tensor:
    # transformers' RobertaClassificationHead / XLMRobertaClassificationHead.forward with tanh replaced
    x = features[:, 0, :]
    x = self.dropout(x)
    x = self.dense(x)
    x = sigmoid_tanh(x)
    x = self.dropout(x)
    return self.out_proj(x)  # type: ignore[no-any-return]


def avoid_mkl_tanh(model: nn.Module) -> int:
    """Rewrites every tanh in `model` in place; returns how many sites were replaced."""
    replaced = 0
    for parent in list(model.modules()):
        for name, child in list(parent.named_children()):
            if isinstance(child, nn.Tanh):
                setattr(parent, name, SigmoidTanh())
                replaced += 1
        if type(parent).__name__.endswith("ClassificationHead") and all(
            hasattr(parent, attr) for attr in ("dense", "dropout", "out_proj")
        ):
            parent.forward = types.MethodType(_classification_head_forward, parent)
            replaced += 1
    return replaced


@contextmanager
def patched_tanh() -> Iterator[None]:
    """For code that builds its own model copy (the ONNX export via optimum traces a fresh PyTorch model): while active,
    `torch.tanh` and `nn.Tanh` compute the sigmoid form, so the trace never reaches MKL's kernel and the exported graph
    contains Sigmoid instead of Tanh (M9)."""
    original_tanh, original_forward = torch.tanh, nn.Tanh.forward
    torch.tanh = sigmoid_tanh  # type: ignore[assignment]
    nn.Tanh.forward = lambda self, x: sigmoid_tanh(x)  # type: ignore[assignment,method-assign]
    try:
        yield
    finally:
        torch.tanh = original_tanh
        nn.Tanh.forward = original_forward  # type: ignore[method-assign]
