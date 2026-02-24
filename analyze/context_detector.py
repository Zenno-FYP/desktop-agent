"""Detect context state (Focused, Reading, Distracted, Idle) from behavioral metrics."""
import sys
from pathlib import Path
from typing import Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.config import Config


class ContextDetector:
    """Detect developer's mental context from 5-minute block metrics.
    
    Uses Application Categorization to distinguish between:
    - Research/Debugging: Multiple app switches but all productive apps
    - True Distraction: Touching social media, messaging, entertainment apps
    - Focus: Deep work with minimal interruptions
    - Idle: No activity or thinking
    """
    
    # App categorization for smarter context detection
    PRODUCTIVITY_APPS = {
        'code', 'code.exe',           # VS Code
        'pycharm', 'pycharm.exe',     # PyCharm
        'idea', 'idea.exe',           # IntelliJ IDEA
        'sublime', 'sublime.exe',     # Sublime Text
        'vim', 'vi',                  # Vim
        'chrome', 'chrome.exe',       # Chrome (docs, research)
        'msedge', 'msedge.exe',       # Edge
        'firefox', 'firefox.exe',     # Firefox
        'terminal', 'terminal.exe',   # Terminal
        'cmd', 'cmd.exe',             # Windows Command Prompt
        'powershell', 'powershell.exe', # PowerShell
        'explorer', 'explorer.exe',   # File Explorer (code/artifact browsing)
        'visual studio', 'devenv.exe', # Visual Studio
        'git', 'git.exe',             # Git CLI
        'notepad', 'notepad.exe',     # Notepad
        'github desktop', 'gitkraken', # Git GUIs
        'slack',                      # Slack (work communication)
        'teams', 'teams.exe',         # Microsoft Teams (work communication)
    }
    
    DISTRACTION_APPS = {
        'discord', 'discord.exe',     # Discord personal
        'telegram', 'telegram.exe',   # Telegram
        'whatsapp', 'whatsapp.exe',   # WhatsApp
        'twitter', 'x.exe',           # Twitter/X
        'instagram', 'instagram.exe', # Instagram
        'tiktok', 'tiktok.exe',       # TikTok
        'youtube', 'youtube.exe',     # YouTube (unless research tab)
        'facebook', 'facebook.exe',   # Facebook
        'reddit', 'reddit.exe',       # Reddit
        'twitch', 'twitch.exe',       # Twitch
        'spotify', 'spotify.exe',     # Spotify (music distraction)
        'netflix', 'netflix.exe',     # Netflix
        'hulu', 'hulu.exe',           # Hulu
        'pinterest', 'pinterest.exe', # Pinterest
        'snapchat', 'snapchat.exe',   # Snapchat
        'messenger',                  # Facebook Messenger
    }
    
    def __init__(self, config: Config = None):
        """Initialize context detector.
        
        Args:
            config: Config instance for reading heuristic thresholds (optional).
                   If not provided, defaults will be used.
        """
        self.config = config or Config()
        
        # Load heuristic thresholds from config with defaults
        heuristics = self.config.get('heuristics', {})
        
        self.focused_kpm_min = heuristics.get('focused_kpm_min', 40)
        self.focused_cpm_min = heuristics.get('focused_cpm_min', 15)
        self.focused_app_switches_max = heuristics.get('focused_app_switches_max', 2)
        self.focused_confidence = heuristics.get('focused_confidence', 0.92)
        
        self.reading_kpm_max = heuristics.get('reading_kpm_max', 20)
        self.reading_cpm_max = heuristics.get('reading_cpm_max', 10)
        self.reading_scrolls_min = heuristics.get('reading_scrolls_min', 5)
        self.reading_confidence = heuristics.get('reading_confidence', 0.80)
        
        self.research_app_switches_min = heuristics.get('research_app_switches_min', 3)
        self.research_scrolls_min = heuristics.get('research_scrolls_min', 5)
        self.research_cpm_min = heuristics.get('research_cpm_min', 5)
        self.research_confidence = heuristics.get('research_confidence', 0.85)
        
        self.idle_ratio_threshold = heuristics.get('idle_ratio_threshold', 0.5)
        self.idle_confidence = heuristics.get('idle_confidence', 0.85)
        
        self.distracted_app_switches_min = heuristics.get('distracted_app_switches_min', 3)
        self.distracted_confidence = heuristics.get('distracted_confidence', 0.75)
        self.distracted_immediate_confidence = heuristics.get('distracted_immediate_confidence', 0.85)
        self.distracted_immediate_kpm_max = heuristics.get('distracted_immediate_kpm_max', 30)
    
    def _classify_app(self, app_name: str) -> str:
        """Classify an app as productive, distraction, or neutral.
        
        Args:
            app_name: Application name (e.g., "Code.exe", "Discord.exe")
        
        Returns:
            "productive", "distraction", or "neutral"
        """
        app_name_lower = app_name.lower() if app_name else ""
        
        # Check distraction apps first (high priority)
        if any(dist_app in app_name_lower for dist_app in self.DISTRACTION_APPS):
            return "distraction"
        
        # Check productivity apps
        if any(prod_app in app_name_lower for prod_app in self.PRODUCTIVITY_APPS):
            return "productive"
        
        return "neutral"
    
    def is_distraction_app(self, app_name: str) -> bool:
        """Check if app is known to be a distraction/entertainment app.
        
        Public convenience method for BlockEvaluator and other modules to check
        if an app visit represents a distraction without doing full classification.
        
        Args:
            app_name: Application name or path (e.g., "Discord.exe")
        
        Returns:
            True if app is classified as distraction, False otherwise
        """
        if not app_name:
            return False
        app_name_lower = app_name.lower()
        return any(dist_app in app_name_lower for dist_app in self.DISTRACTION_APPS)
    
    def detect_context(self, block_metrics: dict) -> Tuple[str, float]:
        """Evaluate developer's mental state for a 5-minute block.
        
        Args:
            block_metrics: Aggregated metrics from a 5-minute block containing:
                - typing_intensity: float - KPM (keystrokes per minute) for this block
                - mouse_click_rate: float - CPM (clicks per minute) for this block
                - mouse_scroll_events: int - Total scrolls in the entire block
                - idle_duration_sec: int - Total seconds idle during block
                - total_duration_sec: int - Total block duration (typically 300 sec)
                - app_switch_count: int - Number of different apps touched
                - project_switch_count: int - Number of different projects touched
                - touched_distraction_app: bool - Did they visit distraction apps? (optional)
        
        Returns:
            Tuple of (context_state, confidence_score) where:
                - context_state: str - One of "Focused", "Reading", "Distracted", "Idle"
                - confidence_score: float - Confidence (0.0-1.0)
        
        Example:
            >>> metrics = {
            ...     'typing_intensity': 45.0,
            ...     'mouse_click_rate': 12.5,
            ...     'mouse_scroll_events': 2,
            ...     'idle_duration_sec': 10,
            ...     'total_duration_sec': 300,
            ...     'app_switch_count': 1,
            ...     'project_switch_count': 1,
            ...     'touched_distraction_app': False,
            ... }
            >>> detector.detect_context(metrics)
            ('Focused', 0.92)
        """
        # Extract metrics
        idle_duration = block_metrics.get('idle_duration_sec', 0)
        total_duration = block_metrics.get('total_duration_sec', 1)
        kpm = block_metrics.get('typing_intensity', 0)
        cpm = block_metrics.get('mouse_click_rate', 0)
        scrolls = block_metrics.get('mouse_scroll_events', 0)
        app_switches = block_metrics.get('app_switch_count', 0)
        project_switches = block_metrics.get('project_switch_count', 0)
        touched_distraction = block_metrics.get('touched_distraction_app', False)
        
        # Calculate ratios
        idle_ratio = idle_duration / max(total_duration, 1)
        
        # --- DECISION TREE (in priority order) ---
        
        # 1. IMMEDIATE DISTRACTION: Touched Discord/WhatsApp while not actively typing
        #    (Strongest signal of actual distraction)
        if touched_distraction and kpm < self.distracted_immediate_kpm_max:
            return "Distracted", self.distracted_immediate_confidence
        
        # 2. HIGH IDLE RATIO: Developer away from keyboard
        #    (But may be thinking - real detection would use Experience Sampling)
        if idle_ratio > self.idle_ratio_threshold:
            return "Idle", self.idle_confidence
        
        # 3. READING: Low typing, low clicks, but active scrolling
        #    (Suggests reading documentation/articles/research)
        if (kpm < self.reading_kpm_max and cpm < self.reading_cpm_max and scrolls > self.reading_scrolls_min):
            return "Reading", self.reading_confidence
        
        # 4. FOCUSED: High typing, moderate clicks, few app switches
        #    (Suggests deep work on single project)
        if kpm > self.focused_kpm_min and cpm > self.focused_cpm_min and app_switches <= self.focused_app_switches_max:
            return "Focused", self.focused_confidence
        
        # 5. RESEARCH/DEBUGGING: Multiple app switches but NO distraction apps
        #    (Debugging involves VS Code → Browser → Terminal → Stack Overflow)
        #    (This is PRODUCTIVE context switching, not distraction)
        if app_switches >= self.research_app_switches_min and not touched_distraction:
            if scrolls > self.research_scrolls_min or cpm > self.research_cpm_min:
                # Reading across multiple productivity apps (documentation, research)
                return "Focused (Research)", self.research_confidence
            elif kpm > self.reading_kpm_max:
                # Active typing/coding across multiple apps
                return "Focused", 0.80
        
        # 6. DISTRACTED: Multiple app switches AND touched distraction apps
        #    (Genuine context switching with non-work apps)
        if app_switches >= self.distracted_app_switches_min and touched_distraction:
            return "Distracted", self.distracted_confidence
        
        # 7. MODERATE DISTRACTION: Multiple project switches without focus
        #    (Hopping between projects without deep work)
        if project_switches >= self.research_app_switches_min and kpm < self.distracted_immediate_kpm_max and not touched_distraction:
            return "Distracted", 0.70
        
        # 8. MODERATE ACTIVITY: Balanced typing/clicking but multiple app switches
        #    (Suggests active development with some context switching)
        if (kpm > self.reading_kpm_max and cpm > self.reading_cpm_max and app_switches >= 2 and not touched_distraction):
            return "Focused", 0.75
        
        # 9. LIGHT ACTIVITY: Low typing, low clicking, minimal scrolling
        #    (Suggests thinking/pausing or very light work)
        if (kpm < 15 and cpm < 8 and scrolls <= 2):
            return "Idle", 0.60
        
        # 10. DEFAULT FALLBACK: Unclassified activity
        return "Idle", 0.50
    
    def describe_classification(self, block_metrics: dict, 
                               context_state: str, confidence: float) -> str:
        """Generate human-readable explanation of classification.
        
        Useful for debugging and understanding why a block was classified a certain way.
        
        Args:
            block_metrics: The metrics that were evaluated
            context_state: The resulting context state
            confidence: The confidence score
        
        Returns:
            Human-readable explanation string
        """
        kpm = block_metrics.get('typing_intensity', 0)
        cpm = block_metrics.get('mouse_click_rate', 0)
        scrolls = block_metrics.get('mouse_scroll_events', 0)
        app_switches = block_metrics.get('app_switch_count', 0)
        touched_distraction = block_metrics.get('touched_distraction_app', False)
        idle_ratio = block_metrics.get('idle_duration_sec', 0) / max(block_metrics.get('total_duration_sec', 1), 1)
        
        signals = []
        
        # Typing signal
        if kpm > 40:
            signals.append(f"high typing ({kpm:.1f} KPM)")
        elif kpm > 20:
            signals.append(f"moderate typing ({kpm:.1f} KPM)")
        elif kpm < 15:
            signals.append(f"low typing ({kpm:.1f} KPM)")
        
        # Click signal
        if cpm > 15:
            signals.append(f"frequent clicks ({cpm:.1f} CPM)")
        elif cpm > 10:
            signals.append(f"moderate clicks ({cpm:.1f} CPM)")
        
        # Scroll signal
        if scrolls > 5:
            signals.append(f"active scrolling ({scrolls} events)")
        
        # App switch signal (with app type distinction)
        if app_switches >= 3:
            if touched_distraction:
                signals.append(f"many app switches + distraction apps ({app_switches})")
            else:
                signals.append(f"many productivity app switches ({app_switches})")
        elif app_switches >= 2:
            if touched_distraction:
                signals.append(f"some switches + distraction apps ({app_switches})")
            else:
                signals.append(f"some productivity switches ({app_switches})")
        
        # Idle signal
        if idle_ratio > 0.5:
            signals.append(f"mostly idle ({idle_ratio:.0%})")
        elif idle_ratio > 0.2:
            signals.append(f"some idle time ({idle_ratio:.0%})")
        
        signal_str = " + ".join(signals) if signals else "minimal activity"
        
        return f"{context_state} ({confidence:.0%}): {signal_str}"
