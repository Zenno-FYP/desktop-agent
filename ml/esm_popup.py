"""ESM (Experience Sampling Method) popup handler for collecting ground-truth verification."""
import tkinter as tk
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ESMPopup:
    """
    Experience Sampling Method popup handler for immediate verification.
    
    When BlockEvaluator detects low-confidence predictions, immediately displays
    a verification popup (if rate limits allow). User verifies while context is fresh.
    Rate limited to 1 per 30 minutes and max 20/day to prevent notification fatigue.
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
        
        logger.info("[ESM] Popup handler initialized (rate limit: 1 per 30 min, max 20/day)")

    def queue_for_verification(self, log_ids: list, context_state: str, confidence: float):
        """Immediately show verification popup if confidence is low and rate limits allow.
        
        Args:
            log_ids: List of log IDs to verify
            context_state: ML's predicted context ("Focused", "Reading", etc)
            confidence: ML's confidence score (0.0-1.0)
        """
        if confidence < self.confidence_threshold:
            # Check rate limits before showing
            if self._check_rate_limit():
                logger.debug(f"[ESM] Showing popup: {context_state} ({confidence:.0%})")
                self._display_toast(log_ids, context_state, confidence)
            else:
                logger.info(f"[ESM] Rate limited, skipping popup (confidence={confidence:.0%})")


    def _check_rate_limit(self) -> bool:
        """Enforce 30-minute minimum interval and 20 max/day limits.
        
        Returns:
            True if we can show a popup, False if rate limited
        """
        now = datetime.now()
        
        # Reset daily counter at midnight
        if now.date() > self.last_reset_date:
            self.popups_today = 0
            self.last_reset_date = now.date()
            logger.debug("[ESM] Daily counter reset at midnight")
        
        # Check minimum interval cooldown (30 minutes = 1800 seconds)
        if (now.timestamp() - self.last_popup_time) < self.min_interval_seconds:
            minutes_remaining = (self.min_interval_seconds - (now.timestamp() - self.last_popup_time)) / 60
            logger.debug(f"[ESM] Cooldown: {minutes_remaining:.1f} minutes remaining")
            return False
        
        # Check daily max
        if self.popups_today >= self.daily_max:
            logger.debug(f"[ESM] Daily max reached: {self.popups_today}/{self.daily_max}")
            return False
        
        return True

    def _display_toast(self, log_ids: list, context_state: str, confidence: float):
        """Display tkinter borderless toast window with 4 clickable buttons.
        
        Args:
            log_ids: List of log IDs this verification applies to
            context_state: ML's predicted context
            confidence: ML's confidence score
        """
        try:
            # Create hidden root window (necessary for tkinter to work)
            root = tk.Tk()
            root.withdraw()  # Hide it initially
            root.attributes('-alpha', 0)  # Make invisible
            
            # Create toast window
            toast = tk.Toplevel(root)
            toast.attributes('-topmost', True)  # Always on top
            toast.geometry(self.window_geometry)  # From config
            toast.attributes('-alpha', 0.95)  # Slight transparency
            toast.resizable(False, False)
            
            # Configure styling (from config)
            toast.configure(bg=self.color_bg)
            
            # Message frame
            msg_frame = tk.Frame(toast, bg=self.color_bg)
            msg_frame.pack(pady=12, padx=15)
            
            # Title
            title = tk.Label(
                msg_frame,
                text="ZENNO Activity Check",
                font=self.font_title,
                fg=self.color_text,
                bg=self.color_bg
            )
            title.pack()
            
            # Prediction message
            msg = f"{context_state} ({confidence:.0%})?"
            msg_label = tk.Label(
                msg_frame,
                text=msg,
                font=self.font_msg,
                fg=self.color_subtext,
                bg=self.color_bg
            )
            msg_label.pack()
            
            # Subtext
            sub = tk.Label(
                msg_frame,
                text="(Is this correct?)",
                font=self.font_subtext,
                fg=self.color_subtext,
                bg=self.color_bg
            )
            sub.pack()
            
            # Button frame
            button_frame = tk.Frame(toast, bg=self.color_bg)
            button_frame.pack(pady=10, padx=10)
            
            # Store selected button
            selected = {'label': None}
            
            def on_button(label):
                """Handle button click - record verification and close."""
                selected['label'] = label
                self._record_verification(log_ids, label)
                self.last_popup_time = datetime.now().timestamp()
                self.popups_today += 1
                logger.info(f"[ESM] User verified: {label} (popups today: {self.popups_today}/20)")
                toast.destroy()
                root.destroy()
            
            # Create 4 buttons with colors from config
            button_labels = ['Focused', 'Reading', 'Distracted', 'Idle']
            for i, label in enumerate(button_labels):
                # Alternate button colors (positive for Focused/Reading, negative for Distracted/Idle)
                is_positive = i < 2
                bg_color = self.color_btn_pos if is_positive else self.color_btn_neg
                hover_color = self.color_btn_hover_pos if is_positive else self.color_btn_hover_neg
                
                btn = tk.Button(
                    button_frame,
                    text=label,
                    width=11,
                    font=self.font_button,
                    fg=self.color_text,
                    bg=bg_color,
                    activebackground=hover_color,
                    relief=tk.RAISED,
                    bd=1,
                    command=lambda l=label: on_button(l)
                )
                btn.grid(row=0, column=i, padx=4)
            
            # Auto-dismiss after configured time
            def auto_dismiss():
                if toast.winfo_exists():
                    logger.info("[ESM] Popup auto-dismissed after configured timeout")
                    toast.destroy()
                    root.destroy()
            
            toast.after(self.auto_dismiss_ms, auto_dismiss)
            
            # Show the window and wait for interaction
            root.deiconify()  # Make root visible briefly if needed
            toast.mainloop()
            
        except Exception as e:
            logger.error(f"[ESM] Error displaying toast: {e}")

    def _record_verification(self, log_ids: list, verified_label: str):
        """Record user's verification choice to database.
        
        Args:
            log_ids: List of log IDs to update
            verified_label: User's correction ("Focused", "Reading", "Distracted", "Idle")
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
