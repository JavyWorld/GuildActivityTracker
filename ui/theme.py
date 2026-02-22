from dataclasses import dataclass

@dataclass
class UITheme:
    bg: str = "#0B1020"
    panel: str = "#121A2D"
    panel_alt: str = "#0F172A"
    text: str = "#E5E7EB"
    muted: str = "#94A3B8"
    accent: str = "#38BDF8"
    ok: str = "#22C55E"
    warn: str = "#F59E0B"
    bad: str = "#EF4444"
    font: str = "Segoe UI"
