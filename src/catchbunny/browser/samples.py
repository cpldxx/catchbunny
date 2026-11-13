"""Files a tester can hand to an upload control.

They are made in memory, so nothing is ever read from the machine Catchbunny runs
on: an action records only a sample's name ("sample.png"), and the same name
produces the same bytes on every replay.

A name is `<variant>.<extension>`. The extension decides the content and the
type; the variant is one of the fuzzer's:

    sample   a small valid file of that type
    empty    the same name with no bytes in it
    odd      valid content under a name full of characters that break naive handling
    large    eight megabytes, the size an unguarded endpoint falls over on
"""

from __future__ import annotations

import struct
import zlib
from typing import Any

_ODD_NAME = "it's a \"test\" <1> & more"
_LARGE_BYTES = 8 * 1024 * 1024


def _png() -> bytes:
    """A valid 8x8 PNG, one flat colour."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    row = b"\x00" + bytes((36, 69, 214)) * 8
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(row * 8)) + chunk(b"IEND", b"")


_PDF_TEXT = b"BT /F1 18 Tf 24 72 Td (Catchbunny sample) Tj ET"


def _pdf() -> bytes:
    """A one-page PDF that says what it is."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(_PDF_TEXT), _PDF_TEXT),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


_TYPES: dict[str, tuple[str, bytes]] = {
    "png": ("image/png", _png()),
    "pdf": ("application/pdf", _pdf()),
    "csv": ("text/csv", b"name,quantity\nBlue Mug,2\nCanvas Tote,1\n"),
    "txt": ("text/plain", b"A sample file uploaded by a Catchbunny tester.\n"),
}

KINDS = {"image": "png", "pdf": "pdf", "csv": "csv", "text": "txt"}  # what a model may ask for


def extension(accept: str) -> str:
    """The sample type a control asks for in its `accept` attribute; plain text when it does not say."""
    want = accept.lower()
    if any(k in want for k in ("image", "png", "jpg", "jpeg", "gif", "webp")):
        return "png"
    if "pdf" in want:
        return "pdf"
    if any(k in want for k in ("csv", "excel", "spreadsheet")):
        return "csv"
    return "txt"


def pick(accept: str, variant: str) -> str:
    """The sample for one fuzz variant of one upload control."""
    prefix = {"empty": "empty", "special": "odd", "boundary": "large"}.get(variant, "sample")
    return f"{prefix}.{extension(accept)}"


def payload(name: str) -> dict[str, Any]:
    """A sample as Playwright takes it. An unknown name is a plain text sample: a replay never fails on its file."""
    variant, _, ext = name.rpartition(".")
    mime, body = _TYPES.get(ext, _TYPES["txt"])
    ext = ext if ext in _TYPES else "txt"
    if variant == "empty":
        body = b""
    elif variant == "large":
        body = body + b"\0" * (_LARGE_BYTES - len(body))
    shown = f"{_ODD_NAME}.{ext}" if variant == "odd" else f"{variant or 'sample'}.{ext}"
    return {"name": shown, "mimeType": mime, "buffer": body}
