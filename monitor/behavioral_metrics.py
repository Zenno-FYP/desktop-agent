"""Track behavioral signals: typing intensity, clicks, scrolls."""
import logging
import time
from typing import Dict, Any
from pynput import keyboard, mouse
from threading import Lock


class BehavioralMetrics:
    """Collect real-time behavioral signals from keyboard and mouse."""

    def __init__(self, click_debounce_ms: int = 50):
        """Initialize metrics trackers.
        
        Args:
            click_debounce_ms: Ignore clicks closer than this (ms) - prevents auto-clicks
        """
        self.lock = Lock()
        
        # Keyboard metrics
        self.key_count = 0
        self.modifier_keys = {'shift', 'ctrl', 'alt', 'cmd'}
        
        # Mouse metrics
        self.click_count = 0
        self.last_click_time = 0
        self.click_debounce_ms = click_debounce_ms  # From config
        
        # Scroll metrics
        self.scroll_count = 0
        
        # Time tracking
        self.start_time = time.time()
        self.last_activity_time = time.time()
        
        # Listeners (will be started/stopped as needed)
        self.keyboard_listener = None
        self.mouse_listener = None

        self.logger = logging.getLogger(__name__)

    def start_listening(self):
        """Start keyboard and mouse listeners."""
        try:
            # Keyboard listener
            self.keyboard_listener = keyboard.Listener(on_press=self._on_key_press)
            self.keyboard_listener.start()
            
            # Mouse listener
            self.mouse_listener = mouse.Listener(
                on_click=self._on_mouse_click,
                on_scroll=self._on_mouse_scroll
            )
            self.mouse_listener.start()

            self.logger.info("[BehavioralMetrics] Listeners started")
        except Exception as e:
            self.logger.exception("[BehavioralMetrics] Error starting listeners")

    def stop_listening(self):
        """Stop keyboard and mouse listeners."""
        try:
            if self.keyboard_listener:
                self.keyboard_listener.stop()
                self.keyboard_listener = None
            if self.mouse_listener:
                self.mouse_listener.stop()
                self.mouse_listener = None
            self.logger.info("[BehavioralMetrics] Listeners stopped")
        except Exception as e:
            self.logger.exception("[BehavioralMetrics] Error stopping listeners")

    def _on_key_press(self, key):
        """Handle keyboard press event."""
        try:
            with self.lock:
                # Get key name - handle both Key and KeyCode objects from pynput
                try:
                    # Try char attribute first (regular Keys)
                    if hasattr(key, 'char') and key.char is not None:
                        key_name = key.char.lower()
                    # Try name attribute (some Key objects)
                    elif hasattr(key, 'name') and key.name is not None:
                        key_name = key.name.lower()
                    # Fallback: convert to string and parse (for KeyCode objects)
                    else:
                        key_name = str(key).replace("Key.", "").replace("KeyCode(", "").replace(")", "").lower()
                except (AttributeError, TypeError):
                    # Last resort: convert to string if all else fails
                    key_name = str(key).replace("Key.", "").replace("KeyCode(", "").replace(")", "").lower()
                
                # Skip modifier keys
                if key_name not in self.modifier_keys:
                    self.key_count += 1
                
                self.last_activity_time = time.time()
        except Exception as e:
            self.logger.exception("[BehavioralMetrics] Key press error")

    def _on_mouse_click(self, x, y, button, pressed):
        """Handle mouse click event."""
        try:
            if pressed:
                with self.lock:
                    current_time = time.time()
                    
                    # Debounce rapid clicks (auto-clicks)
                    if (current_time - self.last_click_time) * 1000 >= self.click_debounce_ms:
                        self.click_count += 1
                        self.last_click_time = current_time
                    
                    self.last_activity_time = current_time
        except Exception as e:
            self.logger.exception("[BehavioralMetrics] Click error")

    def _on_mouse_scroll(self, x, y, button, delta):
        """Handle mouse scroll event."""
        try:
            with self.lock:
                self.scroll_count += 1
                self.last_activity_time = time.time()
        except Exception as e:
            self.logger.exception("[BehavioralMetrics] Scroll error")

    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics and calculate rates.
        
        Returns:
            dict with typing_intensity (KPM), click_rate (CPM), scroll_count
        """
        with self.lock:
            current_time = time.time()
            elapsed_sec = max(current_time - self.start_time, 1)
            
            # Calculate rates per minute
            typing_intensity = (self.key_count / elapsed_sec) * 60
            mouse_click_rate = (self.click_count / elapsed_sec) * 60
            
            return {
                'typing_intensity': round(typing_intensity, 2),
                'mouse_click_rate': round(mouse_click_rate, 2),
                'mouse_scroll_events': self.scroll_count,
                'key_count': self.key_count,
                'click_count': self.click_count,
                'elapsed_sec': elapsed_sec,
            }

    def reset(self):
        """Reset all metrics for new session."""
        with self.lock:
            self.key_count = 0
            self.click_count = 0
            self.scroll_count = 0
            self.last_click_time = 0
            self.start_time = time.time()
            self.last_activity_time = time.time()

    def get_last_activity_time(self) -> float:
        """Get timestamp of last keyboard/mouse activity."""
        with self.lock:
            return self.last_activity_time