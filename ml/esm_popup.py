"""ESM (Experience Sampling Method) popup handler for collecting ground-truth verification."""
import threading
import tkinter as tk
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ESMPopup:
    """
    Experience Sampling Method popup handler for immediate verification.
    
    When BlockEvaluator detects low-confidence predictions, immediately displays
    a verification popup (if rate limits allow). User verifies while context is fresh.
    Rate limits are configured via config.yaml.
    """

    def __init__(self, db, config=None, confidence_threshold: float = None):
        """Initialize ESM popup handler with rate limiting.
        
        Args:
            db: Database connection for recording verifications
            config: Config instance (requires ESM settings from config.yaml)
            confidence_threshold: Override config threshold (for testing/advanced use)
        """
        self.db = db
        self.config = config
        
        # Read settings from config.yaml
        esm_config = config.get("esm_popup")
        
        # Confidence threshold
        self.confidence_threshold = confidence_threshold or esm_config.get("confidence_threshold")
        
        # Rate limiting
        rate_limits = esm_config.get("rate_limiting")
        self.min_interval_seconds = rate_limits.get("min_interval_hours") * 3600
        self.daily_max = rate_limits.get("max_per_day")
        
        # UI settings
        ui_config = esm_config.get("ui")
        self.auto_dismiss_ms = ui_config.get("auto_dismiss_seconds") * 1000
        self.window_geometry = ui_config.get("window_geometry")
        
        # Colors
        colors = ui_config.get("colors")
        self.color_bg = colors.get("background")
        self.color_text = colors.get("text")
        self.color_subtext = colors.get("subtext")
        self.color_btn_pos = colors.get("button_positive")
        self.color_btn_neg = colors.get("button_negative")
        self.color_btn_hover_pos = colors.get("button_hover_pos")
        self.color_btn_hover_neg = colors.get("button_hover_neg")
        
        # Fonts
        fonts = ui_config.get("fonts")
        self.font_title = fonts.get("title")
        self.font_msg = fonts.get("message")
        self.font_subtext = fonts.get("subtext")
        self.font_button = fonts.get("button")
        
        # Rate limiting parameters (from config)
        self.last_popup_time = 0  # Unix timestamp of last popup
        self.popups_today = 0  # Counter for today
        self.last_reset_date = datetime.now().date()  # Daily counter reset

        logger.info(
            "[ESM] Popup handler initialized (min_interval_sec=%s, max_per_day=%s)",
            int(self.min_interval_seconds),
            self.daily_max,
        )

    def queue_for_verification(self, log_ids: list, context_state: str, confidence: float, block_metrics: dict = None):
        """Immediately show verification popup if confidence is low and rate limits allow.
        
        Args:
            log_ids: List of log IDs to verify
            context_state: Predicted context (ML taxonomy): Flow/Debugging/Research/Communication/Distracted
            confidence: ML's confidence score (0.0-1.0)
            block_metrics: Optional block metrics dict for showing signal details to user
        """
        if confidence >= self.confidence_threshold:
            return

        # Check rate limits before showing
        if not self._check_rate_limit():
            logger.info(f"[ESM] Rate limited, skipping popup (confidence={confidence:.0%})")
            return

        # Count the notification when it is shown (not when the user clicks).
        now_ts = datetime.now().timestamp()
        self.last_popup_time = now_ts
        self.popups_today += 1

        logger.debug(
            "[ESM] Showing popup: %s (%s) (popups today: %s/%s)",
            context_state,
            f"{confidence:.0%}",
            self.popups_today,
            self.daily_max,
        )

        # Run Tk in a background thread so the evaluator thread isn't blocked.
        threading.Thread(
            target=self._display_toast,
            args=(log_ids, context_state, confidence, block_metrics or {}),
            daemon=True,
            name="ESMToast",
        ).start()


    def _check_rate_limit(self) -> bool:
        """Enforce configured minimum interval and max/day limits.
        
        Returns:
            True if we can show a popup, False if rate limited
        """
        now = datetime.now()
        
        # Reset daily counter at midnight
        if now.date() > self.last_reset_date:
            self.popups_today = 0
            self.last_reset_date = now.date()
            logger.debug("[ESM] Daily counter reset at midnight")
        
        # Check minimum interval cooldown
        if (now.timestamp() - self.last_popup_time) < self.min_interval_seconds:
            minutes_remaining = (self.min_interval_seconds - (now.timestamp() - self.last_popup_time)) / 60
            logger.debug(f"[ESM] Cooldown: {minutes_remaining:.1f} minutes remaining")
            return False
        
        # Check daily max
        if self.popups_today >= self.daily_max:
            logger.debug(f"[ESM] Daily max reached: {self.popups_today}/{self.daily_max}")
            return False
        
        return True

    def _summarize_signals(self, block_metrics: dict) -> str:
        """Generate human-readable summary of key signals for popup display.
        
        Args:
            block_metrics: Block metrics dict with signals
            
        Returns:
            Short string summarizing key signals (e.g., "KPM: 85 | Velocity: 12 px/s")
        """
        signals = []
        
        # Signal 0: KPM
        kpm = block_metrics.get('typing_intensity', 0)
        if kpm > 0:
            signals.append(f"KPM:{kpm:.0f}")
        
        # Signal 2: Mouse velocity
        total_duration = block_metrics.get('total_duration_sec', 1)
        mouse_distance = block_metrics.get('mouse_movement_distance', 0)
        velocity = mouse_distance / total_duration if total_duration > 0 else 0
        if velocity > 0:
            signals.append(f"Velocity:{velocity:.1f}px/s")
        
        # Signal 7: Fatigue hours
        fatigue = block_metrics.get('consecutive_work_hours', 0)
        if fatigue > 0:
            signals.append(f"Fatigue:{fatigue:.1f}h")
        
        # Signal 1: Correction ratio
        total_keys = block_metrics.get('total_keystrokes', 1)
        deletions = block_metrics.get('deletion_key_presses', 0)
        if total_keys > 0:
            correction = (deletions / total_keys) * 100
            if correction > 5:
                signals.append(f"Corrections:{correction:.0f}%")
        
        return " | ".join(signals) if signals else ""

    def _display_toast(self, log_ids: list, context_state: str, confidence: float, block_metrics: dict = None):
        """Display tkinter popup window with 5 clickable context state buttons (ML taxonomy).
        
        Layout: 2x3 grid of buttons for easy selection with visual hierarchy
        - Top row: Flow, Debugging, Research
        - Bottom row: Communication, Distracted
        
        Args:
            log_ids: List of log IDs this verification applies to
            context_state: ML's predicted context
            confidence: ML's confidence score
            block_metrics: Optional block metrics for showing signal details
        """
        if block_metrics is None:
            block_metrics = {}
            
        try:
            # Create hidden root window (necessary for tkinter to work)
            root = tk.Tk()
            root.withdraw()  # Hide it initially
            root.attributes('-alpha', 0)  # Make invisible
            
            # Create popup window with refined styling
            popup = tk.Toplevel(root)
            popup.attributes('-topmost', True)  # Always on top
            popup.geometry("550x280+950+50")  # Slightly wider for 3-column layout
            popup.resizable(False, False)
            popup.configure(bg=self.color_bg, highlightthickness=1, highlightbackground="#444444")
            
            # ========== HEADER SECTION ==========
            header_frame = tk.Frame(popup, bg=self.color_bg, height=60)
            header_frame.pack(fill=tk.X, padx=0, pady=0)
            header_frame.pack_propagate(False)
            
            # Title
            title_label = tk.Label(
                header_frame,
                text="📊 What were you doing?",
                font=("Arial", 12, "bold"),
                fg=self.color_text,
                bg=self.color_bg
            )
            title_label.pack(pady=(10, 5), anchor=tk.W, padx=15)
            
            # Prediction message (highlighted)
            if block_metrics:
                signal_details = self._summarize_signals(block_metrics)
                msg = f"Detected: {context_state} ({confidence:.0%})"
                if signal_details:
                    msg += f"\n{signal_details}"
            else:
                msg = f"Detected: {context_state} ({confidence:.0%})"
            
            msg_label = tk.Label(
                header_frame,
                text=msg,
                font=("Arial", 9),
                fg="#cccccc",
                bg=self.color_bg,
                justify=tk.LEFT
            )
            msg_label.pack(anchor=tk.W, padx=15)
            
            # ========== CONTENT SECTION ==========
            content_frame = tk.Frame(popup, bg=self.color_bg)
            content_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=12)
            
            # Subtext
            instruction_label = tk.Label(
                content_frame,
                text="Is this correct? Click one:",
                font=("Arial", 9),
                fg="#999999",
                bg=self.color_bg
            )
            instruction_label.pack(anchor=tk.W, pady=(0, 10))
            
            # Store selected button
            selected = {'label': None}
            
            def on_button(label):
                """Handle button click - record verification and close."""
                selected['label'] = label
                self._record_verification(log_ids, label)
                logger.info(
                    "[ESM] User verified: %s (popups today: %s/%s)",
                    label,
                    self.popups_today,
                    self.daily_max,
                )
                popup.destroy()
                root.destroy()
            
            # ========== BUTTON GRID (2x3 layout) ==========
            button_frame = tk.Frame(content_frame, bg=self.color_bg)
            button_frame.pack(fill=tk.BOTH, expand=True)
            
            # Button configuration: label -> (row, col, is_positive)
            button_config = {
                'Flow': (0, 0, True),
                'Debugging': (0, 1, True),
                'Research': (0, 2, True),
                'Communication': (1, 1, True),
                'Distracted': (1, 0, False),
            }
            
            buttons = {}
            for label, (row, col, is_positive) in button_config.items():
                bg_color = self.color_btn_pos if is_positive else self.color_btn_neg
                hover_color = self.color_btn_hover_pos if is_positive else self.color_btn_hover_neg
                
                # Highlight predicted button with special styling
                is_predicted = (label == context_state)
                relief = tk.SUNKEN if is_predicted else tk.RAISED
                bd = 2 if is_predicted else 1
                
                btn = tk.Button(
                    button_frame,
                    text=label,
                    width=14,
                    height=2,
                    font=("Arial", 9, "bold" if is_predicted else "normal"),
                    fg="white",
                    bg=bg_color,
                    activebackground=hover_color,
                    relief=relief,
                    bd=bd,
                    cursor="hand2",
                    command=lambda l=label: on_button(l)
                )
                
                # Add hover effect
                def create_hover_effect(button, orig_bg, hover_bg):
                    def on_enter(event):
                        button.config(bg=hover_bg, relief=tk.SUNKEN)
                    def on_leave(event):
                        button.config(bg=orig_bg, relief=relief)
                    button.bind("<Enter>", on_enter)
                    button.bind("<Leave>", on_leave)
                    return (on_enter, on_leave)
                
                create_hover_effect(btn, bg_color, hover_color)
                
                btn.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
                buttons[label] = btn
            
            # Configure grid weights for balanced spacing
            button_frame.grid_rowconfigure(0, weight=1)
            button_frame.grid_rowconfigure(1, weight=1)
            for col in range(3):
                button_frame.grid_columnconfigure(col, weight=1)
            
            # ========== FOOTER SECTION ==========
            footer_frame = tk.Frame(popup, bg="#1a1a1a", height=25)
            footer_frame.pack(fill=tk.X, side=tk.BOTTOM)
            footer_frame.pack_propagate(False)
            
            footer_label = tk.Label(
                footer_frame,
                text="Auto-closing in 30 seconds | Privacy: Local verification only",
                font=("Arial", 7),
                fg="#666666",
                bg="#1a1a1a"
            )
            footer_label.pack(pady=4)
            
            # Auto-dismiss after configured time
            def auto_dismiss():
                if popup.winfo_exists():
                    logger.info("[ESM] Popup auto-dismissed after configured timeout")
                    popup.destroy()
                    root.destroy()
            
            popup.after(self.auto_dismiss_ms, auto_dismiss)
            
            # Show the window and wait for interaction
            root.deiconify()  # Make root visible briefly if needed
            popup.mainloop()
            
        except Exception as e:
            logger.error(f"[ESM] Error displaying popup: {e}")

    def _record_verification(self, log_ids: list, verified_label: str):
        """Record user's verification choice to database.
        
        Args:
            log_ids: List of log IDs to update
            verified_label: User's correction (Flow/Debugging/Research/Communication/Distracted)
        """
        try:
            for log_id in log_ids:
                success = self.db.update_log_verification(log_id, verified_label)
                if success:
                    logger.debug(f"[ESM] Recorded verification: log_id={log_id}, label={verified_label}")
                else:
                    logger.warning(f"[ESM] Failed to record verification for log_id={log_id}")
        except Exception as e:
            logger.error(f"[ESM] Error recording verification: {e}")

    def stop(self):
        """Graceful shutdown of ESM popup handler."""
        logger.info("[ESM] Popup handler stopped")
