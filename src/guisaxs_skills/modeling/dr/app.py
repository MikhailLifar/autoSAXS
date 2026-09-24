from __future__ import annotations

from typing import Optional

from ..app_common import run_modeling_window
from ..context import ModelingContext
from ..ipc import ModelingIpcChild
from .window import DrModelingWindow


def run_dr_app(argv: Optional[list[str]] = None) -> int:
    def factory(ctx: ModelingContext, ipc: Optional[ModelingIpcChild]) -> DrModelingWindow:
        return DrModelingWindow(ctx, ipc=ipc)

    return run_modeling_window(
        prog="guisaxs-dr",
        modes=["mixture"],
        window_factory=factory,
        argv=argv,
    )
