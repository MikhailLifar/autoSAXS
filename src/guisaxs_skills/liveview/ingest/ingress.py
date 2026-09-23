"""Single front door for settled SampleRevision acceptance."""

from __future__ import annotations

from typing import Any, Callable

from .sample_revision import SampleRevision, SampleRevisionSource


class RevisionIngress:
    """
    Accept path for stable revisions: owned-output filter → boarding → enqueue →
    align detector caches. Detectors observe → settle → ``accept``.
    """

    def __init__(
        self,
        *,
        is_owned_output: Callable[[str], bool],
        remember_boarding: Callable[[str, Any], None],
        enqueue_revision: Callable[[SampleRevision], None],
        infer_boarding: Callable[[str], Any],
        current_intake: Callable[[], Any],
        frame_2d_boarding: Any,
        acknowledge_stat: Callable[[str, Any], None],
    ) -> None:
        self._is_owned = is_owned_output
        self._remember = remember_boarding
        self._enqueue = enqueue_revision
        self._infer = infer_boarding
        self._current_intake = current_intake
        self._frame_2d = frame_2d_boarding
        self._acknowledge = acknowledge_stat

    def accept(
        self,
        revision: SampleRevision,
        *,
        boarding: Any = None,
        boarding_from: str = "intake",
    ) -> None:
        """
        Accept a settled revision into the pipeline.

        ``boarding_from``: ``intake`` (flat watcher), ``infer`` (poll), ``frame_2d`` (tree),
        or pass explicit ``boarding``.
        """
        if revision.source != SampleRevisionSource.MANUAL:
            if self._is_owned(revision.path):
                return
        if boarding is None:
            if boarding_from == "infer":
                boarding = self._infer(revision.path)
            elif boarding_from == "frame_2d":
                boarding = self._frame_2d
            else:
                boarding = self._current_intake()
        self._remember(revision.path, boarding)
        self._enqueue(revision)
        self._acknowledge(revision.path, revision.stat)

    def accept_manual(
        self,
        revision: SampleRevision,
        *,
        boarding: Any,
    ) -> None:
        self._remember(revision.path, boarding)
        self._enqueue(revision)
        self._acknowledge(revision.path, revision.stat)
