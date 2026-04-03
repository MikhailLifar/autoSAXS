"""Event bus for decoupled component communication."""
from typing import Callable, Dict, List, Any
from enum import Enum


class EventType(Enum):
    """Types of events that can be published."""
    FILE_LOADED = "file_loaded"
    CALIBRATION_STARTED = "calibration_started"
    CALIBRATION_COMPLETE = "calibration_complete"
    CALIBRATION_ERROR = "calibration_error"
    PROCESSING_STARTED = "processing_started"
    PROCESSING_COMPLETE = "processing_complete"
    PROCESSING_ERROR = "processing_error"
    STATUS_UPDATE = "status_update"
    CONFIG_CHANGED = "config_changed"


class EventBus:
    """Simple event bus for decoupled communication between components."""
    
    def __init__(self):
        """Initialize the event bus."""
        self.subscribers: Dict[EventType, List[Callable]] = {}
    
    def subscribe(self, event_type: EventType, callback: Callable[[Any], None]):
        """
        Subscribe to an event type.
        
        Args:
            event_type: The type of event to subscribe to
            callback: Function to call when event is published (takes event data as argument)
        """
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(callback)
    
    def unsubscribe(self, event_type: EventType, callback: Callable[[Any], None]):
        """Unsubscribe from an event type."""
        if event_type in self.subscribers:
            try:
                self.subscribers[event_type].remove(callback)
            except ValueError:
                pass  # Callback not in list
    
    def publish(self, event_type: EventType, data: Any = None):
        """
        Publish an event to all subscribers.
        
        Args:
            event_type: The type of event
            data: Optional data to pass to subscribers
        """
        if event_type in self.subscribers:
            for callback in self.subscribers[event_type]:
                try:
                    callback(data)
                except Exception as e:
                    # Log error but don't break other subscribers
                    print(f"Error in event callback for {event_type}: {e}")

