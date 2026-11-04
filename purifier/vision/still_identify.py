"""One-shot Picamera2 capture and YuNet/SFace identification CLI."""

import argparse
import json
import math
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np

DEFAULT_THRESHOLD = 0.363
FRAME_SIZE = (640, 480)


class FaceModel:
    def __init__(self, detector_path: Path, recognizer_path: Path) -> None:
        self.detector = cv2.FaceDetectorYN.create(str(detector_path), "", (320, 320))
        self.recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")

    def embedding(self, image: np.ndarray, *, verbose: bool = False,
                  frame_number: int = 1) -> np.ndarray | None:
        if (image.dtype != np.uint8 or image.ndim != 3
                or image.shape[2] != 3 or image.size == 0):
            raise ValueError("expected a nonempty uint8 BGR image")
        # Use the same image and coordinate system for detection and alignment.
        if (image.shape[1], image.shape[0]) != FRAME_SIZE:
            image = cv2.resize(image, FRAME_SIZE, interpolation=cv2.INTER_AREA)
        height, width = image.shape[:2]
        self.detector.setInputSize((width, height))
        _, faces = self.detector.detect(image)
        if verbose:
            count = 0 if faces is None else len(faces)
            print(f"frame {frame_number}: {width}x{height} BGR, faces={count}",
                  file=sys.stderr)
        if faces is None or len(faces) == 0:
            return None
        face = max(faces, key=lambda row: float(row[2] * row[3]))
        aligned = self.recognizer.alignCrop(image, face)
        vector = self.recognizer.feature(aligned).reshape(-1).astype(np.float32)
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(norm) or norm == 0:
            raise ValueError("invalid SFace embedding")
        return vector / norm


def identify(vectors: list[np.ndarray], enrolled: dict[str, list[list[float]]],
             threshold: float = DEFAULT_THRESHOLD) -> tuple[str, float | None]:
    if not vectors:
        return "no face detected", None
    scores: dict[str, list[float]] = {}
    for vector in vectors:
        query = vector / np.linalg.norm(vector)
        for user, samples in enrolled.items():
            if not samples:
                continue
            refs = np.asarray(samples, dtype=np.float32)
            refs /= np.linalg.norm(refs, axis=1, keepdims=True)
            scores.setdefault(user, []).append(float(np.max(refs @ query)))
    averages = {user: float(np.mean(values)) for user, values in scores.items()}
    if not averages:
        return "unknown", None
    user, score = max(averages.items(), key=lambda item: item[1])
    return (user if score >= threshold else "unknown"), score


def load_images(paths: list[Path]) -> list[np.ndarray]:
    images = []
    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"cannot read image: {path}")
        images.append(image)
    return images


def capture_burst(count: int, delay_s: float) -> list[np.ndarray]:
    if not 1 <= count <= 10 or not math.isfinite(delay_s) or delay_s < 0:
        raise ValueError("capture requires 1..10 frames and a finite nonnegative delay")
    from picamera2 import Picamera2

    camera = Picamera2()
    frames = []
    started = False
    try:
        # Picamera2 RGB888 yields BGR byte order in capture_array for OpenCV.
        camera.configure(camera.create_still_configuration(
            main={"size": FRAME_SIZE, "format": "RGB888"}))
        camera.start()
        started = True
        time.sleep(1.0)
        for index in range(count):
            frames.append(camera.capture_array("main"))
            if index + 1 < count:
                time.sleep(delay_s)
    finally:
        try:
            if started:
                camera.stop()
        finally:
            camera.close()
    return frames


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("enroll", "recognize"):
        command = sub.add_parser(name)
        command.add_argument("--store", type=Path, required=True)
        command.add_argument("--yunet", type=Path, required=True)
        command.add_argument("--sface", type=Path, required=True)
        command.add_argument("--images", type=Path, nargs="+")
        command.add_argument("--verbose", action="store_true",
                             help="Report dimensions, detection counts and elapsed time")
    enroll = sub.choices["enroll"]
    enroll.add_argument("--user", required=True)
    enroll.add_argument("--count", type=int, default=5)
    recognize = sub.choices["recognize"]
    recognize.add_argument("--count", type=int, default=5)
    recognize.add_argument("--delay", type=float, default=0.4)
    recognize.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args(argv)
    if args.command == "enroll" and not 5 <= args.count <= 10:
        parser.error("enrollment count must be 5..10")
    if args.command == "recognize":
        if not 1 <= args.count <= 10:
            parser.error("recognition count must be 1..10")
        if not math.isfinite(args.delay) or args.delay < 0:
            parser.error("delay must be finite and nonnegative")
        if not math.isfinite(args.threshold) or not -1 <= args.threshold <= 1:
            parser.error("threshold must be finite and between -1 and 1")
    if args.images is not None:
        minimum = 5 if args.command == "enroll" else 1
        if not minimum <= len(args.images) <= 10:
            parser.error(f"provide {minimum}..10 images")
    started_at = time.monotonic()
    try:
        return execute(args)
    except (OSError, ValueError, RuntimeError, ImportError, cv2.error) as exc:
        print(f"face identification failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.verbose:
            print(f"elapsed: {time.monotonic() - started_at:.2f}s", file=sys.stderr)


def execute(args: argparse.Namespace) -> int:
    model = FaceModel(args.yunet, args.sface)
    database: dict[str, Any] = json.loads(args.store.read_text()) if args.store.exists() else {}
    delay = 0.5 if args.command == "enroll" else args.delay
    images = load_images(args.images) if args.images else capture_burst(args.count, delay)
    embeddings = [model.embedding(image, verbose=args.verbose, frame_number=index)
                  for index, image in enumerate(images, start=1)]
    vectors = [vector for vector in embeddings if vector is not None]
    if args.verbose:
        print(f"usable frames: {len(vectors)}/{len(images)}", file=sys.stderr)
    if args.command == "enroll":
        if len(images) < 5 or any(vector is None for vector in embeddings):
            raise ValueError("enrollment requires 5..10 photos, each with a detectable face")
        database[args.user] = [vector.tolist() for vector in embeddings if vector is not None]
        args.store.parent.mkdir(parents=True, exist_ok=True)
        args.store.write_text(json.dumps(database))
        args.store.chmod(0o600)
        print(f"enrolled {args.user}: {len(embeddings)} photos")
    else:
        user, score = identify(vectors, database, args.threshold)
        print(f"{user}" + (f" (cosine={score:.3f})" if score is not None else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
