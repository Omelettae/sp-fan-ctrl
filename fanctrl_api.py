import RPi.GPIO as GPIO
import threading
import time
import json
import requests

with open("config.json") as f:
    config = json.load(f)

SERVER = f"{config['serverURL']}"
SENSOR_ID = 1

def server_thread():
    global target_rpm

    while True:

        try:
            r = requests.get(
                f"{SERVER}/api/getFanRPM/{SENSOR_ID}",
                timeout=5
            )

            if r.status_code == 200:
                new_target = r.json()["targetRPM"]
            
                if new_target != target_rpm:
                    print(f"New target RPM: {new_target}")
            
                target_rpm = new_target

        except Exception as e:
            print(e)

        time.sleep(2)
      

PWM_PIN = 18
TACH_PIN = 24

PWM_FREQ = 1000

pulse_count = 0
target_rpm = 0
duty = 0.0

threading.Thread(
    target=server_thread,
    daemon=True
).start()


# ---------- Tach Callback ----------

def tach_callback(channel):
    global pulse_count
    pulse_count += 1


# ---------- Setup ----------

GPIO.setmode(GPIO.BCM)

GPIO.setup(PWM_PIN, GPIO.OUT)

GPIO.setup(
    TACH_PIN,
    GPIO.IN,
    pull_up_down=GPIO.PUD_UP
)

GPIO.add_event_detect(
    TACH_PIN,
    GPIO.FALLING,
    callback=tach_callback,
    bouncetime=2
)

pwm = GPIO.PWM(PWM_PIN, PWM_FREQ)

pwm.start(duty)



# ---------- Controller ----------

Kp = 0.02

try:

    while True:

        pulse_count = 0

        time.sleep(1)

        pulses = pulse_count

        # Same formula as your working test
        rpm = pulses * 30

        error = target_rpm - rpm

        duty += error * Kp

        if target_rpm == 0:
            duty = max(0.0, min(100.0, duty))
        else:
            duty = max(10.0, min(100.0, duty))

        pwm.ChangeDutyCycle(duty)

        print(
            f"Pulses={pulses:3d}  "
            f"Target={target_rpm:4d}  "
            f"RPM={rpm:4d}  "
            f"PWM={duty:5.1f}%"
        )
        try:
            requests.post(
                f"{SERVER}/api/FanLog",
                json={
                    "sensorID": SENSOR_ID,
                    "targetRPM": target_rpm,
                    "actualRPM": rpm,
                    "dutyCycle": duty
                },
                timeout=2
            )
        except Exception as e:
            print("Failed to send fan status:", e)

except KeyboardInterrupt:
    print("\nStopping fan...")

finally:
    try:
        pwm.ChangeDutyCycle(0)
        time.sleep(1)  # give fan time to react

    except:
        pass



    print("Fan stopped.")
