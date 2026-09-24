from __future__ import annotations

from typing import Optional

from ..app_common import run_modeling_window
from ..context import ModelingContext
from ..ipc import ModelingIpcChild
from .window import ShapeModelingWindow


def run_shape_app(argv: Optional[list[str]] = None) -> int:
    def factory(ctx: ModelingContext, ipc: Optional[ModelingIpcChild]) -> ShapeModelingWindow:
        return ShapeModelingWindow(ctx, ipc=ipc)

    return run_modeling_window(
        prog="guisaxs-shape",
        modes=["bodies", "dammif", "denss"],
        window_factory=factory,
        argv=argv,
    )
