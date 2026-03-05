"""Track behavioral signals: typing intensity, clicks, deletion key presses."""
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
        
        # Track active modifiers for combination detection (Ctrl+Z, Cmd+Z)
        self.active_modifiers = set()
        
        # Mouse metrics
        self.click_count = 0
        self.last_click_time = 0
        self.click_debounce_ms = click_debounce_ms  # From config
        
        # Deletion key metrics (Delete, Backspace, Ctrl+Z/Cmd+Z)
        self.deletion_key_count = 0
        
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
            # Keyboard listener with on_release to track modifier state
            self.keyboard_listener = keyboard.Listener(on_press=self._on_key_press, on_release=self._on_key_release)
            self.keyboard_listener.start()
            
            # Mouse listener
            self.mouse_listener = mouse.Listener(
                on_click=self._on_mouse_click
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
            # Extract key name OUTSIDE lock to minimize lock time
            key_name = None
            try:
                if hasattr(key, 'char') and key.char is not None:
                    key_name = key.char.lower()
                elif hasattr(key, 'name') and key.name is not None:
                    key_name = key.name.lower()
                else:
                    key_name = str(key).replace("Key.", "").replace("KeyCode(", "").replace(")", "").lower()
            except (AttributeError, TypeError):
                key_name = str(key).replace("Key.", "").replace("KeyCode(", "").replace(")", "").lower()
            
            # Only hold lock for counter update - minimal time
            with self.lock:
                # Track modifier state
                if key_name in ['ctrl', 'cmd']:
                    self.active_modifiers.add(key_name)
                elif key_name == 'alt':
                    self.active_modifiers.add(key_name)
                else:
                    # Regular key presses (not modifiers)
                    if key_name not in self.modifier_keys:
                        self.key_count += 1
                    
                    # Track deletion keys: delete, backspace, and ctrl+z / cmd+z
                    # (These indicate editorial activity - corrections, undos, etc.)
                    if key_name in {'delete', 'backspace'}:
                        self.deletion_key_count += 1
                    # Detect Ctrl+Z or Cmd+Z (undo/redo)
                    elif key_name == 'z' and ('ctrl' in self.active_modifiers or 'cmd' in self.active_modifiers):
                        self.deletion_key_count += 1
                
                self.last_activity_time = time.time()
        except Exception as e:
            self.logger.exception("[BehavioralMetrics] Key press error")

    def _on_key_release(self, key):
        """Handle keyboard release event (track modifier releases)."""
        try:
            # Extract key name
            key_name = None
            try:
                if hasattr(key, 'char') and key.char is not None:
                    key_name = key.char.lower()
                elif hasattr(key, 'name') and key.name is not None:
                    key_name = key.name.lower()
                else:
                    key_name = str(key).replace("Key.", "").replace("KeyCode(", "").replace(")", "").lower()
            except (AttributeError, TypeError):
                key_name = str(key).replace("Key.", "").replace("KeyCode(", "").replace(")", "").lower()
            
            # Remove from active modifiers when released
            with self.lock:
                if key_name in ['ctrl', 'cmd', 'alt']:
                    self.active_modifiers.discard(key_name)
                self.last_activity_time = time.time()
        except Exception as e:
            self.logger.exception("[BehavioralMetrics] Key release error")

    def _on_mouse_click(self, x, y, button, pressed):
        """Handle mouse click event."""
        try:
            if pressed:
                current_time = time.time()
                # Debounce check (fast, do outside lock first)
                time_since_last_click = (current_time - self.last_click_time) * 1000
                
                if time_since_last_click >= self.click_debounce_ms:
                    # Only acquire lock for update
                    with self.lock:
                        # Double-check after acquiring lock (another thread might have updated)
                        if (current_time - self.last_click_time) * 1000 >= self.click_debounce_ms:
                            self.click_count += 1
                            self.last_click_time = current_time
                            self.last_activity_time = current_time
                else:
                    # Still update activity time even if debounced
                    with self.lock:
                        self.last_activity_time = current_time
        except Exception as e:
            self.logger.exception("[BehavioralMetrics] Click error")



    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics and calculate rates.
        
        Returns:
            dict with typing_intensity (KPM), click_rate (CPM), deletion_key_presses (count)
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
                'deletion_key_presses': self.deletion_key_count,
                'key_count': self.key_count,
                'click_count': self.click_count,
                'elapsed_sec': elapsed_sec,
            }

    def reset(self):
        """Reset all metrics for new session."""
        with self.lock:
            self.key_count = 0
            self.click_count = 0
            self.deletion_key_count = 0
            self.last_click_time = 0
            self.active_modifiers.clear()
            self.start_time = time.time()
            self.last_activity_time = time.time()

    def get_last_activity_time(self) -> float:
        """Get timestamp of last keyboard/mouse activity."""
        with self.lock:
            return self.last_activity_time