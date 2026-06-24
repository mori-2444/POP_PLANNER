#!/usr/bin/env python
#
# *********     Ping Example      *********
#
#
# Available SCServo model on this example : All models using Protocol SCS
# This example is tested with a SCServo(SCS), and an URT
#

import sys
import glob

sys.path.append("..")
from scservo_sdk import *                   # Uses SCServo SDK library


BAUDRATE = 500000
SCAN_ID_START = 0
SCAN_ID_END = 253


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


def choose_serial_port():
    ports = list_serial_ports()

    if not ports:
        print("No serial ports were detected automatically.")
        return input("Please enter the serial port path manually: ").strip()

    if len(ports) == 1:
        print("Detected serial port: %s" % ports[0])
        return ports[0]

    print("Available serial ports:")
    for index, port in enumerate(ports, start=1):
        print("%d. %s" % (index, port))

    while True:
        selection = input("Select a port by number: ").strip()
        if not selection.isdigit():
            print("Please enter a valid number.")
            continue

        selection_index = int(selection) - 1
        if 0 <= selection_index < len(ports):
            return ports[selection_index]

        print("Selection out of range. Try again.")


def scan_servo_ids(packetHandler):
    detected_servos = []

    print("Scanning servo IDs from %d to %d..." % (SCAN_ID_START, SCAN_ID_END))
    for scs_id in range(SCAN_ID_START, SCAN_ID_END + 1):
        scs_model_number, scs_comm_result, scs_error = packetHandler.ping(scs_id)
        if scs_comm_result == COMM_SUCCESS and scs_error == 0:
            detected_servos.append((scs_id, scs_model_number))
            print("Detected [ID:%03d] model number : %d" % (scs_id, scs_model_number))

    return detected_servos


def choose_servo_id(detected_servos):
    if not detected_servos:
        return None

    if len(detected_servos) == 1:
        scs_id, _ = detected_servos[0]
        print("Automatically selected servo ID: %03d" % scs_id)
        return scs_id

    print("Available servo IDs:")
    for index, (scs_id, scs_model_number) in enumerate(detected_servos, start=1):
        print("%d. ID:%03d model number:%d" % (index, scs_id, scs_model_number))

    while True:
        selection = input("Select a servo ID by number: ").strip()
        if not selection.isdigit():
            print("Please enter a valid number.")
            continue

        selection_index = int(selection) - 1
        if 0 <= selection_index < len(detected_servos):
            return detected_servos[selection_index][0]

        print("Selection out of range. Try again.")


port_name = choose_serial_port()
portHandler = PortHandler(port_name)  # ex) Windows: "COM1" Linux: "/dev/ttyUSB0"

packetHandler = scscl(portHandler)

if portHandler.openPort():
    print("Succeeded to open the port")
else:
    print("Failed to open the port")
    quit()

if portHandler.setBaudRate(BAUDRATE):
    print("Succeeded to change the baudrate")
else:
    print("Failed to change the baudrate")
    quit()

detected_servos = scan_servo_ids(packetHandler)
selected_id = choose_servo_id(detected_servos)

if selected_id is None:
    print("No servo IDs responded on %s at baudrate %d." % (port_name, BAUDRATE))
    portHandler.closePort()
    quit()

scs_model_number, scs_comm_result, scs_error = packetHandler.ping(selected_id)
if scs_comm_result != COMM_SUCCESS:
    print("%s" % packetHandler.getTxRxResult(scs_comm_result))
else:
    print("[ID:%03d] ping Succeeded. SCServo model number : %d" % (selected_id, scs_model_number))

if scs_error != 0:
    print("%s" % packetHandler.getRxPacketError(scs_error))

portHandler.closePort()
