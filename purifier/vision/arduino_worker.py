"""Bounded subprocess and local enrollment for Arduino dispense recognition."""

import argparse
import json
import math
import multiprocessing
import resource
import signal
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from uuid import UUID

from purifier.core.config import _pairs
from purifier.vision.arduino_capture import (
    FIRST_FRAME_DELAY_S,
    FaceBurstModel,
    PrivacyInput,
    TimedCamera,
    identify_pour,
    privacy_inputs,
)
from purifier.vision.embedding_store import EmbeddingStore, validate_label
from purifier.vision.enrollment import EnrollmentService, NoUsableFaceError

DEFAULT_RECOGNITION_DEADLINE_S = 5.0
MODEL_ID = "opencv-sface-2021dec"
DIMENSION = 128


@dataclass(frozen=True)
class Match:
    user_id: str
    label: str


Result = tuple[int, Match | None]


@dataclass(frozen=True)
class Job:
    pour_id: int
    started_at: float
    deadline: float


@dataclass(frozen=True)
class Settings:
    yunet: Path
    sface: Path
    directory: Path
    key: Path
    proximity: PrivacyInput | None
    shutter: PrivacyInput | None
    threshold: float = 0.60
    margin: float = 0.05
    shutter_unverified: bool = False
    recognition_deadline_s: float = DEFAULT_RECOGNITION_DEADLINE_S

    @classmethod
    def load(cls, path: Path) -> "Settings":
        raw = json.loads(path.read_text(), object_pairs_hook=_pairs)
        required = {"yunet", "sface", "directory", "key"}
        optional = {
            "proximity",
            "shutter",
            "threshold",
            "margin",
            "recognition_deadline_s",
        }
        if (
            not isinstance(raw, dict)
            or not required <= raw.keys()
            or raw.keys() - required - optional
        ):
            raise ValueError("invalid Arduino vision configuration fields")

        def configured_path(name: str) -> Path:
            value = raw.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"invalid {name} path")
            return path.parent.resolve() / Path(value).expanduser()

        def privacy(name: str) -> PrivacyInput | None:
            value = raw.get(name)
            if value is None:
                return None
            if (
                not isinstance(value, dict)
                or set(value) != {"pin_bcm", "eligible_high", "verified"}
                or type(value["pin_bcm"]) is not int
                or not 0 <= value["pin_bcm"] <= 27
                or type(value["eligible_high"]) is not bool
                or type(value["verified"]) is not bool
            ):
                raise ValueError(f"invalid {name} privacy input")
            if not value["verified"]:
                return None
            return PrivacyInput(value["pin_bcm"], value["eligible_high"])

        proximity, shutter = privacy("proximity"), privacy("shutter")
        if (
            proximity is not None
            and shutter is not None
            and proximity.pin_bcm == shutter.pin_bcm
        ):
            raise ValueError("proximity and shutter must use different BCM pins")
        threshold, margin = raw.get("threshold", 0.60), raw.get("margin", 0.05)
        if (
            type(threshold) not in (int, float)
            or not -1 <= threshold <= 1
            or type(margin) not in (int, float)
            or not 0 < margin <= 2
        ):
            raise ValueError("invalid threshold or runner-up margin")
        deadline_s = raw.get("recognition_deadline_s", DEFAULT_RECOGNITION_DEADLINE_S)
        if type(deadline_s) not in (int, float) or not 5 <= deadline_s <= 20:
            raise ValueError("recognition_deadline_s must be a number from 5 to 20")
        return cls(
            configured_path("yunet"),
            configured_path("sface"),
            configured_path("directory"),
            configured_path("key"),
            proximity,
            shutter,
            threshold,
            margin,
            shutter_unverified=raw.get("shutter") is not None and shutter is None,
            recognition_deadline_s=float(deadline_s),
        )

    def store(self) -> EmbeddingStore:
        return EmbeddingStore(self.directory, self.key, MODEL_ID, DIMENSION)


def vision_debug(started_at: float, now: float, message: str) -> None:
    print(
        f"Vision flow_at={started_at:.6f} elapsed_ms={(now - started_at) * 1000:.0f} "
        f"{message}",
        file=sys.stderr,
        flush=True,
    )


class Recognizer:
    def __init__(self, settings: Settings, *, debug: bool = False) -> None:
        self.settings = settings
        self.model: FaceBurstModel | None = None
        self.debug = debug

    def recognize(self, job: Job) -> Match | None:
        def report(message: str) -> None:
            if self.debug:
                vision_debug(job.started_at, time.monotonic(), message)

        proximity, shutter = self.settings.proximity, self.settings.shutter
        if self.settings.shutter_unverified:
            report("outcome=privacy_blocked")
            return None

        import cv2
        from cryptography.exceptions import InvalidTag

        from purifier.vision.still_identify import FaceModel, open_camera

        try:
            store = self.settings.store()
            enrolled, labels = {}, {}
            for path in sorted(store.directory.glob("*.enc")):
                vectors, label = store.load_labeled(path.stem)
                enrolled[path.stem] = vectors
                labels[path.stem] = label or path.stem
            if not enrolled:
                report("outcome=no_enrollments")
                return None
            if self.model is None:
                report("model_loading")
                cv2.setNumThreads(1)
                self.model = FaceBurstModel(
                    FaceModel(self.settings.yunet, self.settings.sface)
                )

            with privacy_inputs(shutter, proximity=proximity) as eligible:
                camera = TimedCamera(
                    open_camera,
                    eligible,
                    job.started_at + FIRST_FRAME_DELAY_S,
                    job.deadline,
                )
                user = identify_pour(
                    camera,
                    self.model,
                    enrolled,
                    self.settings.threshold,
                    self.settings.margin,
                    debug=report if self.debug else None,
                )
            return Match(user, labels[user]) if user is not None else None
        except (
            OSError,
            ValueError,
            RuntimeError,
            ImportError,
            InvalidTag,
            cv2.error,
        ):
            report("outcome=unavailable")
            return None


def run_worker(
    connection: Connection, recognize: Callable[[Job], Match | None], debug: bool
) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    cold = True
    with connection:
        while True:
            try:
                job: Job = connection.recv()
            except (EOFError, OSError):
                return
            if debug:
                vision_debug(
                    job.started_at,
                    time.monotonic(),
                    f"pour={job.pour_id} worker_ready cold={cold}",
                )
            cold = False
            if time.monotonic() >= job.deadline:
                continue
            match = None
            try:
                match = recognize(job)
            except ImportError:
                if debug:
                    vision_debug(
                        job.started_at,
                        time.monotonic(),
                        "outcome=dependencies_unavailable",
                    )
            # Leave expired jobs outstanding so the monitor kills the worker.
            if time.monotonic() >= job.deadline:
                continue
            try:
                connection.send((job.pour_id, match))
            except OSError:
                return


class RecognitionProcess:
    """A single worker and one outstanding request. Cancellation kills native hangs."""

    def __init__(
        self,
        recognize: Callable[[Job], Match | None],
        *,
        recognition_deadline_s: float = DEFAULT_RECOGNITION_DEADLINE_S,
        debug: bool = False,
    ) -> None:
        self.recognize = recognize
        self.recognition_deadline_s = recognition_deadline_s
        self.debug = debug
        self.context = multiprocessing.get_context("spawn")
        self.process: BaseProcess | None = None
        self.connection: Connection | None = None
        self.job: Job | None = None

    def start(self, pour_id: int, started_at: float) -> bool:
        if self.job is not None:
            return False
        job = Job(pour_id, started_at, started_at + self.recognition_deadline_s)
        if self.process is not None and not self.process.is_alive():
            self.stop()
            self.process.close()
            self.process = None
        if self.connection is None:
            if self.process is not None:
                return False
            try:
                parent, child = self.context.Pipe()
            except OSError:
                return False
            process = self.context.Process(
                target=run_worker, args=(child, self.recognize, self.debug), daemon=True
            )
            try:
                process.start()
            except (OSError, RuntimeError):
                parent.close()
                process.close()
                return False
            finally:
                child.close()
            self.process, self.connection = process, parent
        try:
            self.connection.send(job)
        except OSError:
            self.stop()
            return False
        self.job = job
        return True

    def poll(self, now: float) -> Result | None:
        if self.job is None or self.connection is None:
            return None
        job = self.job
        if now >= job.deadline:
            killed = self.stop()
            if self.debug:
                vision_debug(
                    job.started_at,
                    now,
                    f"pour={job.pour_id} outcome=deadline_expired "
                    f"worker_killed={killed}",
                )
            return job.pour_id, None
        try:
            if not self.connection.poll():
                return None
            result_id, match = self.connection.recv()
        except (EOFError, OSError):
            self.stop()
            if self.debug:
                vision_debug(
                    job.started_at,
                    now,
                    f"pour={job.pour_id} outcome=worker_disconnected",
                )
            return job.pour_id, None
        if result_id != job.pour_id:
            return None
        self.job = None
        return job.pour_id, match

    def cancel(self, pour_id: int) -> None:
        if self.job is not None and self.job.pour_id == pour_id:
            if self.debug:
                vision_debug(
                    self.job.started_at,
                    time.monotonic(),
                    f"pour={pour_id} outcome=cancelled",
                )
            self.stop()

    def stop(self) -> bool:
        self.job = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.process is not None and self.process.is_alive():
            self.process.kill()
            return True
        return False

    def close(self) -> None:
        self.stop()
        if self.process is not None:
            self.process.join(timeout=0.2)
            if not self.process.is_alive():
                self.process.close()
                self.process = None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    sub = parser.add_subparsers(dest="operation", required=True)
    enroll = sub.add_parser("enroll")
    enroll.add_argument("--user-id", required=True, type=UUID)
    enroll.add_argument("--label")
    enroll.add_argument("--samples", type=int, choices=range(3, 11), default=3)
    delete = sub.add_parser("delete")
    delete.add_argument("--user-id", required=True, type=UUID)
    args = parser.parse_args(argv)

    try:
        settings = Settings.load(args.config)
        user_id = str(args.user_id)
        if args.operation == "delete":
            settings.store().delete(user_id)
            print("Enrollment deleted")
            return 0
        if args.label is not None:
            validate_label(args.label)
        if settings.shutter_unverified:
            raise PermissionError(
                "the configured shutter must be verified for enrollment"
            )
    except (TypeError, ValueError, OSError, ImportError) as exc:
        parser.exit(1, f"{exc}\n")

    try:
        import cv2

        from purifier.vision.still_identify import FaceModel, open_camera
    except ImportError as exc:
        parser.exit(
            1,
            "Arduino vision dependencies are unavailable "
            f"({exc.name or 'import failed'})\n",
        )

    step = "preparing the encryption key and enrollment directory"
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        store = settings.store()
        step = "loading the YuNet and SFace models"
        cv2.setNumThreads(1)
        model = FaceBurstModel(FaceModel(settings.yunet, settings.sface))
        step = "checking the configured shutter"
        with privacy_inputs(settings.shutter) as eligible:
            step = "capturing samples or saving the encrypted enrollment"
            camera = TimedCamera(open_camera, eligible, time.monotonic(), math.inf)
            EnrollmentService(store, camera, model, camera.permitted).enroll(
                user_id, args.samples, label=args.label
            )
    except NoUsableFaceError as exc:
        print(
            f"Enrollment failed: {exc}. Face the camera in good light, "
            "move closer, and retry.",
            file=sys.stderr,
        )
        return 1
    except ImportError as exc:
        print(
            f"Enrollment failed while {step}: missing dependency "
            f"({exc.name or 'import failed'}).",
            file=sys.stderr,
        )
        return 1
    except (
        OSError,
        ValueError,
        RuntimeError,
        cv2.error,
    ) as exc:
        print(f"Enrollment failed while {step}: {exc}", file=sys.stderr)
        return 1
    print(f"Enrollment saved for {user_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
