"""Authenticated encrypted enrollment files; no raw image persistence."""

import json
import os
import stat
import tempfile
from pathlib import Path
from uuid import UUID

from purifier.vision.matcher import normalize


class EmbeddingStore:
    def __init__(self, directory: Path, key_path: Path, model_id: str, dimension: int) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if not model_id or not 1 <= dimension <= 1024:
            raise ValueError('model identity and dimension required')
        # Never generate or silently replace an existing encryption key.
        fd = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError('key must be a private regular file (0600 or stricter)')
            key = os.read(fd, 33)
        finally:
            os.close(fd)
        if len(key) != 32:
            raise ValueError('key must contain exactly 32 random bytes')
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_mode & 0o077:
            raise ValueError('enrollment directory must be private (0700)')
        if directory.resolve() in key_path.resolve().parents:
            raise ValueError('encryption key must be outside enrollment directory')
        self.directory, self.model_id, self.dimension = directory, model_id, dimension
        self._cipher = AESGCM(key)

    def _path(self, user_id: str) -> Path:
        return self.directory / (str(UUID(user_id)) + '.enc')

    def _aad(self, user_id: str) -> bytes:
        return f'v1:{UUID(user_id)}:{self.model_id}:{self.dimension}'.encode()

    def _sync(self) -> None:
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def enroll(self, user_id: str, embeddings: list[list[float]]) -> None:
        path = self._path(user_id)
        if not path.exists() and sum(1 for _ in self.directory.glob('*.enc')) >= 100:
            raise ValueError('enrollment capacity reached')
        if not 3 <= len(embeddings) <= 10:
            raise ValueError('enrollment requires 3..10 samples')
        normalized = [normalize(vector) for vector in embeddings]
        if any(len(vector) != self.dimension for vector in normalized):
            raise ValueError('embedding dimension mismatch')
        plaintext = json.dumps(normalized, allow_nan=False).encode()
        nonce = os.urandom(12)
        ciphertext = nonce + self._cipher.encrypt(nonce, plaintext, self._aad(user_id))
        fd, temporary = tempfile.mkstemp(dir=self.directory, prefix='.enroll-')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(ciphertext)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._sync()
        finally:
            Path(temporary).unlink(missing_ok=True)

    def load(self, user_id: str) -> list[list[float]]:
        path = self._path(user_id)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            data = os.read(fd, 300001)
        finally:
            os.close(fd)
        if not 28 <= len(data) <= 300000:
            raise ValueError('invalid enrollment size')
        plaintext = self._cipher.decrypt(data[:12], data[12:], self._aad(user_id))
        vectors = json.loads(plaintext)
        if not isinstance(vectors, list) or not 3 <= len(vectors) <= 10:
            raise ValueError('invalid enrollment')
        result = [list(normalize(vector)) for vector in vectors]
        if any(len(vector) != self.dimension for vector in result):
            raise ValueError('embedding dimension mismatch')
        return result

    def delete(self, user_id: str) -> None:
        self._path(user_id).unlink(missing_ok=True)
        self._sync()
