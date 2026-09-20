from dataclasses import dataclass, field

import numpy as np


@dataclass
class PlayerInput:
    player_id: int
    aim: tuple[float, float] | None = None
    fire: bool = False
    special: bool = False
    connected: bool = True
    aim_age: float = 0.0
    special_pressed: bool = False


@dataclass
class FeedbackEvent:
    player_id: int
    event_id: int
    kind: str = "hit"
    duration_ms: int = 200


@dataclass(frozen=True)
class VisionSnapshot:
    timestamp: float = 0.0
    aims: dict[int, tuple[float, float]] = field(default_factory=dict)
    confidence: dict[int, float] = field(default_factory=dict)
    walls: np.ndarray | None = None
    preview: np.ndarray | None = None
    calibrated: bool = False
    error: str = "Camera starting"
