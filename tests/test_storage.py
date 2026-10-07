import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from workout_app.gpx_parser import parse_gpx
from workout_app.models import Workout
from workout_app.storage import ProfileStore, WorkoutStore

SYNTHETIC_GPX = """<?xml version="1.0"?>
<gpx version="1.1" creator="unit-test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Test Route</name><trkseg>
    <trkpt lat="0" lon="0"><ele>10</ele><time>2026-10-05T08:00:00Z</time><extensions><hr>145</hr></extensions></trkpt>
    <trkpt lat="0" lon="0.001"><ele>12</ele><time>2026-10-05T08:00:10Z</time></trkpt>
    <trkpt lat="0.001" lon="0.001"><ele>11</ele><time>2026-10-05T08:00:20Z</time></trkpt>
  </trkseg></trk>
</gpx>
"""


class StorageTests(unittest.TestCase):
    def test_profile_migration_removes_legacy_goal(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(
                json.dumps(
                    {
                        "name": "테스트",
                        "gender": "남성",
                        "age": 27,
                        "height": 175,
                        "weight": 70,
                        "goal": "weight_loss",
                    }
                ),
                encoding="utf-8",
            )

            profile = ProfileStore(path).load()

            self.assertEqual(profile.name, "테스트")
            normalized = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("goal", normalized)
            self.assertEqual(normalized["height"], 175)
            self.assertEqual(normalized["weight"], 70)
            self.assertNotIn("height_cm", normalized)

    def test_workouts_round_trip_and_load_newest_first(self) -> None:
        with TemporaryDirectory() as directory:
            store = WorkoutStore(Path(directory) / "workouts.json")
            older = Workout(
                start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
                distance_m=1000,
                gpx_path="older.gpx",
                name="Older",
            )
            newer = Workout(
                start_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 2, 0, 10, tzinfo=timezone.utc),
                distance_m=2000,
                gpx_path="newer.gpx",
                name="Newer",
            )
            store.save([older, newer])

            loaded = store.load()

            self.assertEqual([item.id for item in loaded], [newer.id, older.id])
            self.assertEqual(loaded[0].name, "Newer")
            self.assertEqual(loaded[0].start_time.utcoffset(), timezone.utc.utcoffset(None))

    def test_track_points_survive_save_and_reload(self) -> None:
        with TemporaryDirectory() as directory:
            source_path = Path(directory) / "synthetic_route.gpx"
            source_path.write_text(SYNTHETIC_GPX, encoding="utf-8")
            workout = parse_gpx(source_path)
            store = WorkoutStore(Path(directory) / "workouts.json")
            store.save([workout])

            loaded = store.load()[0]

            self.assertEqual(len(loaded.track_points), 3)
            self.assertEqual(loaded.start_time, workout.start_time)
            self.assertEqual(loaded.track_points[0], workout.track_points[0])
            self.assertAlmostEqual(loaded.distance_m, workout.distance_m)


if __name__ == "__main__":
    unittest.main()
