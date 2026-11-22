import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


class IterStream(io.RawIOBase):
    """Wraps an iterator yielding bytes as a file object."""

    def __init__(self, iterator):
        super().__init__()
        self.iterator: Iterator[bytes] = filter(None, iterator)
        self.leftover: bytes | memoryview = b""

    def readable(self):
        return True

    def close(self):
        self.leftover = b""
        self.iterator = iter(())
        super().close()

    def _take(self, size):
        self._checkClosed()
        if size == 0:
            return b""
        chunk = self.leftover or next(self.iterator, b"")
        if size < 0 or size >= len(chunk):
            self.leftover = b""
            return chunk
        # Keep a view of the unread bytes until the response is exhausted.
        view = memoryview(chunk)
        self.leftover = view[size:]
        return view[:size]

    def readinto(self, b):
        dest = memoryview(b).cast("B")
        if dest.readonly:
            raise TypeError("readinto() requires a writable buffer")
        chunk = self._take(len(dest))
        dest[: len(chunk)] = chunk
        return len(chunk)

    readinto1 = readinto

    def read1(self, n=-1):
        return bytes(self._take(n))

    def peek(self, n):
        self._checkClosed()
        available = len(self.leftover)
        if available < n:
            chunks = [self.leftover] if available else []
            try:
                for chunk in self.iterator:
                    chunks.append(chunk)
                    available += len(chunk)
                    if available >= n:
                        break
            finally:
                # Preserve fetched bytes even if a later request fails.
                self.leftover = b"".join(chunks)
        return bytes(self.leftover[:n])
