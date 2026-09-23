from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from ..ingest.sample_revision import normalize_sample_path
from .sample import Sample
from .state import LiveviewIntakeMode


class SampleStore:
    """Sole owner of sample history and per-path boarding."""

    def __init__(self) -> None:
        self._history: List[Sample] = []
        self._by_key: Dict[str, Sample] = {}
        self._index: int = 0

    def clear(self) -> None:
        self._history.clear()
        self._by_key.clear()
        self._index = 0

    @property
    def index(self) -> int:
        return self._index

    @index.setter
    def index(self, value: int) -> None:
        n = len(self._history)
        if n == 0:
            self._index = 0
            return
        self._index = max(0, min(int(value), n - 1))

    def __len__(self) -> int:
        return len(self._history)

    def history(self) -> tuple[Sample, ...]:
        return tuple(self._history)

    def paths(self) -> tuple[str, ...]:
        return tuple(s.path for s in self._history)

    def current(self) -> Optional[Sample]:
        if not self._history:
            return None
        self.index = self._index
        return self._history[self._index]

    def current_path(self) -> str:
        s = self.current()
        return s.path if s is not None else ""

    def get(self, path: str) -> Optional[Sample]:
        return self._by_key.get(normalize_sample_path(path))

    def boarding_for(self, path: str) -> Optional[LiveviewIntakeMode]:
        s = self.get(path)
        return s.boarding if s is not None else None

    def remember(self, sample: Sample) -> Sample:
        """Upsert by path key; boarding/stem/revision from ``sample`` win."""
        key = sample.key
        existing = self._by_key.get(key)
        if existing is not None:
            updated = Sample(
                path=key,
                boarding=sample.boarding,
                stem=sample.stem or existing.stem,
                revision=sample.revision if sample.revision is not None else existing.revision,
            )
            self._by_key[key] = updated
            for i, s in enumerate(self._history):
                if s.key == key:
                    self._history[i] = updated
                    break
            return updated
        self._by_key[key] = sample
        return sample

    def remember_boarding(self, path: str, boarding: LiveviewIntakeMode) -> Sample:
        key = normalize_sample_path(path)
        existing = self._by_key.get(key)
        if existing is not None:
            return self.remember(
                Sample(
                    path=key,
                    boarding=boarding,
                    stem=existing.stem,
                    revision=existing.revision,
                )
            )
        return self.remember(Sample.from_path(key, boarding=boarding))

    def append_history(self, sample: Sample) -> bool:
        """
        Append to session history if not already present (by path).

        Dedup is immediate: an existing path keeps its slot and index is left
        unchanged (avoids off-by-one jumps when a sample is reprocessed).
        Boarding/stem on the existing entry are still upserted via ``remember``.

        Returns True if the ordered history list grew (new path appended).
        """
        key = sample.key
        already = any(s.key == key for s in self._history)
        remembered = self.remember(sample)
        if already:
            return False
        self._history.append(remembered)
        self._index = len(self._history) - 1
        return True

    def replace_history(self, samples: Iterable[Sample], *, index: int = 0) -> None:
        """
        Replace navigable history with ``samples`` (dedup by path, first slot kept,
        later duplicates only refresh boarding via ``remember``).
        """
        self.clear()
        for sample in samples:
            key = sample.key
            remembered = self.remember(sample)
            if any(s.key == key for s in self._history):
                continue
            self._history.append(remembered)
        self.index = index

    def at(self, index: int) -> Optional[Sample]:
        if not self._history:
            return None
        i = max(0, min(int(index), len(self._history) - 1))
        return self._history[i]
