import struct
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SVG = PROJECT_ROOT / "sequence_renamer" / "assets" / "renamr.svg"
SOURCE_SVG = PROJECT_ROOT / "assets" / "Renamr.svg"
WINDOWS_ICON = PROJECT_ROOT / "assets" / "Renamr.ico"
EXPECTED_ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]


def test_runtime_and_packaging_icons_share_the_same_monochrome_mark() -> None:
    source = SOURCE_SVG.read_text(encoding="utf-8")
    root = ElementTree.fromstring(source)
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    background = root.find("svg:rect", namespace)
    mark = root.find("svg:path", namespace)

    assert PACKAGE_SVG.read_text(encoding="utf-8") == source
    assert background is not None
    assert mark is not None
    assert background.attrib["fill"] == "#ffffff"
    assert mark.attrib["fill"] == "#000000"
    assert 'width="62"' in source
    assert 'height="62"' in source
    assert 'rx="12"' in source
    assert "stroke=" not in source
    assert "stroke-width=" not in source
    assert 'fill-rule="evenodd"' in source


def test_windows_icon_contains_every_required_resolution() -> None:
    payload = WINDOWS_ICON.read_bytes()
    reserved, image_type, image_count = struct.unpack_from("<HHH", payload)

    assert (reserved, image_type, image_count) == (0, 1, len(EXPECTED_ICON_SIZES))

    actual_sizes: list[int] = []
    for index in range(image_count):
        (
            encoded_width,
            encoded_height,
            palette_size,
            reserved_byte,
            color_planes,
            bits_per_pixel,
            byte_count,
            image_offset,
        ) = struct.unpack_from("<BBBBHHII", payload, 6 + (index * 16))

        width = encoded_width or 256
        height = encoded_height or 256
        actual_sizes.append(width)
        assert height == width
        assert palette_size == 0
        assert reserved_byte == 0
        assert color_planes == 1
        assert bits_per_pixel == 32
        assert image_offset + byte_count <= len(payload)

        png = payload[image_offset : image_offset + byte_count]
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        assert struct.unpack_from(">II", png, 16) == (width, height)

    assert actual_sizes == EXPECTED_ICON_SIZES


def test_windows_icon_is_generated_from_the_runtime_svg(tmp_path: Path) -> None:
    regenerated_icon = tmp_path / "Renamr.ico"

    subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "generate_icon.py"),
            "--source",
            str(PACKAGE_SVG),
            "--output",
            str(regenerated_icon),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert regenerated_icon.read_bytes() == WINDOWS_ICON.read_bytes()
