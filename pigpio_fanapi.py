"""Fan PWM control + tach feedback loop.

Same shape as dht22.py: config/UUID/network handled by a package, the loop
here only does the thing specific to this device.
"""

import signal
import sys
import time

import pigpio

import actuatorVPD
from actuatorVPD import configReader

config = configReader.readConfig()

PWM_PIN = config["pwmGpio"]
TACH_PIN = config["tachGpio"]

pi = pigpio.pi()
pi.set_mode(TACH_PIN, pigpio.INPUT)
pi.set_pull_up_down(TACH_PIN, pigpio.PUD_UP)

pulse_count = 0
def tach_callback(gpio, level, tick):
    global pulse_count
    pulse_count += 1
pi.callback(TACH_PIN, pigpio.FALLING_EDGE, tach_callback)

def set_speed(duty_percent):
    duty_percent = max(0, min(100, duty_percent))
    pi.hardware_PWM(PWM_PIN, 25000, int(duty_percent * 10000))
    return duty_percent

def get_rpm_and_pulses():
    global pulse_count
    pulse_count = 0
    time.sleep(1)
    pulses = pulse_count
    return pulses, (pulses / 2) * 60

stop_event_hit = False
def handle_stop(signum, frame):
    global stop_event_hit
    print("Stopping...")
    stop_event_hit = True

signal.signal(signal.SIGTERM, handle_stop)
signal.signal(signal.SIGINT, handle_stop)

client = actuatorVPD.ActuatorClient(config)
client.start()
print(f"Fan control started (PWM gpio {PWM_PIN}, tach gpio {TACH_PIN})")

try:
    while not stop_event_hit:
        cmd = client.current_command()
        target = cmd["pwmDutyPercent"] if cmd["action"] != "OFF" else 0
        duty = set_speed(target)

        pulses, rpm = get_rpm_and_pulses()
        client.report_status(duty, pulses, rpm)

        print(f"duty={duty}% pulses={pulses} rpm={rpm}")

finally:
    pi.hardware_PWM(PWM_PIN, 25000, 0)
    pi.stop()
    client.stop()
    sys.exit(0)
