"""Privacy-gated capture for the Arduino monitor and local enrollment."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any

from purifier.vision.pipeline import CapturePermission, Model, Pipeline

FIRST_FRAME_DELAY_S = 1.0
FRAME_INTERVAL_S = 0.4


@dataclass(frozen=True)
class PrivacyInput:
    pin_bcm: int
    eligible_high: bool


@contextmanager
def privacy_inputs(
    proximity: PrivacyInput, shutter: PrivacyInput
) -> Iterator[Callable[[], bool]]:
    from gpiozero import DigitalInputDevice
    from gpiozero.pins.lgpio import LGPIOFactory

    with ExitStack() as stack:
        factory = LGPIOFactory()
        stack.callback(factory.close)
        # A disconnected input is biased to its ineligible state.
        nearby, opened = (
            stack.enter_context(
                DigitalInputDevice(
                    setting.pin_bcm,
                    pull_up=not setting.eligible_high,
                    pin_factory=factory,
                )
            )
            for setting in (proximity, shutter)
        )

        def eligible() -> bool:
            return nearby.is_active is True and opened.is_active is True

        yield eligible


class TimedCamera:
    def __init__(
        self,
        open_camera: Callable[[Callable[[], None]], AbstractContextManager[Any]],
        eligible: Callable[[], bool],
        first_frame_at: float,
        deadline: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.open_camera = open_camera
        self.eligible = eligible
        self.next_frame_at = first_frame_at
        self.deadline = deadline
        self.clock, self.sleep = clock, sleep
        self.stack = ExitStack()
        self.camera: Any = None

    def permitted(self) -> bool:
        return self.clock() < self.deadline and self.eligible() is True

    def check_permission(self) -> None:
        if not self.permitted():
            raise PermissionError("camera permission unavailable")

    def wait_for_frame(self) -> None:
        self.sleep(max(0.0, self.next_frame_at - self.clock()))

    def capture(self) -> FaceFrame:
        self.wait_for_frame()
        self.check_permission()
        if self.camera is None:
            self.camera = self.stack.enter_context(
                self.open_camera(self.check_permission)
            )
        self.check_permission()
        self.next_frame_at = self.clock() + FRAME_INTERVAL_S
        image = self.camera.capture_array("main")
        self.check_permission()
        return FaceFrame(image)

    def close(self) -> None:
        self.stack.close()
        self.camera = None


@dataclass
class FaceFrame:
    image: Any
    face: Any = None


class FaceBurstModel:
    def __init__(self, model: Any) -> None:
        self.model = model

    def quality(self, frame: FaceFrame) -> float | None:
        import cv2

        frame.image, frame.face = self.model.detect(frame.image)
        if frame.face is None:
            return None
        height, width = frame.image.shape[:2]
        x, y, w, h = (float(value) for value in frame.face[:4])
        left, top = max(0, int(x)), max(0, int(y))
        right, bottom = min(width, int(x + w)), min(height, int(y + h))
        if min(right - left, bottom - top) < 32:
            return None
        crop = frame.image[top:bottom, left:right]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        offset = abs((x + w / 2) / width - 0.5) + abs((y + h / 2) / height - 0.5)
        return sharpness * crop.shape[0] * crop.shape[1] / (1.0 + offset)

    def embedding(self, frame: FaceFrame) -> list[float]:
        return self.model.embed_face(frame.image, frame.face).tolist()


def identify_pour(
    camera: TimedCamera,
    model: Model,
    enrolled: dict[str, list[list[float]]],
    threshold: float,
    margin: float,
) -> str | None:
    camera.wait_for_frame()
    pipeline = Pipeline(
        camera,
        model,
        lambda: CapturePermission(True, camera.permitted(), True),
        threshold,
        margin,
    )
    result = pipeline.identify(enrolled)
    return result.user_id if result.status == "matched" else None
