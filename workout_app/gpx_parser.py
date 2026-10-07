from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from workout_app.models import TrackPoint, Workout


class GPXParseError(ValueError):
    """GPX 파일이 유효하지 않거나 운동 기록에 필요한 데이터가 없습니다."""


def parse_gpx(path: str | Path) -> Workout:
    source = Path(path)
    try:
        root = ET.parse(source).getroot()
    except (ET.ParseError, OSError) as error:
        raise GPXParseError(f"{source.name}: GPX 파일을 읽지 못했습니다: {error}") from error

    if _local_name(root.tag) != "gpx":
        raise GPXParseError(f"{source.name}: GPX 루트 요소가 없습니다.")

    track_elements = [element for element in root.iter() if _local_name(element.tag) == "trk"]
    segments: list[list[TrackPoint]] = []
    for track in track_elements:
        for segment in track.iter():
            if _local_name(segment.tag) != "trkseg":
                continue
            points = [
                _parse_point(point, source.name)
                for point in segment
                if _local_name(point.tag) == "trkpt"
            ]
            if points:
                segments.append(points)

    if not segments:
        route_points = [
            _parse_point(element, source.name)
            for element in root.iter()
            if _local_name(element.tag) == "rtept"
        ]
        if route_points:
            segments.append(route_points)

    points = [point for segment in segments for point in segment]
    dated_points = [point.timestamp for point in points if point.timestamp is not None]
    if len(points) < 2:
        raise GPXParseError(f"{source.name}: 거리 계산에 필요한 GPS 포인트가 2개 미만입니다.")
    if not dated_points:
        raise GPXParseError(f"{source.name}: 운동 시작 시각을 계산할 시간 데이터가 없습니다.")

    total_distance_m = sum(
        distance_m(first, second)
        for segment in segments
        for first, second in zip(segment, segment[1:])
    )
    if not math.isfinite(total_distance_m):
        raise GPXParseError(f"{source.name}: 거리 계산 결과가 유효하지 않습니다.")

    name = next(
        (
            (element.text or "").strip()
            for track in track_elements
            for element in track
            if _local_name(element.tag) == "name" and (element.text or "").strip()
        ),
        source.stem,
    )
    return Workout(
        start_time=min(dated_points),
        end_time=max(dated_points),
        distance_m=total_distance_m,
        gpx_path=str(source.resolve()),
        name=name,
        track_points=points,
    )


def _parse_point(element: ET.Element, filename: str) -> TrackPoint:
    try:
        latitude = float(element.attrib["lat"])
        longitude = float(element.attrib["lon"])
    except (KeyError, ValueError) as error:
        raise GPXParseError(f"{filename}: 위도/경도 값이 없거나 올바르지 않습니다.") from error
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise GPXParseError(f"{filename}: 위도/경도 범위가 올바르지 않습니다.")

    fields = {
        _local_name(child.tag).lower(): child
        for child in element.iter()
        if child is not element
    }
    return TrackPoint(
        latitude=latitude,
        longitude=longitude,
        elevation_m=_number(fields.get("ele"), filename, "고도"),
        timestamp=_timestamp(fields.get("time"), filename),
        speed_mps=_number(fields.get("speed"), filename, "속도"),
        course_deg=_number(fields.get("course"), filename, "방향"),
        horizontal_accuracy_m=_number(fields.get("hacc"), filename, "수평 정확도"),
        vertical_accuracy_m=_number(fields.get("vacc"), filename, "수직 정확도"),
        heart_rate_bpm=_number(
            next(
                (
                    fields[key]
                    for key in ("hr", "heartrate", "heart_rate", "heartratebpm", "heart_rate_bpm")
                    if key in fields
                ),
                None,
            ),
            filename,
            "심박수",
        ),
    )


def _number(element: ET.Element | None, filename: str, label: str) -> float | None:
    if element is None or element.text is None:
        return None
    try:
        value = float(element.text)
    except ValueError as error:
        raise GPXParseError(f"{filename}: {label} 값이 숫자가 아닙니다.") from error
    if not math.isfinite(value):
        raise GPXParseError(f"{filename}: {label} 값이 유효하지 않습니다.")
    return value


def _timestamp(element: ET.Element | None, filename: str) -> datetime | None:
    if element is None or not element.text:
        return None
    text = element.text.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError as error:
        raise GPXParseError(f"{filename}: 시각 형식이 올바르지 않습니다 ({text}).") from error
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def distance_m(first: TrackPoint, second: TrackPoint) -> float:
    earth_radius_m = 6_371_000
    latitude_1 = math.radians(first.latitude)
    latitude_2 = math.radians(second.latitude)
    delta_latitude = latitude_2 - latitude_1
    delta_longitude = math.radians(second.longitude - first.longitude)
    haversine = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_1)
        * math.cos(latitude_2)
        * math.sin(delta_longitude / 2) ** 2
    )
    return 2 * earth_radius_m * math.asin(math.sqrt(min(1.0, haversine)))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
