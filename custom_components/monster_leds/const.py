"""Constants for the Monster LEDs integration."""
from __future__ import annotations

DOMAIN = "monster_leds"
DEFAULT_LED_COUNT = 50
IDLE_DISCONNECT_SECONDS = 60  # free the lights for the phone app / proxy slots

RED = (0.0, 100.0, 100.0)
GREEN = (120.0, 100.0, 100.0)
WHITE = (0.0, 0.0, 100.0)

# Animated scenes, as sent by the Surplife app: effect id + color list
SCENES: dict[str, tuple[int, list[tuple[float, float, float]]]] = {
    "Christmas": (0x66, [RED, WHITE, GREEN, WHITE]),
    "Rainbow": (0x65, [(0.0, 100.0, 100.0), (60.0, 100.0, 100.0), (120.0, 100.0, 100.0),
                       (180.0, 100.0, 100.0), (240.0, 100.0, 100.0), (300.0, 100.0, 100.0)]),
}

# Static patterns, repeated along the string
PATTERNS: dict[str, list[tuple[float, float, float]]] = {
    "Candy cane": [RED, WHITE],
    "Red & green": [RED, GREEN],
    "Red, green & white": [RED, GREEN, WHITE],
}

EFFECT_LIST = [*SCENES, *PATTERNS]
SCENE_BY_ID = {effect_id: name for name, (effect_id, _) in SCENES.items()}
