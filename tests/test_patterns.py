"""Tests for Nuke-style frame-pattern parsing and rendering."""

from __future__ import annotations

import unittest

from sequence_renamer.patterns import (
    MAX_PADDING_WIDTH,
    PatternError,
    find_frame_tokens,
    normalize_pattern,
    render_frame_pattern,
)


class FramePatternTests(unittest.TestCase):
    def test_hash_padding_and_large_frames_are_not_truncated(self) -> None:
        self.assertEqual(render_frame_pattern("shot.####.exr", 7), "shot.0007.exr")
        self.assertEqual(render_frame_pattern("shot.####.exr", 12345), "shot.12345.exr")

    def test_printf_tokens_support_padded_and_unpadded_frames(self) -> None:
        self.assertEqual(render_frame_pattern("shot.%04d.exr", 25), "shot.0025.exr")
        self.assertEqual(render_frame_pattern("shot.%d.exr", 25), "shot.25.exr")

    def test_alias_is_normalized_and_rendered(self) -> None:
        self.assertEqual(normalize_pattern("shot.%04.exr"), "shot.%04d.exr")
        self.assertEqual(render_frame_pattern("shot.%04.exr", 9), "shot.0009.exr")

    def test_negative_frames_use_printf_field_width(self) -> None:
        self.assertEqual(render_frame_pattern("shot.####.exr", -1), "shot.-001.exr")
        self.assertEqual(render_frame_pattern("shot.%04d.exr", -12), "shot.-012.exr")

    def test_maximum_padding_width_is_accepted(self) -> None:
        pattern = "shot.%0255d.exr"
        token = find_frame_tokens(pattern)[0]

        self.assertEqual(MAX_PADDING_WIDTH, 255)
        self.assertEqual(token.width, 255)
        self.assertEqual(normalize_pattern(pattern), pattern)
        rendered = render_frame_pattern(pattern, 7)
        self.assertEqual(len(rendered.removeprefix("shot.").removesuffix(".exr")), 255)
        self.assertTrue(rendered.endswith("7.exr"))

    def test_excessive_printf_width_is_rejected_by_all_entry_points(self) -> None:
        pattern = "shot.%0256d.exr"
        calls = (
            lambda: find_frame_tokens(pattern),
            lambda: normalize_pattern(pattern),
            lambda: render_frame_pattern(pattern, 1),
        )

        for call in calls:
            with (
                self.subTest(call=call),
                self.assertRaisesRegex(PatternError, "cannot exceed 255"),
            ):
                call()

    def test_extremely_long_printf_width_is_rejected_before_integer_parsing(
        self,
    ) -> None:
        pattern = f"shot.%0{'9' * 10_000}d.exr"

        with self.assertRaisesRegex(PatternError, "cannot exceed 255"):
            find_frame_tokens(pattern)

    def test_excessive_hash_width_is_rejected(self) -> None:
        pattern = f"shot.{'#' * 256}.exr"

        with self.assertRaisesRegex(PatternError, "cannot exceed 255"):
            render_frame_pattern(pattern, 1)

    def test_find_tokens_reports_offsets_widths_and_aliases(self) -> None:
        tokens = find_frame_tokens("a.####.%08d.%02.png")
        self.assertEqual([token.text for token in tokens], ["####", "%08d", "%02"])
        self.assertEqual([token.width for token in tokens], [4, 8, 2])
        self.assertFalse(tokens[0].is_alias)
        self.assertTrue(tokens[2].is_alias)
        self.assertEqual(tokens[2].normalized, "%02d")

    def test_missing_token_has_an_explicit_error(self) -> None:
        with self.assertRaisesRegex(PatternError, "exactly one frame token"):
            normalize_pattern("shot.exr")

    def test_multiple_tokens_have_an_explicit_error(self) -> None:
        with self.assertRaisesRegex(PatternError, "2 frame tokens"):
            render_frame_pattern("shot.####.%04d.exr", 1001)

    def test_non_integer_frame_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "integer"):
            render_frame_pattern("shot.####.exr", 1.5)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
