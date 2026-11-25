"""Ephemeral capture pipeline with explicit model ports, not a guessed model API.

A model adapter must supply documented detector/alignment/embedding operations
and be benchmarked/licensed before use. No images are serialized or persisted.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from purifier.vision.matcher import Match, match


class Camera(Protocol):
    def capture(self) -> Any: ...
    def close(self) -> None: ...


class Model(Protocol):
    def quality(self, frame: Any) -> float | None: ...
    def embedding(self, frame: Any) -> Sequence[float]: ...


@dataclass(frozen=True)
class CapturePermission:
    dispense_active: bool
    proximity_eligible: bool
    shutter_open: bool

    @property
    def permitted(self) -> bool:
        return self.dispense_active and self.proximity_eligible and self.shutter_open


class Pipeline:
    def __init__(self, camera: Camera, model: Model,
                 permission: Callable[[], CapturePermission],
                 threshold: float, margin: float) -> None:
        self.camera, self.model, self.permission = camera, model, permission
        self.threshold, self.margin = threshold, margin

    def identify(self, enrolled: Mapping[str, Sequence[Sequence[float]]]) -> Match:
        frames: list[Any] = []
        try:
            try:
                for _ in range(3):
                    if not self.permission().permitted:
                        return Match('guest', None, 'privacy_blocked')
                    frames.append(self.camera.capture())
            finally:
                self.camera.close()
            candidates = [(self.model.quality(frame), index) for index, frame in enumerate(frames)]
            valid = [(score, index) for score, index in candidates if score is not None and math.isfinite(score)]
            if not valid or not self.permission().permitted:
                return Match('guest', None, 'no_quality_face')
            _, selected = max(valid)
            vector = self.model.embedding(frames[selected])
            if not self.permission().permitted:
                return Match('guest', None, 'privacy_blocked')
            return match(vector, enrolled, self.threshold, self.margin)
        except (OSError, ValueError):
            return Match('guest', None, 'unavailable')
        finally:
            frames.clear()
