# sp-fan-ctrl
sp fan ctrl for AUB0912VH-CX09 fan

```
sudo apt update
sudo apt install pigpio python3-pigpio
```

```
cd ~
sudo apt update
sudo apt install git build-essential -y
git clone https://github.com/joan2937/pigpio.git
cd pigpio
make
sudo make install
```

1. Check if the library and binaries exist
```
which pigpiod
ls /usr/local/lib | grep pigpio
```
You should see a path like /usr/local/bin/pigpiod and library files like libpigpio.so.

2. Verify it's running
```
pigs t
```

If this prints a number (a tick count), the daemon is alive and ready.

3. Install the Python bindings (if not already)

Since you built from source, the C library is installed, but you still need the Python wrapper:
```
sudo apt update
sudo apt install python3-pip python3-venv
```
```
python3 -m venv ~/venv
source ~/venv/bin/activate
```

```
pip install pigpio
```



# Why pigpio

This project uses pigpio instead of RPi.GPIO or lgpio for PWM fan control, for these reasons:

The fan requires real 25kHz PWM. Delta's AUB0912VH (and PC-style 4-pin PWM fans generally) expect a 21–28kHz control signal on the PWM input to run correctly and silently.

Software PWM can't reliably hit that frequency. Both RPi.GPIO (on current Raspberry Pi OS, which is actually rpi-lgpio under the hood) and raw lgpio generate PWM by bit-banging in software. This has a hard frequency ceiling well below 25kHz — attempting it throws a bad PWM frequency error, or in earlier library versions, produces jittery, inaccurate output.

pigpio drives real hardware PWM. It talks to the Pi's PWM peripheral directly (via DMA/register access) from userspace, producing a clean, accurate 25kHz signal with no jitter — matching what the fan actually expects.

No kernel config required. The alternative way to get true hardware PWM is enabling a device tree overlay (dtoverlay=pwm in config.txt) and driving it via sysfs — this works, but requires editing boot config and a reboot. pigpio achieves the same hardware-level PWM entirely in userspace (just a background daemon, pigpiod), so no kernel/boot changes are needed.

Tach (RPM) reading is also more reliable. The fan's tach line pulses ~127 times/sec at full speed (2 pulses/rev × 3800 RPM). pigpio's daemon-based interrupt handling counts these more reliably than software-level GPIO event detection, which can miss pulses at that rate.

Platform note: pigpio only supports Pi 3B/4 (BCM283x GPIO architecture) — it does not work on Pi 5, which uses the newer RP1 I/O chip. Since this project targets Pi 3B (dev) and Pi 4 (deployment), that's not a limitation here. If ported to Pi 5 later, lgpio or rpi-lgpio would be the required replacement, with the same hardware-PWM caveats re-evaluated.
