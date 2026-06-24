#!/usr/bin/env python3

import copy
import json
import sys
import time
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import cubic_trajectory as ct
from cubic_trajectory import (
    APP_ROOT,
    CHILD_TAB_TO_MODE_ROW_SPACING,
    CONTROLLER_MULTI,
    CONTROLLER_SINGLE,
    DEFAULT_PARAMETER_FILE_NAME,
    INFO_DEFINITIONS,
    MANUAL_CURVE_HISTORY_PADDING_SECONDS,
    MANUAL_CURVE_TIME_WINDOW_SECONDS,
    MODE_RECIPROCATING,
    MODE_SINGLE,
    PARAMETER_DEFINITIONS,
    PARAMETER_EXPORT_DIR,
    PARAMETER_FILE_SUFFIX,
    PARENT_TAB_HORIZONTAL_PADDING,
    PARENT_CHILD_VERTICAL_SPACING,
    READ_ONLY_PARAMETER_KEYS,
    SCSCL_MAX_ANGLE_LIMIT,
    SCSCL_MIN_ANGLE_LIMIT,
    SCSCL_PRESENT_LOAD,
    SCSCL_PRESENT_POSITION,
    SCSCL_PRESENT_SPEED,
    SCSCL_PRESENT_STATUS,
    SCSCL_PRESENT_TEMPERATURE,
    SCSCL_TORQUE_ENABLE,
    TRAJECTORY_PARAMETER_DIR,
    TRAJECTORY_PARAMETER_FILE_SUFFIX,
    TRAJECTORY_PARAMETER_SCHEMA_VERSION,
    UI_FALLBACK_POSITION_MAX,
    UI_FALLBACK_POSITION_MIN,
    UI_ONLY_PARAMETER_KEYS,
    build_runtime_logger,
)


class TrajectoryPlotCanvas(FigureCanvasQTAgg):
    def __init__(self, parent=None):
        self.figure = Figure(figsize=(7, 4), tight_layout=True)
        self.axes = self.figure.add_subplot(111)
        super().__init__(self.figure)
        self.setParent(parent)
        self.ideal_times = []
        self.ideal_positions = []
        self.actual_times = []
        self.actual_positions = []
        self.actual_curves = {}
        self.reset_plot()

    def reset_plot(self):
        self._remove_extra_actual_lines()
        self.axes.clear()
        self.axes.grid(True, alpha=0.3)
        self.ideal_line, = self.axes.plot([], [], color="#2563eb", linewidth=2)
        self.actual_line, = self.axes.plot([], [], color="#dc2626", linewidth=2)
        self.error_line, = self.axes.plot([], [], color="#059669", linewidth=2)
        self.ideal_times = []
        self.ideal_positions = []
        self.actual_times = []
        self.actual_positions = []
        self.actual_curves = {}
        self.refresh_curves()

    def _remove_extra_actual_lines(self):
        for extra_line in getattr(self, "extra_actual_lines", []):
            try:
                extra_line.remove()
            except ValueError:
                pass
        self.extra_actual_lines = []

    def set_ideal_curve(self, sample_times, positions):
        self.ideal_times = list(sample_times)
        self.ideal_positions = list(positions)

    def set_actual_curve(self, sample_times, positions):
        self.actual_times = list(sample_times)
        self.actual_positions = list(positions)
        self.actual_curves = {}

    def set_actual_curves(self, curve_map):
        self.actual_curves = {}
        for curve_key, curve in curve_map.items():
            self.actual_curves[curve_key] = {
                "times": list(curve.get("times", [])),
                "positions": list(curve.get("positions", [])),
                "color": curve.get("color", "#dc2626"),
            }
        self.actual_times = []
        self.actual_positions = []

    def _build_error_curve(self):
        if not self.ideal_times or not self.actual_times:
            return [], []

        ideal_map = {}
        for sample_time, position in zip(self.ideal_times, self.ideal_positions):
            ideal_map[round(sample_time, 6)] = position

        error_times = []
        error_positions = []
        for sample_time, actual_position in zip(self.actual_times, self.actual_positions):
            key = round(sample_time, 6)
            if key in ideal_map:
                error_times.append(sample_time)
                error_positions.append(actual_position - ideal_map[key])
        return error_times, error_positions

    def refresh_curves(self, follow_latest_seconds=None):
        error_times, error_positions = self._build_error_curve()
        self.ideal_line.set_data(self.ideal_times, self.ideal_positions)
        latest_time = None
        if self.actual_curves:
            self.actual_line.set_data([], [])
            self._remove_extra_actual_lines()
            sorted_curve_keys = sorted(self.actual_curves)
            first_curve = self.actual_curves[sorted_curve_keys[0]]
            self.actual_line.set_color(first_curve["color"])
            self.actual_line.set_data(first_curve["times"], first_curve["positions"])
            if first_curve["times"]:
                latest_time = max(latest_time or 0.0, max(first_curve["times"]))
            for curve_key in sorted_curve_keys[1:]:
                curve = self.actual_curves[curve_key]
                extra_line, = self.axes.plot(
                    curve["times"],
                    curve["positions"],
                    color=curve["color"],
                    linewidth=2,
                )
                self.extra_actual_lines.append(extra_line)
                if curve["times"]:
                    latest_time = max(latest_time or 0.0, max(curve["times"]))
        else:
            self._remove_extra_actual_lines()
            self.actual_line.set_color("#dc2626")
            self.actual_line.set_data(self.actual_times, self.actual_positions)
            if self.actual_times:
                latest_time = max(self.actual_times)
        self.error_line.set_data(error_times, error_positions)
        self.axes.relim()
        self.axes.autoscale_view()
        if follow_latest_seconds is not None and latest_time is not None:
            window_seconds = float(follow_latest_seconds)
            right_edge = max(window_seconds, float(latest_time))
            self.axes.set_xlim(right_edge - window_seconds, right_edge)
        self.draw_idle()

    def set_visibility(self, show_ideal, show_actual, show_error):
        self.ideal_line.set_visible(show_ideal)
        self.actual_line.set_visible(show_actual)
        for extra_line in getattr(self, "extra_actual_lines", []):
            extra_line.set_visible(show_actual)
        self.error_line.set_visible(show_error)
        self.draw_idle()


class FanLogoWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(58, 58)

    def start(self):
        if not self._timer.isActive():
            self._timer.start(30)

    def stop(self):
        if self._timer.isActive():
            self._timer.stop()

    def _advance(self):
        self._angle = (self._angle + 14) % 360
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect().adjusted(4, 4, -4, -4)

        painter.setPen(QtGui.QPen(QtGui.QColor("#d6dde6"), 1))
        painter.setBrush(QtGui.QColor("#f8fbff"))
        painter.drawEllipse(rect)

        painter.save()
        painter.translate(rect.center())
        painter.rotate(self._angle)

        blade_color = QtGui.QColor("#0f6cbd")
        blade_shadow = QtGui.QColor(15, 108, 189, 55)
        for index in range(4):
            painter.save()
            painter.rotate(index * 90)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(blade_shadow)
            shadow_path = QtGui.QPainterPath()
            shadow_path.moveTo(0, -4)
            shadow_path.cubicTo(18, -14, 16, -2, 2, 8)
            shadow_path.cubicTo(-2, 5, -3, 1, 0, -4)
            painter.translate(1.5, 1.5)
            painter.drawPath(shadow_path)
            painter.translate(-1.5, -1.5)

            painter.setBrush(blade_color)
            blade_path = QtGui.QPainterPath()
            blade_path.moveTo(0, -4)
            blade_path.cubicTo(16, -14, 14, -2, 2, 8)
            blade_path.cubicTo(-2, 5, -3, 1, 0, -4)
            painter.drawPath(blade_path)
            painter.restore()

        painter.restore()
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#1f2937"))
        painter.drawEllipse(rect.center(), 6, 6)
        painter.setBrush(QtGui.QColor("#9fb3c8"))
        painter.drawEllipse(rect.center(), 2, 2)


class MotorConfigPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _create_velocity_spin(self):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(-1000000.0, 1000000.0)
        spin.setDecimals(3)
        spin.setSingleStep(1.0)
        return spin

    def _build_ui(self):
        root_layout = QtWidgets.QVBoxLayout(self)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.tabBar().hide()
        root_layout.addWidget(self.tabs)

        single_tab = QtWidgets.QWidget()
        single_layout = QtWidgets.QGridLayout(single_tab)
        self.use_current_position_checkbox = QtWidgets.QCheckBox("从当前位置开始")
        self.use_current_position_checkbox.toggled.connect(self._update_single_start_enabled)
        self.start_position_spin = QtWidgets.QSpinBox()
        self.start_position_spin.setRange(-1000000, 1000000)
        self.target_position_spin = QtWidgets.QSpinBox()
        self.target_position_spin.setRange(-1000000, 1000000)
        self.single_duration_spin = QtWidgets.QDoubleSpinBox()
        self.single_duration_spin.setRange(0.001, 3600.0)
        self.single_duration_spin.setDecimals(3)
        self.single_duration_spin.setSingleStep(0.1)
        self.single_start_velocity_spin = self._create_velocity_spin()
        self.single_end_velocity_spin = self._create_velocity_spin()
        single_layout.addWidget(QtWidgets.QLabel("轨迹时长(s)"), 0, 0)
        single_layout.addWidget(self.single_duration_spin, 0, 1)
        single_layout.addWidget(self.use_current_position_checkbox, 1, 0, 1, 2)
        single_layout.addWidget(QtWidgets.QLabel("手动起始位置"), 2, 0)
        single_layout.addWidget(self.start_position_spin, 2, 1)
        single_layout.addWidget(QtWidgets.QLabel("目标点"), 3, 0)
        single_layout.addWidget(self.target_position_spin, 3, 1)
        single_layout.addWidget(QtWidgets.QLabel("起点速度"), 4, 0)
        single_layout.addWidget(self.single_start_velocity_spin, 4, 1)
        single_layout.addWidget(QtWidgets.QLabel("终点速度"), 5, 0)
        single_layout.addWidget(self.single_end_velocity_spin, 5, 1)

        recip_tab = QtWidgets.QWidget()
        recip_layout = QtWidgets.QGridLayout(recip_tab)
        self.recip_duration_spin = QtWidgets.QDoubleSpinBox()
        self.recip_duration_spin.setRange(0.001, 3600.0)
        self.recip_duration_spin.setDecimals(3)
        self.recip_duration_spin.setSingleStep(0.1)
        self.recip_start_position_spin = QtWidgets.QSpinBox()
        self.recip_start_position_spin.setRange(-1000000, 1000000)
        self.recip_end_position_spin = QtWidgets.QSpinBox()
        self.recip_end_position_spin.setRange(-1000000, 1000000)
        self.recip_start_velocity_spin = self._create_velocity_spin()
        self.recip_end_velocity_spin = self._create_velocity_spin()
        recip_layout.addWidget(QtWidgets.QLabel("轨迹时长(s)"), 0, 0)
        recip_layout.addWidget(self.recip_duration_spin, 0, 1)
        recip_layout.addWidget(QtWidgets.QLabel("起始位置"), 1, 0)
        recip_layout.addWidget(self.recip_start_position_spin, 1, 1)
        recip_layout.addWidget(QtWidgets.QLabel("终止位置"), 2, 0)
        recip_layout.addWidget(self.recip_end_position_spin, 2, 1)
        recip_layout.addWidget(QtWidgets.QLabel("起点速度"), 3, 0)
        recip_layout.addWidget(self.recip_start_velocity_spin, 3, 1)
        recip_layout.addWidget(QtWidgets.QLabel("终点速度"), 4, 0)
        recip_layout.addWidget(self.recip_end_velocity_spin, 4, 1)

        self.tabs.addTab(single_tab, "单点控制")
        self.tabs.addTab(recip_tab, "往复控制")

        root_layout.addStretch(1)

    def _update_single_start_enabled(self):
        self.start_position_spin.setEnabled(not self.use_current_position_checkbox.isChecked())

    def set_active_mode(self, mode):
        self.tabs.setCurrentIndex(0 if mode == MODE_SINGLE else 1)

    def load_config(self, config):
        self.use_current_position_checkbox.setChecked(config["single"]["use_current_position_as_start"])
        self.start_position_spin.setValue(config["single"]["start_position"])
        self.target_position_spin.setValue(config["single"]["target_position"])
        self.single_duration_spin.setValue(config["single"]["trajectory_duration"])
        self.single_start_velocity_spin.setValue(config["single"]["start_velocity"])
        self.single_end_velocity_spin.setValue(config["single"]["end_velocity"])
        self.recip_duration_spin.setValue(config["reciprocating"]["trajectory_duration"])
        self.recip_start_position_spin.setValue(config["reciprocating"]["start_position"])
        self.recip_end_position_spin.setValue(config["reciprocating"]["end_position"])
        self.recip_start_velocity_spin.setValue(config["reciprocating"]["start_velocity"])
        self.recip_end_velocity_spin.setValue(config["reciprocating"]["end_velocity"])
        self._update_single_start_enabled()

    def export_config(self):
        return {
            "single": {
                "use_current_position_as_start": self.use_current_position_checkbox.isChecked(),
                "start_position": self.start_position_spin.value(),
                "target_position": self.target_position_spin.value(),
                "trajectory_duration": self.single_duration_spin.value(),
                "start_velocity": self.single_start_velocity_spin.value(),
                "end_velocity": self.single_end_velocity_spin.value(),
            },
            "reciprocating": {
                "start_position": self.recip_start_position_spin.value(),
                "end_position": self.recip_end_position_spin.value(),
                "trajectory_duration": self.recip_duration_spin.value(),
                "start_velocity": self.recip_start_velocity_spin.value(),
                "end_velocity": self.recip_end_velocity_spin.value(),
            },
        }


class FloatingModeWindow(QtWidgets.QWidget):
    activated = QtCore.pyqtSignal(str)

    def __init__(self, mode_key, title, content_widget, parent=None):
        super().__init__(parent)
        self.mode_key = mode_key
        self.setWindowTitle(title)
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.WindowCloseButtonHint)
        self.resize(360, 260)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(content_widget)

    def _emit_activated(self):
        self.activated.emit(self.mode_key)

    def showEvent(self, event):
        super().showEvent(event)
        self._emit_activated()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._emit_activated()

    def mousePressEvent(self, event):
        self._emit_activated()
        super().mousePressEvent(event)

    def closeEvent(self, event):
        parent = self.parent()
        if parent is not None and hasattr(parent, "worker_running") and parent.worker_running():
            event.ignore()
            if hasattr(parent, "set_status"):
                parent.set_status("轨迹执行中，需先停止电机后才能关闭悬浮菜单。")
            self.raise_()
            self.activateWindow()
            return
        event.ignore()
        self.hide()

class TrajectoryWorker(QtCore.QThread):
    log_message = QtCore.pyqtSignal(str)
    finished_ok = QtCore.pyqtSignal(str)
    finished_error = QtCore.pyqtSignal(str)
    actual_sample = QtCore.pyqtSignal(float, float)
    cycle_reset = QtCore.pyqtSignal(float)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._stop_requested = False

    def _log(self, message):
        self.log_message.emit(message)

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        port_handler = None
        try:
            port_handler = ct.PortHandler(self.config["port_name"])
            packet_handler = ct.scscl(port_handler)

            if not port_handler.openPort():
                raise RuntimeError("Failed to open the port")
            self._log("Succeeded to open the port")

            if not port_handler.setBaudRate(self.config["baudrate"]):
                raise RuntimeError("Failed to change the baudrate")
            self._log("Succeeded to change the baudrate")

            if self.config["controller_kind"] == CONTROLLER_SINGLE:
                servo_ids = [self.config["servo_id"]]
            else:
                servo_ids = list(self.config["servo_ids"])
            primary_servo_id = servo_ids[0]

            per_servo_samples = {}
            cycle_servo_samples = {}
            start_positions = {}
            cycle_start_positions = {}
            backend_name = None

            position_mode_fixed_ids = []
            for servo_id in servo_ids:
                mode_config = self.config["motor_modes"][servo_id]
                if self.parent()._ensure_position_mode_for_servo(packet_handler, servo_id):
                    position_mode_fixed_ids.append(servo_id)
                comm_result, error = packet_handler.write1ByteTxRx(
                    servo_id,
                    SCSCL_TORQUE_ENABLE,
                    self.config["torque_enabled"],
                )
                if comm_result != ct.COMM_SUCCESS:
                    raise RuntimeError(packet_handler.getTxRxResult(comm_result))
                if error != 0:
                    raise RuntimeError(packet_handler.getRxPacketError(error))
                if mode_config["mode"] == MODE_RECIPROCATING:
                    start_positions[servo_id] = ct.read_start_position(packet_handler, servo_id)
                elif mode_config["mode"] == MODE_SINGLE and mode_config["use_current_position_as_start"]:
                    start_positions[servo_id] = ct.read_start_position(packet_handler, servo_id)
                else:
                    start_positions[servo_id] = mode_config["start_position"]

                if mode_config["mode"] == MODE_RECIPROCATING:
                    current_backend_name, samples = ct.build_segment_samples(
                        start_positions[servo_id],
                        mode_config["recip_end_position"],
                        mode_config["start_velocity"],
                        mode_config["end_velocity"],
                        mode_config["trajectory_duration"],
                        self.config["sample_interval"],
                    )
                    cycle_mode_config = copy.deepcopy(mode_config)
                    cycle_mode_config["start_position"] = mode_config["recip_end_position"]
                    cycle_mode_config["target_position"] = mode_config["start_position"]
                    cycle_mode_config["recip_end_position"] = mode_config["start_position"]
                    _cycle_backend_name, cycle_samples, _cycle_summary = ct.build_mode_samples(
                        cycle_mode_config,
                        self.config["sample_interval"],
                    )
                    cycle_servo_samples[servo_id] = cycle_samples
                    cycle_start_positions[servo_id] = mode_config["recip_end_position"]
                else:
                    current_backend_name, samples, _summary = ct.build_mode_samples(
                        mode_config,
                        self.config["sample_interval"],
                        start_override=start_positions[servo_id],
                    )
                if backend_name is None:
                    backend_name = current_backend_name
                per_servo_samples[servo_id] = samples

            if position_mode_fixed_ids:
                self._log("已恢复位置模式 | ID: %s" % ", ".join(str(i) for i in position_mode_fixed_ids))
            self._log("Trajectory backend: %s" % backend_name)
            self._log("Trajectory execution started")
            self.actual_sample.emit(0.0, float(start_positions[primary_servo_id]))

            reference_samples = per_servo_samples[primary_servo_id]
            segment_time_ms = int(round(self.config["sample_interval"] * 1000.0))
            should_repeat = self.config["mode"] == MODE_RECIPROCATING

            def run_sample_batch(sample_map, reference_sample_list):
                max_count = max(len(samples) for samples in sample_map.values())
                for sample_index in range(1, max_count):
                    if self._stop_requested:
                        self.finished_ok.emit("Trajectory stopped by user.")
                        return False

                    sample_time = reference_sample_list[min(sample_index, len(reference_sample_list) - 1)][0]

                    if self.config["controller_kind"] == CONTROLLER_SINGLE:
                        position = reference_sample_list[sample_index][1]
                        velocity = reference_sample_list[sample_index][2]
                        goal_position = int(round(position))
                        goal_speed = int(round(abs(velocity)))
                        goal_speed = self.parent()._limit_goal_speed(primary_servo_id, goal_speed)
                        self.parent()._validate_writepos_target(primary_servo_id, goal_position)
                        comm_result, error = packet_handler.WritePos(
                            primary_servo_id,
                            goal_position,
                            segment_time_ms,
                            goal_speed,
                        )
                        if comm_result != ct.COMM_SUCCESS:
                            raise RuntimeError(packet_handler.getTxRxResult(comm_result))
                        if error != 0:
                            raise RuntimeError(packet_handler.getRxPacketError(error))
                    else:
                        for servo_id in servo_ids:
                            servo_samples = sample_map[servo_id]
                            servo_sample = servo_samples[min(sample_index, len(servo_samples) - 1)]
                            position = servo_sample[1]
                            velocity = servo_sample[2]
                            goal_position = int(round(position))
                            goal_speed = int(round(abs(velocity)))
                            goal_speed = self.parent()._limit_goal_speed(servo_id, goal_speed)
                            self.parent()._validate_writepos_target(servo_id, goal_position, "ID %03d: " % servo_id)
                            comm_result, error = packet_handler.WritePos(
                                servo_id,
                                goal_position,
                                segment_time_ms,
                                goal_speed,
                            )
                            if comm_result != ct.COMM_SUCCESS:
                                raise RuntimeError("ID %03d: %s" % (servo_id, packet_handler.getTxRxResult(comm_result)))
                            if error != 0:
                                raise RuntimeError("ID %03d: %s" % (servo_id, packet_handler.getRxPacketError(error)))

                    time.sleep(self.config["sample_interval"])
                    actual_position, comm_result, error = packet_handler.ReadPos(primary_servo_id)
                    if comm_result != ct.COMM_SUCCESS:
                        raise RuntimeError(packet_handler.getTxRxResult(comm_result))
                    if error != 0:
                        raise RuntimeError(packet_handler.getRxPacketError(error))
                    self.actual_sample.emit(sample_time, float(actual_position))
                return True

            if should_repeat:
                if not run_sample_batch(per_servo_samples, reference_samples):
                    return
                per_servo_samples = cycle_servo_samples
                reference_samples = per_servo_samples[primary_servo_id]
                start_positions = cycle_start_positions

            while True:
                if should_repeat:
                    self.cycle_reset.emit(float(start_positions[primary_servo_id]))

                if not run_sample_batch(per_servo_samples, reference_samples):
                    return

                if not should_repeat:
                    break

            final_position, final_speed, comm_result, error = packet_handler.ReadPosSpeed(primary_servo_id)
            if comm_result == ct.COMM_SUCCESS and error == 0:
                self.finished_ok.emit(
                    "[ID:%03d] Final position:%d Final speed:%d"
                    % (primary_servo_id, final_position, final_speed)
                )
            else:
                self.finished_ok.emit("Trajectory command finished.")
        except Exception as exc:
            self.finished_error.emit(str(exc))
        finally:
            if port_handler is not None:
                try:
                    port_handler.closePort()
                except Exception:
                    pass


class ManualDragWorker(QtCore.QThread):
    status_message = QtCore.pyqtSignal(str)
    error_message = QtCore.pyqtSignal(str)
    actual_sample = QtCore.pyqtSignal(int, float)
    feedback_sample = QtCore.pyqtSignal(int, int, int, int, int, int)
    link_active_changed = QtCore.pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mutex = QtCore.QMutex()
        self._wait_condition = QtCore.QWaitCondition()
        self._stop_requested = False
        self._disconnect_requested = False
        self._pending_torque_command = None
        self._pending_position_command = None
        self._position_mode_ready_ids = set()
        self._link_active = False
        self._last_drag_status_time = 0.0

    def submit_torque(self, command):
        locker = QtCore.QMutexLocker(self._mutex)
        self._pending_torque_command = command
        self._wait_condition.wakeOne()

    def submit_positions(self, command):
        locker = QtCore.QMutexLocker(self._mutex)
        self._pending_position_command = command
        self._wait_condition.wakeOne()

    def request_disconnect(self):
        locker = QtCore.QMutexLocker(self._mutex)
        self._disconnect_requested = True
        self._wait_condition.wakeOne()

    def request_stop(self):
        locker = QtCore.QMutexLocker(self._mutex)
        self._stop_requested = True
        self._wait_condition.wakeOne()

    def _set_link_active(self, active):
        if self._link_active != active:
            self._link_active = active
            self.link_active_changed.emit(active)

    def _close_link(self, port_handler):
        if port_handler is not None:
            try:
                port_handler.closePort()
            except Exception:
                pass
        self._position_mode_ready_ids = set()
        self._set_link_active(False)
        return None, None, None

    def _ensure_link(self, port_handler, packet_handler, current_key, command):
        port_name = command["port_name"]
        baudrate = command["baudrate"]
        next_key = (port_name, baudrate)
        if current_key != next_key:
            port_handler, packet_handler, current_key = self._close_link(port_handler)

        if port_handler is None:
            port_handler = ct.PortHandler(port_name)
            packet_handler = ct.scscl(port_handler)
            if not port_handler.openPort():
                raise RuntimeError("Failed to open the port")
            if not port_handler.setBaudRate(baudrate):
                raise RuntimeError("Failed to change the baudrate")
            current_key = next_key
            self._set_link_active(True)
        return port_handler, packet_handler, current_key

    def run(self):
        port_handler = None
        packet_handler = None
        current_key = None
        try:
            while True:
                locker = QtCore.QMutexLocker(self._mutex)
                while (
                    not self._stop_requested
                    and not self._disconnect_requested
                    and self._pending_torque_command is None
                    and self._pending_position_command is None
                ):
                    self._wait_condition.wait(self._mutex)

                if self._stop_requested:
                    break

                disconnect_requested = self._disconnect_requested
                torque_command = self._pending_torque_command
                position_command = self._pending_position_command
                self._disconnect_requested = False
                self._pending_torque_command = None
                self._pending_position_command = None
                del locker

                if disconnect_requested:
                    port_handler, packet_handler, current_key = self._close_link(port_handler)
                    continue

                if torque_command is not None:
                    port_handler, packet_handler, current_key = self._ensure_link(
                        port_handler,
                        packet_handler,
                        current_key,
                        torque_command,
                    )
                    torque_enabled = 1 if torque_command["torque_enabled"] else 0
                    for servo_id in torque_command["servo_ids"]:
                        comm_result, error = packet_handler.write1ByteTxRx(
                            servo_id,
                            SCSCL_TORQUE_ENABLE,
                            torque_enabled,
                        )
                        if comm_result != ct.COMM_SUCCESS:
                            raise RuntimeError(packet_handler.getTxRxResult(comm_result))
                        if error != 0:
                            raise RuntimeError(packet_handler.getRxPacketError(error))
                    self.status_message.emit(
                        "扭矩已%s | ID: %s"
                        % (("使能" if torque_enabled else "关闭"), ", ".join(str(i) for i in torque_command["servo_ids"]))
                    )

                if position_command is not None:
                    port_handler, packet_handler, current_key = self._ensure_link(
                        port_handler,
                        packet_handler,
                        current_key,
                        position_command,
                    )
                    queued_positions = position_command["queued_positions"]
                    for servo_id, limits in position_command["position_limits"].items():
                        if servo_id in self._position_mode_ready_ids:
                            continue
                        min_angle_limit, max_angle_limit = limits
                        changed = ct.ensure_scscl_position_mode(packet_handler, servo_id, min_angle_limit, max_angle_limit)
                        self._position_mode_ready_ids.add(servo_id)
                        if changed:
                            self.status_message.emit("已恢复位置模式 | ID: %d" % servo_id)

                    segment_time_ms = position_command["segment_time_ms"]
                    goal_speeds = position_command["goal_speeds"]
                    if len(queued_positions) > 1:
                        for servo_id, goal_position in queued_positions.items():
                            add_ok = packet_handler.SyncWritePos(
                                servo_id,
                                goal_position,
                                segment_time_ms,
                                goal_speeds[servo_id],
                            )
                            if not add_ok:
                                raise RuntimeError("ID %03d: groupSyncWrite addparam failed" % servo_id)
                        comm_result = packet_handler.groupSyncWrite.txPacket()
                        packet_handler.groupSyncWrite.clearParam()
                        if comm_result != ct.COMM_SUCCESS:
                            raise RuntimeError(packet_handler.getTxRxResult(comm_result))
                    else:
                        for servo_id, goal_position in queued_positions.items():
                            comm_result, error = packet_handler.WritePos(
                                servo_id,
                                goal_position,
                                segment_time_ms,
                                goal_speeds[servo_id],
                            )
                            if comm_result != ct.COMM_SUCCESS:
                                raise RuntimeError("ID %03d: %s" % (servo_id, packet_handler.getTxRxResult(comm_result)))
                            if error != 0:
                                raise RuntimeError("ID %03d: %s" % (servo_id, packet_handler.getRxPacketError(error)))

                    actual_positions = {}
                    for servo_id in queued_positions:
                        actual_position, comm_result, error = packet_handler.read2ByteTxRx(
                            servo_id,
                            SCSCL_PRESENT_POSITION,
                        )
                        if comm_result != ct.COMM_SUCCESS or error != 0:
                            continue
                        actual_positions[servo_id] = int(actual_position)
                        self.actual_sample.emit(servo_id, float(actual_position))

                    plotted_servo_id = position_command["plotted_servo_id"]
                    if plotted_servo_id in actual_positions:
                        speed_raw, comm_result, error = packet_handler.read2ByteTxRx(plotted_servo_id, SCSCL_PRESENT_SPEED)
                        if comm_result == ct.COMM_SUCCESS and error == 0:
                            load_raw, comm_result, error = packet_handler.read2ByteTxRx(plotted_servo_id, SCSCL_PRESENT_LOAD)
                            if comm_result == ct.COMM_SUCCESS and error == 0:
                                temperature, comm_result, error = packet_handler.read1ByteTxRx(
                                    plotted_servo_id,
                                    SCSCL_PRESENT_TEMPERATURE,
                                )
                                if comm_result == ct.COMM_SUCCESS and error == 0:
                                    status, comm_result, error = packet_handler.read1ByteTxRx(
                                        plotted_servo_id,
                                        SCSCL_PRESENT_STATUS,
                                    )
                                    if comm_result == ct.COMM_SUCCESS and error == 0:
                                        self.feedback_sample.emit(
                                            plotted_servo_id,
                                            actual_positions[plotted_servo_id],
                                            ct.decode_scscl_direction_value(speed_raw),
                                            ct.decode_scscl_direction_value(load_raw),
                                            int(temperature),
                                            int(status),
                                        )

                    now = time.time()
                    if now - self._last_drag_status_time >= 0.2:
                        status_parts = [
                            "ID %d -> %d" % (servo_id, goal_position)
                            for servo_id, goal_position in queued_positions.items()
                        ]
                        self.status_message.emit("拖动给位已下发 | %s" % " | ".join(status_parts))
                        self._last_drag_status_time = now
        except Exception as exc:
            self.error_message.emit(str(exc))
        finally:
            self._close_link(port_handler)


class CubicTrajectoryWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.runtime_logger, self.runtime_log_handler, self.runtime_log_path = build_runtime_logger()
        self.worker = None
        self.ideal_times = []
        self.ideal_positions = []
        self.actual_times = []
        self.actual_positions = []
        self.detected_servo_ids = []
        self.multi_id_checkboxes = {}
        self.motor_configs = {}
        self.servo_parameter_configs = {}
        self._multi_editor_loading = False
        self._multi_editor_loaded_ids = {
            MODE_SINGLE: None,
            MODE_RECIPROCATING: None,
        }
        self._drag_sync_updating = False
        self.active_single_mode = MODE_SINGLE
        self.active_multi_mode = MODE_SINGLE
        self.active_controller_kind = CONTROLLER_SINGLE
        self.parameter_inputs = {}
        self.info_value_labels = {}
        self.drag_servo_controls = {}
        self.drag_reverse_references = {}
        self.parameter_file_paths = {}
        self._parameter_file_loading = False
        self.run_action_buttons = []
        self.stop_action_buttons = []
        self.info_refresh_timer = QtCore.QTimer(self)
        self.info_refresh_timer.timeout.connect(self._auto_refresh_servo_info)
        self.drag_send_timer = QtCore.QTimer(self)
        self.drag_send_timer.setSingleShot(True)
        self.drag_send_timer.timeout.connect(self._flush_drag_position_commands)
        self.pending_drag_positions = {}
        self.position_mode_ready_ids = set()
        self.manual_drag_worker = None
        self.manual_drag_link_active = False
        self.manual_feedback_positions = {}
        self.manual_curve_start_time = None
        self.manual_curve_series = {}
        self.manual_curve_last_refresh_time = 0.0
        self._screen_hooks_installed = False
        self.setWindowTitle("SCSCL Cubic Trajectory UI")
        self.setWindowFlag(QtCore.Qt.Window, True)
        self.setWindowFlag(QtCore.Qt.WindowMinMaxButtonsHint, True)
        self.setMinimumSize(960, 720)
        self.resize(1280, 900)
        self._build_ui()
        self._disable_spinbox_buttons()
        self._apply_visual_style()
        self.refresh_ports()
        self.apply_defaults()
        self.info_refresh_timer.start(500)
        self._ensure_manual_drag_worker()
        self._write_runtime_log("INFO", "上位机启动 | 日志文件: %s" % self.runtime_log_path.name)

    def _build_ui(self):
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(8, 6, 8, 8)
        root_layout.setSpacing(6)

        top_content_layout = QtWidgets.QHBoxLayout()
        top_content_layout.setSpacing(8)
        top_content_layout.setAlignment(QtCore.Qt.AlignTop)
        root_layout.addLayout(top_content_layout)

        left_top_layout = QtWidgets.QVBoxLayout()
        left_top_layout.setSpacing(8)
        left_top_layout.setAlignment(QtCore.Qt.AlignTop)
        top_content_layout.addLayout(left_top_layout, 1)

        right_top_layout = QtWidgets.QVBoxLayout()
        right_top_layout.setSpacing(8)
        right_top_layout.setAlignment(QtCore.Qt.AlignTop)
        top_content_layout.addLayout(right_top_layout, 1)

        self.device_group = QtWidgets.QGroupBox("设备")
        self.device_group.setObjectName("deviceGroup")
        self.device_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.device_group.setMinimumHeight(148)
        device_layout = QtWidgets.QGridLayout(self.device_group)

        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setEditable(True)
        self.refresh_ports_button = QtWidgets.QPushButton("刷新串口")
        self.refresh_ports_button.clicked.connect(self.refresh_ports)

        self.baudrate_spin = QtWidgets.QSpinBox()
        self.baudrate_spin.setRange(1, 5000000)

        self.id_combo = QtWidgets.QComboBox()
        self.id_combo.setEditable(True)
        self.scan_ids_button = QtWidgets.QPushButton("扫描ID")
        self.scan_ids_button.clicked.connect(self.scan_ids)

        device_layout.addWidget(QtWidgets.QLabel("串口"), 0, 0)
        device_layout.addWidget(self.port_combo, 0, 1)
        device_layout.addWidget(self.refresh_ports_button, 0, 2)
        device_layout.addWidget(QtWidgets.QLabel("波特率"), 1, 0)
        device_layout.addWidget(self.baudrate_spin, 1, 1)
        device_layout.addWidget(QtWidgets.QLabel("舵机ID"), 2, 0)
        device_layout.addWidget(self.id_combo, 2, 1)
        device_layout.addWidget(self.scan_ids_button, 2, 2)
        left_top_layout.addWidget(self.device_group)

        self.workspace_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.workspace_splitter.setChildrenCollapsible(False)
        root_layout.addWidget(self.workspace_splitter, 1)

        control_area = QtWidgets.QWidget()
        control_area_layout = QtWidgets.QVBoxLayout(control_area)
        control_area_layout.setContentsMargins(0, 0, 0, 0)
        control_area_layout.setSpacing(6)
        self.workspace_splitter.addWidget(control_area)

        self.outer_group = QtWidgets.QGroupBox("轨迹规划配置")
        self.outer_group.setObjectName("trajectoryGroup")
        self.outer_group.setMinimumHeight(320)
        self.outer_group_layout = QtWidgets.QHBoxLayout(self.outer_group)
        self.outer_group_layout.setContentsMargins(12, 16, 12, 12)
        self.outer_group_layout.setSpacing(12)
        control_area_layout.addWidget(self.outer_group)

        self.trajectory_left_widget = QtWidgets.QWidget()
        self.trajectory_left_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        trajectory_left_layout = QtWidgets.QVBoxLayout(self.trajectory_left_widget)
        trajectory_left_layout.setContentsMargins(0, 0, 0, 0)
        trajectory_left_layout.setSpacing(10)

        sample_group = QtWidgets.QWidget()
        outer_layout = QtWidgets.QGridLayout(sample_group)
        outer_layout.setContentsMargins(0, 0, 0, 4)
        outer_layout.setHorizontalSpacing(10)
        outer_layout.setVerticalSpacing(10)
        self.sample_interval_spin = QtWidgets.QDoubleSpinBox()
        self.sample_interval_spin.setRange(0.001, 10.0)
        self.sample_interval_spin.setDecimals(3)
        self.sample_interval_spin.setSingleStep(0.01)
        outer_layout.addWidget(QtWidgets.QLabel("采样周期(s)"), 0, 0)
        outer_layout.addWidget(self.sample_interval_spin, 0, 1)
        outer_layout.setColumnStretch(2, 1)
        trajectory_left_layout.addWidget(sample_group)

        self.drag_control_group = QtWidgets.QGroupBox("手动模式")
        self.drag_control_group.setObjectName("dragControlGroup")
        drag_layout = QtWidgets.QGridLayout(self.drag_control_group)
        self.drag_speed_spin = QtWidgets.QSpinBox()
        self.drag_speed_spin.setRange(0, 1000)
        self.drag_speed_spin.setToolTip("拖动给位时使用的实时下发速度。")
        self.drag_torque_enable_checkbox = QtWidgets.QPushButton("扭矩使能: 开")
        self.drag_torque_enable_checkbox.setObjectName("torqueToggleButton")
        self.drag_torque_enable_checkbox.setCheckable(True)
        self.drag_torque_enable_checkbox.setMinimumHeight(28)
        self.drag_torque_enable_checkbox.setMinimumWidth(120)
        self.drag_torque_enable_checkbox.setMaximumWidth(140)
        self.drag_torque_enable_checkbox.toggled.connect(self._handle_drag_torque_toggled)
        drag_layout.setContentsMargins(8, 10, 8, 8)
        drag_layout.setHorizontalSpacing(10)
        drag_layout.setVerticalSpacing(10)
        drag_layout.addWidget(QtWidgets.QLabel("拖动速度"), 0, 0)
        drag_layout.addWidget(self.drag_speed_spin, 0, 1)
        drag_speed_hint = QtWidgets.QLabel("0-1000 步/s")
        drag_speed_hint.setObjectName("parameterRangeHint")
        drag_layout.addWidget(drag_speed_hint, 0, 2, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        drag_layout.addWidget(self.drag_torque_enable_checkbox, 1, 0, 1, 1, QtCore.Qt.AlignLeft)
        self.drag_servo_scroll = QtWidgets.QScrollArea()
        self.drag_servo_scroll.setWidgetResizable(True)
        self.drag_servo_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.drag_servo_content = QtWidgets.QWidget()
        self.drag_servo_layout = QtWidgets.QVBoxLayout(self.drag_servo_content)
        self.drag_servo_layout.setContentsMargins(0, 0, 0, 0)
        self.drag_servo_layout.setSpacing(8)
        self.drag_servo_layout.addStretch(1)
        self.drag_servo_scroll.setWidget(self.drag_servo_content)
        drag_layout.addWidget(self.drag_servo_scroll, 3, 0, 1, 3)
        drag_layout.setRowStretch(3, 1)
        self.drag_control_group.setMaximumWidth(1200)
        self.outer_group_layout.addWidget(self.trajectory_left_widget, 3)
        self.outer_group_layout.addWidget(self.drag_control_group, 7)

        self.info_group = QtWidgets.QGroupBox("")
        self.info_group.setObjectName("infoGroup")
        self.info_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.info_group.setMinimumHeight(148)
        info_overlay_layout = QtWidgets.QGridLayout(self.info_group)
        info_overlay_layout.setContentsMargins(8, 10, 8, 8)
        info_overlay_layout.setSpacing(0)
        info_content_widget = QtWidgets.QWidget(self.info_group)
        info_content_widget.setObjectName("infoContentWidget")
        info_layout = QtWidgets.QVBoxLayout(info_content_widget)
        info_layout.setContentsMargins(0, 0, 56, 0)
        info_layout.setSpacing(6)
        info_form_layout = QtWidgets.QFormLayout()
        info_form_layout.setContentsMargins(0, 0, 0, 0)
        info_form_layout.setHorizontalSpacing(120)
        info_form_layout.setVerticalSpacing(8)
        info_form_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldsStayAtSizeHint)
        info_form_layout.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        info_form_layout.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        for field_key, field_label in INFO_DEFINITIONS:
            label_widget = QtWidgets.QLabel(field_label)
            value_widget = QtWidgets.QLabel("--")
            value_widget.setMinimumWidth(350)
            value_widget.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            value_widget.setFrameStyle(QtWidgets.QFrame.Panel | QtWidgets.QFrame.Sunken)
            info_form_layout.addRow(label_widget, value_widget)
            self.info_value_labels[field_key] = value_widget
        info_row_layout = QtWidgets.QHBoxLayout()
        info_row_layout.setContentsMargins(0, 0, 0, 0)
        info_row_layout.setSpacing(0)
        info_row_layout.addLayout(info_form_layout, 0)
        info_row_layout.addStretch(1)
        info_layout.addLayout(info_row_layout)
        info_overlay_layout.addWidget(info_content_widget, 0, 0)
        self.fan_logo = FanLogoWidget(self.info_group)
        info_overlay_layout.addWidget(self.fan_logo, 0, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignTop)
        right_top_layout.addWidget(self.info_group)

        self.main_menu_tabs = QtWidgets.QTabWidget()
        trajectory_left_layout.addWidget(self.main_menu_tabs, 1)

        control_page = QtWidgets.QScrollArea()
        control_page.setWidgetResizable(True)
        control_page.setFrameShape(QtWidgets.QFrame.NoFrame)
        control_page_content = QtWidgets.QWidget()
        control_page_layout = QtWidgets.QVBoxLayout(control_page_content)
        control_page_layout.setContentsMargins(8, PARENT_CHILD_VERTICAL_SPACING, 8, 8)
        control_page_layout.setSpacing(PARENT_CHILD_VERTICAL_SPACING)
        control_page.setWidget(control_page_content)
        self.main_menu_tabs.addTab(control_page, "控制菜单")

        self.parameter_page = QtWidgets.QScrollArea()
        self.parameter_page.setWidgetResizable(True)
        self.parameter_page.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.parameter_page_content = QtWidgets.QWidget()
        parameter_page_layout = QtWidgets.QVBoxLayout(self.parameter_page_content)
        parameter_page_layout.setContentsMargins(8, PARENT_CHILD_VERTICAL_SPACING, 8, 8)
        parameter_page_layout.setSpacing(PARENT_CHILD_VERTICAL_SPACING)
        self.parameter_page.setWidget(self.parameter_page_content)
        self.main_menu_tabs.addTab(self.parameter_page, "电机配置")

        self.controller_tabs = QtWidgets.QTabWidget()
        self.controller_tabs.setMinimumHeight(180)
        self.controller_tabs.setMaximumHeight(260)
        self.controller_tabs.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.single_controller_page = QtWidgets.QWidget()
        self.multi_controller_page = QtWidgets.QWidget()
        self.controller_tabs.addTab(self.single_controller_page, "单电机控制")
        self.controller_tabs.addTab(self.multi_controller_page, "多电机控制")
        self.controller_tabs.setCurrentIndex(0)
        self.controller_tabs.currentChanged.connect(self._update_controller_mode_ui)
        control_page_layout.addWidget(self.controller_tabs, 1)

        self.single_controller_panel = QtWidgets.QWidget()
        self.single_controller_panel.setMinimumHeight(88)
        self.single_controller_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        single_controller_layout = QtWidgets.QVBoxLayout(self.single_controller_panel)
        single_controller_layout.setContentsMargins(0, CHILD_TAB_TO_MODE_ROW_SPACING, 0, 0)
        single_controller_layout.setSpacing(CHILD_TAB_TO_MODE_ROW_SPACING)
        single_controller_page_layout = QtWidgets.QVBoxLayout(self.single_controller_page)
        single_controller_page_layout.setContentsMargins(0, CHILD_TAB_TO_MODE_ROW_SPACING, 0, 0)
        single_controller_page_layout.setSpacing(CHILD_TAB_TO_MODE_ROW_SPACING)
        single_mode_button_row = QtWidgets.QHBoxLayout()
        self.open_single_point_window_button = QtWidgets.QPushButton("单点控制")
        self.open_single_point_window_button.setCheckable(True)
        self.open_single_point_window_button.clicked.connect(
            lambda: self._show_single_mode_window(MODE_SINGLE)
        )
        self.open_recip_window_button = QtWidgets.QPushButton("往复控制")
        self.open_recip_window_button.setCheckable(True)
        self.open_recip_window_button.clicked.connect(
            lambda: self._show_single_mode_window(MODE_RECIPROCATING)
        )
        self.single_mode_status_label = QtWidgets.QLabel("当前执行模式: 单点控制")
        self.single_mode_status_label.setObjectName("modeStatusLabel")
        self.single_mode_status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.single_mode_status_label.setMinimumHeight(28)
        single_mode_button_row.setAlignment(QtCore.Qt.AlignTop)
        single_mode_button_row.addWidget(self.open_single_point_window_button)
        single_mode_button_row.addWidget(self.open_recip_window_button)
        single_mode_button_row.addWidget(self.single_mode_status_label, 1)
        single_controller_layout.addLayout(single_mode_button_row)
        self.single_controller_single_tab = QtWidgets.QWidget()
        single_point_layout = QtWidgets.QGridLayout(self.single_controller_single_tab)
        self.single_duration_spin = QtWidgets.QDoubleSpinBox()
        self.single_duration_spin.setRange(0.001, 3600.0)
        self.single_duration_spin.setDecimals(3)
        self.single_duration_spin.setSingleStep(0.1)
        self.use_current_position_checkbox = QtWidgets.QCheckBox("从当前位置开始")
        self.use_current_position_checkbox.toggled.connect(self._update_single_start_position_enabled)
        self.start_position_spin = QtWidgets.QSpinBox()
        self.start_position_spin.setRange(-1000000, 1000000)
        self.target_position_spin = QtWidgets.QSpinBox()
        self.target_position_spin.setRange(-1000000, 1000000)
        self.single_start_velocity_spin = QtWidgets.QDoubleSpinBox()
        self.single_start_velocity_spin.setRange(-1000000.0, 1000000.0)
        self.single_start_velocity_spin.setDecimals(3)
        self.single_start_velocity_spin.setSingleStep(1.0)
        self.single_end_velocity_spin = QtWidgets.QDoubleSpinBox()
        self.single_end_velocity_spin.setRange(-1000000.0, 1000000.0)
        self.single_end_velocity_spin.setDecimals(3)
        self.single_end_velocity_spin.setSingleStep(1.0)
        single_point_layout.addWidget(QtWidgets.QLabel("轨迹时长(s)"), 0, 0)
        single_point_layout.addWidget(self.single_duration_spin, 0, 1)
        single_point_layout.addWidget(self.use_current_position_checkbox, 1, 0, 1, 2)
        single_point_layout.addWidget(QtWidgets.QLabel("手动起始位置"), 2, 0)
        single_point_layout.addWidget(self.start_position_spin, 2, 1)
        single_point_layout.addWidget(QtWidgets.QLabel("目标点"), 3, 0)
        single_point_layout.addWidget(self.target_position_spin, 3, 1)
        single_point_layout.addWidget(QtWidgets.QLabel("起点速度"), 4, 0)
        single_point_layout.addWidget(self.single_start_velocity_spin, 4, 1)
        single_point_layout.addWidget(QtWidgets.QLabel("终点速度"), 5, 0)
        single_point_layout.addWidget(self.single_end_velocity_spin, 5, 1)

        self.single_controller_recip_tab = QtWidgets.QWidget()
        recip_layout = QtWidgets.QGridLayout(self.single_controller_recip_tab)
        self.recip_duration_spin = QtWidgets.QDoubleSpinBox()
        self.recip_duration_spin.setRange(0.001, 3600.0)
        self.recip_duration_spin.setDecimals(3)
        self.recip_duration_spin.setSingleStep(0.1)
        self.recip_start_position_spin = QtWidgets.QSpinBox()
        self.recip_start_position_spin.setRange(-1000000, 1000000)
        self.recip_end_position_spin = QtWidgets.QSpinBox()
        self.recip_end_position_spin.setRange(-1000000, 1000000)
        self.recip_start_velocity_spin = QtWidgets.QDoubleSpinBox()
        self.recip_start_velocity_spin.setRange(-1000000.0, 1000000.0)
        self.recip_start_velocity_spin.setDecimals(3)
        self.recip_start_velocity_spin.setSingleStep(1.0)
        self.recip_end_velocity_spin = QtWidgets.QDoubleSpinBox()
        self.recip_end_velocity_spin.setRange(-1000000.0, 1000000.0)
        self.recip_end_velocity_spin.setDecimals(3)
        self.recip_end_velocity_spin.setSingleStep(1.0)
        recip_layout.addWidget(QtWidgets.QLabel("轨迹时长(s)"), 0, 0)
        recip_layout.addWidget(self.recip_duration_spin, 0, 1)
        recip_layout.addWidget(QtWidgets.QLabel("起始位置"), 1, 0)
        recip_layout.addWidget(self.recip_start_position_spin, 1, 1)
        recip_layout.addWidget(QtWidgets.QLabel("终止位置"), 2, 0)
        recip_layout.addWidget(self.recip_end_position_spin, 2, 1)
        recip_layout.addWidget(QtWidgets.QLabel("起点速度"), 3, 0)
        recip_layout.addWidget(self.recip_start_velocity_spin, 3, 1)
        recip_layout.addWidget(QtWidgets.QLabel("终点速度"), 4, 0)
        recip_layout.addWidget(self.recip_end_velocity_spin, 4, 1)

        self.single_point_window_content = QtWidgets.QWidget()
        single_point_window_layout = QtWidgets.QVBoxLayout(self.single_point_window_content)
        single_point_window_layout.setContentsMargins(0, 0, 0, 0)
        single_point_window_layout.setSpacing(10)
        single_point_window_layout.addWidget(self.single_controller_single_tab)
        single_point_window_layout.addLayout(self._create_mode_action_row(CONTROLLER_SINGLE, MODE_SINGLE))

        self.single_recip_window_content = QtWidgets.QWidget()
        single_recip_window_layout = QtWidgets.QVBoxLayout(self.single_recip_window_content)
        single_recip_window_layout.setContentsMargins(0, 0, 0, 0)
        single_recip_window_layout.setSpacing(10)
        single_recip_window_layout.addWidget(self.single_controller_recip_tab)
        single_recip_window_layout.addLayout(self._create_mode_action_row(CONTROLLER_SINGLE, MODE_RECIPROCATING))

        self.single_point_window = FloatingModeWindow(
            MODE_SINGLE,
            "单点控制",
            self.single_point_window_content,
            self,
        )
        self.single_recip_window = FloatingModeWindow(
            MODE_RECIPROCATING,
            "往复控制",
            self.single_recip_window_content,
            self,
        )
        self.single_point_window.activated.connect(self._set_active_single_mode)
        self.single_recip_window.activated.connect(self._set_active_single_mode)
        single_controller_page_layout.addWidget(self.single_controller_panel, 0, QtCore.Qt.AlignTop)
        single_controller_page_layout.addStretch(1)

        self.multi_controller_panel = QtWidgets.QWidget()
        self.multi_controller_panel.setMinimumHeight(132)
        self.multi_controller_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        multi_controller_layout = QtWidgets.QVBoxLayout(self.multi_controller_panel)
        multi_controller_layout.setContentsMargins(0, CHILD_TAB_TO_MODE_ROW_SPACING, 0, 0)
        multi_controller_layout.setSpacing(10)
        multi_controller_page_layout = QtWidgets.QVBoxLayout(self.multi_controller_page)
        multi_controller_page_layout.setContentsMargins(0, CHILD_TAB_TO_MODE_ROW_SPACING, 0, 0)
        multi_controller_page_layout.setSpacing(CHILD_TAB_TO_MODE_ROW_SPACING)
        multi_mode_button_row = QtWidgets.QHBoxLayout()
        self.open_multi_single_window_button = QtWidgets.QPushButton("单点控制")
        self.open_multi_single_window_button.setCheckable(True)
        self.open_multi_single_window_button.clicked.connect(
            lambda: self._show_multi_mode_window(MODE_SINGLE)
        )
        self.open_multi_recip_window_button = QtWidgets.QPushButton("往复控制")
        self.open_multi_recip_window_button.setCheckable(True)
        self.open_multi_recip_window_button.clicked.connect(
            lambda: self._show_multi_mode_window(MODE_RECIPROCATING)
        )
        self.multi_mode_status_label = QtWidgets.QLabel("当前执行模式: 单点控制")
        self.multi_mode_status_label.setObjectName("modeStatusLabel")
        self.multi_mode_status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.multi_mode_status_label.setMinimumHeight(28)
        multi_mode_button_row.setAlignment(QtCore.Qt.AlignTop)
        multi_mode_button_row.addWidget(self.open_multi_single_window_button)
        multi_mode_button_row.addWidget(self.open_multi_recip_window_button)
        multi_mode_button_row.addWidget(self.multi_mode_status_label, 1)
        multi_controller_layout.addLayout(multi_mode_button_row)

        self.multi_single_window_content = QtWidgets.QWidget()
        multi_single_window_layout = QtWidgets.QVBoxLayout(self.multi_single_window_content)
        multi_single_window_layout.setContentsMargins(0, 0, 0, 0)
        multi_single_window_layout.setSpacing(10)
        multi_single_selector_layout = QtWidgets.QHBoxLayout()
        multi_single_selector_layout.addWidget(QtWidgets.QLabel("当前编辑ID"))
        self.multi_single_edit_id_combo = QtWidgets.QComboBox()
        self.multi_single_edit_id_combo.currentIndexChanged.connect(self._on_multi_single_edit_servo_changed)
        multi_single_selector_layout.addWidget(self.multi_single_edit_id_combo)
        multi_single_selector_layout.addStretch(1)
        multi_single_window_layout.addLayout(multi_single_selector_layout)
        self.multi_single_config_panel = MotorConfigPanel(self)
        self.multi_single_config_panel.setEnabled(False)
        self.multi_single_config_panel.set_active_mode(MODE_SINGLE)
        multi_single_window_layout.addWidget(self.multi_single_config_panel)
        multi_single_window_layout.addLayout(self._create_mode_action_row(CONTROLLER_MULTI, MODE_SINGLE))

        self.multi_recip_window_content = QtWidgets.QWidget()
        multi_recip_window_layout = QtWidgets.QVBoxLayout(self.multi_recip_window_content)
        multi_recip_window_layout.setContentsMargins(0, 0, 0, 0)
        multi_recip_window_layout.setSpacing(10)
        multi_recip_selector_layout = QtWidgets.QHBoxLayout()
        multi_recip_selector_layout.addWidget(QtWidgets.QLabel("当前编辑ID"))
        self.multi_recip_edit_id_combo = QtWidgets.QComboBox()
        self.multi_recip_edit_id_combo.currentIndexChanged.connect(self._on_multi_recip_edit_servo_changed)
        multi_recip_selector_layout.addWidget(self.multi_recip_edit_id_combo)
        multi_recip_selector_layout.addStretch(1)
        multi_recip_window_layout.addLayout(multi_recip_selector_layout)
        self.multi_recip_config_panel = MotorConfigPanel(self)
        self.multi_recip_config_panel.setEnabled(False)
        self.multi_recip_config_panel.set_active_mode(MODE_RECIPROCATING)
        multi_recip_window_layout.addWidget(self.multi_recip_config_panel)
        multi_recip_window_layout.addLayout(self._create_mode_action_row(CONTROLLER_MULTI, MODE_RECIPROCATING))

        self.multi_single_window = FloatingModeWindow(
            MODE_SINGLE,
            "多电机单点控制",
            self.multi_single_window_content,
            self,
        )
        self.multi_recip_window = FloatingModeWindow(
            MODE_RECIPROCATING,
            "多电机往复控制",
            self.multi_recip_window_content,
            self,
        )
        self.multi_single_window.activated.connect(self._set_active_multi_mode)
        self.multi_recip_window.activated.connect(self._set_active_multi_mode)

        self.multi_id_group = QtWidgets.QGroupBox("")
        self.multi_id_group.setObjectName("selectionGroup")
        self.multi_id_group.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        multi_id_layout = QtWidgets.QVBoxLayout(self.multi_id_group)
        self.multi_id_layout = QtWidgets.QGridLayout()
        multi_id_layout.addLayout(self.multi_id_layout)
        multi_controller_layout.addWidget(self.multi_id_group, 0, QtCore.Qt.AlignTop)
        multi_controller_page_layout.addWidget(self.multi_controller_panel, 0, QtCore.Qt.AlignTop)
        multi_controller_page_layout.addStretch(1)

        parameter_group = QtWidgets.QWidget()
        parameter_layout = QtWidgets.QVBoxLayout(parameter_group)
        parameter_layout.setContentsMargins(0, 0, 0, 0)
        parameter_layout.setSpacing(10)

        protection_group = QtWidgets.QGroupBox("保护配置")
        protection_group.setObjectName("protectionGroup")
        protection_form = QtWidgets.QFormLayout(protection_group)
        debug_group = QtWidgets.QGroupBox("调试配置")
        debug_group.setObjectName("debugGroup")
        debug_form = QtWidgets.QFormLayout(debug_group)

        for field_key, field_label, _address, byte_size, minimum, maximum, range_hint, group_name in PARAMETER_DEFINITIONS:
            spinbox = QtWidgets.QSpinBox()
            spinbox.setRange(minimum, maximum)
            spinbox.setFixedWidth(104)
            if byte_size == 2:
                spinbox.setSingleStep(1)
            if field_key in READ_ONLY_PARAMETER_KEYS:
                spinbox.setReadOnly(True)
                spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
                spinbox.setFocusPolicy(QtCore.Qt.NoFocus)
            self.parameter_inputs[field_key] = spinbox
            row_widget = self._create_parameter_input_row(spinbox, range_hint)
            if group_name == "protection":
                protection_form.addRow(field_label, row_widget)
            elif group_name == "debug":
                debug_form.addRow(field_label, row_widget)

        parameter_layout.addWidget(protection_group)
        parameter_layout.addWidget(debug_group)

        parameter_file_group = QtWidgets.QGroupBox("配置")
        parameter_file_group.setObjectName("parameterFileGroup")
        parameter_file_layout = QtWidgets.QVBoxLayout(parameter_file_group)
        parameter_file_layout.setContentsMargins(8, 10, 8, 8)
        parameter_file_layout.setSpacing(8)
        parameter_file_button_layout = QtWidgets.QHBoxLayout()
        self.export_parameter_button = QtWidgets.QPushButton("导出配置")
        self.export_parameter_button.clicked.connect(self.export_servo_parameters)
        self.refresh_parameter_files_button = QtWidgets.QPushButton("刷新")
        self.refresh_parameter_files_button.clicked.connect(self.refresh_parameter_files)
        parameter_file_button_layout.addWidget(self.export_parameter_button)
        parameter_file_button_layout.addWidget(self.refresh_parameter_files_button)
        parameter_file_button_layout.addStretch(1)
        parameter_file_layout.addLayout(parameter_file_button_layout)
        self.parameter_file_list = QtWidgets.QListWidget()
        self.parameter_file_list.setAlternatingRowColors(True)
        self.parameter_file_list.itemChanged.connect(self._handle_parameter_file_item_changed)
        parameter_file_layout.addWidget(self.parameter_file_list)

        parameter_layout.addWidget(parameter_file_group)
        parameter_button_layout = QtWidgets.QHBoxLayout()
        self.stage_parameter_button = QtWidgets.QPushButton("载入配置")
        self.stage_parameter_button.setToolTip("只将选中的配置文件解析到参数框，不写入电机")
        self.stage_parameter_button.clicked.connect(self.stage_selected_parameter_file)
        self.apply_parameter_button = QtWidgets.QPushButton("写入电机")
        self.apply_parameter_button.setToolTip("将参数框中的当前数值写入电机")
        self.apply_parameter_button.clicked.connect(self.apply_servo_parameters)
        parameter_button_layout.addWidget(self.stage_parameter_button)
        parameter_button_layout.addWidget(self.apply_parameter_button)
        parameter_button_layout.addStretch(1)
        parameter_layout.addLayout(parameter_button_layout)
        parameter_page_layout.addWidget(parameter_group)
        parameter_page_layout.addStretch(1)


        self.curve_group = QtWidgets.QGroupBox("轨迹曲线")
        self.curve_group.setObjectName("curveGroup")
        self.curve_group.setMinimumHeight(300)
        curve_layout = QtWidgets.QVBoxLayout(self.curve_group)
        curve_toggle_layout = QtWidgets.QHBoxLayout()
        self.show_ideal_checkbox = QtWidgets.QCheckBox("Ideal")
        self.show_actual_checkbox = QtWidgets.QCheckBox("Actual")
        self.show_error_checkbox = QtWidgets.QCheckBox("Error")
        self.show_ideal_checkbox.setChecked(True)
        self.show_actual_checkbox.setChecked(True)
        self.show_error_checkbox.setChecked(True)
        self.show_ideal_checkbox.toggled.connect(self._update_curve_visibility)
        self.show_actual_checkbox.toggled.connect(self._update_curve_visibility)
        self.show_error_checkbox.toggled.connect(self._update_curve_visibility)
        curve_toggle_layout.addWidget(self.show_ideal_checkbox)
        curve_toggle_layout.addWidget(self.show_actual_checkbox)
        curve_toggle_layout.addWidget(self.show_error_checkbox)
        self.clear_curve_button = QtWidgets.QPushButton("Clear")
        self.clear_curve_button.clicked.connect(self.clear_curve_panel)
        curve_toggle_layout.addWidget(self.clear_curve_button)
        curve_toggle_layout.addStretch(1)
        curve_layout.addLayout(curve_toggle_layout)
        self.plot_canvas = TrajectoryPlotCanvas(self)
        self.plot_canvas.setMinimumHeight(240)
        curve_layout.addWidget(self.plot_canvas)
        self.workspace_splitter.addWidget(self.curve_group)
        self.workspace_splitter.setStretchFactor(0, 2)
        self.workspace_splitter.setStretchFactor(1, 5)
        self.workspace_splitter.setSizes([420, 560])

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("statusLabel")
        root_layout.addWidget(self.status_label)

    def _disable_spinbox_buttons(self):
        for spinbox in self.findChildren(QtWidgets.QAbstractSpinBox):
            spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)

    def _create_parameter_input_row(self, spinbox, range_hint):
        row_widget = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        hint_label = QtWidgets.QLabel(range_hint)
        hint_label.setObjectName("parameterRangeHint")
        hint_label.setWordWrap(True)
        hint_label.setMinimumWidth(150)
        row_layout.addWidget(spinbox, 0)
        row_layout.addWidget(hint_label, 1)
        return row_widget

    def showEvent(self, event):
        super().showEvent(event)
        if not self._screen_hooks_installed:
            self._screen_hooks_installed = True
            window_handle = self.windowHandle()
            if window_handle is not None:
                window_handle.screenChanged.connect(self._handle_screen_changed)
                self._adapt_layout_for_screen(window_handle.screen())
            else:
                screen = QtWidgets.QApplication.screenAt(self.frameGeometry().center())
                self._adapt_layout_for_screen(screen)

    def _handle_screen_changed(self, screen):
        QtCore.QTimer.singleShot(0, lambda: self._adapt_layout_for_screen(screen))

    def _sync_top_panel_heights(self):
        if not hasattr(self, "device_group") or not hasattr(self, "info_group"):
            return
        target_height = max(
            148,
            self.device_group.sizeHint().height(),
            self.info_group.sizeHint().height(),
        )
        self.device_group.setFixedHeight(target_height)
        self.info_group.setFixedHeight(target_height)

    def _sync_middle_panel_heights(self):
        if not hasattr(self, "trajectory_left_widget") or not hasattr(self, "drag_control_group"):
            return
        if not hasattr(self, "outer_group_layout"):
            return
        margins = self.outer_group_layout.contentsMargins()
        available_height = max(
            220,
            self.outer_group.height() - margins.top() - margins.bottom(),
        )
        self.trajectory_left_widget.setFixedHeight(available_height)
        self.drag_control_group.setFixedHeight(available_height)

    def _adapt_layout_for_screen(self, screen):
        if screen is None:
            return
        available = screen.availableGeometry()
        control_min_height = max(240, min(320, int(available.height() * 0.28)))
        control_max_height = max(control_min_height + 20, min(420, int(available.height() * 0.36)))
        curve_min_height = max(240, min(360, int(available.height() * 0.34)))
        plot_min_height = max(200, min(300, int(available.height() * 0.27)))
        drag_max_width = max(520, min(1400, int(available.width() * 0.70)))

        self.outer_group.setMinimumHeight(control_min_height)
        self.outer_group.setMaximumHeight(control_max_height)
        self.curve_group.setMinimumHeight(curve_min_height)
        self.plot_canvas.setMinimumHeight(plot_min_height)
        self.drag_control_group.setMaximumWidth(drag_max_width)
        self._sync_top_panel_heights()
        self._sync_middle_panel_heights()
        self.workspace_splitter.setSizes(
            [int(available.height() * 0.40), int(available.height() * 0.50)]
        )

        max_width = max(self.minimumWidth(), available.width() - 32)
        max_height = max(self.minimumHeight(), available.height() - 48)
        if self.width() > max_width or self.height() > max_height:
            self.resize(min(self.width(), max_width), min(self.height(), max_height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_top_panel_heights()
        self._sync_middle_panel_heights()

    def closeEvent(self, event):
        self._write_runtime_log("INFO", "上位机关闭")
        self._shutdown_manual_drag_worker()
        if self.runtime_log_handler is not None:
            self.runtime_logger.removeHandler(self.runtime_log_handler)
            self.runtime_log_handler.close()
            self.runtime_log_handler = None
        super().closeEvent(event)

    def _apply_visual_style(self):
        combo_arrow_path = (CURRENT_DIR / "assets" / "combobox_down_arrow.svg").as_posix()
        self.setStyleSheet(
            """
            QWidget {
                background: #f3f6fb;
                color: #1f2937;
                font-size: 13px;
            }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #d9e2ec;
                border-radius: 8px;
                margin-top: 10px;
                padding: 10px 10px 8px 10px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                top: 2px;
                padding: 0 4px;
                color: #334155;
            }
            QTabWidget::pane {
                border: 1px solid #d9e2ec;
                border-radius: 8px;
                background: #ffffff;
                top: -1px;
            }
            QTabBar::tab {
                background: #e9eef5;
                color: #475569;
                border: 1px solid #d9e2ec;
                padding: 7px %dpx;
                min-width: 92px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #0f172a;
                border-bottom-color: #ffffff;
            }
            QPushButton {
                background: #e8f0fb;
                color: #0f4c81;
                border: 1px solid #bfd3ea;
                border-radius: 6px;
                padding: 7px 14px;
                min-height: 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #dbe9f8;
            }
            QPushButton:pressed {
                background: #cbdff5;
            }
            QPushButton:disabled {
                background: #eef2f7;
                color: #94a3b8;
                border-color: #d7dde5;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox {
                background: #fbfdff;
                border: 1px solid #cfd8e3;
                border-radius: 6px;
                padding: 4px 8px;
                min-height: 28px;
                selection-background-color: #bfdbfe;
            }
            QComboBox {
                background: #fbfdff;
                border: 1px solid #cfd8e3;
                border-radius: 6px;
                padding: 4px 30px 4px 8px;
                min-height: 28px;
                selection-background-color: #bfdbfe;
            }
            QComboBox::drop-down {
                width: 24px;
                border: none;
                background: transparent;
                subcontrol-origin: border;
                subcontrol-position: top right;
            }
            QComboBox::down-arrow {
                image: url(%s);
                width: 10px;
                height: 6px;
            }
            QCheckBox {
                spacing: 8px;
            }
            QLabel {
                background: transparent;
            }
            QPushButton#torqueToggleButton {
                background: #d9f99d;
                color: #1f2937;
                border: 1px solid #84cc16;
                border-radius: 6px;
                padding: 4px 10px;
                font-weight: 700;
                text-align: center;
            }
            QLabel#modeStatusLabel {
                color: #0f4c81;
                font-weight: 600;
                padding: 4px 8px;
            }
            QPushButton#torqueToggleButton:checked {
                background: #dcfce7;
                color: #166534;
                border-color: #22c55e;
            }
            QPushButton#torqueToggleButton:!checked {
                background: #fee2e2;
                color: #991b1b;
                border-color: #ef4444;
            }
            QLabel#statusLabel {
                color: #475569;
                padding: 4px 2px 0 2px;
            }
            QLabel#parameterRangeHint {
                color: #64748b;
                font-size: 11px;
            }
            QWidget#infoContentWidget {
                background: transparent;
            }
            QGroupBox#infoGroup QLabel {
                font-size: 13px;
            }
            QGroupBox#infoGroup QLabel[frameShape="2"] {
                background: #f8fbff;
                border: 1px solid #cfd8e3;
                border-radius: 4px;
                padding: 2px 8px;
            }
            """ % (PARENT_TAB_HORIZONTAL_PADDING, combo_arrow_path)
        )
        self.main_menu_tabs.setDocumentMode(True)
        self.controller_tabs.setDocumentMode(True)
        self.port_combo.setMinimumWidth(240)
        self.id_combo.setMinimumWidth(120)
        self.baudrate_spin.setMinimumWidth(140)
        self.sample_interval_spin.setMinimumWidth(140)

    def apply_defaults(self):
        self.baudrate_spin.setValue(500000)
        self.sample_interval_spin.setValue(0.05)
        self.drag_speed_spin.setValue(200)
        self.drag_torque_enable_checkbox.setText("扭矩使能: 关")
        self.target_position_spin.setValue(500)
        self.single_duration_spin.setValue(0.5)
        self.recip_end_position_spin.setValue(500)
        self.recip_duration_spin.setValue(0.5)
        if "max_speed_limit" in self.parameter_inputs:
            self.parameter_inputs["max_speed_limit"].setValue(1000)
        self._rebuild_drag_servo_controls()

        self.id_combo.currentTextChanged.connect(self._handle_selected_servo_changed)
        self._update_single_start_position_enabled()
        self._update_controller_mode_ui()
        self._set_active_single_mode(MODE_SINGLE)
        self._set_active_multi_mode(MODE_SINGLE)
        latest_parameter_file = self._latest_parameter_file()
        if latest_parameter_file is not None:
            self.refresh_parameter_files(selected_path=latest_parameter_file)
        else:
            self.refresh_parameter_files()

    def _selected_main_servo_id(self):
        servo_id_text = self.id_combo.currentText().strip()
        if not servo_id_text:
            raise ValueError("请先输入或扫描舵机 ID。")
        try:
            return int(servo_id_text)
        except ValueError as exc:
            raise ValueError("舵机 ID 必须是整数。") from exc

    def _open_current_servo_link(self):
        port_name = self.port_combo.currentText().strip()
        if not port_name:
            raise ValueError("请先选择串口。")

        self._disconnect_manual_drag_worker()
        port_handler = ct.PortHandler(port_name)
        packet_handler = ct.scscl(port_handler)
        if not port_handler.openPort():
            raise RuntimeError("Failed to open the port")
        if not port_handler.setBaudRate(self.baudrate_spin.value()):
            raise RuntimeError("Failed to change the baudrate")
        return port_handler, packet_handler

    def _close_servo_link(self, port_handler):
        if port_handler is not None:
            try:
                port_handler.closePort()
            except Exception:
                pass

    def _check_packet_result(self, packet_handler, comm_result, error):
        ct.check_packet_result(packet_handler, comm_result, error)

    def _write_runtime_log(self, level, message):
        if getattr(self, "runtime_logger", None) is None:
            return
        log_method = getattr(self.runtime_logger, level.lower(), self.runtime_logger.info)
        log_method(message)

    def _set_error_status(self, prefix, error):
        message = "%s：%s" % (prefix, error)
        self._write_runtime_log("ERROR", message)
        self.set_status(message)

    def _ensure_manual_drag_worker(self):
        if self.manual_drag_worker is not None:
            return
        self.manual_drag_worker = ManualDragWorker(self)
        self.manual_drag_worker.status_message.connect(self.set_status)
        self.manual_drag_worker.error_message.connect(lambda message: self._set_error_status("手动模式失败", message))
        self.manual_drag_worker.actual_sample.connect(self._record_manual_drag_sample)
        self.manual_drag_worker.feedback_sample.connect(self._record_manual_feedback_sample)
        self.manual_drag_worker.link_active_changed.connect(self._set_manual_drag_link_active)
        self.manual_drag_worker.start()

    def _shutdown_manual_drag_worker(self):
        if self.manual_drag_worker is None:
            return
        self.manual_drag_worker.request_stop()
        self.manual_drag_worker.wait()
        self.manual_drag_worker = None
        self.manual_drag_link_active = False

    def _disconnect_manual_drag_worker(self):
        if self.manual_drag_worker is None:
            return
        self.manual_drag_worker.request_disconnect()
        deadline = time.time() + 1.0
        while self.manual_drag_link_active and time.time() < deadline:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.01)

    def _set_manual_drag_link_active(self, active):
        self.manual_drag_link_active = active

    def _current_parameter_values(self):
        return {field_key: spinbox.value() for field_key, spinbox in self.parameter_inputs.items()}

    def _start_manual_curve(self, servo_id):
        self.manual_curve_start_time = time.time()
        self.manual_curve_last_refresh_time = 0.0
        self.manual_curve_series = {}
        self.ideal_times = []
        self.ideal_positions = []
        self.actual_times = []
        self.actual_positions = []
        self.plot_canvas.reset_plot()
        self.plot_canvas.set_ideal_curve([], [])
        self.plot_canvas.set_actual_curve([], [])
        self.plot_canvas.refresh_curves()
        self._update_curve_visibility()

    def _append_manual_curve_point(self, servo_id, position):
        if self.manual_curve_start_time is None:
            self._start_manual_curve(servo_id)
        sample_time = max(0.0, time.time() - self.manual_curve_start_time)
        if servo_id not in self.manual_curve_series:
            color = self.drag_servo_controls.get(servo_id, {}).get("color", "#dc2626")
            self.manual_curve_series[servo_id] = {
                "times": [],
                "positions": [],
                "color": color,
            }
        self.manual_curve_series[servo_id]["times"].append(sample_time)
        self.manual_curve_series[servo_id]["positions"].append(float(position))
        self._trim_manual_curve_series(sample_time)
        self.plot_canvas.set_actual_curves(self.manual_curve_series)
        self.plot_canvas.refresh_curves(follow_latest_seconds=MANUAL_CURVE_TIME_WINDOW_SECONDS)
        self._update_curve_visibility()

    def _trim_manual_curve_series(self, latest_sample_time):
        keep_after_time = max(
            0.0,
            float(latest_sample_time) - MANUAL_CURVE_TIME_WINDOW_SECONDS - MANUAL_CURVE_HISTORY_PADDING_SECONDS,
        )
        for curve in self.manual_curve_series.values():
            times = curve["times"]
            positions = curve["positions"]
            trim_count = 0
            while trim_count < len(times) - 1 and times[trim_count] < keep_after_time:
                trim_count += 1
            if trim_count:
                del times[:trim_count]
                del positions[:trim_count]

    def _record_manual_drag_sample(self, servo_id, position):
        self.manual_feedback_positions[servo_id] = int(round(position))
        if servo_id in self.drag_servo_controls:
            slider = self.drag_servo_controls[servo_id]["slider"]
            if not slider.isSliderDown():
                self._drag_sync_updating = True
                try:
                    self._apply_drag_slider_value(servo_id, int(round(position)), queue_command=False)
                    self._update_drag_reverse_reference(servo_id)
                finally:
                    self._drag_sync_updating = False
        now = time.time()
        if now - self.manual_curve_last_refresh_time < 0.016:
            return
        self.manual_curve_last_refresh_time = now
        self._append_manual_curve_point(servo_id, position)

    def _record_manual_feedback_sample(self, servo_id, position, speed, load, temperature, status):
        self.manual_feedback_positions[servo_id] = int(position)
        if servo_id != self._selected_main_servo_id():
            return
        self.info_value_labels["present_position"].setText(self._format_position_text(position))
        self.info_value_labels["present_speed"].setText(self._format_speed_text(speed))
        self.info_value_labels["present_load"].setText(self._format_load_text(load))
        self.info_value_labels["present_temperature"].setText(self._format_temperature_text(temperature))
        self.info_value_labels["present_status"].setText(self._servo_status_text(status))
        self._update_parameter_alert_status(servo_id, temperature, load)

    def _single_mode_values_from_ui(self):
        return {
            "use_current_position_as_start": self.use_current_position_checkbox.isChecked(),
            "start_position": self.start_position_spin.value(),
            "target_position": self.target_position_spin.value(),
            "trajectory_duration": self.single_duration_spin.value(),
            "start_velocity": self.single_start_velocity_spin.value(),
            "end_velocity": self.single_end_velocity_spin.value(),
        }

    def _recip_mode_values_from_ui(self):
        return {
            "start_position": self.recip_start_position_spin.value(),
            "end_position": self.recip_end_position_spin.value(),
            "trajectory_duration": self.recip_duration_spin.value(),
            "start_velocity": self.recip_start_velocity_spin.value(),
            "end_velocity": self.recip_end_velocity_spin.value(),
        }

    def _update_parameter_alert_status(self, servo_id, temperature, load):
        parameter_values = self.servo_parameter_configs.get(servo_id)
        if parameter_values is None:
            return
        alerts = []
        max_temperature = parameter_values.get("max_temperature")
        max_torque = parameter_values.get("max_torque")
        if max_temperature is not None and temperature > int(max_temperature):
            alerts.append("温度超限 %d>%d" % (temperature, int(max_temperature)))
        if max_torque is not None and load > int(max_torque):
            alerts.append("扭矩超限 %d>%d" % (load, int(max_torque)))
        if alerts:
            self.set_status("ID %d 警报 | %s" % (servo_id, " | ".join(alerts)))

    def _servo_status_text(self, status):
        return "正常" if int(status) == 0 else "异常"

    def _format_position_text(self, position):
        return str(int(position))

    def _format_speed_text(self, speed):
        return "%+d" % int(speed)

    def _format_load_text(self, load):
        return "%+d" % int(load)

    def _format_temperature_text(self, temperature):
        return str(int(temperature))

    def _servo_status_text(self, status):
        return "正常" if int(status) == 0 else "异常"

    def _ensure_servo_parameter_config(self, servo_id):
        return self.servo_parameter_configs.get(servo_id)

    def _servo_position_limits(self, servo_id=None):
        if servo_id is None:
            values = self._current_parameter_values()
        elif servo_id in self.servo_parameter_configs:
            values = self.servo_parameter_configs[servo_id]
        else:
            return UI_FALLBACK_POSITION_MIN, UI_FALLBACK_POSITION_MAX
        minimum = int(values.get("min_angle_limit", UI_FALLBACK_POSITION_MIN))
        maximum = int(values.get("max_angle_limit", UI_FALLBACK_POSITION_MAX))
        if minimum > maximum:
            minimum, maximum = maximum, minimum
        return minimum, maximum

    def _servo_speed_limit(self, servo_id=None):
        if servo_id is None:
            values = self._current_parameter_values()
        elif servo_id in self.servo_parameter_configs:
            values = self.servo_parameter_configs[servo_id]
        else:
            return 1000
        speed_limit = int(values.get("max_speed_limit", 1000))
        return max(0, speed_limit)

    def _limit_goal_speed(self, servo_id, goal_speed):
        speed_limit = self._servo_speed_limit(servo_id)
        return max(0, min(int(goal_speed), speed_limit))

    def _ensure_position_mode_for_servo(self, packet_handler, servo_id):
        if servo_id in self.position_mode_ready_ids:
            return False
        if servo_id not in self.servo_parameter_configs:
            self.position_mode_ready_ids.add(servo_id)
            return False
        min_angle_limit, max_angle_limit = self._servo_position_limits(servo_id)
        changed = ct.ensure_scscl_position_mode(packet_handler, servo_id, min_angle_limit, max_angle_limit)
        self.position_mode_ready_ids.add(servo_id)
        return changed

    def _handle_selected_servo_changed(self):
        self._clear_info_display()
        if not self.id_combo.currentText().strip():
            self.set_status("当前舵机 ID：--")
            return
        self.set_status("当前舵机 ID：%s" % self.id_combo.currentText().strip())
        self.load_servo_parameters()
        self._auto_refresh_servo_info()

    def _clear_info_display(self):
        for label in self.info_value_labels.values():
            label.setText("--")

    def _ensure_parameter_export_dir(self):
        parameter_dir = APP_ROOT / PARAMETER_EXPORT_DIR
        parameter_dir.mkdir(parents=True, exist_ok=True)
        return parameter_dir

    def _display_parameter_path(self, path):
        path = Path(path)
        try:
            return str(path.relative_to(APP_ROOT))
        except ValueError:
            return str(path)

    def _prompt_json_save_path(self, title, initial_dir, suggested_name):
        file_path, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            title,
            str(initial_dir / suggested_name),
            "JSON Files (*.json)",
        )
        if not file_path:
            return None
        save_path = Path(file_path)
        if save_path.suffix.lower() != ".json":
            save_path = save_path.with_suffix(".json")
        return save_path

    def _ensure_trajectory_parameter_dir(self):
        trajectory_dir = APP_ROOT / TRAJECTORY_PARAMETER_DIR
        trajectory_dir.mkdir(parents=True, exist_ok=True)
        return trajectory_dir

    def _build_trajectory_mode_config(self, controller_kind, mode, servo_id=None):
        if controller_kind == CONTROLLER_SINGLE:
            if mode == MODE_SINGLE:
                single_values = self._single_mode_values_from_ui()
                return {
                    "mode": MODE_SINGLE,
                    **single_values,
                }
            recip_values = self._recip_mode_values_from_ui()
            return {
                "mode": MODE_RECIPROCATING,
                "use_current_position_as_start": False,
                "start_position": recip_values["start_position"],
                "target_position": recip_values["end_position"],
                "recip_end_position": recip_values["end_position"],
                "trajectory_duration": recip_values["trajectory_duration"],
                "start_velocity": recip_values["start_velocity"],
                "end_velocity": recip_values["end_velocity"],
            }

        if servo_id is None:
            raise ValueError("多电机模式缺少 servo_id。")
        motor_config = copy.deepcopy(self._ensure_motor_config(servo_id))
        if mode == MODE_SINGLE:
            return {
                "mode": MODE_SINGLE,
                "use_current_position_as_start": motor_config["single"]["use_current_position_as_start"],
                "start_position": motor_config["single"]["start_position"],
                "target_position": motor_config["single"]["target_position"],
                "trajectory_duration": motor_config["single"]["trajectory_duration"],
                "start_velocity": motor_config["single"]["start_velocity"],
                "end_velocity": motor_config["single"]["end_velocity"],
            }
        return {
            "mode": MODE_RECIPROCATING,
            "use_current_position_as_start": False,
            "start_position": motor_config["reciprocating"]["start_position"],
            "target_position": motor_config["reciprocating"]["end_position"],
            "recip_end_position": motor_config["reciprocating"]["end_position"],
            "trajectory_duration": motor_config["reciprocating"]["trajectory_duration"],
            "start_velocity": motor_config["reciprocating"]["start_velocity"],
            "end_velocity": motor_config["reciprocating"]["end_velocity"],
        }

    def _collect_trajectory_parameter_context(self, controller_kind, mode):
        if controller_kind == CONTROLLER_SINGLE:
            servo_id = self._selected_main_servo_id()
            mode_config = self._build_trajectory_mode_config(controller_kind, mode)
            if mode == MODE_SINGLE:
                mode_config["recip_end_position"] = mode_config["target_position"]
            self._validate_mode_config(mode_config, "")
            return {
                "controller_kind": controller_kind,
                "mode": mode,
                "servo_ids": [servo_id],
                "motor_modes": {servo_id: mode_config},
            }

        self._save_current_multi_editor()
        servo_ids = self._selected_multi_servo_ids()
        if not servo_ids:
            raise ValueError("多电机模式请先勾选至少一个舵机 ID。")

        motor_modes = {}
        for servo_id in servo_ids:
            mode_config = self._build_trajectory_mode_config(controller_kind, mode, servo_id=servo_id)
            if mode == MODE_SINGLE:
                mode_config["recip_end_position"] = mode_config["target_position"]
            self._validate_mode_config(mode_config, "ID %d " % servo_id)
            motor_modes[servo_id] = mode_config
        return {
            "controller_kind": controller_kind,
            "mode": mode,
            "servo_ids": servo_ids,
            "motor_modes": motor_modes,
        }

    def _trajectory_parameter_payload(self, controller_kind, mode):
        context = self._collect_trajectory_parameter_context(controller_kind, mode)
        payload = {
            "schema_version": TRAJECTORY_PARAMETER_SCHEMA_VERSION,
            "payload_type": "scscl_cubic_trajectory",
            "exported_at": QtCore.QDateTime.currentDateTime().toString(QtCore.Qt.ISODate),
            "controller_kind": controller_kind,
            "mode": mode,
            "servo_ids": list(context["servo_ids"]),
            "trajectory_parameters": [],
        }
        for servo_id in context["servo_ids"]:
            mode_config = context["motor_modes"][servo_id]
            payload["trajectory_parameters"].append(
                {
                    "servo_id": servo_id,
                    "version": TRAJECTORY_PARAMETER_SCHEMA_VERSION,
                    "polynomial_type": "cubic",
                    "use_current_position_as_start": bool(mode_config.get("use_current_position_as_start", False)),
                    "start_position": mode_config["start_position"],
                    "target_position": mode_config["target_position"],
                    "recip_end_position": mode_config.get("recip_end_position", mode_config["target_position"]),
                    "trajectory_duration": mode_config["trajectory_duration"],
                    "start_velocity": mode_config["start_velocity"],
                    "end_velocity": mode_config["end_velocity"],
                }
            )
        return payload

    def export_trajectory_parameters(self, controller_kind, mode):
        try:
            payload = self._trajectory_parameter_payload(controller_kind, mode)
            export_dir = self._ensure_trajectory_parameter_dir()
            servo_label = "-".join(str(servo_id) for servo_id in payload["servo_ids"])
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            mode_label = "single" if mode == MODE_SINGLE else "recip"
            controller_label = "single_motor" if controller_kind == CONTROLLER_SINGLE else "multi_motor"
            suggested_name = (
                "trajectory_%s_%s_id%s_%s%s"
                % (controller_label, mode_label, servo_label, timestamp, TRAJECTORY_PARAMETER_FILE_SUFFIX)
            )
            export_path = self._prompt_json_save_path("导出轨迹参数", export_dir, suggested_name)
            if export_path is None:
                self.set_status("已取消轨迹参数导出。")
                return
            export_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            self.set_status("轨迹参数已导出到 %s" % self._display_parameter_path(export_path))
        except Exception as exc:
            self._set_error_status("轨迹参数导出失败", exc)

    def _apply_single_trajectory_parameter(self, mode, entry):
        self.active_controller_kind = CONTROLLER_SINGLE
        if self.controller_tabs.currentIndex() != 0:
            self.controller_tabs.setCurrentIndex(0)
        self._set_active_single_mode(mode)
        self.id_combo.setCurrentText(str(entry["servo_id"]))
        if mode == MODE_SINGLE:
            self.use_current_position_checkbox.setChecked(bool(entry.get("use_current_position_as_start", False)))
            self.start_position_spin.setValue(int(entry["start_position"]))
            self.target_position_spin.setValue(int(entry["target_position"]))
            self.single_duration_spin.setValue(float(entry["trajectory_duration"]))
            self.single_start_velocity_spin.setValue(float(entry["start_velocity"]))
            self.single_end_velocity_spin.setValue(float(entry["end_velocity"]))
        else:
            self.recip_start_position_spin.setValue(int(entry["start_position"]))
            self.recip_end_position_spin.setValue(int(entry.get("recip_end_position", entry["target_position"])))
            self.recip_duration_spin.setValue(float(entry["trajectory_duration"]))
            self.recip_start_velocity_spin.setValue(float(entry["start_velocity"]))
            self.recip_end_velocity_spin.setValue(float(entry["end_velocity"]))

    def _apply_multi_trajectory_parameters(self, mode, entries):
        self.active_controller_kind = CONTROLLER_MULTI
        if self.controller_tabs.currentIndex() != 1:
            self.controller_tabs.setCurrentIndex(1)
        self._set_active_multi_mode(mode)
        imported_servo_ids = [int(entry["servo_id"]) for entry in entries]
        self.detected_servo_ids = sorted(set(self.detected_servo_ids).union(imported_servo_ids))
        self._rebuild_multi_id_options()
        for entry in entries:
            servo_id = int(entry["servo_id"])
            motor_config = self._ensure_motor_config(servo_id)
            if mode == MODE_SINGLE:
                motor_config["single"] = {
                    "use_current_position_as_start": bool(entry.get("use_current_position_as_start", False)),
                    "start_position": int(entry["start_position"]),
                    "target_position": int(entry["target_position"]),
                    "trajectory_duration": float(entry["trajectory_duration"]),
                    "start_velocity": float(entry["start_velocity"]),
                    "end_velocity": float(entry["end_velocity"]),
                }
            else:
                motor_config["reciprocating"] = {
                    "start_position": int(entry["start_position"]),
                    "end_position": int(entry.get("recip_end_position", entry["target_position"])),
                    "trajectory_duration": float(entry["trajectory_duration"]),
                    "start_velocity": float(entry["start_velocity"]),
                    "end_velocity": float(entry["end_velocity"]),
                }
        for servo_id, checkbox in self.multi_id_checkboxes.items():
            checkbox.setChecked(servo_id in imported_servo_ids)
        self._refresh_multi_editor_selector()

    def import_trajectory_parameters(self, controller_kind, mode):
        try:
            import_dir = self._ensure_trajectory_parameter_dir()
            file_path, _filter = QtWidgets.QFileDialog.getOpenFileName(
                self,
                "导入轨迹参数",
                str(import_dir),
                "JSON Files (*.json)",
            )
            if not file_path:
                return
            payload = json.loads(Path(file_path).read_text(encoding="utf-8"))
            if payload.get("payload_type") != "scscl_cubic_trajectory":
                raise ValueError("这不是受支持的轨迹参数文件。")
            if payload.get("controller_kind") != controller_kind:
                raise ValueError("导入文件的控制类型与当前悬浮菜单不匹配。")
            if payload.get("mode") != mode:
                raise ValueError("导入文件的运行模式与当前悬浮菜单不匹配。")
            entries = payload.get("trajectory_parameters")
            if not isinstance(entries, list) or not entries:
                raise ValueError("轨迹参数文件缺少 trajectory_parameters。")

            if controller_kind == CONTROLLER_SINGLE:
                self._apply_single_trajectory_parameter(mode, entries[0])
            else:
                self._apply_multi_trajectory_parameters(mode, entries)
            self.set_status("已导入轨迹参数：%s" % self._display_parameter_path(file_path))
        except Exception as exc:
            self._set_error_status("轨迹参数导入失败", exc)

    def _latest_parameter_file(self):
        export_dir = self._ensure_parameter_export_dir()
        default_parameter_file = export_dir / DEFAULT_PARAMETER_FILE_NAME
        if default_parameter_file.exists():
            return default_parameter_file
        parameter_files = sorted(
            export_dir.glob("*%s" % PARAMETER_FILE_SUFFIX),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return parameter_files[0] if parameter_files else None

    def _parameter_field_definition_map(self):
        return {field_key: definition for field_key, *definition in PARAMETER_DEFINITIONS}

    def _current_parameter_snapshot(self):
        snapshot = {}
        definition_map = self._parameter_field_definition_map()
        for field_key, spinbox in self.parameter_inputs.items():
            field_label, address, byte_size, _minimum, _maximum, _range_hint, _group_name = definition_map[field_key]
            snapshot[field_key] = {
                "label": field_label,
                "address": address,
                "byte_size": byte_size,
                "value": spinbox.value(),
            }
        return snapshot

    def _build_parameter_export_payload(self):
        servo_id_value = self.parameter_inputs["servo_id"].value() if "servo_id" in self.parameter_inputs else None
        return {
            "schema_version": 1,
            "device_type": "SCSCL",
            "exported_at": QtCore.QDateTime.currentDateTime().toString(QtCore.Qt.ISODate),
            "servo_id": servo_id_value,
            "parameters": self._current_parameter_snapshot(),
        }

    def export_servo_parameters(self):
        try:
            export_dir = self._ensure_parameter_export_dir()
            payload = self._build_parameter_export_payload()
            servo_id_value = payload["servo_id"] if payload["servo_id"] is not None else "unknown"
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            suggested_name = "scscl_id%s_%s%s" % (servo_id_value, timestamp, PARAMETER_FILE_SUFFIX)
            export_path = self._prompt_json_save_path("导出电机配置", export_dir, suggested_name)
            if export_path is None:
                self.set_status("已取消电机配置导出。")
                return
            export_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            self.refresh_parameter_files(selected_path=export_path)
            self.set_status("配置已导出到 %s" % self._display_parameter_path(export_path))
        except Exception as exc:
            self._set_error_status("配置导出失败", exc)

    def refresh_parameter_files(self, selected_path=None):
        try:
            export_dir = self._ensure_parameter_export_dir()
        except Exception as exc:
            self.set_status("配置目录创建失败：%s" % exc)
            return

        selected_path = str(Path(selected_path).resolve()) if selected_path else None
        self.parameter_file_paths = {}
        self._parameter_file_loading = True
        try:
            self.parameter_file_list.clear()
            parameter_files = sorted(export_dir.glob("*%s" % PARAMETER_FILE_SUFFIX), key=lambda path: path.stat().st_mtime, reverse=True)
            for parameter_file in parameter_files:
                item = QtWidgets.QListWidgetItem(parameter_file.stem)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(
                    QtCore.Qt.Checked
                    if selected_path and str(parameter_file.resolve()) == selected_path
                    else QtCore.Qt.Unchecked
                )
                item.setToolTip(str(parameter_file))
                self.parameter_file_paths[id(item)] = parameter_file
                self.parameter_file_list.addItem(item)
        finally:
            self._parameter_file_loading = False

        if selected_path:
            for index in range(self.parameter_file_list.count()):
                item = self.parameter_file_list.item(index)
                if item.flags() & QtCore.Qt.ItemIsUserCheckable and item.checkState() == QtCore.Qt.Checked:
                    self.parameter_file_list.setCurrentItem(item)
                    break

    def _load_parameter_file_into_inputs(self, file_path):
        payload = json.loads(Path(file_path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("配置档案格式无效。")
        parameters = payload.get("parameters")
        if not isinstance(parameters, dict):
            raise ValueError("配置缺少 parameters 字段。")

        for field_key, spinbox in self.parameter_inputs.items():
            if field_key not in parameters:
                continue
            field_payload = parameters[field_key]
            if not isinstance(field_payload, dict) or "value" not in field_payload:
                continue
            spinbox.setValue(int(field_payload["value"]))

        self.set_status("配置文件已解析到参数框，尚未写入电机；最终生效以参数框当前数值为准：%s" % Path(file_path).name)

    def _selected_parameter_file_path(self):
        for index in range(self.parameter_file_list.count()):
            item = self.parameter_file_list.item(index)
            if item.flags() & QtCore.Qt.ItemIsUserCheckable and item.checkState() == QtCore.Qt.Checked:
                return self.parameter_file_paths.get(id(item))
        current_item = self.parameter_file_list.currentItem()
        if current_item is not None:
            return self.parameter_file_paths.get(id(current_item))
        return None

    def stage_selected_parameter_file(self):
        try:
            target_path = self._selected_parameter_file_path()
            if target_path is None:
                raise ValueError("请先在配置列表中选择一个配置文件。")
            self._load_parameter_file_into_inputs(target_path)
        except Exception as exc:
            self._set_error_status("配置写入参数框失败", exc)

    def _handle_parameter_file_item_changed(self, item):
        if self._parameter_file_loading:
            return
        if not (item.flags() & QtCore.Qt.ItemIsUserCheckable):
            return
        if item.checkState() != QtCore.Qt.Checked:
            return

        target_path = self.parameter_file_paths.get(id(item))
        if target_path is None:
            return

        self._parameter_file_loading = True
        try:
            for index in range(self.parameter_file_list.count()):
                other_item = self.parameter_file_list.item(index)
                if other_item is item:
                    continue
                if other_item.flags() & QtCore.Qt.ItemIsUserCheckable:
                    other_item.setCheckState(QtCore.Qt.Unchecked)
        finally:
            self._parameter_file_loading = False

        self.parameter_file_list.setCurrentItem(item)
        self.set_status("已选择配置：%s；点击载入配置只会填入参数框，不会写入电机。" % Path(target_path).name)

    def _auto_refresh_servo_info(self):
        self.refresh_servo_info(silent=True)

    def refresh_servo_info(self, silent=False):
        if self.worker_running() or self.manual_drag_link_active:
            return
        port_handler = None
        try:
            servo_id = self._selected_main_servo_id()
            port_handler, packet_handler = self._open_current_servo_link()

            position, comm_result, error = packet_handler.read2ByteTxRx(servo_id, SCSCL_PRESENT_POSITION)
            self._check_packet_result(packet_handler, comm_result, error)
            speed_raw, comm_result, error = packet_handler.read2ByteTxRx(servo_id, SCSCL_PRESENT_SPEED)
            self._check_packet_result(packet_handler, comm_result, error)
            load_raw, comm_result, error = packet_handler.read2ByteTxRx(servo_id, SCSCL_PRESENT_LOAD)
            self._check_packet_result(packet_handler, comm_result, error)
            temperature, comm_result, error = packet_handler.read1ByteTxRx(servo_id, SCSCL_PRESENT_TEMPERATURE)
            self._check_packet_result(packet_handler, comm_result, error)
            status, comm_result, error = packet_handler.read1ByteTxRx(servo_id, SCSCL_PRESENT_STATUS)
            self._check_packet_result(packet_handler, comm_result, error)
            speed = ct.decode_scscl_direction_value(speed_raw)
            load = ct.decode_scscl_direction_value(load_raw)

            self.info_value_labels["present_position"].setText(self._format_position_text(position))
            self.info_value_labels["present_speed"].setText(self._format_speed_text(speed))
            self.info_value_labels["present_load"].setText(self._format_load_text(load))
            self.info_value_labels["present_temperature"].setText(self._format_temperature_text(temperature))
            self.info_value_labels["present_status"].setText(self._servo_status_text(status))
            self.manual_feedback_positions[servo_id] = int(position)
            if servo_id in self.drag_servo_controls:
                slider = self.drag_servo_controls[servo_id]["slider"]
                if not slider.isSliderDown():
                    self._drag_sync_updating = True
                    try:
                        self._apply_drag_slider_value(servo_id, int(position), queue_command=False)
                        self._update_drag_reverse_reference(servo_id)
                    finally:
                        self._drag_sync_updating = False
            self._update_parameter_alert_status(servo_id, temperature, load)
        except Exception as exc:
            if not silent:
                self._set_error_status("信息读取失败", exc)
            else:
                self._clear_info_display()
        finally:
            self._close_servo_link(port_handler)

    def load_servo_parameters(self):
        port_handler = None
        try:
            servo_id = self._selected_main_servo_id()
            port_handler, packet_handler = self._open_current_servo_link()
            for field_key, _field_label, address, byte_size, _minimum, _maximum, _range_hint, _group_name in PARAMETER_DEFINITIONS:
                if field_key in UI_ONLY_PARAMETER_KEYS:
                    continue
                if byte_size == 1:
                    value, comm_result, error = packet_handler.read1ByteTxRx(servo_id, address)
                else:
                    value, comm_result, error = packet_handler.read2ByteTxRx(servo_id, address)
                self._check_packet_result(packet_handler, comm_result, error)
                self.parameter_inputs[field_key].setValue(value)
            self.servo_parameter_configs[servo_id] = self._current_parameter_values()
            self._rebuild_drag_servo_controls()
            self.set_status("已自动读取电机内部配置 | ID %d" % servo_id)
        except Exception as exc:
            self._set_error_status("自动读取电机内部配置失败", exc)
        finally:
            self._close_servo_link(port_handler)

    def apply_servo_parameters(self):
        port_handler = None
        try:
            current_servo_id = self._selected_main_servo_id()
            target_servo_id = self.parameter_inputs["servo_id"].value()
            port_handler, packet_handler = self._open_current_servo_link()
            comm_result, error = packet_handler.unLockEprom(current_servo_id)
            self._check_packet_result(packet_handler, comm_result, error)

            comm_result, error = packet_handler.write1ByteTxRx(current_servo_id, 8, 1)
            self._check_packet_result(packet_handler, comm_result, error)

            for field_key, _field_label, address, byte_size, _minimum, _maximum, _range_hint, _group_name in PARAMETER_DEFINITIONS:
                if field_key == "servo_id" or field_key in READ_ONLY_PARAMETER_KEYS or field_key in UI_ONLY_PARAMETER_KEYS:
                    continue
                value = self.parameter_inputs[field_key].value()
                if byte_size == 1:
                    comm_result, error = packet_handler.write1ByteTxRx(current_servo_id, address, value)
                else:
                    comm_result, error = packet_handler.write2ByteTxRx(current_servo_id, address, value)
                self._check_packet_result(packet_handler, comm_result, error)

            if target_servo_id != current_servo_id:
                comm_result, error = packet_handler.write1ByteTxRx(current_servo_id, 5, target_servo_id)
                self._check_packet_result(packet_handler, comm_result, error)

            comm_result, error = packet_handler.LockEprom(target_servo_id)
            self._check_packet_result(packet_handler, comm_result, error)

            updated_values = self._current_parameter_values()
            self.servo_parameter_configs[target_servo_id] = updated_values
            if target_servo_id != current_servo_id and current_servo_id in self.servo_parameter_configs:
                del self.servo_parameter_configs[current_servo_id]
            if target_servo_id != current_servo_id:
                self.id_combo.setCurrentText(str(target_servo_id))
            self._rebuild_drag_servo_controls()
            self.set_status("应用完成，已将参数框当前数值写入电机。")
            QtWidgets.QMessageBox.information(
                self,
                "应用成功",
                "已将参数框中的当前数值写入电机 ID %d。" % target_servo_id,
            )
        except Exception as exc:
            self._set_error_status("配置应用失败", exc)
        finally:
            self._close_servo_link(port_handler)

    def refresh_ports(self):
        current_text = self.port_combo.currentText()
        ports = ct.list_serial_ports()
        self.port_combo.clear()
        self.port_combo.addItems(ports)
        if current_text:
            self.port_combo.setCurrentText(current_text)
        elif ports:
            self.port_combo.setCurrentIndex(0)
        self.set_status("检测到 %d 个串口。" % len(ports))

    def set_status(self, message):
        self.status_label.setText(message)
        self._write_runtime_log("INFO", message)

    def worker_running(self):
        return self.worker is not None and self.worker.isRunning()

    def _default_motor_config(self):
        single_values = self._single_mode_values_from_ui()
        recip_values = self._recip_mode_values_from_ui()
        return {
            "single": {
                **single_values,
            },
            "reciprocating": {
                **recip_values,
            },
        }

    def _ensure_motor_config(self, servo_id):
        if servo_id not in self.motor_configs:
            self.motor_configs[servo_id] = self._default_motor_config()
        return self.motor_configs[servo_id]

    def _create_mode_action_row(self, controller_kind, mode):
        action_layout = QtWidgets.QHBoxLayout()
        action_layout.setSpacing(8)
        run_button = QtWidgets.QPushButton("执行轨迹")
        run_button.clicked.connect(lambda _checked=False, kind=controller_kind: self.run_trajectory(kind))
        stop_button = QtWidgets.QPushButton("停止执行")
        stop_button.clicked.connect(self.stop_trajectory)
        stop_button.setEnabled(False)
        import_button = QtWidgets.QPushButton("参数导入")
        import_button.clicked.connect(
            lambda _checked=False, kind=controller_kind, current_mode=mode: self.import_trajectory_parameters(kind, current_mode)
        )
        export_button = QtWidgets.QPushButton("参数导出")
        export_button.clicked.connect(
            lambda _checked=False, kind=controller_kind, current_mode=mode: self.export_trajectory_parameters(kind, current_mode)
        )
        action_layout.addWidget(run_button)
        action_layout.addWidget(stop_button)
        action_layout.addWidget(import_button)
        action_layout.addWidget(export_button)
        action_layout.addStretch(1)
        self.run_action_buttons.append(run_button)
        self.stop_action_buttons.append(stop_button)
        return action_layout

    def _multi_editor_contexts(self):
        return (
            (MODE_SINGLE, self.multi_single_edit_id_combo, self.multi_single_config_panel),
            (MODE_RECIPROCATING, self.multi_recip_edit_id_combo, self.multi_recip_config_panel),
        )

    def _save_current_multi_editor(self, mode_filter=None):
        if self._multi_editor_loading:
            return
        for mode, combo, panel in self._multi_editor_contexts():
            if mode_filter is not None and mode != mode_filter:
                continue
            servo_id = self._multi_editor_loaded_ids.get(mode)
            if servo_id is None:
                continue
            panel_config = panel.export_config()
            motor_config = self._ensure_motor_config(servo_id)
            if mode == MODE_SINGLE:
                motor_config["single"] = panel_config["single"]
            else:
                motor_config["reciprocating"] = panel_config["reciprocating"]

    def _load_multi_editor_for_servo(self, servo_id, mode=None):
        self._multi_editor_loading = True
        try:
            enabled = bool(self._selected_multi_servo_ids()) and not self.worker_running()
            for context_mode, combo, panel in self._multi_editor_contexts():
                if mode is not None and context_mode != mode:
                    continue
                if servo_id is None:
                    panel.setEnabled(False)
                    combo.setEnabled(False)
                    self._multi_editor_loaded_ids[context_mode] = None
                    continue
                panel.load_config(self._ensure_motor_config(servo_id))
                panel.set_active_mode(context_mode)
                panel.setEnabled(enabled)
                combo.setEnabled(enabled)
                self._multi_editor_loaded_ids[context_mode] = servo_id
        finally:
            self._multi_editor_loading = False

    def _refresh_multi_editor_selector(self):
        self._save_current_multi_editor()
        selected_ids = self._selected_multi_servo_ids()
        current_ids = {mode: combo.currentData() for mode, combo, _panel in self._multi_editor_contexts()}
        for mode, combo, _panel in self._multi_editor_contexts():
            combo.blockSignals(True)
            combo.clear()
            for servo_id in selected_ids:
                combo.addItem("ID %d" % servo_id, servo_id)
            combo.blockSignals(False)
            if not selected_ids:
                continue
            current_id = current_ids.get(mode)
            index = selected_ids.index(current_id) if current_id in selected_ids else 0
            combo.setCurrentIndex(index)
            self._load_multi_editor_for_servo(selected_ids[index], mode)

        if not selected_ids:
            self._load_multi_editor_for_servo(None)

    def _on_multi_single_edit_servo_changed(self):
        if self._multi_editor_loading:
            return
        self._save_current_multi_editor(MODE_SINGLE)
        self._load_multi_editor_for_servo(self.multi_single_edit_id_combo.currentData(), MODE_SINGLE)

    def _on_multi_recip_edit_servo_changed(self):
        if self._multi_editor_loading:
            return
        self._save_current_multi_editor(MODE_RECIPROCATING)
        self._load_multi_editor_for_servo(self.multi_recip_edit_id_combo.currentData(), MODE_RECIPROCATING)

    def _clear_multi_id_widgets(self):
        while self.multi_id_layout.count():
            item = self.multi_id_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.multi_id_checkboxes = {}

    def _clear_drag_servo_widgets(self):
        while self.drag_servo_layout.count():
            item = self.drag_servo_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.drag_servo_controls = {}
        self.drag_reverse_references = {}

    def _drag_slider_style(self, color):
        return """
            QSlider::groove:horizontal {
                border: 1px solid #d1d9e6;
                height: 8px;
                background: #eef2f7;
                border-radius: 4px;
            }
            QSlider::sub-page:horizontal {
                background: %s;
                border-radius: 4px;
            }
            QSlider::add-page:horizontal {
                background: #eef2f7;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: %s;
                border: 1px solid rgba(15, 23, 42, 0.2);
                width: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
        """ % (color, color)

    def _rebuild_drag_servo_controls(self):
        self._clear_drag_servo_widgets()
        servo_ids = list(self.detected_servo_ids)
        if not servo_ids:
            self.drag_servo_layout.addStretch(1)
            return

        palette = [
            "#ef4444",
            "#f59e0b",
            "#10b981",
            "#3b82f6",
            "#8b5cf6",
            "#ec4899",
            "#14b8a6",
            "#f97316",
        ]
        for index, servo_id in enumerate(servo_ids):
            color = palette[index % len(palette)]
            position_min, position_max = self._servo_position_limits(servo_id)
            row_frame = QtWidgets.QFrame()
            row_frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
            row_layout = QtWidgets.QGridLayout(row_frame)
            row_layout.setContentsMargins(8, 8, 8, 8)
            row_layout.setHorizontalSpacing(8)
            row_layout.setVerticalSpacing(6)

            title_label = QtWidgets.QLabel("ID %d" % servo_id)
            title_label.setStyleSheet("color: %s; font-weight: 700;" % color)
            select_checkbox = QtWidgets.QCheckBox()
            reverse_checkbox = QtWidgets.QCheckBox("反向")
            initial_position = 0 if position_min <= 0 <= position_max else position_min
            value_label = QtWidgets.QLabel(str(initial_position))
            value_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(position_min, position_max)
            slider.setTracking(True)
            slider.setValue(initial_position)
            slider.setStyleSheet(self._drag_slider_style(color))
            slider.valueChanged.connect(
                lambda value, current_servo_id=servo_id: self._handle_drag_servo_slider_changed(current_servo_id, value)
            )
            reverse_checkbox.toggled.connect(
                lambda checked, current_servo_id=servo_id: self._handle_drag_reverse_toggled(current_servo_id, checked)
            )

            row_layout.addWidget(title_label, 0, 0)
            row_layout.addWidget(reverse_checkbox, 0, 1)
            row_layout.addWidget(value_label, 0, 2)
            row_layout.addWidget(select_checkbox, 1, 0)
            row_layout.addWidget(slider, 1, 1, 1, 2)
            self.drag_servo_layout.addWidget(row_frame)
            self.drag_servo_controls[servo_id] = {
                "frame": row_frame,
                "slider": slider,
                "select_checkbox": select_checkbox,
                "reverse_checkbox": reverse_checkbox,
                "value_label": value_label,
                "color": color,
            }
            self.drag_reverse_references[servo_id] = {
                "slider": int(initial_position),
                "position": int(initial_position),
            }

        self.drag_servo_layout.addStretch(1)

    def _rebuild_multi_id_options(self):
        self._clear_multi_id_widgets()
        if not self.detected_servo_ids:
            self._refresh_multi_editor_selector()
            self._rebuild_drag_servo_controls()
            return

        for index, servo_id in enumerate(self.detected_servo_ids):
            checkbox = QtWidgets.QCheckBox("ID %d" % servo_id)
            checkbox.toggled.connect(self._update_multi_button_state)
            self.multi_id_checkboxes[servo_id] = checkbox
            self.multi_id_layout.addWidget(checkbox, index // 4, index % 4)
            self._ensure_motor_config(servo_id)
        self._rebuild_drag_servo_controls()
        self._update_multi_button_state()

    def scan_ids(self):
        port_name = self.port_combo.currentText().strip()
        if not port_name:
            self.set_status("请先选择或输入串口。")
            return

        self.set_status("正在扫描 %s 上的舵机 ID..." % port_name)
        port_handler = None
        try:
            port_handler = ct.PortHandler(port_name)
            packet_handler = ct.scscl(port_handler)
            if not port_handler.openPort():
                raise RuntimeError("Failed to open the port")
            if not port_handler.setBaudRate(self.baudrate_spin.value()):
                raise RuntimeError("Failed to change the baudrate")

            detected_servos = ct.scan_servo_ids(packet_handler)
            self.detected_servo_ids = [scs_id for scs_id, _model_number in detected_servos]
            self.position_mode_ready_ids = set()
            current_id_text = self.id_combo.currentText().strip()
            self.id_combo.blockSignals(True)
            try:
                self.id_combo.clear()
                for servo_id in self.detected_servo_ids:
                    self.id_combo.addItem(str(servo_id))
                if current_id_text:
                    self.id_combo.setCurrentText(current_id_text)
                elif self.detected_servo_ids:
                    self.id_combo.setCurrentText(str(self.detected_servo_ids[0]))
            finally:
                self.id_combo.blockSignals(False)
            self._rebuild_multi_id_options()
            if self.id_combo.currentText().strip():
                self._handle_selected_servo_changed()

            if detected_servos:
                self.set_status("扫描到 ID：%s" % ", ".join(str(item[0]) for item in detected_servos))
            else:
                self.set_status("没有舵机响应。")
        except Exception as exc:
            self._set_error_status("ID 扫描失败", exc)
        finally:
            if port_handler is not None:
                try:
                    port_handler.closePort()
                except Exception:
                    pass

    def _update_single_start_position_enabled(self):
        self.start_position_spin.setEnabled(not self.use_current_position_checkbox.isChecked())

    def _update_controller_mode_ui(self):
        is_multi = self.controller_tabs.currentIndex() == 1
        self.active_controller_kind = CONTROLLER_MULTI if is_multi else CONTROLLER_SINGLE
        self.single_controller_panel.setVisible(not is_multi)
        self.multi_controller_panel.setVisible(is_multi)
        self.id_combo.setEnabled(not is_multi)
        self.scan_ids_button.setEnabled(not self.worker_running())
        self.drag_control_group.setEnabled(not self.worker_running())
        self._update_multi_button_state()

    def _apply_drag_slider_value(self, servo_id, position, queue_command=True):
        if servo_id not in self.drag_servo_controls:
            return
        position_min, position_max = self._servo_position_limits(servo_id)
        clamped_position = int(max(position_min, min(position_max, position)))
        control = self.drag_servo_controls[servo_id]
        control["value_label"].setText(str(clamped_position))
        if control["slider"].value() != clamped_position:
            control["slider"].blockSignals(True)
            control["slider"].setValue(clamped_position)
            control["slider"].blockSignals(False)
        if queue_command:
            self.pending_drag_positions[servo_id] = clamped_position

    def _latest_manual_feedback_position(self, servo_id):
        if servo_id in self.manual_feedback_positions:
            return int(self.manual_feedback_positions[servo_id])
        if servo_id in self.drag_servo_controls:
            return int(self.drag_servo_controls[servo_id]["slider"].value())
        return 0

    def _update_drag_reverse_reference(self, servo_id, reference_position=None):
        if servo_id not in self.drag_servo_controls:
            return
        if reference_position is None:
            reference_position = self._latest_manual_feedback_position(servo_id)
        self.drag_reverse_references[servo_id] = {
            "slider": int(self.drag_servo_controls[servo_id]["slider"].value()),
            "position": int(reference_position),
        }

    def _submit_drag_hold_position(self, servo_id):
        if self.worker_running() or servo_id not in self.drag_servo_controls:
            return
        if not self.drag_torque_enable_checkbox.isChecked():
            return
        port_name = self.port_combo.currentText().strip()
        if not port_name:
            return
        hold_position = int(self._latest_manual_feedback_position(servo_id))
        self._validate_writepos_target(servo_id, hold_position, "ID %03d: " % servo_id)
        self._ensure_manual_drag_worker()
        self.manual_drag_worker.submit_positions(
            {
                "port_name": port_name,
                "baudrate": self.baudrate_spin.value(),
                "queued_positions": {servo_id: hold_position},
                "segment_time_ms": 0,
                "goal_speeds": {servo_id: self._limit_goal_speed(servo_id, self.drag_speed_spin.value())},
                "position_limits": {servo_id: self._servo_position_limits(servo_id)},
                "plotted_servo_id": servo_id,
            }
        )

    def _drag_target_position_for(self, servo_id, reverse_checked):
        control = self.drag_servo_controls[servo_id]
        slider_position = control["slider"].value()
        reference = self.drag_reverse_references.get(servo_id)
        if reference is None:
            self._update_drag_reverse_reference(servo_id)
            reference = self.drag_reverse_references[servo_id]
        delta = int(slider_position) - int(reference["slider"])
        if reverse_checked:
            target_position = int(reference["position"]) - delta
        else:
            target_position = int(reference["position"]) + delta
        position_min, position_max = self._servo_position_limits(servo_id)
        return int(max(position_min, min(position_max, target_position)))

    def _current_drag_target_position(self, servo_id):
        control = self.drag_servo_controls[servo_id]
        return self._drag_target_position_for(servo_id, control["reverse_checkbox"].isChecked())

    def _handle_drag_reverse_toggled(self, servo_id, checked):
        if servo_id not in self.drag_servo_controls:
            return
        control = self.drag_servo_controls[servo_id]
        previous_target_position = self._drag_target_position_for(servo_id, not checked)
        self.drag_send_timer.stop()
        self.pending_drag_positions.pop(servo_id, None)
        self.drag_reverse_references[servo_id] = {
            "slider": int(control["slider"].value()),
            "position": int(previous_target_position),
        }

    def _handle_drag_servo_slider_changed(self, servo_id, position):
        if self._drag_sync_updating or self.worker_running():
            return
        control = self.drag_servo_controls[servo_id]
        control["value_label"].setText(str(position))
        if not control["select_checkbox"].isChecked():
            self.set_status("请先勾选 ID %d 再拖动。" % servo_id)
            return

        selected_servo_ids = [
            current_servo_id
            for current_servo_id, current_control in self.drag_servo_controls.items()
            if current_control["select_checkbox"].isChecked()
        ]
        if len(selected_servo_ids) > 1:
            self._drag_sync_updating = True
            try:
                for current_servo_id in selected_servo_ids:
                    if current_servo_id == servo_id:
                        continue
                    self._apply_drag_slider_value(current_servo_id, position, queue_command=False)
            finally:
                self._drag_sync_updating = False

        self.pending_drag_positions = {
            current_servo_id: self._current_drag_target_position(current_servo_id)
            for current_servo_id in selected_servo_ids
        }
        self.drag_send_timer.start(2)

    def _handle_drag_torque_toggled(self, checked):
        self.drag_torque_enable_checkbox.setText("扭矩使能: 开" if checked else "扭矩使能: 关")
        if self.worker_running():
            return
        try:
            servo_ids = list(self.drag_servo_controls) or [self._selected_main_servo_id()]
            port_name = self.port_combo.currentText().strip()
            if not port_name:
                raise ValueError("请先选择串口。")
            self._ensure_manual_drag_worker()
            self.manual_drag_worker.submit_torque(
                {
                    "port_name": port_name,
                    "baudrate": self.baudrate_spin.value(),
                    "servo_ids": servo_ids,
                    "torque_enabled": bool(checked),
                }
            )
        except Exception as exc:
            self._set_error_status("扭矩切换失败", exc)

    def _flush_drag_position_commands(self):
        if not self.pending_drag_positions or self.worker_running():
            return
        try:
            torque_enabled = 1 if self.drag_torque_enable_checkbox.isChecked() else 0
            queued_positions = dict(self.pending_drag_positions)
            if not torque_enabled:
                self.set_status("拖动给位已下发扭矩关闭 | ID: %s" % ", ".join(str(i) for i in queued_positions))
                return
            port_name = self.port_combo.currentText().strip()
            if not port_name:
                raise ValueError("请先选择串口。")
            segment_time_ms = 0
            goal_speed = self.drag_speed_spin.value()
            plotted_servo_id = None
            position_limits = {}
            goal_speeds = {}
            for servo_id, goal_position in queued_positions.items():
                self._validate_writepos_target(servo_id, goal_position, "ID %03d: " % servo_id)
                position_limits[servo_id] = self._servo_position_limits(servo_id)
                goal_speeds[servo_id] = self._limit_goal_speed(servo_id, goal_speed)
                if plotted_servo_id is None:
                    plotted_servo_id = servo_id
            selected_servo_id_text = self.id_combo.currentText().strip()
            if selected_servo_id_text:
                try:
                    selected_servo_id = int(selected_servo_id_text)
                except ValueError:
                    selected_servo_id = None
                if selected_servo_id in queued_positions:
                    plotted_servo_id = selected_servo_id
            self._ensure_manual_drag_worker()
            self.manual_drag_worker.submit_positions(
                {
                    "port_name": port_name,
                    "baudrate": self.baudrate_spin.value(),
                    "queued_positions": queued_positions,
                    "segment_time_ms": segment_time_ms,
                    "goal_speeds": goal_speeds,
                    "position_limits": position_limits,
                    "plotted_servo_id": plotted_servo_id,
                }
            )
        except Exception as exc:
            self._set_error_status("拖动给位失败", exc)
        finally:
            self.pending_drag_positions = {}

    def _update_curve_visibility(self):
        self.plot_canvas.set_visibility(
            self.show_ideal_checkbox.isChecked(),
            self.show_actual_checkbox.isChecked(),
            self.show_error_checkbox.isChecked(),
        )

    def clear_curve_panel(self):
        if self.worker_running():
            self.set_status("轨迹执行中，不能清除曲线。")
            return
        self.manual_curve_start_time = None
        self.manual_curve_series = {}
        self.manual_curve_last_refresh_time = 0.0
        self.ideal_times = []
        self.ideal_positions = []
        self.actual_times = []
        self.actual_positions = []
        self.plot_canvas.reset_plot()
        self.plot_canvas.set_ideal_curve([], [])
        self.plot_canvas.set_actual_curve([], [])
        self.plot_canvas.refresh_curves()
        self._update_curve_visibility()
        self.set_status("已清除轨迹曲线。")

    def _selected_multi_servo_ids(self):
        servo_ids = []
        for servo_id, checkbox in self.multi_id_checkboxes.items():
            if checkbox.isChecked():
                servo_ids.append(servo_id)
        return servo_ids

    def _update_multi_button_state(self):
        enabled = bool(self._selected_multi_servo_ids()) and not self.worker_running()
        self._save_current_multi_editor()
        self._refresh_multi_editor_selector()
        for _mode, combo, panel in self._multi_editor_contexts():
            combo.setEnabled(enabled)
            panel.setEnabled(enabled and combo.currentData() is not None)

    def _set_active_single_mode(self, mode):
        self.active_single_mode = mode
        is_single = mode == MODE_SINGLE
        self.open_single_point_window_button.setChecked(is_single)
        self.open_recip_window_button.setChecked(not is_single)
        self.single_mode_status_label.setText(
            "当前执行模式: %s" % ("单点控制" if is_single else "往复控制")
        )

    def _show_single_mode_window(self, mode):
        self.active_controller_kind = CONTROLLER_SINGLE
        if self.controller_tabs.currentIndex() != 0:
            self.controller_tabs.setCurrentIndex(0)
        self._set_active_single_mode(mode)
        window = self.single_point_window if mode == MODE_SINGLE else self.single_recip_window
        window.show()
        window.raise_()
        window.activateWindow()

    def _set_active_multi_mode(self, mode):
        self.active_multi_mode = mode
        is_single = mode == MODE_SINGLE
        self.open_multi_single_window_button.setChecked(is_single)
        self.open_multi_recip_window_button.setChecked(not is_single)
        self.multi_mode_status_label.setText(
            "当前执行模式: %s" % ("单点控制" if is_single else "往复控制")
        )

    def _show_multi_mode_window(self, mode):
        self.active_controller_kind = CONTROLLER_MULTI
        if self.controller_tabs.currentIndex() != 1:
            self.controller_tabs.setCurrentIndex(1)
        self._set_active_multi_mode(mode)
        self._refresh_multi_editor_selector()
        window = self.multi_single_window if mode == MODE_SINGLE else self.multi_recip_window
        window.show()
        window.raise_()
        window.activateWindow()

    def _validate_mode_config(self, mode_config, prefix):
        if mode_config["trajectory_duration"] <= 0:
            raise ValueError("%s轨迹时长必须大于 0。" % prefix)

    def _validate_writepos_target(self, servo_id, goal_position, prefix=""):
        position_min, position_max = self._servo_position_limits(servo_id)
        if goal_position < position_min or goal_position > position_max:
            raise ValueError(
                "%s目标位置 %d 超出电机配置限制范围 [%d, %d]。"
                % (prefix, goal_position, position_min, position_max)
            )

    def _collect_config(self):
        port_name = self.port_combo.currentText().strip()
        if not port_name:
            raise ValueError("请先选择串口。")
        if self.sample_interval_spin.value() <= 0:
            raise ValueError("采样周期必须大于 0。")
        self._save_current_multi_editor()

        controller_kind = self.active_controller_kind
        config = {
            "controller_kind": controller_kind,
            "port_name": port_name,
            "baudrate": self.baudrate_spin.value(),
            "sample_interval": self.sample_interval_spin.value(),
            "torque_enabled": 1 if self.drag_torque_enable_checkbox.isChecked() else 0,
            "servo_id": None,
            "servo_ids": [],
            "mode": MODE_SINGLE,
            "motor_modes": {},
        }

        if controller_kind == CONTROLLER_SINGLE:
            servo_id_text = self.id_combo.currentText().strip()
            if not servo_id_text:
                raise ValueError("请先输入或扫描舵机 ID。")
            try:
                servo_id = int(servo_id_text)
            except ValueError as exc:
                raise ValueError("舵机 ID 必须是整数。") from exc

            mode_config = self._build_trajectory_mode_config(controller_kind, self.active_single_mode)
            if mode_config["mode"] == MODE_SINGLE:
                mode_config["recip_end_position"] = mode_config["target_position"]
            self._validate_mode_config(mode_config, "")
            config["servo_id"] = servo_id
            config["servo_ids"] = [servo_id]
            config["mode"] = mode_config["mode"]
            config["motor_modes"][servo_id] = mode_config
            return config

        servo_ids = self._selected_multi_servo_ids()
        if not servo_ids:
            raise ValueError("多电机控制至少要勾选一个电机 ID。")

        for servo_id in servo_ids:
            mode_config = self._build_trajectory_mode_config(controller_kind, self.active_multi_mode, servo_id=servo_id)
            if mode_config["mode"] == MODE_SINGLE:
                mode_config["recip_end_position"] = mode_config["target_position"]
            self._validate_mode_config(mode_config, "ID %d " % servo_id)
            config["motor_modes"][servo_id] = mode_config

        config["servo_ids"] = servo_ids
        config["mode"] = next(iter(config["motor_modes"].values()))["mode"]
        return config

    def preview_trajectory(self):
        try:
            config = self._collect_config()
        except ValueError as exc:
            self.set_status(str(exc))
            return False, None

        self.manual_curve_start_time = None
        self.manual_curve_series = {}
        primary_servo_id = config["servo_ids"][0]
        mode_config = copy.deepcopy(config["motor_modes"][primary_servo_id])
        try:
            backend_name, samples, summary = ct.build_mode_samples(
                mode_config,
                config["sample_interval"],
            )
        except Exception as exc:
            self._set_error_status("轨迹生成失败", exc)
            return False, None

        self.ideal_times = [sample[0] for sample in samples]
        self.ideal_positions = [sample[1] for sample in samples]
        self.actual_times = []
        self.actual_positions = []
        self.plot_canvas.reset_plot()
        self.plot_canvas.set_ideal_curve(self.ideal_times, self.ideal_positions)
        self.plot_canvas.set_actual_curve([], [])
        self.plot_canvas.refresh_curves()
        self._update_curve_visibility()

        if config["controller_kind"] == CONTROLLER_MULTI:
            self.set_status("ID %d | %s | %s" % (primary_servo_id, backend_name, summary))
        else:
            self.set_status("%s | %s" % (backend_name, summary))
        return True, config

    def set_running(self, running):
        self._save_current_multi_editor()
        for button in self.run_action_buttons:
            button.setEnabled(not running)
        for button in self.stop_action_buttons:
            button.setEnabled(running)
        self.refresh_ports_button.setEnabled(not running)
        self.scan_ids_button.setEnabled(not running)
        self.controller_tabs.setEnabled(not running)
        self.open_multi_single_window_button.setEnabled(not running)
        self.open_multi_recip_window_button.setEnabled(not running)
        for _mode, combo, panel in self._multi_editor_contexts():
            combo.setEnabled(not running and bool(self._selected_multi_servo_ids()))
            panel.setEnabled(not running and combo.currentData() is not None and bool(self._selected_multi_servo_ids()))
        self.drag_control_group.setEnabled(not running)
        self.open_single_point_window_button.setEnabled(not running)
        self.open_recip_window_button.setEnabled(not running)
        if running:
            self.drag_send_timer.stop()
            self.pending_drag_positions = {}
            self.fan_logo.start()
        else:
            self.fan_logo.stop()

    def run_trajectory(self, controller_kind=None):
        if controller_kind is not None:
            self.active_controller_kind = controller_kind
            target_index = 1 if controller_kind == CONTROLLER_MULTI else 0
            if self.controller_tabs.currentIndex() != target_index:
                self.controller_tabs.setCurrentIndex(target_index)
        if self.worker is not None and self.worker.isRunning():
            self.set_status("当前已有轨迹任务在执行。")
            return

        self._disconnect_manual_drag_worker()
        ok, config = self.preview_trajectory()
        if not ok:
            return

        self.manual_curve_start_time = None
        self.manual_curve_series = {}
        self.actual_times = []
        self.actual_positions = []
        self.set_status("开始执行轨迹。")
        self.worker = TrajectoryWorker(config, self)
        self.worker.log_message.connect(self.set_status)
        self.worker.actual_sample.connect(self._record_actual_sample)
        self.worker.cycle_reset.connect(self._reset_reciprocating_cycle)
        self.worker.finished_ok.connect(self._handle_run_success)
        self.worker.finished_error.connect(self._handle_run_error)
        self.worker.finished.connect(lambda: self.set_running(False))
        self.set_running(True)
        self.worker.start()

    def stop_trajectory(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_stop()
            self.set_status("正在请求停止轨迹执行...")

    def _record_actual_sample(self, sample_time, position):
        self.actual_times.append(sample_time)
        self.actual_positions.append(position)
        self.plot_canvas.set_actual_curve(self.actual_times, self.actual_positions)
        self.plot_canvas.refresh_curves()
        self._update_curve_visibility()

    def _reset_reciprocating_cycle(self, start_position):
        self.actual_times = [0.0]
        self.actual_positions = [start_position]
        self.plot_canvas.set_actual_curve(self.actual_times, self.actual_positions)
        self.plot_canvas.refresh_curves()
        self._update_curve_visibility()

    def _handle_run_success(self, message):
        self.set_status(message)

    def _handle_run_error(self, message):
        self.set_status("执行失败：%s" % message)
