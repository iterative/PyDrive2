import io


class IterStream(io.RawIOBase):
    """Wraps an iterator yielding bytes as a file object"""

    def __init__(self, iterator):  # pylint: disable=super-init-not-called
        self.iterator = iterator
        self.leftover: bytes | memoryview = b""

    def readable(self):
        return True

    def writable(self) -> bool:
        return False

    def _take(self, size):
        if size == 0:
            return b""
        chunk = self.leftover or next(self.iterator, b"")
        if size < 0 or size >= len(chunk):
            self.leftover = b""
            return chunk
        # A Drive response can be much larger than the caller's buffer.
        view = memoryview(chunk)
        self.leftover = view[size:]
        return view[:size]

    def readinto(self, b):
        chunk = self._take(len(b))
        b[: len(chunk)] = chunk
        return len(chunk)

    readinto1 = readinto

    def read1(self, n=-1):
        return bytes(self._take(n))

    def peek(self, n):
        available = len(self.leftover)
        if available < n:
            chunks = [self.leftover]
            for chunk in self.iterator:
                chunks.append(chunk)
                available += len(chunk)
                if available >= n:
                    break
            self.leftover = b"".join(chunks)
        return bytes(self.leftover[:n])
