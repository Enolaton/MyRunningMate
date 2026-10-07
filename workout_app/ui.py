from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from PyQt5.QtCore import QDate, QPointF, QRectF, QSize, QStandardPaths, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QTextCharFormat
from PyQt5.QtNetwork import (
    QNetworkAccessManager,
    QNetworkDiskCache,
    QNetworkReply,
    QNetworkRequest,
)
from PyQt5.QtWidgets import *

from gui import Ui_MainWindow
from workout_app.gpx_parser import GPXParseError, distance_m, parse_gpx
from workout_app.models import Profile, TrackPoint, Workout
from workout_app.storage import ProfileStore, WorkoutStore

KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc


class WorkoutPlot(QWidget):
    tile_error = pyqtSignal(str)

    def __init__(self, title: str, plot_type: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.plot_type = plot_type
        self.workout: Workout | None = None
        self.osm_enabled = False
        self._network: QNetworkAccessManager | None = None
        self._tile_images: dict[tuple[int, int, int], QImage] = {}
        self._pending_tiles: set[tuple[int, int, int]] = set()
        self._tile_replies: dict[tuple[int, int, int], QNetworkReply] = {}
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_workout(self, workout: Workout | None) -> None:
        self.workout = workout
        self._tile_images.clear()
        self.update()

    def set_osm_enabled(self, enabled: bool) -> None:
        if enabled and self._network is None:
            self._create_tile_network()
        self.osm_enabled = enabled
        if not enabled:
            for reply in tuple(self._tile_replies.values()):
                reply.abort()
        self.update()

    def _create_tile_network(self) -> None:
        network = QNetworkAccessManager(self)
        cache = QNetworkDiskCache(network)
        cache_root = Path(
            QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
        ) / "osm-tiles"
        cache_root.mkdir(parents=True, exist_ok=True)
        cache.setCacheDirectory(str(cache_root))
        cache.setMaximumCacheSize(256 * 1024 * 1024)
        network.setCache(cache)
        self._network = network

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        is_route_map = self.plot_type == "route" and self.osm_enabled
        if not is_route_map:
            self._draw_title(painter)

        available_plot = (
            self.rect().adjusted(2, 2, -2, -2)
            if is_route_map
            else self.rect().adjusted(22, 42, -22, -24)
        )
        plot = (
            _fit_aspect_rect(available_plot, 4 / 3)
            if is_route_map
            else available_plot
        )
        if self.workout is None:
            painter.setPen(QColor("#8490a3"))
            painter.drawText(plot, Qt.AlignCenter, "표시할 운동 데이터가 없습니다.")
            return

        if self.plot_type == "route":
            self._draw_route(painter, plot)
        else:
            self._draw_elevation(painter, plot)

    def _draw_title(self, painter: QPainter) -> None:
        painter.setPen(QColor("#172033"))
        painter.drawText(18, 27, self.title)

    def _draw_route(self, painter: QPainter, plot: object) -> None:
        assert self.workout is not None
        points = self.workout.track_points
        if len(points) < 2:
            painter.setPen(QColor("#8490a3"))
            painter.drawText(plot, Qt.AlignCenter, "경로를 표시할 GPS 데이터가 없습니다.")
            return
        if self.osm_enabled:
            zoom, center_x, center_y, origin_x, origin_y = _map_view_transform(plot, points)
            self._draw_osm_tiles(painter, plot, zoom, origin_x, origin_y)
            self._draw_projected_route(
                painter,
                plot,
                points,
                zoom,
                center_x,
                center_y,
            )
            self._draw_attribution(painter, self._map_overlay_bounds(plot))
            return

        min_lat = min(point.latitude for point in points)
        max_lat = max(point.latitude for point in points)
        min_lon = min(point.longitude for point in points)
        max_lon = max(point.longitude for point in points)
        lat_span = max(max_lat - min_lat, 1e-9)
        lon_span = max(max_lon - min_lon, 1e-9)
        aspect = max(plot.width() / max(plot.height(), 1), 1e-9)
        lon_scale = math.cos(math.radians((max_lat + min_lat) / 2))
        lon_span *= max(lon_scale, 0.01)
        lat_span = max(lat_span, lon_span / aspect)
        lon_span = max(lon_span, lat_span * aspect)
        center_lat = (max_lat + min_lat) / 2
        center_lon = (max_lon + min_lon) / 2
        positions: list[QPointF] = []
        for point in points:
            x = plot.center().x() + (point.longitude - center_lon) * lon_scale / lon_span * plot.width()
            y = plot.center().y() - (point.latitude - center_lat) / lat_span * plot.height()
            positions.append(QPointF(x, y))
        self._paint_intensity_route(painter, positions, points, plot)

    def _draw_projected_route(
        self,
        painter: QPainter,
        plot: object,
        points: list[TrackPoint],
        zoom: int,
        center_x: float,
        center_y: float,
    ) -> None:
        positions: list[QPointF] = []
        for point in points:
            world_x, world_y = _mercator_world_pixel(point.latitude, point.longitude, zoom)
            positions.append(QPointF(
                plot.center().x() + world_x - center_x,
                plot.center().y() + world_y - center_y,
            ))
        self._paint_intensity_route(painter, positions, points, plot)

    def _paint_intensity_route(
        self,
        painter: QPainter,
        positions: list[QPointF],
        points: list[TrackPoint],
        plot: object,
    ) -> None:
        _, segment_colors = _route_intensity_colors(points)
        for start, end, color in zip(positions, positions[1:], segment_colors):
            painter.setPen(QPen(color, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawLine(start, end)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(positions[0], 5, 5)
        painter.setBrush(segment_colors[-1])
        painter.drawEllipse(positions[-1], 5, 5)
        self._draw_intensity_legend(painter, self._map_overlay_bounds(plot))

    def _map_overlay_bounds(self, plot: object) -> object:
        if self.plot_type == "route" and self.osm_enabled:
            return self.rect().adjusted(2, 2, -2, -2)
        return plot

    @staticmethod
    def _draw_intensity_legend(
        painter: QPainter,
        plot: object,
    ) -> None:
        entries = [
            ("약함", QColor("#2eaa68")),
            ("보통", QColor("#f4c542")),
            ("강함", QColor("#e74c3c")),
        ]
        metrics = painter.fontMetrics()
        entries_width = sum(metrics.horizontalAdvance(label) + 22 for label, _ in entries)
        width = entries_width + 14
        height = metrics.height() + 10
        bounds = QRectF(plot.left() + 4, plot.bottom() - height - 4, width, height)
        painter.fillRect(bounds, QColor(255, 255, 255, 225))
        x = bounds.left() + 7
        baseline = bounds.top() + 5 + metrics.ascent()
        for label, color in entries:
            painter.setPen(QPen(color, 3, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(x + 3, baseline - 4), QPointF(x + 12, baseline - 4))
            painter.setPen(QColor("#172033"))
            painter.drawText(QPointF(x + 16, baseline), label)
            x += metrics.horizontalAdvance(label) + 22

    def _draw_osm_tiles(
        self,
        painter: QPainter,
        plot: object,
        zoom: int,
        origin_x: float,
        origin_y: float,
    ) -> None:
        world_tile_count = 2**zoom
        min_tile_x = math.floor(origin_x / 256)
        max_tile_x = math.floor((origin_x + plot.width()) / 256)
        min_tile_y = max(0, math.floor(origin_y / 256))
        max_tile_y = min(
            world_tile_count - 1,
            math.floor((origin_y + plot.height()) / 256),
        )
        painter.fillRect(plot, QColor("#e8eef0"))
        for tile_y in range(min_tile_y, max_tile_y + 1):
            for unwrapped_x in range(min_tile_x, max_tile_x + 1):
                tile_x = unwrapped_x % world_tile_count
                key = (zoom, tile_x, tile_y)
                target = QRectF(
                    unwrapped_x * 256 - origin_x + plot.left(),
                    tile_y * 256 - origin_y + plot.top(),
                    256,
                    256,
                )
                image = self._tile_images.get(key)
                if image is not None:
                    painter.drawImage(target, image)
                elif key not in self._pending_tiles:
                    self._request_tile(key)
        if not self._tile_images:
            painter.setPen(QColor("#687386"))
            message = (
                "OpenStreetMap 지도를 불러오는 중..."
                if self._pending_tiles
                else "지도 타일을 불러오지 못했습니다. 경로만 표시합니다."
            )
            painter.drawText(plot, Qt.AlignCenter, message)

    def _request_tile(self, key: tuple[int, int, int]) -> None:
        if self._network is None:
            return
        zoom, x, y = key
        request = QNetworkRequest(
            QUrl(f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png")
        )
        request.setAttribute(
            QNetworkRequest.CacheLoadControlAttribute,
            QNetworkRequest.PreferCache,
        )
        request.setRawHeader(
            b"User-Agent",
            b"RunningMate/1.0 (personal GPX workout application)",
        )
        self._pending_tiles.add(key)
        reply = self._network.get(request)
        self._tile_replies[key] = reply
        reply.finished.connect(lambda tile=key, response=reply: self._tile_received(tile, response))

    def _tile_received(self, key: tuple[int, int, int], reply: QNetworkReply) -> None:
        self._pending_tiles.discard(key)
        self._tile_replies.pop(key, None)
        if reply.error() == QNetworkReply.OperationCanceledError:
            reply.deleteLater()
            self.update()
            return
        status_code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        image = QImage()
        if reply.error() == QNetworkReply.NoError and image.loadFromData(reply.readAll()):
            self._tile_images[key] = image
        else:
            reason = reply.errorString()
            if status_code is not None:
                reason = f"HTTP {status_code}: {reason}"
            self.tile_error.emit(f"OpenStreetMap 타일을 불러오지 못했습니다 ({reason}).")
        reply.deleteLater()
        self.update()

    @staticmethod
    def _draw_attribution(painter: QPainter, plot: object) -> None:
        text = "© OpenStreetMap contributors"
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 12
        height = metrics.height() + 6
        bounds = QRectF(
            plot.right() - width - 4,
            plot.bottom() - height - 4,
            width,
            height,
        )
        painter.fillRect(bounds, QColor(255, 255, 255, 220))
        painter.setPen(QColor("#172033"))
        painter.drawText(bounds, Qt.AlignCenter, text)

    def _draw_elevation(self, painter: QPainter, plot: object) -> None:
        assert self.workout is not None
        points = [point for point in self.workout.track_points if point.elevation_m is not None]
        if len(points) < 2:
            painter.setPen(QColor("#8490a3"))
            painter.drawText(plot, Qt.AlignCenter, "고도 데이터가 충분하지 않습니다.")
            return
        values = [point.elevation_m for point in points]
        low = min(values)
        high = max(values)
        spread = max(high - low, 1.0)
        low -= spread * 0.12
        high += spread * 0.12

        painter.setPen(QPen(QColor("#e7edf2"), 1))
        for fraction in (0.0, 0.5, 1.0):
            y = plot.bottom() - fraction * plot.height()
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        path = QPainterPath()
        for index, value in enumerate(values):
            x = plot.left() + index / (len(values) - 1) * plot.width()
            y = plot.bottom() - (value - low) / (high - low) * plot.height()
            position = QPointF(x, y)
            if index == 0:
                path.moveTo(position)
            else:
                path.lineTo(position)
        fill = QPainterPath(path)
        fill.lineTo(QPointF(plot.right(), plot.bottom()))
        fill.lineTo(QPointF(plot.left(), plot.bottom()))
        fill.closeSubpath()
        painter.fillPath(fill, QColor(39, 134, 111, 28))
        painter.setPen(QPen(QColor("#27866f"), 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(path)
        painter.setPen(QColor("#8490a3"))
        painter.drawText(4, int(plot.top() + 5), f"{high:.0f} m")
        painter.drawText(4, int(plot.bottom()), f"{low:.0f} m")


class ProfileDialog(QDialog):
    def __init__(self, profile: Profile | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("사용자 프로필")
        self.setModal(True)

        self.name_input = QLineEdit(profile.name if profile else "")
        self.gender_input = QComboBox()
        self.gender_input.addItem("선택 안 함", "")
        self.gender_input.addItem("남성", "male")
        self.gender_input.addItem("여성", "female")
        self.gender_input.addItem("기타", "other")
        if profile:
            index = self.gender_input.findData(profile.gender)
            if index < 0:
                index = self.gender_input.findText(profile.gender)
            if index >= 0:
                self.gender_input.setCurrentIndex(index)
        self.age_input = QSpinBox()
        self.age_input.setRange(1, 120)
        self.age_input.setValue(profile.age if profile else 30)
        self.height_input = QDoubleSpinBox()
        self.height_input.setRange(1, 250)
        self.height_input.setDecimals(1)
        self.height_input.setSuffix(" cm")
        self.height_input.setValue(profile.height_cm if profile else 170.0)
        self.weight_input = QDoubleSpinBox()
        self.weight_input.setRange(1, 500)
        self.weight_input.setDecimals(1)
        self.weight_input.setSuffix(" kg")
        self.weight_input.setValue(profile.weight_kg if profile else 65.0)

        form = QFormLayout()
        form.addRow("이름", self.name_input)
        form.addRow("성별", self.gender_input)
        form.addRow("나이", self.age_input)
        form.addRow("키", self.height_input)
        form.addRow("몸무게", self.weight_input)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def profile(self) -> Profile:
        return Profile(
            name=self.name_input.text().strip(),
            gender=str(self.gender_input.currentData() or ""),
            age=self.age_input.value(),
            height_cm=self.height_input.value(),
            weight_kg=self.weight_input.value(),
        )

    def _accept_if_valid(self) -> None:
        if not self.name_input.text().strip():
            QMessageBox.warning(self, "입력 확인", "이름을 입력해 주세요.")
            self.name_input.setFocus()
            return
        self.accept()


class UsageDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("러닝 메이트 사용법")
        self.resize(720, 640)

        self.instructions = QTextBrowser()
        self.instructions.setObjectName("usageInstructions")
        self.instructions.setOpenExternalLinks(False)
        self.instructions.setHtml(
            """
            <html><body style="font-family: sans-serif; color: #263445; line-height: 1.55;">
              <h2 style="color: #175b4c;">아이폰 건강 데이터 준비</h2>
              <ol>
                <li>아이폰에서 <b>건강</b> 앱을 열고 오른쪽 위의 프로필 사진 또는 이니셜을 누릅니다.</li>
                <li><b>모든 건강 데이터 내보내기</b>를 선택하고 내보내기를 진행합니다.</li>
                <li>공유 화면에서 <b>파일에 저장</b>을 선택하면 ZIP 파일로 저장됩니다.</li>
              </ol>
              <p><b>중요:</b> 건강 앱의 전체 내보내기는 보통 ZIP 안에 XML 형식으로 저장되며, 이 앱에서 직접 불러올 수 있는 GPX 파일이 아닙니다.
              운동 경로가 필요하면 Apple 건강/운동 데이터에 접근하여 해당 운동을 <b>GPX로 내보내는 기능</b>을 제공하는 경로 내보내기 도구를 사용하세요.
              내보낸 결과가 반드시 <code>.gpx</code>인지 확인한 뒤 아이폰의 파일 앱 또는 PC에서 접근할 수 있는 위치에 저장합니다.
              GPX를 만들 수 없는 경우에는 이 앱에 해당 운동 경로를 가져올 수 없습니다.</p>

              <h2 style="color: #175b4c;">GPX 파일 불러오기</h2>
              <ol>
                <li>러닝 메이트의 <b>파일 &gt; 데이터 불러오기</b>를 선택합니다.</li>
                <li>파일 선택창에서 하나 이상의 <code>.gpx</code> 파일을 선택합니다. PC에 있는 파일은 아이폰에서 전송하거나 PC에서 앱을 실행해 선택할 수 있습니다.</li>
                <li><b>열기</b>를 누르면 각 GPX 파일이 별도의 기록으로 등록되고, 기록은 최신순으로 표시됩니다.</li>
              </ol>
              <p>GPX에는 위치 좌표와 운동 시각이 포함됩니다. 경로 내보내기 도구의 공유/저장 기능을 사용할 때 원본 데이터를 신뢰할 수 있는 앱과 저장 위치만 선택하세요.</p>

              <h2 style="color: #175b4c;">기록 화면에서 확인할 내용</h2>
              <ul>
                <li><b>기록 목록:</b> 운동 날짜만 표시합니다. 기록 행의 X 버튼으로 삭제할 수 있습니다.</li>
                <li><b>요약 카드:</b> 운동 거리, 운동 시간, 평균 속도, GPX에 속도 정보가 있을 때 최고 속도를 표시합니다.</li>
                <li><b>이동 경로:</b> 구간별 운동 강도를 약함(초록), 보통(노랑), 강함(빨강)으로 표시합니다. 확인 가능한 속도와 고도 변화를 결합해 해당 운동 안에서 상대적으로 계산합니다. 원하면 OpenStreetMap 배경을 불러올 수 있습니다.</li>
                <li><b>고도 변화:</b> GPX에 고도 데이터가 있으면 포인트 순서에 따른 높낮이 그래프를 표시합니다.</li>
              </ul>
              <p>속도와 고도 변화량은 각 운동 안에서 상대 점수로 환산한 뒤 같은 비중으로 결합합니다. 값이 같은 구간은 노랑으로 표시합니다. OpenStreetMap 배경은 기본적으로 꺼져 있습니다. 지도 체크박스를 켜면 현재 경로 주변의 보이는 지도 타일을 온라인으로 요청하며, 이때 해당 위치 정보가 타일 제공자에게 전달됩니다. 타일 요청 전에 확인 창이 표시됩니다. 지도에는 © OpenStreetMap contributors 출처를 표시하며, 네트워크 연결이 없거나 사용을 거부해도 경로 그래프는 계속 볼 수 있습니다.</p>
              <p>목록에서 기록을 선택하면 오른쪽 요약과 그래프가 해당 운동으로 바뀝니다.
              원시 GPS 포인트 표나 파일명은 화면에 표시하지 않습니다.</p>
              <p><b>일일 요약</b> 화면에서 <b>리스트로 보기</b>와 <b>캘린더로 보기</b> 탭을 전환할 수 있습니다.
              리스트에는 날짜만 표시되며, 캘린더에서 운동이 있는 날짜를 선택하면 그날의 기록이 표시됩니다.
              <b>주간 요약</b> 탭에서 전체 또는 주차별 운동 횟수, 총 거리, 평균 거리, 총 운동 시간, 거리 가중 평균 페이스를 확인할 수 있습니다.
              주간 요약은 연도와 월을 고른 뒤 <b>전체</b> 또는 1~5주차 버튼을 사용합니다. 각 주차는 해당 월의 1~7일, 8~14일처럼 7일 구간으로 나뉩니다.
              리스트 또는 캘린더의 기록 행 끝에 있는 <b>X</b>를 누르면 확인 후 기록이 삭제됩니다. 최신순/과거순 메뉴는 리스트로 보기에서만 표시됩니다.</p>
            </body></html>
            """
        )
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.instructions)
        layout.addWidget(buttons)


class WorkoutListRow(QWidget):
    def __init__(
        self,
        label: str,
        on_select: Callable[[], None],
        on_delete: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.on_select = on_select
        text = QLabel(label)
        text.setAttribute(Qt.WA_TransparentForMouseEvents)
        text.setMinimumWidth(0)
        text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        remove_button = QToolButton()
        remove_button.setText("X")
        remove_button.setToolTip("이 운동 기록 삭제")
        remove_button.setAccessibleName("이 운동 기록 삭제")
        remove_button.setCursor(Qt.PointingHandCursor)
        remove_button.setFixedSize(26, 26)
        remove_button.clicked.connect(on_delete)
        remove_button.setStyleSheet(
            "QToolButton { color: #687386; border: none; font-weight: 700; "
            "padding: 4px 8px; }"
            "QToolButton:hover { color: #b42318; background: #fde8e7; border-radius: 8px; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 3, 2)
        layout.setSpacing(3)
        layout.addWidget(text, 1)
        layout.addWidget(remove_button, alignment=Qt.AlignVCenter)
        self.setMinimumHeight(42)

    def mousePressEvent(self, event: object) -> None:
        self.on_select()
        super().mousePressEvent(event)


class SummaryPage(QWidget):
    def __init__(self, period: str, workouts: list[Workout]) -> None:
        super().__init__()
        self.period = period
        self.workouts = workouts
        today = QDate.currentDate()
        self.selected_year = today.year()
        self.selected_month = today.month()
        self.selected_week: int | None = None
        self.year_input = QSpinBox()
        self.year_input.setRange(1900, 2200)
        self.year_input.setValue(self.selected_year)
        self.year_input.valueChanged.connect(self._year_changed)
        self.month_buttons: dict[int, QPushButton] = {}
        self.week_buttons: dict[int, QPushButton] = {}
        self.all_button: QPushButton | None = None
        self._month_group = QButtonGroup(self)
        self._month_group.setExclusive(True)
        self._week_group = QButtonGroup(self)
        self._week_group.setExclusive(True)
        self.period_label = QLabel()
        self.metric_values: dict[str, QLabel] = {}
        metrics = QHBoxLayout()
        metric_titles = ["운동 횟수", "총 거리", "총 운동 시간", "평균 거리", "평균 페이스"]
        for title in metric_titles:
            card = QFrame()
            card.setObjectName("metricCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            caption = QLabel(title)
            caption.setStyleSheet("color: #687386; font-size: 12px;")
            value = QLabel("—")
            value.setStyleSheet("color: #172033; font-size: 18px; font-weight: 700;")
            card_layout.addWidget(caption)
            card_layout.addWidget(value)
            metrics.addWidget(card)
            self.metric_values[title] = value

        self.daily_list = QListWidget()
        self.daily_list.setAlternatingRowColors(True)
        heading = "날짜별 거리" if period == "week" else "일별 운동 거리"
        self.daily_heading = QLabel(heading)
        self.daily_heading.setStyleSheet("font-size: 16px; font-weight: 700; color: #172033;")

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("연도"))
        controls.addWidget(self.year_input)
        controls.addStretch(1)
        controls.addWidget(self.period_label)
        layout.addLayout(controls)
        self.month_selector = QWidget()
        self.month_grid = QGridLayout(self.month_selector)
        self.month_grid.setContentsMargins(0, 4, 0, 4)
        self.month_grid.setSpacing(6)
        for month in range(1, 13):
            button = QPushButton(f"{month}월")
            button.setCheckable(True)
            button.setMinimumHeight(34)
            button.clicked.connect(lambda _checked=False, value=month: self._select_month(value))
            self._month_group.addButton(button, month)
            self.month_buttons[month] = button
            self.month_grid.addWidget(button, (month - 1) // 6, (month - 1) % 6)
        layout.addWidget(QLabel("월 선택"))
        layout.addWidget(self.month_selector)
        self.week_selector = QWidget()
        self.week_buttons_layout = QHBoxLayout(self.week_selector)
        self.week_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.week_heading = QLabel("주차 선택")
        layout.addWidget(self.week_heading)
        layout.addWidget(self.week_selector)
        if self.period == "month":
            self.week_heading.hide()
            self.week_selector.hide()
            self.month_selector.setObjectName("monthButtonGrid")
        else:
            self.month_selector.setObjectName("weekMonthButtonGrid")
        self.setStyleSheet(
            "QPushButton { padding: 5px 10px; border: 1px solid #d8e1e5; "
            "border-radius: 7px; background: white; }"
            "QPushButton:checked { background: #27866f; color: white; "
            "border-color: #27866f; font-weight: 700; }"
        )
        self.month_buttons[self.selected_month].setChecked(True)
        self._rebuild_week_buttons()
        layout.addLayout(metrics)
        layout.addWidget(self.daily_heading)
        layout.addWidget(self.daily_list)
        self.refresh()

    def set_workouts(self, workouts: list[Workout]) -> None:
        self.workouts = workouts
        self.refresh()

    def _year_changed(self, year: int) -> None:
        self.selected_year = year
        self._rebuild_week_buttons()
        self.refresh()

    def _select_month(self, month: int) -> None:
        self.selected_month = month
        self.month_buttons[month].setChecked(True)
        self.selected_week = None
        self._rebuild_week_buttons()
        self.refresh()

    def _select_week(self, week: int) -> None:
        self.selected_week = week
        self.week_buttons[week].setChecked(True)
        if self.all_button is not None:
            self.all_button.setChecked(False)
        self.refresh()

    def _select_all(self) -> None:
        self.selected_week = None
        if self.all_button is not None:
            self.all_button.setChecked(True)
        self.refresh()

    def _rebuild_week_buttons(self) -> None:
        while self.week_buttons_layout.count():
            item = self.week_buttons_layout.takeAt(0)
            if item.widget() is not None:
                self._week_group.removeButton(item.widget())
                item.widget().deleteLater()
        self.week_buttons.clear()
        self.all_button = QPushButton("전체")
        self.all_button.setCheckable(True)
        self.all_button.setMinimumHeight(48)
        self.all_button.clicked.connect(self._select_all)
        self._week_group.addButton(self.all_button, 0)
        self.week_buttons_layout.addWidget(self.all_button)
        month_length = _days_in_month(self.selected_year, self.selected_month)
        week_count = (month_length + 6) // 7
        if self.selected_week is not None:
            self.selected_week = min(self.selected_week, week_count)
        for week in range(1, week_count + 1):
            first_day = (week - 1) * 7 + 1
            last_day = min(week * 7, month_length)
            button = QPushButton(f"{week}주차\n{first_day}~{last_day}일")
            button.setCheckable(True)
            button.setMinimumHeight(48)
            button.clicked.connect(
                lambda _checked=False, value=week: self._select_week(value)
            )
            self._week_group.addButton(button, week)
            self.week_buttons[week] = button
            self.week_buttons_layout.addWidget(button)
        if self.selected_week is None:
            self.all_button.setChecked(True)
        else:
            self.week_buttons[self.selected_week].setChecked(True)

    def refresh(self) -> None:
        selected = date(self.selected_year, self.selected_month, 1)
        if self.selected_week is None:
            start_date = selected
            end_date = date(
                self.selected_year,
                self.selected_month,
                _days_in_month(self.selected_year, self.selected_month),
            )
            period_text = f"{self.selected_year}년 {self.selected_month}월 전체"
        else:
            month_length = _days_in_month(self.selected_year, self.selected_month)
            first_day = (self.selected_week - 1) * 7 + 1
            last_day = min(self.selected_week * 7, month_length)
            start_date = date(self.selected_year, self.selected_month, first_day)
            end_date = date(self.selected_year, self.selected_month, last_day)
            period_text = (
                f"{self.selected_year}년 {self.selected_month}월 {self.selected_week}주차"
                f" · {first_day}~{last_day}일"
            )
        self.period_label.setText(period_text)

        period_workouts = [
            workout
            for workout in self.workouts
            if start_date <= workout.start_time.astimezone(KST).date() <= end_date
        ]
        period_workouts.sort(key=lambda workout: workout.start_time.astimezone(KST))
        total_distance = sum(workout.distance_m for workout in period_workouts)
        total_seconds = sum(max(workout.duration.total_seconds(), 0) for workout in period_workouts)
        total_hours, remainder = divmod(int(total_seconds), 3600)
        total_minutes = remainder // 60
        total_km = total_distance / 1000
        pace_seconds = total_seconds / total_km if total_km > 0 else None
        if pace_seconds is None:
            pace_text = "—"
        else:
            pace_minutes, seconds = divmod(int(round(pace_seconds)), 60)
            pace_text = f"{pace_minutes}:{seconds:02d} /km"

        self.metric_values["운동 횟수"].setText(f"{len(period_workouts)}회")
        self.metric_values["총 거리"].setText(f"{total_km:.2f} km")
        self.metric_values["총 운동 시간"].setText(
            f"{total_hours}시간 {total_minutes}분"
            if total_hours
            else f"{total_minutes}분"
        )
        self.metric_values["평균 거리"].setText(
            f"{total_km / len(period_workouts):.2f} km" if period_workouts else "—"
        )
        self.metric_values["평균 페이스"].setText(pace_text)

        daily: dict[date, list[Workout]] = {}
        for workout in period_workouts:
            day = workout.start_time.astimezone(KST).date()
            daily.setdefault(day, []).append(workout)
        self.daily_list.clear()
        if not daily:
            self.daily_list.addItem("선택한 기간에 운동 기록이 없습니다.")
            return
        for day in sorted(daily):
            day_workouts = daily[day]
            distance = sum(workout.distance_m for workout in day_workouts) / 1000
            self.daily_list.addItem(
                f"{day:%Y-%m-%d}  ·  {distance:.2f} km  ·  {len(day_workouts)}회"
            )


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        following_month = date(year + 1, 1, 1)
    else:
        following_month = date(year, month + 1, 1)
    return (following_month - timedelta(days=1)).day


class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(
        self,
        profile: Profile,
        workouts: list[Workout],
        profile_store: ProfileStore,
        workout_store: WorkoutStore,
    ) -> None:
        super().__init__()
        self.profile = profile
        self.workouts = workouts
        self.profile_store = profile_store
        self.workout_store = workout_store
        self.newest_first = True
        self._highlighted_calendar_dates: set[QDate] = set()
        self._workout_calendar_dates: set[QDate] = set()
        self._last_calendar_workout_date: QDate | None = None

        self.setWindowTitle("러닝 메이트")
        self.resize(1120, 760)
        self._build_ui()
        self._build_actions()
        self._refresh_workouts()
        self.statusBar().showMessage(f"{self.profile.name}님의 운동 기록 {len(self.workouts)}개")

    def _build_ui(self) -> None:
        self.setupUi(self)

        self.workout_list.currentItemChanged.connect(self._show_selected_workout)
        self.workout_list.setSpacing(5)
        self.calendar.setFixedSize(280, 280)
        self._calendar_last_selection = self.calendar.selectedDate()
        self.calendar.selectionChanged.connect(self._refresh_calendar_day)
        self.calendar.currentPageChanged.connect(
            lambda _year, _month: self._refresh_calendar_marks()
        )
        self.calendar_workout_list.currentItemChanged.connect(self._show_selected_workout)
        self.calendar_workout_list.setMaximumHeight(150)
        self.record_views.currentChanged.connect(self._update_sort_menu_visibility)
        self.detail_title.setStyleSheet("font-size: 22px; font-weight: 700; color: #172033;")
        self.detail_text.setStyleSheet("font-size: 13px; color: #687386;")
        self.metric_values: dict[str, QLabel] = {}
        self.metrics_layout.setSpacing(10)
        for title in ("거리", "운동 시간", "평균 속도", "최고 속도"):
            card = QFrame()
            card.setObjectName("metricCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            caption = QLabel(title)
            caption.setStyleSheet("color: #687386; font-size: 12px;")
            value = QLabel("—")
            value.setStyleSheet("color: #172033; font-size: 18px; font-weight: 700;")
            card_layout.addWidget(caption)
            card_layout.addWidget(value)
            self.metrics_layout.addWidget(card)
            self.metric_values[title] = value

        self.route_plot = WorkoutPlot("이동 경로", "route")
        self.elevation_plot = WorkoutPlot("고도 변화", "elevation")
        self.route_plot_container.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        self.elevation_plot_container.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        self.route_plot_layout.addWidget(self.route_plot)
        self.elevation_plot_layout.addWidget(self.elevation_plot)
        self.detailPanelLayout.setStretch(4, 3)
        self.detailPanelLayout.setStretch(5, 2)
        self.osm_map_toggle.toggled.connect(self._toggle_osm_map)
        self.route_plot.tile_error.connect(
            lambda message: self.statusBar().showMessage(message, 10000)
        )
        self.elevation_caption.setStyleSheet("color: #687386; font-size: 11px;")

        self.recordListPanel.setFixedWidth(300)
        self.recordListPanelLayout.setContentsMargins(0, 0, 0, 0)
        self.listPageLayout.setContentsMargins(6, 6, 6, 6)
        self.calendarPageLayout.setContentsMargins(6, 6, 6, 6)
        self.workout_list.setStyleSheet(
            "QListWidget { padding: 0px; }"
            "QListWidget::item { padding: 0px 6px; }"
        )
        self.weekly_summary = SummaryPage("week", self.workouts)
        self.main_tabs.addTab(self.weekly_summary, "주간 요약")
        self.main_tabs.currentChanged.connect(self._update_sort_menu_visibility)
        self.setStyleSheet(
            "QMainWindow { background: #f4f7f8; }"
            "QFrame#metricCard { background: white; border: 1px solid #e6ecef; border-radius: 10px; }"
            "QListWidget { background: white; border: 1px solid #e6ecef; border-radius: 8px; padding: 6px; }"
            "QListWidget::item { padding: 0px 6px; border-radius: 6px; }"
            "QListWidget::item:selected { background: #e5f3ef; color: #175b4c; }"
        )

    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("파일")
        import_gpx = QAction("데이터 불러오기", self)
        import_gpx.triggered.connect(self._choose_gpx)
        exit_action = QAction("종료", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(import_gpx)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        profile_menu = self.menuBar().addMenu("프로필")
        edit_profile = QAction("프로필 수정", self)
        edit_profile.triggered.connect(self._edit_profile)
        profile_menu.addAction(edit_profile)

        view_menu = self.menuBar().addMenu("보기")
        self.sort_menu = view_menu
        self.newest_action = QAction("최신순", self, checkable=True)
        self.oldest_action = QAction("과거순", self, checkable=True)
        self.newest_action.setChecked(True)
        self.newest_action.triggered.connect(lambda: self._set_sort_order(True))
        self.oldest_action.triggered.connect(lambda: self._set_sort_order(False))
        view_menu.addActions([self.newest_action, self.oldest_action])

        usage_menu = self.menuBar().addMenu("사용법")
        usage_action = QAction("사용법 보기", self)
        usage_action.triggered.connect(self._show_usage)
        usage_menu.addAction(usage_action)

        toolbar = QToolBar("기본 도구")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        toolbar.addAction(import_gpx)
        toolbar.addAction(edit_profile)
        self._update_sort_menu_visibility()

    def _show_usage(self) -> None:
        UsageDialog(self).exec_()

    def _toggle_osm_map(self, enabled: bool) -> None:
        if enabled:
            answer = QMessageBox.question(
                self,
                "온라인 지도 사용 동의",
                "OpenStreetMap 지도 타일을 불러오면 현재 운동 경로의 위치 정보가 "
                "인터넷을 통해 OpenStreetMap 타일 제공자에게 전달됩니다.\n\n"
                "지도를 불러오시겠습니까?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.osm_map_toggle.blockSignals(True)
                self.osm_map_toggle.setChecked(False)
                self.osm_map_toggle.blockSignals(False)
                return
        try:
            self.route_plot.set_osm_enabled(enabled)
        except OSError as error:
            self.route_plot.set_osm_enabled(False)
            self.osm_map_toggle.blockSignals(True)
            self.osm_map_toggle.setChecked(False)
            self.osm_map_toggle.blockSignals(False)
            QMessageBox.warning(
                self,
                "지도 캐시를 사용할 수 없습니다",
                f"지도 타일 캐시를 생성할 수 없어 지도를 켜지 못했습니다.\n{error}",
            )

    def _choose_gpx(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "데이터 불러오기", "", "GPX 파일 (*.gpx);;모든 파일 (*)"
        )
        if paths:
            self._import_gpx(paths)

    def _import_gpx(self, paths: list[str]) -> None:
        known_paths = {self._canonical_path(workout.gpx_path) for workout in self.workouts}
        imported: list[Workout] = []
        errors: list[str] = []
        duplicates = 0
        for path in paths:
            canonical = self._canonical_path(path)
            if canonical in known_paths:
                duplicates += 1
                continue
            try:
                workout = parse_gpx(path)
            except (GPXParseError, OSError, ValueError) as error:
                errors.append(str(error))
                continue
            imported.append(workout)
            known_paths.add(canonical)

        if imported:
            self.workouts.extend(imported)
            try:
                self.workout_store.save(self.workouts)
            except OSError as error:
                self.workouts = [workout for workout in self.workouts if workout not in imported]
                QMessageBox.critical(self, "저장 실패", f"운동 기록을 저장하지 못했습니다:\n{error}")
                return
            self._refresh_workouts()
            self.statusBar().showMessage(f"운동 기록 {len(imported)}개를 가져왔습니다.", 5000)

        messages = []
        if duplicates:
            messages.append(f"이미 등록된 파일 {duplicates}개는 건너뛰었습니다.")
        if errors:
            messages.append("가져오지 못한 파일:\n" + "\n".join(errors))
        if messages:
            QMessageBox.warning(self, "GPX 가져오기 결과", "\n\n".join(messages))

    def _edit_profile(self) -> None:
        dialog = ProfileDialog(self.profile, self)
        if dialog.exec_() != ProfileDialog.Accepted:
            return
        updated_profile = dialog.profile()
        try:
            self.profile_store.save(updated_profile)
        except OSError as error:
            QMessageBox.critical(self, "저장 실패", f"프로필을 저장하지 못했습니다:\n{error}")
            return
        self.profile = updated_profile
        self.statusBar().showMessage(f"{self.profile.name}님의 운동 기록 {len(self.workouts)}개")

    def _set_sort_order(self, newest_first: bool) -> None:
        self.newest_first = newest_first
        self.newest_action.setChecked(newest_first)
        self.oldest_action.setChecked(not newest_first)
        self._refresh_workouts()

    def _update_sort_menu_visibility(self, *_args: object) -> None:
        if not hasattr(self, "sort_menu"):
            return
        self.sort_menu.menuAction().setVisible(
            self.main_tabs.currentIndex() == 0
            and self.record_views.currentIndex() == 0
        )

    def _delete_workout(self, workout_id: str) -> None:
        workout = next((item for item in self.workouts if item.id == workout_id), None)
        if workout is None:
            return
        start = workout.start_time.astimezone(KST)
        prompt = (
            f"{start:%Y-%m-%d} {_format_korean_time(start)} 기록 "
            f"({workout.distance_m / 1000:.2f} km)을 삭제할까요?"
        )
        answer = QMessageBox.question(
            self,
            "운동 기록 삭제",
            prompt,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        remaining = [item for item in self.workouts if item.id != workout_id]
        try:
            self.workout_store.save(remaining)
        except OSError as error:
            QMessageBox.critical(self, "저장 실패", f"운동 기록을 삭제하지 못했습니다:\n{error}")
            return
        self.workouts = remaining
        self._refresh_workouts()
        self.statusBar().showMessage("운동 기록을 삭제했습니다.", 5000)

    def _add_workout_row(
        self,
        workout_list: QListWidget,
        workout: Workout,
        label: str,
    ) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.UserRole, workout.id)
        row = WorkoutListRow(
            label,
            on_select=lambda: workout_list.setCurrentItem(item),
            on_delete=lambda _checked=False, workout_id=workout.id: self._delete_workout(workout_id),
        )
        item.setSizeHint(QSize(0, max(42, row.sizeHint().height())))
        workout_list.addItem(item)
        workout_list.setItemWidget(item, row)
        return item

    def _refresh_workouts(self) -> None:
        selected_id = (
            self.workout_list.currentItem().data(Qt.UserRole)
            if self.workout_list.currentItem()
            else None
        )
        ordered = sorted(
            self.workouts,
            key=lambda workout: workout.start_time.astimezone(UTC),
            reverse=self.newest_first,
        )
        self.workout_list.clear()
        selected_item: QListWidgetItem | None = None
        for workout in ordered:
            item = self._add_workout_row(
                self.workout_list,
                workout,
                self._list_label(workout),
            )
            if workout.id == selected_id:
                selected_item = item
        self._refresh_calendar_marks()
        self._refresh_calendar_day()
        self.weekly_summary.set_workouts(self.workouts)
        if selected_item is not None:
            self.workout_list.setCurrentItem(selected_item)
        elif self.workout_list.count():
            self.workout_list.setCurrentRow(0)
        else:
            self._show_selected_workout(None, None)
        self.statusBar().showMessage(f"{self.profile.name}님의 운동 기록 {len(self.workouts)}개")

    def _refresh_calendar_marks(self) -> None:
        default_format = QTextCharFormat()
        for marked_date in self._highlighted_calendar_dates:
            self.calendar.setDateTextFormat(marked_date, default_format)
        marked_dates = {
            QDate(day.year, day.month, day.day)
            for day in (workout.start_time.astimezone(KST).date() for workout in self.workouts)
        }
        self._workout_calendar_dates = marked_dates
        self.calendar.setEnabled(bool(marked_dates))
        page_start = QDate(self.calendar.yearShown(), self.calendar.monthShown(), 1)
        disabled_format = QTextCharFormat()
        disabled_format.setForeground(QColor("#c2c8d0"))
        for day in range(1, page_start.daysInMonth() + 1):
            date = QDate(page_start.year(), page_start.month(), day)
            if date not in marked_dates:
                self.calendar.setDateTextFormat(date, disabled_format)
        workout_format = QTextCharFormat()
        workout_format.setBackground(QColor("#d8eee7"))
        workout_format.setForeground(QColor("#175b4c"))
        workout_format.setFontWeight(QFont.Bold)
        for marked_date in marked_dates:
            self.calendar.setDateTextFormat(marked_date, workout_format)
        self._highlighted_calendar_dates = marked_dates
        if self._last_calendar_workout_date not in marked_dates:
            self._last_calendar_workout_date = (
                max(marked_dates) if marked_dates else None
            )

    def _refresh_calendar_day(self) -> None:
        selected_qdate = self.calendar.selectedDate()
        if selected_qdate not in self._workout_calendar_dates:
            selected_qdate = (
                self._last_calendar_workout_date
                if self._last_calendar_workout_date is not None
                else self._calendar_last_selection
            )
            if selected_qdate is None:
                self.calendar_workout_list.clear()
                return
            self.calendar.blockSignals(True)
            self.calendar.setSelectedDate(selected_qdate)
            self.calendar.blockSignals(False)
        self._last_calendar_workout_date = selected_qdate
        self._calendar_last_selection = selected_qdate
        selected_date = selected_qdate.toPyDate()
        day_workouts = [
            workout
            for workout in self.workouts
            if workout.start_time.astimezone(KST).date() == selected_date
        ]
        day_workouts.sort(key=lambda workout: workout.start_time.astimezone(UTC), reverse=True)
        self.calendar_workout_list.clear()
        for workout in day_workouts:
            self._add_workout_row(
                self.calendar_workout_list,
                workout,
                self._calendar_item_label(workout),
            )
        if day_workouts:
            self.calendar_workout_list.setCurrentRow(0)

    def _show_selected_workout(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            self.detail_title.setText("운동 기록을 선택해 주세요.")
            self.detail_text.clear()
            for value in self.metric_values.values():
                value.setText("—")
            self.route_plot.set_workout(None)
            self.elevation_plot.set_workout(None)
            return
        workout_id = current.data(Qt.UserRole)
        workout = next((item for item in self.workouts if item.id == workout_id), None)
        if workout is None:
            return
        start = workout.start_time.astimezone(KST)
        end = workout.end_time.astimezone(KST)
        duration_seconds = max(int(workout.duration.total_seconds()), 0)
        hours, remainder = divmod(duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        duration_text = f"{hours}시간 {minutes}분" if hours else f"{minutes}분 {seconds}초"
        max_speed = workout.maximum_speed_mps
        self.detail_title.setText(f"{start:%Y년 %m월 %d일}")
        self.detail_text.setText(
            f"{_format_korean_time(start)} 시작  ·  {_format_korean_time(end)} 종료 (한국 시간)"
        )
        self.metric_values["거리"].setText(f"{workout.distance_m / 1000:.2f} km")
        self.metric_values["운동 시간"].setText(duration_text)
        self.metric_values["평균 속도"].setText(f"{workout.average_speed_kmh:.1f} km/h")
        self.metric_values["최고 속도"].setText(
            f"{max_speed * 3.6:.1f} km/h" if max_speed is not None else "—"
        )
        self.route_plot.set_workout(workout)
        self.elevation_plot.set_workout(workout)

    @staticmethod
    def _list_label(workout: Workout) -> str:
        start = workout.start_time.astimezone(KST)
        return f"{start:%Y-%m-%d}"

    @staticmethod
    def _calendar_item_label(workout: Workout) -> str:
        start = workout.start_time.astimezone(KST)
        return f"{start:%Y-%m-%d}\n{_format_korean_time(start)}"

    @staticmethod
    def _canonical_path(path: str) -> str:
        return str(Path(path).resolve()).casefold()


def _format_korean_time(value: datetime) -> str:
    hour = value.hour
    period = "오전" if hour < 12 else "오후"
    display_hour = hour % 12 or 12
    return f"{period} {display_hour}:{value.minute:02d}"


def _route_intensity_colors(
    points: list[TrackPoint],
) -> tuple[str | None, list[QColor]]:
    speed_values: list[float | None] = []
    elevation_values: list[float | None] = []
    for first, second in zip(points, points[1:]):
        distance = distance_m(first, second)
        known_speeds = [
            value
            for value in (first.speed_mps, second.speed_mps)
            if value is not None and math.isfinite(value) and value >= 0
        ]
        speed = sum(known_speeds) / len(known_speeds) if known_speeds else None
        if speed is None and first.timestamp is not None and second.timestamp is not None:
            elapsed_seconds = (second.timestamp - first.timestamp).total_seconds()
            if elapsed_seconds > 0:
                speed = distance / elapsed_seconds
        speed_values.append(speed)

        if (
            distance > 0
            and first.elevation_m is not None
            and second.elevation_m is not None
            and math.isfinite(first.elevation_m)
            and math.isfinite(second.elevation_m)
        ):
            elevation_values.append(abs(second.elevation_m - first.elevation_m) / distance)
        else:
            elevation_values.append(None)

    relative_speeds = _relative_scores(speed_values)
    relative_elevations = _relative_scores(elevation_values)
    segment_values = [
        sum(value for value in (speed, elevation) if value is not None)
        / sum(value is not None for value in (speed, elevation))
        if speed is not None or elevation is not None
        else None
        for speed, elevation in zip(relative_speeds, relative_elevations)
    ]
    valid_values = [value for value in segment_values if value is not None]
    if not valid_values:
        return None, [QColor("#8490a3") for _ in segment_values]

    low_threshold = _percentile(valid_values, 1 / 3)
    high_threshold = _percentile(valid_values, 2 / 3)
    if low_threshold == high_threshold:
        colors = [QColor("#f4c542") for _ in segment_values]
    else:
        colors = []
        for value in segment_values:
            if value is None:
                colors.append(QColor("#8490a3"))
            elif value <= low_threshold:
                colors.append(QColor("#2eaa68"))
            elif value <= high_threshold:
                colors.append(QColor("#f4c542"))
            else:
                colors.append(QColor("#e74c3c"))
    return "속도·고도", colors


def _relative_scores(values: list[float | None]) -> list[float | None]:
    valid_values = sorted(value for value in values if value is not None)
    if not valid_values:
        return [None for _ in values]
    if len(valid_values) == 1:
        return [0.5 if value is not None else None for value in values]
    return [
        (
            (bisect_left(valid_values, value) + bisect_right(valid_values, value) - 1)
            / (2 * (len(valid_values) - 1))
            if value is not None
            else None
        )
        for value in values
    ]


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _fit_aspect_rect(rect: object, aspect_ratio: float) -> QRectF:
    width = rect.width()
    height = rect.height()
    if width / max(height, 1) > aspect_ratio:
        fitted_width = height * aspect_ratio
        return QRectF(
            rect.center().x() - fitted_width / 2,
            rect.top(),
            fitted_width,
            height,
        )
    fitted_height = width / aspect_ratio
    return QRectF(
        rect.left(),
        rect.center().y() - fitted_height / 2,
        width,
        fitted_height,
    )


def _mercator_world_pixel(latitude: float, longitude: float, zoom: int) -> tuple[float, float]:
    latitude = max(min(latitude, 85.05112878), -85.05112878)
    world_size = 256 * (2**zoom)
    x = (longitude + 180) / 360 * world_size
    latitude_radians = math.radians(latitude)
    y = (
        1 - math.asinh(math.tan(latitude_radians)) / math.pi
    ) / 2 * world_size
    return x, y


def _map_view_transform(
    plot: object,
    points: list[object],
) -> tuple[int, float, float, float, float]:
    projected = [
        _mercator_world_pixel(point.latitude, point.longitude, 0)
        for point in points
    ]
    min_x = min(point[0] for point in projected)
    max_x = max(point[0] for point in projected)
    min_y = min(point[1] for point in projected)
    max_y = max(point[1] for point in projected)
    center_x_at_zero = (min_x + max_x) / 2
    center_y_at_zero = (min_y + max_y) / 2
    width_at_zero = max(max_x - min_x, 1 / 256)
    height_at_zero = max(max_y - min_y, 1 / 256)
    available_width = max(plot.width() * 0.94, 1)
    available_height = max(plot.height() * 0.94, 1)
    zoom = 0
    for candidate in range(19, -1, -1):
        scale = 2**candidate
        if (
            width_at_zero * scale <= available_width
            and height_at_zero * scale <= available_height
        ):
            zoom = candidate
            break
    scale = 2**zoom
    center_x = center_x_at_zero * scale
    center_y = center_y_at_zero * scale
    origin_x = center_x - plot.width() / 2
    origin_y = center_y - plot.height() / 2
    return zoom, center_x, center_y, origin_x, origin_y
