#!/usr/bin/env python3

import sys
from pathlib import Path

from PyQt5 import QtWidgets


CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from cubic_trajectory_ui import CubicTrajectoryWindow


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = CubicTrajectoryWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
