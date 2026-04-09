from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

from .deps import (
    EventBus,
    EventType,
    IntegratorExtended,
    apply_batch,
    integrate_2d_to_1d,
    read_from_tiff,
    run_with_cache,
)
from .common import (
    PathExpressionArg,
    SingletonPathExpressionArg,
    coerce_path_expression,
    coerce_singleton_path_expression,
    expand_files_from_unwrapped,
)


def integrate(
    images: PathExpressionArg,
    integrator_dir: SingletonPathExpressionArg,
    output_dir: str = ".",
    *,
    npt: int = 1000,
    use_cache: bool = True,
) -> Dict[str, Union[str, List[str]]]:
    """
    Integrate 2D SAXS images to 1D curves (q, I, sigma) using a calibrated integrator produced by `calibrate`.
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    images = coerce_path_expression(images)
    integrator_dir = coerce_singleton_path_expression(integrator_dir)
    expanded_images = expand_files_from_unwrapped(images.unwrap(), kind="2d_tif")
    int_dir = integrator_dir.unwrap()[0]
    return _integrate_paths(
        input_paths={"images": expanded_images, "integrator_dir": int_dir},
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        npt=npt,
    )


@apply_batch(stem_from_keys="images", single_output_dir=True)
@run_with_cache(
    path_keys_for_hash=["images", "integrator_dir"],
    kwargs_for_hash_keys=["npt"],
    include_config_in_hash=False,
)
def _integrate_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = True,
    sample_index: int = 0,
    npt: int = 1000,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    images = input_paths.get("images")
    if isinstance(images, str):
        images = [images]
    if not images:
        raise ValueError("integrate requires input_paths['images']")
    integrator_dir = input_paths.get("integrator_dir")
    if isinstance(integrator_dir, list):
        integrator_dir = integrator_dir[0] if integrator_dir else None
    if not integrator_dir or not os.path.isdir(integrator_dir):
        raise FileNotFoundError("integrate requires input_paths['integrator_dir']")
    integrator = IntegratorExtended.from_disk(integrator_dir)
    os.makedirs(output_dir, exist_ok=True)
    integrated: List[str] = []
    for idx, im_path in enumerate(images):
        if event_bus:
            event_bus.publish(EventType.MESSAGE, {"text": f"Integration {idx + 1}/{len(images)}…"})
        data = read_from_tiff(im_path)
        base = os.path.splitext(os.path.basename(im_path))[0]
        dest = os.path.join(output_dir, f"int_{base}.dat")
        _q, _I, _sigma = integrate_2d_to_1d(integrator, data, npt=npt, destpath=dest)
        integrated.append(dest)
    return {"integrated_1d": integrated}

