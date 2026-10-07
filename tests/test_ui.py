import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QDate, QPointF, QRect, QRectF, Qt
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtWidgets import QApplication, QLabel, QMessageBox, QToolButton

from workout_app.models import Profile, TrackPoint, Workout
from workout_app.storage import ProfileStore, WorkoutStore
from workout_app.ui import (
    MainWindow,
    ProfileDialog,
    UsageDialog,
    WorkoutPlot,
    _format_korean_time,
    _fit_aspect_rect,
    _map_view_transform,
    _route_intensity_colors,
)


class WorkoutWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_latest_first_selection_and_sort_controls(self) -> None:
        older = Workout(
            start_time=datetime(2026, 10, 4, 10, tzinfo=timezone.utc),
            end_time=datetime(2026, 10, 4, 11, tzinfo=timezone.utc),
            distance_m=4000,
            gpx_path="older.gpx",
            name="Older",
        )
        newer = Workout(
            start_time=datetime(2026, 10, 5, 8, tzinfo=timezone.utc),
            end_time=datetime(2026, 10, 5, 9, tzinfo=timezone.utc),
            distance_m=5000,
            gpx_path="newer.gpx",
            name="Newer",
        )
        with TemporaryDirectory() as directory:
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [older, newer],
                ProfileStore(Path(directory) / "profile.json"),
                WorkoutStore(Path(directory) / "workouts.json"),
            )

            self.assertEqual(window.workout_list.item(0).data(Qt.UserRole), newer.id)
            item_label = window.workout_list.itemWidget(window.workout_list.item(0)).findChild(QLabel)
            self.assertEqual(item_label.text(), "2026-10-05")
            self.assertNotIn("newer.gpx", item_label.text())
            self.assertNotIn("Newer", item_label.text())
            self.assertEqual(window.detail_title.text(), "2026년 10월 05일")
            self.assertIn("오후 5:00 시작", window.detail_text.text())
            self.assertIn("오후 6:00 종료", window.detail_text.text())
            self.assertEqual(window.metric_values["거리"].text(), "5.00 km")
            self.assertEqual(
                window.elevation_caption.text(),
                "운동 경로를 따라 기록된 고도의 오르내림을 보여줍니다.",
            )
            self.assertEqual(window.windowTitle(), "러닝 메이트")
            self.assertFalse(hasattr(window, "point_table"))
            self.assertNotIn("GPS 포인트", window.detail_text.text())
            self.assertNotIn("파일:", window.detail_text.text())
            self.assertIs(window.route_plot.workout, newer)
            window._set_sort_order(False)
            self.assertEqual(window.workout_list.item(0).data(Qt.UserRole), older.id)
            self.assertTrue(window.oldest_action.isChecked())
            self.assertTrue(window.sort_menu.menuAction().isVisible())

    def test_record_view_tabs_and_date_only_list(self) -> None:
        workout = Workout(
            start_time=datetime(2026, 10, 5, 8, tzinfo=timezone.utc),
            end_time=datetime(2026, 10, 5, 9, tzinfo=timezone.utc),
            distance_m=5000,
            gpx_path="run.gpx",
            name="Run",
        )
        with TemporaryDirectory() as directory:
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [workout],
                ProfileStore(Path(directory) / "profile.json"),
                WorkoutStore(Path(directory) / "workouts.json"),
            )
            self.assertEqual(window.record_views.tabText(0), "리스트로 보기")
            self.assertEqual(window.record_views.tabText(1), "캘린더로 보기")
            self.assertEqual(window.main_tabs.tabText(0), "일일 요약")
            self.assertEqual(window.recordListPanel.width(), 300)
            self.assertFalse(hasattr(window, "record_splitter"))
            item_label = window.workout_list.itemWidget(window.workout_list.item(0)).findChild(QLabel)
            self.assertEqual(item_label.text(), "2026-10-05")
            self.assertEqual(window.calendar.width(), window.calendar.height())
            self.assertLessEqual(window.calendar.width(), 300)
            window.record_views.setCurrentIndex(1)
            self.assertFalse(window.sort_menu.menuAction().isVisible())
            window.record_views.setCurrentIndex(0)
            window.main_tabs.setCurrentIndex(1)
            self.assertFalse(window.sort_menu.menuAction().isVisible())

    def test_main_window_loads_layout_from_project_gui_ui(self) -> None:
        with TemporaryDirectory() as directory:
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [],
                ProfileStore(Path(directory) / "profile.json"),
                WorkoutStore(Path(directory) / "workouts.json"),
            )

            designer_file = Path(__file__).resolve().parents[1] / "gui.ui"
            self.assertTrue(designer_file.is_file())
            self.assertIsNotNone(window.findChild(QLabel, "elevation_caption"))
            self.assertIsNotNone(window.findChild(QLabel, "detail_title"))
            self.assertEqual(window.main_tabs.tabText(0), "일일 요약")

    def test_import_dialog_always_allows_multiple_files(self) -> None:
        with TemporaryDirectory() as directory:
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [],
                ProfileStore(Path(directory) / "profile.json"),
                WorkoutStore(Path(directory) / "workouts.json"),
            )
            selected_paths = ["one.gpx", "two.gpx"]
            with (
                patch(
                    "workout_app.ui.QFileDialog.getOpenFileNames",
                    return_value=(selected_paths, "GPX 파일 (*.gpx)"),
                ) as multi_picker,
                patch.object(window, "_import_gpx") as import_gpx,
            ):
                window._choose_gpx()

            multi_picker.assert_called_once()
            self.assertEqual(multi_picker.call_args.args[1], "데이터 불러오기")
            import_gpx.assert_called_once_with(selected_paths)

    def test_profile_dialog_has_no_goal_field(self) -> None:
        dialog = ProfileDialog(Profile("Test", "male", 27, 175, 70))
        self.assertEqual(dialog.profile().name, "Test")
        self.assertEqual(dialog.profile().gender, "male")
        self.assertFalse(hasattr(dialog, "goal_input"))

    def test_korean_time_uses_am_pm_and_twelve_hour_clock(self) -> None:
        self.assertEqual(_format_korean_time(datetime(2026, 1, 1, 0, 5)), "오전 12:05")
        self.assertEqual(_format_korean_time(datetime(2026, 1, 1, 11, 59)), "오전 11:59")
        self.assertEqual(_format_korean_time(datetime(2026, 1, 1, 12, 0)), "오후 12:00")
        self.assertEqual(_format_korean_time(datetime(2026, 1, 1, 23, 59)), "오후 11:59")

    def test_usage_menu_opens_instructions_covering_health_export_and_results(self) -> None:
        with TemporaryDirectory() as directory:
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [],
                ProfileStore(Path(directory) / "profile.json"),
                WorkoutStore(Path(directory) / "workouts.json"),
            )
            menu_labels = [action.text() for action in window.menuBar().actions()]
            self.assertIn("보기", menu_labels)
            self.assertEqual(menu_labels[-1], "사용법")
            instructions = UsageDialog(window)
            help_text = instructions.instructions.toPlainText()
            self.assertIn("모든 건강 데이터 내보내기", help_text)
            self.assertIn("ZIP", help_text)
            self.assertIn("GPX", help_text)
            self.assertIn("평균 속도", help_text)
            self.assertIn("OpenStreetMap", help_text)
            self.assertIn("위치 정보가 타일 제공자에게 전달됩니다", help_text)

    def test_osm_map_requires_explicit_location_consent(self) -> None:
        with TemporaryDirectory() as directory:
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [],
                ProfileStore(Path(directory) / "profile.json"),
                WorkoutStore(Path(directory) / "workouts.json"),
            )
            self.assertFalse(window.osm_map_toggle.isChecked())
            self.assertFalse(window.route_plot.osm_enabled)
            self.assertIsNone(window.route_plot._network)
            with patch(
                "workout_app.ui.QMessageBox.question",
                return_value=QMessageBox.No,
            ) as confirmation:
                window.osm_map_toggle.setChecked(True)

            confirmation.assert_called_once()
            self.assertFalse(window.osm_map_toggle.isChecked())
            self.assertFalse(window.route_plot.osm_enabled)
            self.assertIsNone(window.route_plot._network)

    def test_osm_map_starts_only_after_consent_is_accepted(self) -> None:
        with TemporaryDirectory() as directory:
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [],
                ProfileStore(Path(directory) / "profile.json"),
                WorkoutStore(Path(directory) / "workouts.json"),
            )
            with (
                patch(
                    "workout_app.ui.QMessageBox.question",
                    return_value=QMessageBox.Yes,
                ),
                patch.object(window.route_plot, "_create_tile_network") as create_network,
            ):
                window.osm_map_toggle.setChecked(True)

            self.assertTrue(window.route_plot.osm_enabled)
            create_network.assert_called_once()

    def test_osm_tile_request_identifies_app_and_uses_https(self) -> None:
        plot = WorkoutPlot("Route", "route")
        reply = Mock()
        reply.finished = Mock()
        plot._network = Mock()
        plot._network.get.return_value = reply

        plot._request_tile((12, 345, 678))

        request = plot._network.get.call_args.args[0]
        self.assertEqual(
            request.url().toString(),
            "https://tile.openstreetmap.org/12/345/678.png",
        )
        self.assertEqual(
            bytes(request.rawHeader(b"User-Agent")),
            b"RunningMate/1.0 (personal GPX workout application)",
        )
        self.assertEqual(
            request.attribute(request.CacheLoadControlAttribute),
            request.PreferCache,
        )
        reply.finished.connect.assert_called_once()

    def test_osm_view_transform_fits_gps_points_inside_viewport(self) -> None:
        class PlotBounds:
            def width(self) -> int:
                return 600

            def height(self) -> int:
                return 300

        class Point:
            def __init__(self, latitude: float, longitude: float) -> None:
                self.latitude = latitude
                self.longitude = longitude

        points = [Point(37.5, 127.0), Point(37.501, 127.002)]
        zoom, center_x, center_y, origin_x, origin_y = _map_view_transform(
            PlotBounds(), points
        )
        self.assertGreaterEqual(zoom, 0)
        self.assertLessEqual(zoom, 19)
        world_size = 256 * 2**zoom
        self.assertGreaterEqual(center_x, 0)
        self.assertLessEqual(center_x, world_size)
        self.assertGreaterEqual(center_y, 0)
        self.assertLessEqual(center_y, world_size)
        self.assertAlmostEqual(origin_x + 300, center_x)
        self.assertAlmostEqual(origin_y + 150, center_y)

    def test_route_intensity_combines_relative_speed_and_elevation_change(self) -> None:
        points = [
            TrackPoint(
                latitude=37.5,
                longitude=127 + index * 0.001,
                elevation_m=float(index * index),
                timestamp=None,
                speed_mps=float(index + 1),
                heart_rate_bpm=float(200 - index * 20),
            )
            for index in range(7)
        ]
        metric, colors = _route_intensity_colors(points)
        self.assertEqual(metric, "속도·고도")
        self.assertEqual(colors[0].name(), "#2eaa68")
        self.assertEqual(colors[-1].name(), "#e74c3c")
        self.assertIn("#f4c542", {color.name() for color in colors})

        opposing_elevation_points = [
            TrackPoint(
                latitude=0,
                longitude=127 + index * 0.001,
                elevation_m=float(height),
                timestamp=None,
                speed_mps=float(index + 1),
            )
            for index, height in enumerate((0, 6, 11, 15, 18, 20, 21))
        ]
        _, opposing_colors = _route_intensity_colors(opposing_elevation_points)
        self.assertEqual(
            {color.name() for color in opposing_colors},
            {"#f4c542"},
        )

    def test_route_intensity_uses_available_speed_when_elevation_is_missing(self) -> None:
        points = [
            TrackPoint(
                latitude=37.5,
                longitude=127 + index * 0.001,
                elevation_m=None,
                timestamp=None,
                speed_mps=float(10 - index),
            )
            for index in range(3)
        ]
        metric, colors = _route_intensity_colors(points)
        self.assertEqual(metric, "속도·고도")
        self.assertEqual(colors[0].name(), "#e74c3c")
        self.assertEqual(colors[-1].name(), "#2eaa68")

    def test_route_plot_renders_colored_track_segments(self) -> None:
        workout = Workout(
            start_time=datetime(2026, 10, 5, 8, tzinfo=timezone.utc),
            end_time=datetime(2026, 10, 5, 9, tzinfo=timezone.utc),
            distance_m=300,
            gpx_path="run.gpx",
            name="Run",
            track_points=[
                TrackPoint(37.5, 127.0, None, None, speed_mps=1),
                TrackPoint(37.501, 127.001, None, None, speed_mps=2),
                TrackPoint(37.502, 127.002, None, None, speed_mps=3),
            ],
        )
        plot = WorkoutPlot("이동 경로", "route")
        plot.resize(640, 320)
        plot.set_workout(workout)
        image = QImage(plot.size(), QImage.Format_ARGB32)

        plot.render(image)

        self.assertFalse(image.isNull())

    def test_osm_map_hides_title_and_uses_full_route_plot_area(self) -> None:
        workout = Workout(
            start_time=datetime(2026, 10, 5, 8, tzinfo=timezone.utc),
            end_time=datetime(2026, 10, 5, 9, tzinfo=timezone.utc),
            distance_m=300,
            gpx_path="run.gpx",
            name="Run",
            track_points=[
                TrackPoint(37.5, 127.0, None, None, speed_mps=1),
                TrackPoint(37.501, 127.001, None, None, speed_mps=2),
            ],
        )
        plot = WorkoutPlot("이동 경로", "route")
        plot.resize(640, 320)
        plot.set_workout(workout)
        plot.osm_enabled = True
        image = QImage(plot.size(), QImage.Format_ARGB32)
        with (
            patch.object(plot, "_draw_title") as draw_title,
            patch.object(plot, "_draw_osm_tiles") as draw_tiles,
            patch.object(plot, "_draw_projected_route"),
            patch.object(plot, "_draw_attribution") as draw_attribution,
        ):
            plot.render(image)

        draw_title.assert_not_called()
        map_rect = draw_tiles.call_args.args[1]
        self.assertAlmostEqual(map_rect.width() / map_rect.height(), 4 / 3)
        self.assertAlmostEqual(map_rect.center().x(), plot.rect().center().x(), delta=1)
        self.assertAlmostEqual(map_rect.center().y(), plot.rect().center().y(), delta=1)
        self.assertEqual(
            draw_attribution.call_args.args[1],
            plot.rect().adjusted(2, 2, -2, -2),
        )

        painter = QPainter(image)
        with patch.object(plot, "_draw_intensity_legend") as draw_legend:
            plot._paint_intensity_route(
                painter,
                [QPointF(50, 50), QPointF(100, 100)],
                workout.track_points,
                map_rect,
            )
        painter.end()
        self.assertEqual(
            draw_legend.call_args.args[1],
            plot.rect().adjusted(2, 2, -2, -2),
        )

    def test_route_plot_expands_when_main_window_is_resized(self) -> None:
        with TemporaryDirectory() as directory:
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [],
                ProfileStore(Path(directory) / "profile.json"),
                WorkoutStore(Path(directory) / "workouts.json"),
            )
            window.show()
            self.app.processEvents()
            original_size = window.route_plot.size()

            window.resize(1500, 1000)
            self.app.processEvents()
            enlarged_size = window.route_plot.size()

            self.assertGreater(enlarged_size.width(), original_size.width())
            self.assertGreater(enlarged_size.height(), original_size.height())
            window.close()

    def test_aspect_fit_keeps_map_canvas_at_four_by_three(self) -> None:
        wide_bounds = QRectF(0, 0, 1200, 600)
        tall_bounds = QRectF(0, 0, 600, 900)
        wide_rect = _fit_aspect_rect(wide_bounds, 4 / 3)
        tall_rect = _fit_aspect_rect(tall_bounds, 4 / 3)

        self.assertAlmostEqual(wide_rect.width() / wide_rect.height(), 4 / 3)
        self.assertAlmostEqual(tall_rect.width() / tall_rect.height(), 4 / 3)
        self.assertAlmostEqual(wide_rect.center().x(), wide_bounds.center().x())
        self.assertAlmostEqual(tall_rect.center().y(), tall_bounds.center().y())

    def test_osm_draw_requests_current_view_tiles(self) -> None:
        class Point:
            def __init__(self, latitude: float, longitude: float) -> None:
                self.latitude = latitude
                self.longitude = longitude

        plot_bounds = QRect(22, 42, 596, 254)
        points = [Point(37.5, 127.0), Point(37.501, 127.002)]
        zoom, _, _, origin_x, origin_y = _map_view_transform(
            plot_bounds, points
        )
        widget = WorkoutPlot("Route", "route")
        widget._network = Mock()
        reply = Mock()
        reply.finished = Mock()
        widget._network.get.return_value = reply
        image = QImage(640, 320, QImage.Format_ARGB32)
        painter = QPainter(image)
        widget._draw_osm_tiles(
            painter, plot_bounds, zoom, origin_x, origin_y
        )
        painter.end()

        requested_urls = [
            call.args[0].url().toString()
            for call in widget._network.get.call_args_list
        ]
        self.assertGreater(len(requested_urls), 0)
        self.assertLessEqual(len(requested_urls), 16)
        self.assertTrue(
            all(url.startswith(f"https://tile.openstreetmap.org/{zoom}/") for url in requested_urls)
        )

    def test_weekly_summary_supports_month_total_and_average_distance(self) -> None:
        workouts = [
            Workout(
                start_time=datetime(2026, 10, 4, 14, tzinfo=timezone.utc),
                end_time=datetime(2026, 10, 4, 14, 20, tzinfo=timezone.utc),
                distance_m=4000,
                gpx_path="sunday.gpx",
                name="Sunday",
            ),
            Workout(
                start_time=datetime(2026, 10, 7, 15, tzinfo=timezone.utc),
                end_time=datetime(2026, 10, 7, 15, 40, tzinfo=timezone.utc),
                distance_m=8000,
                gpx_path="monday.gpx",
                name="Monday",
            ),
        ]
        with TemporaryDirectory() as directory:
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                workouts,
                ProfileStore(Path(directory) / "profile.json"),
                WorkoutStore(Path(directory) / "workouts.json"),
            )
            self.assertIsNone(window.weekly_summary.selected_week)
            self.assertTrue(window.weekly_summary.all_button.isChecked())
            window.weekly_summary.year_input.setValue(2026)
            window.weekly_summary.month_buttons[10].click()
            self.assertIsNone(window.weekly_summary.selected_week)
            self.assertTrue(window.weekly_summary.all_button.isChecked())
            window.weekly_summary.week_buttons[2].click()
            self.assertEqual(window.weekly_summary.metric_values["운동 횟수"].text(), "1회")
            self.assertEqual(window.weekly_summary.metric_values["총 거리"].text(), "8.00 km")
            self.assertEqual(window.weekly_summary.metric_values["평균 거리"].text(), "8.00 km")
            self.assertEqual(
                list(window.weekly_summary.metric_values)[-2:],
                ["평균 거리", "평균 페이스"],
            )
            self.assertEqual(window.weekly_summary.daily_list.count(), 1)
            self.assertEqual(
                window.weekly_summary.week_buttons_layout.itemAt(0).widget().text(),
                "전체",
            )

            window.weekly_summary.all_button.click()
            self.assertEqual(window.weekly_summary.metric_values["운동 횟수"].text(), "2회")
            self.assertEqual(window.weekly_summary.metric_values["총 거리"].text(), "12.00 km")
            self.assertEqual(window.weekly_summary.metric_values["평균 거리"].text(), "6.00 km")
            self.assertEqual(window.weekly_summary.daily_list.count(), 2)
            self.assertEqual(window.main_tabs.count(), 2)
            self.assertEqual(
                [window.main_tabs.tabText(i) for i in range(window.main_tabs.count())],
                ["일일 요약", "주간 요약"],
            )
            window.weekly_summary.month_buttons[2].click()
            window.weekly_summary.year_input.setValue(2024)
            self.assertEqual(len(window.weekly_summary.week_buttons), 5)
            self.assertEqual(window.weekly_summary.week_buttons[5].text(), "5주차\n29~29일")

    def test_calendar_tab_lists_workouts_for_selected_date(self) -> None:
        workout = Workout(
            start_time=datetime(2026, 10, 5, 8, tzinfo=timezone.utc),
            end_time=datetime(2026, 10, 5, 9, tzinfo=timezone.utc),
            distance_m=5000,
            gpx_path="run.gpx",
            name="Run",
        )
        with TemporaryDirectory() as directory:
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [workout],
                ProfileStore(Path(directory) / "profile.json"),
                WorkoutStore(Path(directory) / "workouts.json"),
            )
            window.record_views.setCurrentIndex(1)
            window.calendar.setSelectedDate(QDate(2026, 10, 5))

            self.assertEqual(window.calendar_workout_list.count(), 1)
            self.assertEqual(
                window.calendar_workout_list.currentItem().data(Qt.UserRole),
                workout.id,
            )
            row = window.calendar_workout_list.itemWidget(
                window.calendar_workout_list.currentItem()
            )
            self.assertIsNotNone(row.findChild(QToolButton))
            self.assertEqual(
                row.findChild(QLabel).text(),
                "2026-10-05\n오후 5:00",
            )
            self.assertGreater(row.findChild(QLabel).width(), 0)
            self.assertGreater(row.findChild(QToolButton).width(), 0)
            item_rect = window.calendar_workout_list.visualItemRect(
                window.calendar_workout_list.currentItem()
            )
            self.assertLessEqual(row.geometry().top() - item_rect.top(), 2)
            self.assertAlmostEqual(
                row.findChild(QLabel).geometry().center().y(),
                row.findChild(QToolButton).geometry().center().y(),
                delta=1,
            )
            self.assertNotIn(
                "선택한 날짜의 기록",
                [label.text() for label in window.findChildren(QLabel)],
            )
            self.assertFalse(window.sort_menu.menuAction().isVisible())

    def test_calendar_rejects_dates_without_workouts(self) -> None:
        workout = Workout(
            start_time=datetime(2026, 10, 5, 8, tzinfo=timezone.utc),
            end_time=datetime(2026, 10, 5, 9, tzinfo=timezone.utc),
            distance_m=5000,
            gpx_path="run.gpx",
            name="Run",
        )
        with TemporaryDirectory() as directory:
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [workout],
                ProfileStore(Path(directory) / "profile.json"),
                WorkoutStore(Path(directory) / "workouts.json"),
            )
            window.calendar.setSelectedDate(QDate(2026, 10, 6))

            self.assertEqual(window.calendar.selectedDate(), QDate(2026, 10, 5))
            self.assertEqual(window.calendar_workout_list.count(), 1)
            self.assertEqual(
                window.calendar_workout_list.currentItem().data(Qt.UserRole),
                workout.id,
            )

    def test_calendar_is_disabled_when_there_are_no_workouts(self) -> None:
        with TemporaryDirectory() as directory:
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [],
                ProfileStore(Path(directory) / "profile.json"),
                WorkoutStore(Path(directory) / "workouts.json"),
            )

            self.assertFalse(window.calendar.isEnabled())

    def test_inline_delete_button_confirms_and_persists_deletion(self) -> None:
        workout = Workout(
            start_time=datetime(2026, 10, 5, 8, tzinfo=timezone.utc),
            end_time=datetime(2026, 10, 5, 9, tzinfo=timezone.utc),
            distance_m=5000,
            gpx_path="run.gpx",
            name="Run",
        )
        with TemporaryDirectory() as directory:
            store = WorkoutStore(Path(directory) / "workouts.json")
            store.save([workout])
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [workout],
                ProfileStore(Path(directory) / "profile.json"),
                store,
            )
            row = window.workout_list.itemWidget(window.workout_list.item(0))
            remove_button = row.findChild(QToolButton)
            self.assertIsNotNone(remove_button)
            with patch(
                "workout_app.ui.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ) as confirmation:
                remove_button.click()

            confirmation.assert_called_once()
            self.assertEqual(window.workouts, [])
            self.assertEqual(store.load(), [])
            self.assertEqual(window.workout_list.count(), 0)

    def test_inline_delete_cancel_keeps_record(self) -> None:
        workout = Workout(
            start_time=datetime(2026, 10, 5, 8, tzinfo=timezone.utc),
            end_time=datetime(2026, 10, 5, 9, tzinfo=timezone.utc),
            distance_m=5000,
            gpx_path="run.gpx",
            name="Run",
        )
        with TemporaryDirectory() as directory:
            store = WorkoutStore(Path(directory) / "workouts.json")
            store.save([workout])
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [workout],
                ProfileStore(Path(directory) / "profile.json"),
                store,
            )
            row = window.workout_list.itemWidget(window.workout_list.item(0))
            remove_button = row.findChild(QToolButton)
            with patch(
                "workout_app.ui.QMessageBox.question",
                return_value=QMessageBox.No,
            ) as confirmation:
                remove_button.click()

            confirmation.assert_called_once()
            self.assertEqual(len(window.workouts), 1)
            self.assertEqual(len(store.load()), 1)

    def test_calendar_x_button_deletes_selected_date_record(self) -> None:
        workout = Workout(
            start_time=datetime(2026, 10, 5, 8, tzinfo=timezone.utc),
            end_time=datetime(2026, 10, 5, 9, tzinfo=timezone.utc),
            distance_m=5000,
            gpx_path="run.gpx",
            name="Run",
        )
        with TemporaryDirectory() as directory:
            store = WorkoutStore(Path(directory) / "workouts.json")
            store.save([workout])
            window = MainWindow(
                Profile("Test", "", 30, 170, 65),
                [workout],
                ProfileStore(Path(directory) / "profile.json"),
                store,
            )
            window.record_views.setCurrentIndex(1)
            window.calendar.setSelectedDate(QDate(2026, 10, 5))
            row = window.calendar_workout_list.itemWidget(
                window.calendar_workout_list.item(0)
            )
            remove_button = row.findChild(QToolButton)
            with patch(
                "workout_app.ui.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ) as confirmation:
                remove_button.click()

            confirmation.assert_called_once()
            self.assertEqual(window.workouts, [])
            self.assertEqual(store.load(), [])
            self.assertEqual(window.calendar_workout_list.count(), 0)


if __name__ == "__main__":
    unittest.main()
