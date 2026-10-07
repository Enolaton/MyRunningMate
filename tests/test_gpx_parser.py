from datetime import timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from workout_app.gpx_parser import GPXParseError, parse_gpx

SYNTHETIC_GPX = """<?xml version="1.0"?>
<gpx version="1.1" creator="unit-test" xmlns="http://www.topografix.com/GPX/1/1" xmlns:app="https://example.com/gpx">
  <trk><name>Test Route</name><trkseg>
    <trkpt lat="0" lon="0"><ele>10</ele><time>2026-10-05T08:00:00Z</time><extensions><speed>1.5</speed><hr>145</hr><app:hAcc>3.4</app:hAcc><app:vAcc>5.6</app:vAcc></extensions></trkpt>
    <trkpt lat="0" lon="0.001"><ele>12</ele><time>2026-10-05T08:00:10Z</time></trkpt>
    <trkpt lat="0.001" lon="0.001"><ele>11</ele><time>2026-10-05T08:00:20Z</time></trkpt>
  </trkseg></trk>
</gpx>
"""


class GPXParserTests(unittest.TestCase):
    def test_parses_gpx_and_normalizes_timestamps_to_utc(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic_route.gpx"
            path.write_text(SYNTHETIC_GPX, encoding="utf-8")
            workout = parse_gpx(path)

        self.assertEqual(len(workout.track_points), 3)
        self.assertEqual(workout.name, "Test Route")
        self.assertEqual(workout.start_time.isoformat(), "2026-10-05T08:00:00+00:00")
        self.assertEqual(workout.end_time.isoformat(), "2026-10-05T08:00:20+00:00")
        self.assertEqual(workout.start_time.utcoffset(), timezone.utc.utcoffset(None))
        self.assertAlmostEqual(workout.distance_m, 222.39, delta=0.1)
        self.assertEqual(workout.duration.total_seconds(), 20)
        self.assertEqual(workout.track_points[0].latitude, 0)
        self.assertEqual(workout.track_points[0].longitude, 0)
        self.assertAlmostEqual(workout.track_points[0].speed_mps, 1.5)
        self.assertEqual(workout.track_points[0].heart_rate_bpm, 145)
        self.assertAlmostEqual(workout.track_points[0].horizontal_accuracy_m, 3.4)
        self.assertAlmostEqual(workout.track_points[0].vertical_accuracy_m, 5.6)
        self.assertAlmostEqual(workout.average_speed_kmh, 40.03, places=1)

    def test_rejects_non_gpx_files_with_a_clear_error(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "not_gpx.xml"
            path.write_text("<not-gpx />", encoding="utf-8")
            with self.assertRaises(GPXParseError):
                parse_gpx(path)


if __name__ == "__main__":
    unittest.main()
