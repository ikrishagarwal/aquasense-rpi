"""Ephemeral capture pipeline with explicit model ports, not a guessed model API.

A model adapter must supply documented detector/alignment/embedding operations
and be benchmarked/licensed before use. No images are serialized or persisted.
"""

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from purifier.vision.matcher import Match, match


class RecognitionDeadlineExpired(TimeoutError):
    """The recognition budget expired during capture or inference."""


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
    def __init__(
        self,
        camera: Camera,
        model: Model,
        permission: Callable[[], CapturePermission],
        threshold: float,
        margin: float,
        *,
        debug: Callable[[str], None] | None = None,
    ) -> None:
        self.camera, self.model, self.permission = camera, model, permission
        self.threshold, self.margin = threshold, margin
        self.debug = debug

    def identify(self, enrolled: Mapping[str, Sequence[Sequence[float]]]) -> Match:
        frames: list[Any] = []
        capture_at = time.monotonic()
        if self.debug:
            self.debug("capture_started")
        try:
            try:
                for _ in range(3):
                    if not self.permission().permitted:
                        return Match("guest", None, "privacy_blocked")
                    frames.append(self.camera.capture())
            finally:
                self.camera.close()
            if not self.permission().permitted:
                return Match("guest", None, "privacy_blocked")
            scoring_at = time.monotonic()
            if self.debug:
                self.debug(
                    f"capture_finished frames={len(frames)} "
                    f"duration_ms={(scoring_at - capture_at) * 1000:.0f}"
                )
                self.debug("scoring_started")
            candidates = [
                (self.model.quality(frame), index) for index, frame in enumerate(frames)
            ]
            valid = [
                (score, index)
                for score, index in candidates
                if score is not None and math.isfinite(score)
            ]
            embedding_at = time.monotonic()
            if self.debug:
                self.debug(
                    f"scoring_finished usable_frames={len(valid)} "
                    f"duration_ms={(embedding_at - scoring_at) * 1000:.0f}"
                )
            if not self.permission().permitted:
                return Match("guest", None, "privacy_blocked")
            if not valid:
                return Match("guest", None, "no_quality_face")
            _, selected = max(valid)
            if self.debug:
                self.debug("embedding_started")
            vector = self.model.embedding(frames[selected])
            if self.debug:
                self.debug(
                    f"embedding_finished duration_ms="
                    f"{(time.monotonic() - embedding_at) * 1000:.0f}"
                )
            if not self.permission().permitted:
                return Match("guest", None, "privacy_blocked")
            result = match(vector, enrolled, self.threshold, self.margin)
            if not self.permission().permitted:
                return Match("guest", None, "privacy_blocked")
            return result
        except RecognitionDeadlineExpired:
            return Match("guest", None, "deadline_expired")
        except PermissionError:
            return Match("guest", None, "privacy_blocked")
        except (OSError, ValueError):
            return Match("guest", None, "unavailable")
        finally:
            frames.clear()
