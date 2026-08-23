import json
import os
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Separate UUID file from sensors' device_uuid.txt - an Actuator is its own
# identity in the schema (Actuator.deviceUUID), not tied to Device/Sensor.
UUID_FILE = Path(os.environ.get("ACTUATOR_UUID_FILE", BASE_DIR / "actuator_uuid.txt"))

DEFAULT_CONFIG_FILE = os.environ.get("ACTUATOR_CONFIG", "actuator_config.txt")


def get_actuator_uuid():
    if not UUID_FILE.exists():
        UUID_FILE.write_text(str(uuid.uuid4()))
    return UUID_FILE.read_text().strip()


def resolve_config_path(filename=None):
    path = Path(filename or DEFAULT_CONFIG_FILE)
    if not path.is_absolute():
        candidate = BASE_DIR / path
        path = candidate if candidate.exists() else path
    return path


def _parse_text_config(text):
    data = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        if key:
            data[key] = value.strip()
    return data


def readConfig(filename=None):
    path = resolve_config_path(filename)

    with open(path, "r") as file:
        raw = file.read()

    try:
        config_data = json.loads(raw)
    except ValueError:
        config_data = _parse_text_config(raw)

    if not isinstance(config_data, dict):
        raise ValueError(f"{path}: expected a set of settings, got {type(config_data).__name__}")

    actuator_type = config_data.get("Type") or config_data.get("actuatorType")
    location = config_data.get("Location") or config_data.get("locationName")
    name = config_data.get("Name") or config_data.get("actuatorName")
    description = config_data.get("description") or config_data.get("Description")
    pwm_gpio = config_data.get("PWM_GPIO")
    tach_gpio = config_data.get("TACH_GPIO")

    if not actuator_type or not location:
        raise ValueError(
            f"{path}: 'Type' and 'Location' are required "
            f"(got Type={actuator_type!r}, Location={location!r})"
        )

    return {
        "deviceUUID": get_actuator_uuid(),
        "actuatorType": actuator_type,
        "locationName": location,
        "actuatorName": name,
        "description": description,
        "pwmGpio": int(pwm_gpio) if pwm_gpio is not None else None,
        "tachGpio": int(tach_gpio) if tach_gpio is not None else None,
    }
