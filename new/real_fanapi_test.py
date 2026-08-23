#!/usr/bin/env python3
"""
fantest.py - fan control + tach reading, wired up to the backend API.

On startup this finds-or-creates its Actuator row on the backend (via
/api/registerActuator, keyed on a UUID that persists across reboots - see
generate_uuid.py), then:
  - polls /api/actuatorCommand every commandPollSeconds for the latest
    ON/OFF/SET_SPEED command and applies it to the PWM pin
  - reports telemetry (duty applied, pulse count, measured RPM) back via
    /api/actuatorStatus every statusReportSeconds

Requires: pip install pigpio requests
Requires the pigpio daemon running: sudo pigpiod

Config: fan_config.json in the same directory, or pass a path as argv[1].
    python3 fantest.py
    python3 fantest.py /path/to/other_config.json
"""

import json
import sys
import threading
import time

import pigpio
import requests

from generate_uuid import get_or_create_uuid

# ===========================================================================
# CONFIG
# ===========================================================================


def load_config(path):
    with open(path) as f:
        return json.load(f)


CONFIG_PATH = sys.argv[1] if len(sys.argv) > 1 else "fan_config.json"
config = load_config(CONFIG_PATH)

BASE_URL = config["server"]["baseUrl"].rstrip("/")
REQUEST_TIMEOUT = config["server"].get("requestTimeoutSeconds", 5)

ACTUATOR_TYPE = config["actuator"]["actuatorType"]
LOCATION_NAME = config["actuator"]["locationName"]
ACTUATOR_NAME = config["actuator"].get("actuatorName")
DESCRIPTION = config["actuator"].get("description")

UUID_FILE = config["uuid"]["filePath"]

PWM_PIN = config["hardware"]["pwmPin"]
TACH_PIN = config["hardware"]["tachPin"]
PWM_FREQ_HZ = config["hardware"].get("pwmFrequencyHz", 25000)
PULSES_PER_REV = config["hardware"].get("pulsesPerRevolution", 2)

COMMAND_POLL_SECONDS = config["polling"].get("commandPollSeconds", 5)
STATUS_REPORT_SECONDS = config["polling"].get("statusReportSeconds", 15)
RPM_WINDOW_SECONDS = config["polling"].get("rpmMeasurementWindowSeconds", 1)


# ===========================================================================
# PIGPIO / HARDWARE
# ===========================================================================

pi = pigpio.pi()
if not pi.connected:
    print("Could not connect to pigpiod - is it running? (sudo pigpiod)")
    sys.exit(1)

pi.set_mode(TACH_PIN, pigpio.INPUT)
pi.set_pull_up_down(TACH_PIN, pigpio.PUD_UP)

pulse_count = 0
pulse_lock = threading.Lock()


def _tach_callback(gpio, level, tick):
    global pulse_count
    with pulse_lock:
        pulse_count += 1


pi.callback(TACH_PIN, pigpio.FALLING_EDGE, _tach_callback)


def set_speed(duty_percent):
    """duty_percent: 0-100. Also how OFF is applied (duty forced to 0)."""
    duty_percent = max(0, min(100, duty_percent))
    pi.hardware_PWM(PWM_PIN, PWM_FREQ_HZ, int(duty_percent * 10000))
    return duty_percent


def measure_rpm(window_seconds):
    """Zeroes the tach counter, waits, and converts pulses to RPM."""
    global pulse_count
    with pulse_lock:
        pulse_count = 0
    time.sleep(window_seconds)
    with pulse_lock:
        count = pulse_count
    return (count / PULSES_PER_REV) * (60 / window_seconds)


# ===========================================================================
# BACKEND API
# ===========================================================================


def register_actuator(device_uuid, retries=None, retry_delay=10):
    """Find-or-create this actuator on the backend. Retries (default:
    forever) since the server may not be reachable yet when the Pi boots."""
    attempt = 0
    while retries is None or attempt < retries:
        attempt += 1
        try:
            r = requests.post(
                f"{BASE_URL}/api/registerActuator",
                json={
                    "deviceUUID": device_uuid,
                    "actuatorType": ACTUATOR_TYPE,
                    "locationName": LOCATION_NAME,
                    "actuatorName": ACTUATOR_NAME,
                    "description": DESCRIPTION,
                },
                timeout=REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("success"):
                print(f"Registered actuator: actuatorID={data['actuatorID']}")
                return data["actuatorID"]
            print(f"registerActuator returned failure: {data}")
        except requests.exceptions.RequestException as e:
            print(f"registerActuator failed (attempt {attempt}): {e}")
        time.sleep(retry_delay)
    raise RuntimeError("Could not register actuator with backend")


def fetch_command(actuator_id):
    """Returns (action, pwmDutyPercent) or None on request failure."""
    try:
        r = requests.get(
            f"{BASE_URL}/api/actuatorCommand",
            params={"actuatorID": actuator_id},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("action"), data.get("pwmDutyPercent")
    except requests.exceptions.RequestException as e:
        print(f"fetch_command failed: {e}")
        return None


def report_status(actuator_id, duty_percent, pulse_count_snapshot, rpm):
    try:
        r = requests.post(
            f"{BASE_URL}/api/actuatorStatus",
            json={
                "actuatorID": actuator_id,
                "pwmDutyPercent": duty_percent,
                "pulseCount": pulse_count_snapshot,
                "rpm": round(rpm),
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"report_status failed: {e}")


# ===========================================================================
# COMMAND POLLING (background thread)
# ===========================================================================

target_duty = 0
target_lock = threading.Lock()


def command_poll_loop(actuator_id):
    global target_duty
    while True:
        result = fetch_command(actuator_id)
        if result is not None:
            action, duty = result
            with target_lock:
                if action == "OFF":
                    target_duty = 0
                elif action in ("ON", "SET_SPEED"):
                    # ON with no explicit duty keeps whatever was last set;
                    # falls back to a safe default if nothing has run yet.
                    target_duty = duty if duty is not None else (target_duty or 50)
        time.sleep(COMMAND_POLL_SECONDS)


# ===========================================================================
# MAIN
# ===========================================================================


def main():
    device_uuid = get_or_create_uuid(UUID_FILE)
    print(f"Device UUID: {device_uuid}")

    actuator_id = register_actuator(device_uuid)

    poller = threading.Thread(target=command_poll_loop, args=(actuator_id,), daemon=True)
    poller.start()

    applied_duty = -1  # force the first set_speed call
    last_status_report = 0.0

    try:
        while True:
            with target_lock:
                desired = target_duty

            if desired != applied_duty:
                applied_duty = set_speed(desired)
                print(f"Duty set to {applied_duty}%")

            rpm = measure_rpm(RPM_WINDOW_SECONDS)
            print(f"RPM: {rpm:.0f}  (duty={applied_duty}%)")

            now = time.time()
            if now - last_status_report >= STATUS_REPORT_SECONDS:
                with pulse_lock:
                    snapshot = pulse_count
                report_status(actuator_id, applied_duty, snapshot, rpm)
                last_status_report = now

    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        pi.hardware_PWM(PWM_PIN, PWM_FREQ_HZ, 0)  # stop PWM
        pi.stop()


if __name__ == "__main__":
    main()
