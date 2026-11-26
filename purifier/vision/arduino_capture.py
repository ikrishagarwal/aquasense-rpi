"""Privacy-gated capture for the Arduino monitor and local enrollment."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any

from purifier.vision.pipeline import (
    CapturePermission,
    Model,
    Pipeline,
    RecognitionDeadlineExpired,
)

FIRST_FRAME_DELAY_S = 1.0
FRAME_INTERVAL_S = 0.4


@dataclass(frozen=True)
class PrivacyInput:
    pin_bcm: int
    eligible_high: bool


@contextmanager
def privacy_inputs(
    shutter: PrivacyInput | None = None, *, proximity: PrivacyInput | None = None
) -> Iterator[Callable[[], bool]]:
    if shutter is None and proximity is None:
        yield lambda: True
        return

    from gpiozero import DigitalInputDevice
    from gpiozero.exc import GPIOZeroError
    from gpiozero.pins.lgpio import LGPIOFactory
    from lgpio import error as LGPIOError

    try:
        with ExitStack() as stack:
            factory = LGPIOFactory()
            stack.callback(factory.close)

            def open_input(setting: PrivacyInput) -> DigitalInputDevice:
                # A disconnected input is biased to its ineligible state.
                return stack.enter_context(
                    DigitalInputDevice(
                        setting.pin_bcm,
                        pull_up=not setting.eligible_high,
                        pin_factory=factory,
                    )
                )

            opened = open_input(shutter) if shutter is not None else None
            nearby = open_input(proximity) if proximity is not None else None

            def eligible() -> bool:
                return (opened is None or opened.is_active is True) and (
                    nearby is None or nearby.is_active is True
                )

            yield eligible
    except (GPIOZeroError, LGPIOError) as exc:
        raise OSError("camera privacy input unavailable") from exc


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

    def check_deadline(self) -> None:
        if self.clock() >= self.deadline:
            raise RecognitionDeadlineExpired("recognition deadline expired")

    def check_permission(self) -> None:
        self.check_deadline()
        if self.eligible() is not True:
            raise PermissionError("camera permission unavailable")

    def wait_for_frame(self) -> None:
        wake_at = min(self.next_frame_at, self.deadline)
        self.sleep(max(0.0, wake_at - self.clock()))

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
    *,
    debug: Callable[[str], None] | None = None,
) -> str | None:
    camera.wait_for_frame()

    def permission() -> CapturePermission:
        camera.check_deadline()
        return CapturePermission(True, camera.eligible() is True, True)

    pipeline = Pipeline(
        camera,
        model,
        permission,
        threshold,
        margin,
        debug=debug,
    )
    result = pipeline.identify(enrolled)
    if debug:
        debug(f"outcome={result.status}")
    return result.user_id if result.status == "matched" else None
