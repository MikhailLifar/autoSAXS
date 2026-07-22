"""
Shared processing primitives (core layer).

Re-exports only from ``core`` submodules — never from ``skill`` or ``pipeline``.
"""

from .integrator import IntegratorExtended
from .utils import get_detector

__all__ = [
    "IntegratorExtended",
    "get_detector",
]
