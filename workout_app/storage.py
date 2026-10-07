from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from workout_app.models import Profile, Workout


class ProfileStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> Profile | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            profile = Profile.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError(f"프로필 파일을 읽을 수 없습니다 ({self.path}): {error}") from error
        if "goal" in data or set(data) != set(profile.to_dict()):
            self.save(profile)
        return profile

    def save(self, profile: Profile) -> None:
        _atomic_write(self.path, json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))


class WorkoutStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> list[Workout]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("운동 기록 데이터는 JSON 배열이어야 합니다.")
            workouts = [Workout.from_dict(item) for item in data]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError(f"운동 기록 파일을 읽을 수 없습니다 ({self.path}): {error}") from error
        return sorted(workouts, key=lambda workout: workout.start_time, reverse=True)

    def save(self, workouts: list[Workout]) -> None:
        ordered = sorted(workouts, key=lambda workout: workout.start_time, reverse=True)
        serialized = json.dumps(
            [workout.to_dict() for workout in ordered],
            ensure_ascii=False,
            indent=2,
        )
        _atomic_write(self.path, serialized)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = file.name
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    except OSError:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
        raise
