"""Render the deterministic SVG application mark to a multi-size Windows ICO."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _render_png(renderer: QSvgRenderer, size: int) -> bytes:
    """Render one square SVG representation as an in-memory PNG."""

    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, size, size))
    finally:
        painter.end()

    encoded = QByteArray()
    buffer = QBuffer(encoded)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("Qt could not open the in-memory icon buffer.")
    try:
        if not image.save(buffer, "PNG"):
            raise RuntimeError(f"Qt could not encode the {size}x{size} icon image.")
    finally:
        buffer.close()
    return bytes(encoded)


def _encode_ico(images: list[tuple[int, bytes]]) -> bytes:
    """Encode PNG representations into a standards-compliant ICO container."""

    header = struct.pack("<HHH", 0, 1, len(images))
    image_offset = len(header) + (16 * len(images))
    directory_entries: list[bytes] = []
    payloads: list[bytes] = []

    for size, payload in images:
        dimension = 0 if size == 256 else size
        directory_entries.append(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(payload),
                image_offset,
            )
        )
        payloads.append(payload)
        image_offset += len(payload)

    return b"".join((header, *directory_entries, *payloads))


def generate_icon(source: Path, target: Path) -> None:
    """Render the source SVG into a deterministic multi-resolution ICO."""

    renderer = QSvgRenderer(QByteArray(source.read_bytes()))
    if not renderer.isValid():
        raise RuntimeError(f"Invalid SVG source: {source}")
    images = [(size, _render_png(renderer, size)) for size in ICON_SIZES]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_encode_ico(images))


def _build_parser(project_root: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=project_root / "sequence_renamer" / "assets" / "renamr.svg",
        help="SVG source path.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / "assets" / "Renamr.ico",
        help="ICO output path.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    project_root = Path(__file__).resolve().parents[1]
    options = _build_parser(project_root).parse_args(argv)
    application = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    generate_icon(options.source.resolve(), options.output.resolve())
    print(options.output.resolve())
    del application
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
