"""One-shot Picamera2 capture and YuNet/SFace identification CLI."""

import argparse
import json
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

DEFAULT_THRESHOLD = 0.363


class FaceModel:
    def __init__(self, detector_path: Path, recognizer_path: Path) -> None:
        self.detector = cv2.FaceDetectorYN.create(str(detector_path), "", (320, 320))
        self.recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")

    def embedding(self, image: np.ndarray) -> np.ndarray | None:
        height, width = image.shape[:2]
        self.detector.setInputSize((width, height))
        _, faces = self.detector.detect(image)
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
    from picamera2 import Picamera2

    camera = Picamera2()
    frames = []
    try:
        camera.configure(camera.create_still_configuration())
        camera.start()
        time.sleep(1.0)
        for index in range(count):
            frames.append(camera.capture_array("main"))
            if index + 1 < count:
                time.sleep(delay_s)
    finally:
        camera.stop()
        camera.close()
    return frames


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("enroll", "recognize"):
        command = sub.add_parser(name)
        command.add_argument("--store", type=Path, required=True)
        command.add_argument("--yunet", type=Path, required=True)
        command.add_argument("--sface", type=Path, required=True)
        command.add_argument("--images", type=Path, nargs="*")
    enroll = sub.choices["enroll"]
    enroll.add_argument("--user", required=True)
    enroll.add_argument("--count", type=int, default=5)
    recognize = sub.choices["recognize"]
    recognize.add_argument("--count", type=int, default=5)
    recognize.add_argument("--delay", type=float, default=0.4)
    recognize.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args()
    if args.command == "enroll" and not 5 <= args.count <= 10:
        parser.error("enrollment count must be 5..10")
    model = FaceModel(args.yunet, args.sface)
    database: dict[str, Any] = json.loads(args.store.read_text()) if args.store.exists() else {}
    if args.command == "enroll":
        images = load_images(args.images) if args.images else capture_burst(args.count, 0.5)
        embeddings = [model.embedding(image) for image in images]
        if len(images) < 5 or any(vector is None for vector in embeddings):
            raise ValueError("enrollment requires 5..10 photos, each with a detectable face")
        database[args.user] = [vector.tolist() for vector in embeddings if vector is not None]
        args.store.parent.mkdir(parents=True, exist_ok=True)
        args.store.write_text(json.dumps(database))
        args.store.chmod(0o600)
        print(f"enrolled {args.user}: {len(embeddings)} photos")
    else:
        images = load_images(args.images) if args.images else capture_burst(args.count, args.delay)
        vectors = [vector for image in images if (vector := model.embedding(image)) is not None]
        user, score = identify(vectors, database, args.threshold)
        print(f"{user}" + (f" (cosine={score:.3f})" if score is not None else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
