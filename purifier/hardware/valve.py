"""Disabled adapter, with no GPIO imports or physical output access."""


class DisabledValve:
    def close(self) -> None:
        # No device was opened; this is a close request, not physical feedback.
        pass

    def open(self) -> None:
        raise RuntimeError("physical actuation disabled")
