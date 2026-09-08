"""Behaviour of the encoder's plate video key.

The agreement test proves this module and its TypeScript copy state the same
rule; these prove the rule is actually implemented, which that comparison
cannot see.
"""

from __future__ import annotations

import plate_video_path as pvp


class TestPlateVideoPath:
    def test_builds_the_key_the_existing_videos_live_under(self):
        # Objects already exist under this layout; changing it orphans them.
        assert pvp.plate_video_path(12, 3, "Plate_9") == "12/wave-3/Plate_9.mp4"

    def test_a_plate_with_no_wave_gets_its_own_segment(self):
        # An empty segment would give `12//Plate_9.mp4`.
        assert pvp.plate_video_path(12, None, "Plate_9") == "12/wave-none/Plate_9.mp4"

    def test_waves_stay_apart(self):
        """Plate ids repeat across waves; a shared key would overwrite."""
        assert pvp.plate_video_path(12, 2, "Plate_9") != pvp.plate_video_path(
            12, 3, "Plate_9"
        )

    def test_wave_zero_is_a_wave_not_an_absence(self):
        # The scanner app sends 0 when no wave is set, so 0 arrives in practice.
        assert pvp.plate_video_path(12, 0, "Plate_9") == "12/wave-0/Plate_9.mp4"

    def test_refuses_a_plate_id_carrying_a_path_separator(self):
        for plate in ("../../secrets", "a/b", "a\\b"):
            assert pvp.plate_video_path(12, 3, plate) is None, plate

    def test_refuses_a_leading_dot(self):
        """So `..` can never be constructed."""
        for plate in (".hidden", "..", "."):
            assert pvp.plate_video_path(12, 3, plate) is None, plate

    def test_refuses_empty_and_overlong(self):
        assert pvp.plate_video_path(12, 3, "") is None
        assert pvp.plate_video_path(12, 3, "P" * 65) is None
        assert pvp.plate_video_path(12, 3, "P" * 64) is not None

    def test_refuses_whitespace_and_unicode(self):
        for plate in ("Plate_9 ", "Plate 9", "Platé_9", "Plate_9\n"):
            assert pvp.plate_video_path(12, 3, plate) is None, repr(plate)

    def test_a_trailing_newline_cannot_slip_through(self):
        """`re.match` would accept this; `fullmatch` refuses it."""
        assert pvp.is_valid_plate_id("Plate_9\n") is False

    def test_refuses_a_non_positive_experiment_id(self):
        for exp in (0, -1):
            assert pvp.plate_video_path(exp, 3, "Plate_9") is None, exp

    def test_refuses_a_bool_as_an_id_or_wave(self):
        """bool is an int subclass, so True would render as `wave-True`."""
        assert pvp.plate_video_path(True, 3, "Plate_9") is None
        assert pvp.plate_video_path(12, True, "Plate_9") is None

    def test_refuses_a_negative_wave(self):
        assert pvp.plate_video_path(12, -1, "Plate_9") is None

    def test_refuses_a_float_id_or_wave(self):
        # `12.0` and `1.5` would render as `12.0/...` and `wave-1.5`.
        assert pvp.plate_video_path(12.0, 3, "Plate_9") is None
        assert pvp.plate_video_path(1.5, 3, "Plate_9") is None
        assert pvp.plate_video_path(12, 1.5, "Plate_9") is None
        assert pvp.plate_video_path(12, 2.0, "Plate_9") is None


class TestIsValidPlateId:
    def test_accepts_the_shapes_the_scanner_produces(self):
        for plate in ("Plate_9", "Plate_13", "PLATE-001", "P1", "9"):
            assert pvp.is_valid_plate_id(plate) is True, plate

    def test_rejects_what_must_never_reach_a_path(self):
        for plate in ("", "..", "./x", "a/b", "a b", "-lead", "_lead"):
            assert pvp.is_valid_plate_id(plate) is False, plate

    def test_a_non_string_is_not_a_plate_id(self):
        assert pvp.is_valid_plate_id(None) is False  # type: ignore[arg-type]
        assert pvp.is_valid_plate_id(9) is False  # type: ignore[arg-type]


class TestWaveSegment:
    def test_names_the_absent_wave(self):
        assert pvp.wave_segment(None) == "wave-none"

    def test_returns_none_for_a_wave_it_cannot_name(self):
        assert pvp.wave_segment(-1) is None
        assert pvp.wave_segment(True) is None


class TestBuckets:
    def test_constants_name_the_graviscan_buckets(self):
        assert pvp.GRAVISCAN_VIDEOS_BUCKET == "graviscan-videos"
        assert pvp.GRAVISCAN_IMAGES_BUCKET == "graviscan-images"

    def test_the_names_do_not_collide_with_the_cylinder_buckets(self):
        """video.py exports VIDEOS_BUCKET/IMAGES_BUCKET meaning the cylinder
        buckets, and both modules are importable by bare name."""
        assert not hasattr(pvp, "VIDEOS_BUCKET")
        assert not hasattr(pvp, "IMAGES_BUCKET")
