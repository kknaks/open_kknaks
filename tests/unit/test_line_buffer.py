"""Tests for LineBuffer."""

from open_kknaks.worker.line_buffer import LineBuffer


class TestLineBuffer:
    def test_single_complete_line(self) -> None:
        buf = LineBuffer()
        buf.feed(b"hello world\n")
        assert buf.get_lines() == ["hello world"]

    def test_multiple_lines(self) -> None:
        buf = LineBuffer()
        buf.feed(b"line1\nline2\nline3\n")
        assert buf.get_lines() == ["line1", "line2", "line3"]

    def test_incomplete_line_buffered(self) -> None:
        buf = LineBuffer()
        buf.feed(b"incom")
        assert buf.get_lines() == []
        buf.feed(b"plete\n")
        assert buf.get_lines() == ["incomplete"]

    def test_mixed_complete_and_incomplete(self) -> None:
        buf = LineBuffer()
        buf.feed(b"first\nsec")
        assert buf.get_lines() == ["first"]
        buf.feed(b"ond\n")
        assert buf.get_lines() == ["second"]

    def test_empty_lines_filtered(self) -> None:
        buf = LineBuffer()
        buf.feed(b"\n\nhello\n\n")
        assert buf.get_lines() == ["hello"]

    def test_crlf_line_endings(self) -> None:
        buf = LineBuffer()
        buf.feed(b"windows\r\nstyle\r\n")
        lines = buf.get_lines()
        assert lines == ["windows", "style"]

    def test_utf8_complete(self) -> None:
        buf = LineBuffer()
        buf.feed("한글 테스트\n".encode())
        assert buf.get_lines() == ["한글 테스트"]

    def test_utf8_split_across_chunks(self) -> None:
        text = "가나다\n"
        encoded = text.encode("utf-8")
        buf = LineBuffer()
        # Split in the middle of a 3-byte UTF-8 character
        buf.feed(encoded[:2])  # incomplete "가"
        assert buf.get_lines() == []
        buf.feed(encoded[2:])
        assert buf.get_lines() == ["가나다"]

    def test_invalid_utf8_replaced(self) -> None:
        buf = LineBuffer()
        buf.feed(b"\xff\xfe hello\n")
        lines = buf.get_lines()
        assert len(lines) == 1
        assert "hello" in lines[0]

    def test_flush_remaining(self) -> None:
        buf = LineBuffer()
        buf.feed(b"no newline")
        assert buf.get_lines() == []
        result = buf.flush()
        assert result == "no newline"

    def test_flush_empty(self) -> None:
        buf = LineBuffer()
        assert buf.flush() is None

    def test_flush_whitespace_only(self) -> None:
        buf = LineBuffer()
        buf.feed(b"   \t  ")
        assert buf.flush() is None

    def test_multiple_feeds(self) -> None:
        buf = LineBuffer()
        buf.feed(b"a")
        buf.feed(b"b")
        buf.feed(b"c\n")
        assert buf.get_lines() == ["abc"]

    def test_large_chunk(self) -> None:
        buf = LineBuffer()
        line = "x" * 10000
        buf.feed(f"{line}\n".encode())
        assert buf.get_lines() == [line]
