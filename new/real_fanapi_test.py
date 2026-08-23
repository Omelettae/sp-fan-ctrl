#!/usr/bin/env python3
"""
fantest.py - fan control + tach reading, wired up to the backend API.

On startup this finds-or-creates its Actuator row on the backend (via
/api/registerActuator, keyed on a UUID that persists across reboots - see
generate_uuid.py), then:
  - polls /api/actuatorCommand every commandPollSeconds for the latest
    command and applies it to the PWM pin:
      OFF        -> duty 0
      ON         -> a fixed full-speed duty (hardware.onDutyPercent)
      SET_SPEED  -> the explicit pwmDutyPercent from the command (the only
                    case where an arbitrary duty is applied - set by the
                    Windows CLI or backend automation)
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
# The duty applied for a plain ON command - fixed, not derived from whatever
# duty a previous SET_SPEED happened to leave behind.
ON_DUTY_PERCENT = config["hardware"].get("onDutyPercent", 100)

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
    """duty_percent: 0-100 (numeric or numeric string). Also how OFF is
    applied (duty forced to 0)."""
    duty_percent = float(duty_percent)
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
    """Returns (action, pwmDutyPercent) or None on request failure.
    pwmDutyPercent comes back as a string from the backend - mysql2 returns
    DECIMAL columns as strings by default - so it's cast to float here,
    once, rather than trusting every caller to remember."""
    try:
        r = requests.get(
            f"{BASE_URL}/api/actuatorCommand",
            params={"actuatorID": actuator_id},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        duty = data.get("pwmDutyPercent")
        return data.get("action"), (float(duty) if duty is not None else None)
    except requests.exceptions.RequestException as e:
        print(f"fetch_command failed: {e}")
        return None
    except (TypeError, ValueError) as e:
        print(f"fetch_command got an unparsable pwmDutyPercent: {e}")
        return data.get("action"), None


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

target_action = "OFF"
target_duty = 0  # only meaningful when target_action == "SET_SPEED"
target_lock = threading.Lock()


def command_poll_loop(actuator_id):
    global target_action, target_duty
    while True:
        result = fetch_command(actuator_id)
        if result is not None:
            action, duty = result
            with target_lock:
                target_action = action
                if action == "SET_SPEED":
                    # Only a SET_SPEED command - issued from the Windows CLI
                    # or backend automation - carries an explicit duty.
                    target_duty = duty if duty is not None else target_duty
        time.sleep(COMMAND_POLL_SECONDS)


def apply_action(action, duty):
    """set_speed() is only called with an arbitrary duty for SET_SPEED.
    ON and OFF are fixed points (ON_DUTY_PERCENT / 0) so they never depend
    on whatever duty a previous SET_SPEED left behind."""
    if action == "OFF":
        return set_speed(0)
    if action == "ON":
        return set_speed(ON_DUTY_PERCENT)
    if action == "SET_SPEED":
        return set_speed(duty if duty is not None else ON_DUTY_PERCENT)
    # Unrecognized/absent command - fail safe to OFF.
    return set_speed(0)


# ===========================================================================
# MAIN
# ===========================================================================


def main():
    device_uuid = get_or_create_uuid(UUID_FILE)
    print(f"Device UUID: {device_uuid}")

    actuator_id = register_actuator(device_uuid)

    poller = threading.Thread(target=command_poll_loop, args=(actuator_id,), daemon=True)
    poller.start()

    applied_action = None  # force the first apply_action call
    applied_duty = -1
    last_status_report = 0.0

    try:
        while True:
            with target_lock:
                desired_action = target_action
                desired_duty = target_duty

            changed = (
                desired_action != applied_action
                or (desired_action == "SET_SPEED" and desired_duty != applied_duty)
            )
            if changed:
                applied_duty = apply_action(desired_action, desired_duty)
                applied_action = desired_action
                print(f"Action={applied_action} -> duty {applied_duty}%")

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
