#!/usr/bin/env python3

import glob
import logging
import sys
import time
from pathlib import Path


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scservo_sdk import *  # noqa: F401,F403

from cubic_trajectory_planner import build_segment_samples


if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).resolve().parent
else:
    APP_ROOT = PROJECT_ROOT

MODE_SINGLE = "single"
MODE_RECIPROCATING = "reciprocating"
CONTROLLER_SINGLE = "single_motor"
CONTROLLER_MULTI = "multi_motor"

SCSCL_MIN_ANGLE_LIMIT = 9
SCSCL_MAX_ANGLE_LIMIT = 11
SCSCL_TORQUE_ENABLE = 40
SCSCL_PRESENT_POSITION = 56
SCSCL_PRESENT_SPEED = 58
SCSCL_PRESENT_LOAD = 60
SCSCL_PRESENT_TEMPERATURE = 63
SCSCL_PRESENT_STATUS = 65

UI_FALLBACK_POSITION_MIN = 0
UI_FALLBACK_POSITION_MAX = 1023
SCSCL_DIRECTION_BIT = 10
SCSCL_DIRECTION_MAGNITUDE_MASK = (1 << SCSCL_DIRECTION_BIT) - 1

PARAMETER_DEFINITIONS = [
    ("servo_id", "舵机ID", 5, 1, 0, 253, "0-253", "debug"),
    ("min_angle_limit", "最小角度限制", 9, 2, 0, 1023, "0-1023 步", "protection"),
    ("max_angle_limit", "最大角度限制", 11, 2, 1, 1023, "1-1023 步", "protection"),
    ("max_temperature", "最高温度上限", 13, 1, 0, 100, "0-100 °C", "protection"),
    ("max_torque", "最大扭矩", 16, 2, 0, 1000, "0-1000, 0.1%", "protection"),
    ("position_p", "位置环P比例系数", 21, 1, 0, 254, "0-254", "debug"),
    ("position_d", "位置环D微分系数", 22, 1, 0, 254, "0-254", "debug"),
    ("min_start_force", "最小启动力", 24, 2, 0, 1000, "0-1000, 0.1%", "protection"),
    ("positive_deadband", "正向不灵敏区", 26, 1, 0, 16, "0-16 步", "debug"),
    ("negative_deadband", "负向不灵敏区", 27, 1, 0, 16, "0-16 步", "debug"),
    ("protection_time", "保护时间", 38, 1, 0, 254, "0-254, 10ms", "protection"),
    ("overload_torque", "过载扭矩", 39, 1, 0, 254, "0-254, 1%", "protection"),
    ("max_speed_limit", "最大速度限制", None, 2, 0, 1000, "0-1000 步/s", "protection"),
]

READ_ONLY_PARAMETER_KEYS = set()
UI_ONLY_PARAMETER_KEYS = {
    "max_speed_limit",
}

INFO_DEFINITIONS = [
    ("present_position", "当前位置"),
    ("present_speed", "当前速度"),
    ("present_load", "当前负载"),
    ("present_temperature", "当前温度"),
    ("present_status", "舵机状态"),
]

PARAMETER_EXPORT_DIR = Path("config")
PARAMETER_FILE_SUFFIX = ".json"
DEFAULT_PARAMETER_FILE_NAME = "scscl_default.json"
TRAJECTORY_PARAMETER_DIR = Path("parameter")
TRAJECTORY_PARAMETER_FILE_SUFFIX = ".json"
TRAJECTORY_PARAMETER_SCHEMA_VERSION = "1.0"
LOG_DIR = Path("log")

PARENT_TAB_HORIZONTAL_PADDING = 16
PARENT_CHILD_VERTICAL_SPACING = 12
CHILD_TAB_TO_MODE_ROW_SPACING = 10
MANUAL_CURVE_TIME_WINDOW_SECONDS = 10.0
MANUAL_CURVE_HISTORY_PADDING_SECONDS = 1.0


def build_runtime_logger():
    log_dir = APP_ROOT / LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / ("runtime_%s.log" % time.strftime("%Y%m%d_%H%M%S"))
    logger_name = "scscl_ui_runtime_%s" % int(time.time() * 1000)
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(str(log_path), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger, handler, log_path


def list_serial_ports():
    patterns = [
        "/dev/ttyUSB*",
        "/dev/ttyACM*",
        "/dev/ttyS*",
        "/dev/ttyAMA*",
        "/dev/tty.usbserial-*",
        "/dev/cu.usbserial-*",
    ]
    ports = []
    for pattern in patterns:
        ports.extend(glob.glob(pattern))
    return sorted(set(ports))


def check_packet_result(packet_handler, comm_result, error):
    if comm_result != COMM_SUCCESS:
        raise RuntimeError(packet_handler.getTxRxResult(comm_result))
    if error != 0:
        raise RuntimeError(packet_handler.getRxPacketError(error))


def scan_servo_ids(packet_handler, id_start=0, id_end=5):
    detected_servos = []
    for scs_id in range(id_start, id_end + 1):
        model_number, comm_result, error = packet_handler.ping(scs_id)
        if comm_result == COMM_SUCCESS and error == 0:
            detected_servos.append((scs_id, model_number))
    return detected_servos


def decode_scscl_direction_value(raw_value):
    magnitude = int(raw_value) & SCSCL_DIRECTION_MAGNITUDE_MASK
    if int(raw_value) & (1 << SCSCL_DIRECTION_BIT):
        return -magnitude
    return magnitude


def ensure_scscl_position_mode(packet_handler, servo_id, min_angle_limit, max_angle_limit):
    min_angle, comm_result, error = packet_handler.read2ByteTxRx(servo_id, SCSCL_MIN_ANGLE_LIMIT)
    check_packet_result(packet_handler, comm_result, error)
    max_angle, comm_result, error = packet_handler.read2ByteTxRx(servo_id, SCSCL_MAX_ANGLE_LIMIT)
    check_packet_result(packet_handler, comm_result, error)
    if min_angle == min_angle_limit and max_angle == max_angle_limit:
        return False

    comm_result, error = packet_handler.unLockEprom(servo_id)
    check_packet_result(packet_handler, comm_result, error)
    comm_result, error = packet_handler.write2ByteTxRx(servo_id, SCSCL_MIN_ANGLE_LIMIT, min_angle_limit)
    check_packet_result(packet_handler, comm_result, error)
    comm_result, error = packet_handler.write2ByteTxRx(servo_id, SCSCL_MAX_ANGLE_LIMIT, max_angle_limit)
    check_packet_result(packet_handler, comm_result, error)
    comm_result, error = packet_handler.LockEprom(servo_id)
    check_packet_result(packet_handler, comm_result, error)
    return True


def read_start_position(packet_handler, servo_id):
    present_position, comm_result, error = packet_handler.ReadPos(servo_id)
    check_packet_result(packet_handler, comm_result, error)
    return present_position


def build_mode_samples(mode_config, sample_interval, start_override=None):
    if mode_config["mode"] == MODE_SINGLE:
        start_position = start_override if start_override is not None else mode_config["start_position"]
        backend_name, samples = build_segment_samples(
            start_position,
            mode_config["target_position"],
            mode_config["start_velocity"],
            mode_config["end_velocity"],
            mode_config["trajectory_duration"],
            sample_interval,
        )
        summary = "单点控制 | start=%d | target=%d | duration=%.3fs" % (
            int(round(start_position)),
            mode_config["target_position"],
            mode_config["trajectory_duration"],
        )
        return backend_name, samples, summary

    if mode_config["mode"] != MODE_RECIPROCATING:
        raise ValueError("不支持的轨迹模式：%s" % mode_config["mode"])

    start_position = start_override if start_override is not None else mode_config["start_position"]
    end_position = mode_config["recip_end_position"]
    backend_name, forward_samples = build_segment_samples(
        start_position,
        end_position,
        mode_config["start_velocity"],
        mode_config["end_velocity"],
        mode_config["trajectory_duration"],
        sample_interval,
    )
    _backend_name, backward_samples = build_segment_samples(
        end_position,
        start_position,
        mode_config["start_velocity"],
        mode_config["end_velocity"],
        mode_config["trajectory_duration"],
        sample_interval,
        time_offset=mode_config["trajectory_duration"],
        include_first_point=False,
    )
    summary = "往复控制 | start=%d | end=%d | duration=%.3fs" % (
        start_position,
        end_position,
        mode_config["trajectory_duration"],
    )
    return backend_name, forward_samples + backward_samples, summary
