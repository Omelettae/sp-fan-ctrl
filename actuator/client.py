"""All backend traffic for an actuator, on a background thread.

Mirrors sensorVPD.client.BackendClient's shape (discovery, registration,
heartbeat all on one maintenance cycle) but the direction of one thing is
reversed: a sensor PUSHES readings it already has; an actuator must POLL for
a command, because the backend has no way to reach into the Pi. Telemetry
(pulses/rpm/duty) is pushed back the same maintenance cycle, right after the
command is applied by the caller.
"""

import threading
import time

import requests

from sensorVPD import network


DEFAULT_NETWORK_LIST = "networkList.txt"
DEFAULT_PORT = 5000
DEFAULT_MAINTENANCE_SECONDS = 1  # fan control wants ~1s resolution, not 60s


class ActuatorClient:
    def __init__(self, config, network_list=None, port=DEFAULT_PORT,
                 maintenance_seconds=DEFAULT_MAINTENANCE_SECONDS):
        self.config = config
        self.network_list = str(network_list or (__import__("sensorVPD").cache.BASE_DIR / DEFAULT_NETWORK_LIST))
        self.port = port
        self.maintenance_seconds = maintenance_seconds

        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._session = requests.Session()

        self._base_url = None
        self._actuator_id = None

        # What the caller (fan_control.py) should currently be doing.
        self._pending_command = {"action": "OFF", "pwmDutyPercent": 0}
        # What the caller last measured - set via report_status(), read here.
        self._last_telemetry = None

    @property
    def base_url(self):
        with self._lock:
            return self._base_url

    @property
    def actuator_id(self):
        with self._lock:
            return self._actuator_id

    def current_command(self):
        """Latest known command. Called from the control loop every cycle."""
        with self._lock:
            return dict(self._pending_command)

    def report_status(self, pwm_duty_percent, pulse_count, rpm):
        """Called by the control loop right after it applies a duty and
        measures the tach - queued for the next maintenance tick to push."""
        with self._lock:
            self._last_telemetry = {
                "pwmDutyPercent": pwm_duty_percent,
                "pulseCount": pulse_count,
                "rpm": rpm,
            }

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="actuator-backend", daemon=True)
        self._thread.start()

    def stop(self, timeout=5):
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.run_maintenance()
            except Exception as e:
                print("[actuator] maintenance error:", e)
            self.stop_event.wait(self.maintenance_seconds)

    def run_maintenance(self):
        self.discover_backend()
        self.register_if_needed()
        self.poll_command()
        self.push_status()

    # -- discovery / registration -------------------------------------------

    def discover_backend(self):
        if self._base_url is not None and self._probe_current_backend():
            return
        try:
            new_url = network.networkSearch(self.network_list, self.port, "")
        except Exception as e:
            print("[actuator] backend discovery error:", e)
            new_url = None

        with self._lock:
            changed = new_url != self._base_url
            self._base_url = new_url
        if changed:
            print("[actuator] Backend discovered:" if new_url else "[actuator] Backend unavailable", new_url or "")

    def _probe_current_backend(self):
        try:
            r = self._session.get(f"{self._base_url}/api/time", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def register_if_needed(self):
        base_url = self.base_url
        if base_url is None or self.actuator_id is not None:
            return
        try:
            r = self._session.post(
                f"{base_url}/api/registerActuator",
                json={
                    "deviceUUID": self.config["deviceUUID"],
                    "actuatorType": self.config["actuatorType"],
                    "locationName": self.config["locationName"],
                    "actuatorName": self.config.get("actuatorName"),
                    "description": self.config.get("description"),
                },
                timeout=5,
            )
            r.raise_for_status()
            actuator_id = r.json().get("actuatorID")
            if actuator_id:
                with self._lock:
                    self._actuator_id = actuator_id
                print(f"[actuator] Registered actuator ID: {actuator_id}")
        except Exception as e:
            print("[actuator] Registration failed:", e)

    # -- command polling ------------------------------------------------------

    def poll_command(self):
        base_url = self.base_url
        actuator_id = self.actuator_id
        if base_url is None or actuator_id is None:
            return
        try:
            r = self._session.get(
                f"{base_url}/api/actuatorCommand",
                params={"actuatorID": actuator_id},
                timeout=5,
            )
            if r.status_code != 200:
                return
            body = r.json()
            with self._lock:
                self._pending_command = {
                    "action": body.get("action", "OFF"),
                    "pwmDutyPercent": body.get("pwmDutyPercent") or 0,
                }
        except Exception as e:
            print("[actuator] Command poll failed:", e)

    # -- telemetry --------------------------------------------------------

    def push_status(self):
        base_url = self.base_url
        actuator_id = self.actuator_id
        with self._lock:
            telemetry = self._last_telemetry
        if base_url is None or actuator_id is None or telemetry is None:
            return
        try:
            self._session.post(
                f"{base_url}/api/actuatorStatus",
                json={"actuatorID": actuator_id, **telemetry},
                timeout=5,
            )
        except Exception as e:
            print("[actuator] Status push failed:", e)
