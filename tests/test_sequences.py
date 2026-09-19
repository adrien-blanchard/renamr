"""Tests for frame discovery and stable image-sequence grouping."""

from __future__ import annotations

from pathlib import Path
import unittest

from sequence_renamer.sequences import detect_sequences


class SequenceDetectionTests(unittest.TestCase):
    def test_group_preserves_real_frames_gaps_and_selection_order(self) -> None:
        paths = [
            Path("plates/plate.1004.exr"),
            Path("plates/plate.1001.exr"),
            Path("plates/plate.1002.exr"),
        ]

        detected, groups = detect_sequences(paths)

        self.assertEqual([item.path for item in detected], paths)
        self.assertEqual([item.selection_index for item in detected], [0, 1, 2])
        self.assertEqual([item.frame_number for item in detected], [1004, 1001, 1002])
        self.assertEqual(len(groups), 1)
        self.assertEqual([item.path for item in groups[0].files], paths)
        self.assertEqual(groups[0].frames, (1001, 1002, 1004))
        self.assertEqual(groups[0].missing_frames, (1003,))
        self.assertEqual(groups[0].missing_frame_count, 1)

    def test_large_frame_span_counts_gaps_without_enumerating_them(self) -> None:
        _, groups = detect_sequences(
            [Path("plates/plate.000000.exr"), Path("plates/plate.200000.exr")]
        )

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].missing_frames, ())
        self.assertEqual(groups[0].missing_frame_count, 199_999)

    def test_last_relevant_group_is_frame_not_version(self) -> None:
        detected, groups = detect_sequences(
            [Path("shot010_comp_v003.1001.exr"), Path("shot010_comp_v003.1002.exr")]
        )

        self.assertEqual([item.frame_number for item in detected], [1001, 1002])
        self.assertEqual(detected[0].frame_text, "1001")
        self.assertEqual(detected[0].sequence_pattern, "shot010_comp_v003.####.exr")
        self.assertEqual(groups[0].prefix, "shot010_comp_v003.")
        self.assertTrue(all(item.detection_warning is None for item in detected))
        self.assertFalse(groups[0].ambiguous)

    def test_multiple_varying_numeric_fields_are_blocked_as_ambiguous(self) -> None:
        detected, groups = detect_sequences(
            [Path("shot.1001.part1.exr"), Path("shot.1002.part2.exr")]
        )

        self.assertTrue(all(item.detection_warning is not None for item in detected))
        self.assertTrue(
            all(
                "multiple numeric fields vary" in (item.detection_warning or "")
                for item in detected
            )
        )
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0].ambiguous)

    def test_numeric_frame_directly_appended_to_image_name_is_valid(self) -> None:
        detected, groups = detect_sequences(
            [Path("image1001.png"), Path("image1002.png")]
        )

        self.assertEqual([item.frame_number for item in detected], [1001, 1002])
        self.assertTrue(all(item.detection_warning is None for item in detected))
        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0].ambiguous)

    def test_short_take_identifiers_are_not_silently_treated_as_frames(self) -> None:
        detected, groups = detect_sequences([Path("take01.exr"), Path("take02.exr")])

        self.assertTrue(all(item.detection_warning is not None for item in detected))
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0].ambiguous)

    def test_delimited_padded_frames_remain_unambiguous(self) -> None:
        detected, groups = detect_sequences(
            [Path("render.0001.exr"), Path("render.0002.exr")]
        )

        self.assertEqual([item.frame_number for item in detected], [1, 2])
        self.assertTrue(all(item.detection_warning is None for item in detected))
        self.assertFalse(groups[0].ambiguous)

    def test_version_number_alone_is_not_a_frame(self) -> None:
        detected, groups = detect_sequences([Path("shot_comp_v003.exr")])

        self.assertFalse(detected[0].is_sequence)
        self.assertIsNone(detected[0].frame_number)
        self.assertEqual(groups, ())

    def test_png_and_exr_are_grouped_separately_case_insensitively(self) -> None:
        detected, groups = detect_sequences(
            [
                Path("render.0001.PNG"),
                Path("render.0002.png"),
                Path("render.0001.exr"),
                Path("notes.0001.txt"),
            ]
        )

        self.assertEqual(
            [item.is_sequence for item in detected], [True, True, True, False]
        )
        self.assertEqual(len(groups), 2)
        self.assertEqual([len(group.files) for group in groups], [2, 1])

    def test_padding_is_retained_in_detected_patterns(self) -> None:
        detected, _ = detect_sequences([Path("render.7.png"), Path("render.0025.png")])

        self.assertEqual(detected[0].sequence_pattern, "render.#.png")
        self.assertEqual(detected[1].sequence_pattern, "render.####.png")

    def test_negative_frames_and_hyphen_separator_are_distinguished(self) -> None:
        detected, groups = detect_sequences(
            [Path("render.-001.exr"), Path("render.0001.exr"), Path("render-0002.exr")]
        )

        self.assertEqual([item.frame_number for item in detected], [-1, 1, 2])
        self.assertEqual(detected[0].sequence_pattern, "render.####.exr")
        self.assertEqual(detected[2].sequence_pattern, "render-####.exr")
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].frames, (-1, 1))


if __name__ == "__main__":
    unittest.main()
