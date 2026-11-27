"""Constants and effect definitions for the Surplife integration."""
from __future__ import annotations

from dataclasses import dataclass, field

DOMAIN = "surplife"

DEFAULT_LED_COUNT = 50
IDLE_DISCONNECT_SECONDS = 60  # free the lights for the phone app / proxy slots

Color = tuple[float, float, float]  # hue degrees, saturation 0-100, value 0-100

RED: Color = (0.0, 100.0, 100.0)
GREEN: Color = (120.0, 100.0, 100.0)
BLUE: Color = (240.0, 100.0, 100.0)
ORANGE: Color = (25.0, 100.0, 100.0)
YELLOW: Color = (50.0, 100.0, 100.0)
GOLD: Color = (40.0, 85.0, 100.0)
WHITE: Color = (0.0, 0.0, 100.0)
WARM_WHITE: Color = (30.0, 65.0, 100.0)
ICE: Color = (195.0, 45.0, 100.0)

# Static patterns, repeated along the string
PATTERNS: dict[str, list[Color]] = {
    "Candy cane": [RED, WHITE],
    "Red & green": [RED, GREEN],
    "Red, green & white": [RED, GREEN, WHITE],
    "Classic multicolor": [RED, GREEN, BLUE, ORANGE, YELLOW],
    "Gold & white": [GOLD, WHITE],
    "Warm white": [WARM_WHITE],
    "Winter ice": [ICE, WHITE],
}


@dataclass(frozen=True)
class Animation:
    """An effect Home Assistant animates by sending a new frame every `interval` seconds."""

    kind: str                       # "chase", "twinkle", "alternate" or "fade"
    colors: list[Color]
    interval: float = 1.5
    sparkle: Color = WHITE          # twinkle only
    sparkle_share: float = 0.15     # twinkle only: share of pixels lit each frame
    frames: list[list[Color]] = field(default_factory=list)  # alternate only
    steps: int = 10                 # fade only: frames to fade up (and again to fade down)


ANIMATIONS: dict[str, Animation] = {
    "Candy cane chase": Animation("chase", [RED, RED, WHITE, WHITE]),
    "Christmas chase": Animation("chase", [RED, RED, GREEN, GREEN, WHITE, WHITE]),
    "Multicolor chase": Animation("chase", [RED, GREEN, BLUE, ORANGE, YELLOW]),
    "Christmas twinkle": Animation("twinkle", [RED, GREEN], interval=1.0),
    "Warm white twinkle": Animation("twinkle", [WARM_WHITE], interval=1.0, sparkle=WHITE,
                                    sparkle_share=0.2),
    "Red & green flip": Animation("alternate", [], interval=2.0,
                                  frames=[[RED, GREEN], [GREEN, RED]]),
    # Whole string fades up and down in red, then in green
    "R+G Fade": Animation("fade", [RED, GREEN], interval=0.06, steps=30),
}

# Animated scenes built into the Surplife controller: effect id + color list
SURPLIFE_SCENES: dict[str, tuple[int, list[Color]]] = {
    "Christmas": (0x66, [RED, WHITE, GREEN, WHITE]),
    "Christmas flow": (0x65, [RED, GREEN, WHITE]),
    "Rainbow": (0x65, [(0.0, 100.0, 100.0), (60.0, 100.0, 100.0), (120.0, 100.0, 100.0),
                       (180.0, 100.0, 100.0), (240.0, 100.0, 100.0), (300.0, 100.0, 100.0)]),
}
SURPLIFE_SCENE_BY_ID = {0x66: "Christmas", 0x65: "Rainbow"}
