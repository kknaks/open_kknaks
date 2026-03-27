"""Byte stream to line-based buffering for PTY output."""


class LineBuffer:
    """Convert arbitrary byte chunks into complete lines.

    PTY returns arbitrary-length chunks. This assembles them into complete
    lines based on newline delimiters, handling incomplete UTF-8 sequences.
    """

    def __init__(self) -> None:
        self._buf: bytes = b""

    def feed(self, data: bytes) -> None:
        """Add bytes to the internal buffer."""
        self._buf += data

    def get_lines(self) -> list[str]:
        """Return complete lines and keep incomplete tail in buffer.

        - Splits on newline
        - Strips carriage return for mixed line endings
        - Decodes UTF-8 with errors='replace'
        - Filters empty lines
        """
        lines: list[str] = []
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            decoded = line.decode("utf-8", errors="replace").strip()
            if decoded:
                lines.append(decoded)
        return lines

    def flush(self) -> str | None:
        """Flush any remaining bytes in buffer as a final line.

        Returns None if buffer is empty.
        """
        if not self._buf:
            return None
        decoded = self._buf.decode("utf-8", errors="replace").strip()
        self._buf = b""
        return decoded if decoded else None
