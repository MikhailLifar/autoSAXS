from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class ViewerProtocol(Protocol):
    """
    Minimal plotting/viewing surface used across skills/pipeline.

    This is intentionally permissive (Any args/kwargs) to avoid tight coupling
    between high-level orchestration and plotting implementation details.
    """

    @staticmethod
    def show(duration: Optional[float] = None) -> None: ...

    @staticmethod
    def view_center(*args: Any, **kwargs: Any) -> Any: ...

    @staticmethod
    def view_rings(*args: Any, **kwargs: Any) -> Any: ...

    @staticmethod
    def view_refined_curve(*args: Any, **kwargs: Any) -> Any: ...

    @staticmethod
    def view_calibration(*args: Any, **kwargs: Any) -> Any: ...

    @staticmethod
    def view_mask(*args: Any, **kwargs: Any) -> Any: ...

    @staticmethod
    def view_curves(*args: Any, **kwargs: Any) -> Any: ...

    @staticmethod
    def plot_structure_and_scattering(*args: Any, **kwargs: Any) -> Any: ...

    @staticmethod
    def plot_3d_views_and_scattering(*args: Any, **kwargs: Any) -> Any: ...

