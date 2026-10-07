from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Profile:
    name: str
    gender: str
    age: int
    height_cm: float
    weight_kg: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        return cls(
            name=str(data["name"]),
            gender=str(data.get("gender", "")),
            age=int(data["age"]),
            height_cm=float(data.get("height_cm", data.get("height"))),
            weight_kg=float(data.get("weight_kg", data.get("weight"))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "gender": self.gender,
            "age": self.age,
            "height": self.height_cm,
            "weight": self.weight_kg,
        }


@dataclass(frozen=True)
class TrackPoint:
    latitude: float
    longitude: float
    elevation_m: float | None
    timestamp: datetime | None
    speed_mps: float | None = None
    course_deg: float | None = None
    horizontal_accuracy_m: float | None = None
    vertical_accuracy_m: float | None = None
    heart_rate_bpm: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "elevation_m": self.elevation_m,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "speed_mps": self.speed_mps,
            "course_deg": self.course_deg,
            "horizontal_accuracy_m": self.horizontal_accuracy_m,
            "vertical_accuracy_m": self.vertical_accuracy_m,
            "heart_rate_bpm": self.heart_rate_bpm,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrackPoint:
        return cls(
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"]),
            elevation_m=_optional_float(data.get("elevation_m")),
            timestamp=_optional_datetime(data.get("timestamp")),
            speed_mps=_optional_float(data.get("speed_mps")),
            course_deg=_optional_float(data.get("course_deg")),
            horizontal_accuracy_m=_optional_float(data.get("horizontal_accuracy_m")),
            vertical_accuracy_m=_optional_float(data.get("vertical_accuracy_m")),
            heart_rate_bpm=_optional_float(data.get("heart_rate_bpm")),
        )


@dataclass
class Workout:
    start_time: datetime
    end_time: datetime
    distance_m: float
    gpx_path: str
    name: str
    track_points: list[TrackPoint] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid4()))

    @property
    def duration(self) -> timedelta:
        return self.end_time - self.start_time

    @property
    def average_speed_kmh(self) -> float:
        seconds = self.duration.total_seconds()
        return self.distance_m / seconds * 3.6 if seconds > 0 else 0.0

    @property
    def maximum_speed_mps(self) -> float | None:
        speeds = [point.speed_mps for point in self.track_points if point.speed_mps is not None]
        return max(speeds) if speeds else None

    @property
    def elevation_range_m(self) -> tuple[float, float] | None:
        elevations = [
            point.elevation_m for point in self.track_points if point.elevation_m is not None
        ]
        return (min(elevations), max(elevations)) if elevations else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "distance_m": self.distance_m,
            "gpx_path": self.gpx_path,
            "name": self.name,
            "track_points": [point.to_dict() for point in self.track_points],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workout:
        return cls(
            id=str(data["id"]),
            start_time=_required_datetime(data["start_time"]),
            end_time=_required_datetime(data["end_time"]),
            distance_m=float(data["distance_m"]),
            gpx_path=str(data["gpx_path"]),
            name=str(data.get("name") or "Workout"),
            track_points=[TrackPoint.from_dict(point) for point in data["track_points"]],
        )


def _optional_float(value: Any) -> float | None:
    return None if value is None or value == "" else float(value)


def _optional_datetime(value: Any) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


def _required_datetime(value: Any) -> datetime:
    result = datetime.fromisoformat(str(value))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("운동 기록의 시작/종료 시각에는 시간대 정보가 필요합니다.")
    return result
