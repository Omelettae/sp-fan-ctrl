import RPi.GPIO as GPIO
import threading
import time

PWM_PIN = 18
TACH_PIN = 24

PWM_FREQ = 1000

pulse_count = 0
target_rpm = 0
duty = 0.0


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


# Ask for initial target before starting
target_rpm = int(input("Initial target RPM: "))

pwm.start(duty)


# ---------- Background Input Thread ----------

def input_thread():
    global target_rpm

    while True:
        try:
            value = input("\nNew target RPM: ")

            rpm = int(value)

            if rpm == 0:
                rpm = max(0, min(3400, rpm))
            else:
                rpm = max(500, min(3400, rpm))

            target_rpm = rpm

            print(f"\nTarget RPM changed to {target_rpm}")

        except ValueError:
            print("Please enter a number.")


threading.Thread(
    target=input_thread,
    daemon=True
).start()


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

except KeyboardInterrupt:
    print("\nStopping fan...")

finally:
    try:
        pwm.ChangeDutyCycle(0)
        time.sleep(1)  # give fan time to react

    except:
        pass



    print("Fan stopped.")
