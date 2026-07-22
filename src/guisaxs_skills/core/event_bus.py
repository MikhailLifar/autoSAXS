from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, DefaultDict, List, Type


Handler = Callable[[Any], None]


@dataclass(frozen=True)
class Subscription:
    event_type: Type
    handler: Handler


class EventBus:
    """
    Minimal internal pub/sub bus.

    Note: handlers execute in the thread that calls `publish`. UI code should
    use Qt signal/slots or `QTimer.singleShot(0, ...)` if marshalling is needed.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._handlers: DefaultDict[Type, List[Handler]] = defaultdict(list)

    def subscribe(self, event_type: Type, handler: Handler) -> Subscription:
        with self._lock:
            self._handlers[event_type].append(handler)
        return Subscription(event_type=event_type, handler=handler)

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            handlers = self._handlers.get(sub.event_type, [])
            self._handlers[sub.event_type] = [h for h in handlers if h is not sub.handler]

    def publish(self, event: Any) -> None:
        event_type = type(event)
        with self._lock:
            handlers = list(self._handlers.get(event_type, []))
        for h in handlers:
            h(event)

