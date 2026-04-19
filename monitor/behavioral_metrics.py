"""Track behavioral signals: typing intensity, clicks, deletion key presses."""
import logging
import time
import math
from typing import Dict, Any
from pynput import keyboard, mouse
from threading import Lock, Thread, Event


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
        self.modifier_keys = {'ctrl', 'alt', 'cmd'}  # Shift is now tracked in active_modifiers
        
        # Track active modifiers for combination detection (Ctrl+Z, Cmd+Z, Shift+Delete)
        self.active_modifiers = set()
        
        # Mouse metrics
        self.click_count = 0
        self.last_click_time = 0
        self.click_debounce_ms = click_debounce_ms  # From config
        self.mouse_movement_distance = 0  # Total pixels moved (Euclidean distance)
        self.last_mouse_x = None
        self.last_mouse_y = None

        # Scroll metrics — pynput emits one on_scroll per wheel notch (vertical
        # OR horizontal). We track the count separately from clicks so the
        # aggregator can later distinguish "active reading" (lots of scroll,
        # few clicks) from "active typing" or "navigation" (lots of clicks).
        self.scroll_event_count = 0
        
        # Deletion key metrics (Delete, Backspace, Ctrl+Z/Cmd+Z)
        self.deletion_key_count = 0
        
        # Time tracking
        self.start_time = time.time()
        self.last_activity_time = time.time()
        
        # Listeners (will be started/stopped as needed)
        self.keyboard_listener = None
        self.mouse_listener = None
        
        # Mouse movement sampling thread (100ms poll interval)
        self.mouse_movement_thread = None
        self.mouse_movement_stop_event = Event()
        self.movement_sample_interval = 0.1  # 100ms = 10Hz sampling

        self.logger = logging.getLogger(__name__)

    def start_listening(self):
        """Start keyboard and mouse listeners."""
        try:
            # Keyboard listener with on_release to track modifier state
            self.keyboard_listener = keyboard.Listener(on_press=self._on_key_press, on_release=self._on_key_release)
            self.keyboard_listener.start()
            
            # Mouse listener: clicks + scroll wheel. We deliberately do NOT
            # subscribe to on_move (that's polled by the sampler thread) so the
            # listener stays responsive even on high-DPI mice.
            self.mouse_listener = mouse.Listener(
                on_click=self._on_mouse_click,
                on_scroll=self._on_mouse_scroll,
            )
            self.mouse_listener.start()
            
            # Start background thread for mouse movement sampling (100ms intervals)
            self.mouse_movement_stop_event.clear()
            self.mouse_movement_thread = Thread(
                target=self._sample_mouse_movement,
                daemon=True,
                name="MouseMovementSampler"
            )
            self.mouse_movement_thread.start()

            self.logger.info("[BehavioralMetrics] Listeners started (keyboard, clicks) + MouseMovementSampler thread")
        except Exception as e:
            self.logger.exception("[BehavioralMetrics] Error starting listeners")

    def stop_listening(self):
        """Stop keyboard and mouse listeners."""
        try:
            # Stop mouse movement sampling thread
            if self.mouse_movement_thread:
                self.mouse_movement_stop_event.set()
                self.mouse_movement_thread.join(timeout=2)
                self.mouse_movement_thread = None
            
            # Stop keyboard listener
            if self.keyboard_listener:
                self.keyboard_listener.stop()
                self.keyboard_listener = None
            
            # Stop click listener
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
                if key_name in ['ctrl', 'cmd', 'shift']:
                    self.active_modifiers.add(key_name)
                elif key_name == 'alt':
                    self.active_modifiers.add(key_name)
                else:
                    # Determine if this is a deletion key
                    is_deletion_key = (
                        key_name in {'delete', 'backspace'} or
                        (key_name == 'z' and ('ctrl' in self.active_modifiers or 'cmd' in self.active_modifiers)) or
                        (key_name == 'delete' and 'shift' in self.active_modifiers)  # Shift+Delete
                    )
                    
                    # Count ALL keystrokes (including deletions) in key_count
                    # Deletion keys are valid keystrokes that should contribute to typing_intensity
                    if key_name not in self.modifier_keys:
                        self.key_count += 1
                    
                    # ALSO track deletion keys separately for correction_ratio calculation
                    # Track deletion keys: delete, backspace, ctrl+z, cmd+z, and shift+delete
                    # (These indicate editorial activity - corrections, undos, etc.)
                    if key_name in {'delete', 'backspace'}:
                        self.deletion_key_count += 1
                    # Detect Ctrl+Z or Cmd+Z (undo/redo)
                    elif key_name == 'z' and ('ctrl' in self.active_modifiers or 'cmd' in self.active_modifiers):
                        self.deletion_key_count += 1
                    # Detect Shift+Delete (permanent delete / cut)
                    elif key_name == 'delete' and 'shift' in self.active_modifiers:
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
                if key_name in ['ctrl', 'cmd', 'alt', 'shift']:
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

    def _on_mouse_scroll(self, x, y, dx, dy):
        """Handle mouse-wheel scroll event.

        pynput delivers one event per notch — `dx` is horizontal,
        `dy` is vertical. We count both as a single scroll event because the
        downstream signal we care about is "user is engaged with content",
        not the direction.
        """
        try:
            with self.lock:
                self.scroll_event_count += 1
                self.last_activity_time = time.time()
        except Exception:
            self.logger.exception("[BehavioralMetrics] Scroll error")

    def _sample_mouse_movement(self):
        """Background thread: Sample mouse position every 100ms and calculate distance.

        Uses polling (not event-based) for CPU efficiency.
        Calculates Euclidean distance between samples.
        Runs at 10Hz (100ms intervals).

        Sanity guards:
        - Distances <=1px are treated as noise (jitter).
        - Distances >MAX_REASONABLE_JUMP px in one 100ms tick are treated as
          a teleport (e.g. screen unlock, monitor hot-plug, RDP reconnect)
          and discarded — otherwise they would inflate the metric by tens
          of thousands of pixels per event.
        """
        # ~5000px in 100ms == ~50 m/s of pointer travel — safely above any
        # human flick on a 4K-wide setup but below the kind of jumps caused
        # by Win+L / display switch.
        MAX_REASONABLE_JUMP = 5000.0
        try:
            mouse_controller = mouse.Controller()

            while not self.mouse_movement_stop_event.is_set():
                try:
                    pos = mouse_controller.position
                    if pos is None:
                        # Locked screen / secure desktop — drop the previous
                        # anchor so the next sample doesn't compute a giant
                        # jump back to the visible cursor.
                        with self.lock:
                            self.last_mouse_x = None
                            self.last_mouse_y = None
                        self.mouse_movement_stop_event.wait(self.movement_sample_interval)
                        continue
                    current_x, current_y = pos

                    with self.lock:
                        if self.last_mouse_x is not None and self.last_mouse_y is not None:
                            dx = current_x - self.last_mouse_x
                            dy = current_y - self.last_mouse_y
                            distance = math.sqrt(dx * dx + dy * dy)

                            if 1.0 < distance < MAX_REASONABLE_JUMP:
                                self.mouse_movement_distance += distance
                            elif distance >= MAX_REASONABLE_JUMP:
                                self.logger.debug(
                                    "[MouseMovementSampler] Discarded teleport jump=%.1fpx",
                                    distance,
                                )

                        self.last_mouse_x = current_x
                        self.last_mouse_y = current_y

                    self.mouse_movement_stop_event.wait(self.movement_sample_interval)

                except Exception as e:
                    self.logger.warning(f"[MouseMovementSampler] Error during sampling: {e}")
                    self.mouse_movement_stop_event.wait(self.movement_sample_interval)

        except Exception:
            self.logger.exception("[MouseMovementSampler] Fatal error in sampling thread")



    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics and calculate rates.
        
        Returns:
            dict with typing_intensity (KPM), click_rate (CPM), deletion_key_presses (count),
            and mouse_movement_distance (total pixels moved)
        """
        with self.lock:
            current_time = time.time()
            elapsed_sec = max(current_time - self.start_time, 1)
            
            # Calculate rates per minute
            typing_intensity = (self.key_count / elapsed_sec) * 60
            mouse_click_rate = (self.click_count / elapsed_sec) * 60
            mouse_scroll_rate = (self.scroll_event_count / elapsed_sec) * 60

            return {
                'typing_intensity': round(typing_intensity, 2),
                'mouse_click_rate': round(mouse_click_rate, 2),
                'mouse_scroll_rate': round(mouse_scroll_rate, 2),
                'deletion_key_presses': self.deletion_key_count,
                'key_count': self.key_count,
                'click_count': self.click_count,
                'mouse_scroll_events': self.scroll_event_count,
                'mouse_movement_distance': round(self.mouse_movement_distance, 2),  # Total pixels moved
                'elapsed_sec': elapsed_sec,
            }

    def reset(self):
        """Reset all metrics for new session."""
        with self.lock:
            self.key_count = 0
            self.click_count = 0
            self.scroll_event_count = 0
            self.deletion_key_count = 0
            self.mouse_movement_distance = 0
            self.last_click_time = 0
            self.last_mouse_x = None
            self.last_mouse_y = None
            self.active_modifiers.clear()
            self.start_time = time.time()
            self.last_activity_time = time.time()

    def get_last_activity_time(self) -> float:
        """Get timestamp of last keyboard/mouse activity."""
        with self.lock:
            return self.last_activity_time