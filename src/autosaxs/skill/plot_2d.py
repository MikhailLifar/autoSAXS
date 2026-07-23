from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

import matplotlib.pyplot as plt
import numpy as np

from .deps import (
    EventBus,
    EventType,
    _strip_sub_int_prefix,
    apply_batch,
    read_from_tiff,
    run_with_cache,
)
from .common import (
    ConfigPathExpressionArg,
    TiffPathExpressionArg,
    coerce_path_expression,
    expand_files_from_unwrapped,
)


def plot_2d(
    image: TiffPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: render 2D SAXS TIFF image(s) to PNG using log-intensity scaling (2D detector view).

    ### Arguments

    - `image` (str): 2D path expression (file/directory/glob). Directories expand to `*.tif` (non-recursive).
    - `output_dir` (str, default `.`): Directory where PNG(s) are written.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Returns

    `dict[str, str | list[str]]` with:

    - `plot_2d_png`: Path (or list of paths, if `image` is a directory) to generated PNG(s).

    ### Python usage

    ```python
    from autosaxs.skill import plot_2d

    out = plot_2d(
        image="raw/sample_01.tif",
        output_dir="plots_2d",
        use_cache=False,
    )

    print(out["plot_2d_png"])
    ```

    ### CLI usage

    ```bash
    autosaxs plot-2d raw/sample_01.tif --output-dir plots_2d
    ```
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    image_expr = coerce_path_expression(image)
    expanded_images = expand_files_from_unwrapped(image_expr.unwrap(), kind="2d_tif")
    for p in expanded_images:
        if Path(p).suffix.lower() != ".tif":
            raise ValueError("plot_2d input files must have .tif extension")
    input_batch = [{"image": p} for p in expanded_images]
    return _plot_2d_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
    )


@apply_batch(stem_from_keys="image", single_output_dir=True)
@run_with_cache(
    path_keys_for_hash=["image"],
    kwargs_for_hash=None,
    include_config_in_hash=False,
)
def _plot_2d_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    image = input_paths.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if not image or not os.path.isfile(image):
        raise FileNotFoundError("plot_2d requires input_paths['image']")

    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "Rendering 2D SAXS plot..."})

    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(image))[0])
    plot_2d_png = os.path.join(output_dir, f"plot_2d_{base}.png")

    img_data = read_from_tiff(image).astype(float)

    fig, ax = plt.subplots()
    ax.imshow(np.log1p(img_data), cmap="viridis", origin="lower")
    ax.set_title(f"2D SAXS Data: {os.path.basename(image)}")
    ax.set_xlabel("Pixel X")
    ax.set_ylabel("Pixel Y")
    fig.savefig(plot_2d_png)
    plt.close(fig)

    from autosaxs.core.report_fragments import write_skill_report_fragments

    md_lines = [
        "### 2D detector view\n",
        f"![2D log intensity]({os.path.basename(plot_2d_png)})\n",
    ]
    write_skill_report_fragments(
        output_dir,
        base,
        "plot_2d",
        "".join(md_lines),
        summary_references=[{"role": "plot_2d_png", "path": os.path.basename(plot_2d_png), "format": "png"}],
    )
    return {"plot_2d_png": plot_2d_png}

