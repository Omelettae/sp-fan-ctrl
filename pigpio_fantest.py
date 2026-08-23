import pigpio
import time

PWM_PIN = 18
TACH_PIN = 24

pi = pigpio.pi()

# --- Tach setup ---
pi.set_mode(TACH_PIN, pigpio.INPUT)
pi.set_pull_up_down(TACH_PIN, pigpio.PUD_UP)

pulse_count = 0

def tach_callback(gpio, level, tick):
    global pulse_count
    pulse_count += 1

pi.callback(TACH_PIN, pigpio.FALLING_EDGE, tach_callback)

# --- PWM control ---
def set_speed(duty_percent):
    """duty_percent: 0-100"""
    duty_percent = max(0, min(100, duty_percent))
    pi.hardware_PWM(PWM_PIN, 25000, int(duty_percent * 10000))

def get_rpm():
    global pulse_count
    pulse_count = 0
    time.sleep(1)  # measurement window
    return (pulse_count / 2) * 60  # 2 pulses per revolution

# --- Run ---
try:
    set_speed(50)  # start at 50% duty
    while True:
        rpm = get_rpm()
        print(f"RPM: {rpm}")
except KeyboardInterrupt:
    print("Stopping...")
finally:
    pi.hardware_PWM(PWM_PIN, 25000, 0)  # stop PWM
    pi.stop()
