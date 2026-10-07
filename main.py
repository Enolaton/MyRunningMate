from __future__ import annotations

import sys
import subprocess
from pathlib import Path

GUI_FILE_NAME = "gui"
PROJECT_DIR = Path(__file__).resolve().parent


def main() -> int:
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyQt5.uic.pyuic",
                "-x",
                f"{GUI_FILE_NAME}.ui",
                "-o",
                f"{GUI_FILE_NAME}.py",
            ],
            cwd=PROJECT_DIR,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"UI 파일 변환에 실패했습니다: {error}", file=sys.stderr)
        return 1

    from PyQt5.QtWidgets import QApplication, QMessageBox

    from workout_app.storage import ProfileStore, WorkoutStore
    from workout_app.ui import MainWindow, ProfileDialog

    app = QApplication(sys.argv)
    app.setApplicationName("러닝 메이트")
    data_dir = Path(__file__).resolve().parent / "data"
    profile_store = ProfileStore(data_dir / "profile.json")
    workout_store = WorkoutStore(data_dir / "workouts.json")

    try:
        profile = profile_store.load()
        if profile is None:
            dialog = ProfileDialog()
            if dialog.exec_() != ProfileDialog.Accepted:
                return 0
            profile = dialog.profile()
            profile_store.save(profile)

        workouts = workout_store.load()
    except (OSError, ValueError) as error:
        QMessageBox.critical(None, "데이터를 불러올 수 없습니다", str(error))
        return 1

    window = MainWindow(profile, workouts, profile_store, workout_store)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
