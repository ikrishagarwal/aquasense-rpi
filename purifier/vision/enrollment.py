"""Local vision-side enrollment operations; captures never cross the boundary."""

import argparse
import math
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from purifier.vision.embedding_store import EmbeddingStore
from purifier.vision.pipeline import Camera, Model


class EnrollmentService:
    def __init__(self, store: EmbeddingStore, camera: Camera, model: Model,
                 shutter_open: Callable[[], bool]) -> None:
        self.store, self.camera, self.model = store, camera, model
        self.shutter_open = shutter_open

    def enroll(self, user_id: str, samples: int = 3) -> None:
        UUID(user_id)
        if not 3 <= samples <= 10:
            raise ValueError('enrollment requires 3..10 samples')
        vectors: list[list[float]] = []
        frames = []
        try:
            for _ in range(samples):
                frames.clear()
                for _ in range(3):
                    if not self.shutter_open():
                        raise PermissionError('privacy shutter closed or unknown')
                    frames.append(self.camera.capture())
                candidates = [(self.model.quality(frame), index) for index, frame in enumerate(frames)]
                valid = [(score, index) for score, index in candidates
                         if score is not None and math.isfinite(score)]
                if not valid:
                    raise ValueError('no acceptable enrollment face')
                if not self.shutter_open():
                    raise PermissionError('privacy shutter closed or unknown')
                vectors.append(list(self.model.embedding(frames[max(valid)[1]])))
            if not self.shutter_open():
                raise PermissionError('privacy shutter closed or unknown')
            self.store.enroll(user_id, vectors)
        finally:
            frames.clear()
            vectors.clear()
            self.camera.close()

    def delete(self, user_id: str) -> None:
        self.store.delete(user_id)


def main() -> None:
    parser = argparse.ArgumentParser(description='Local vision-side enrollment deletion; no images or embeddings printed')
    parser.add_argument('operation', choices=['delete'])
    parser.add_argument('--user-id', required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--key', type=Path, required=True)
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--dimension', type=int, required=True)
    args = parser.parse_args()
    store = EmbeddingStore(args.directory, args.key, args.model_id, args.dimension)
    store.delete(args.user_id)
    print('enrollment deleted')


if __name__ == '__main__':
    main()
