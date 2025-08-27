import os
import time
import signal
import threading
import RPi.GPIO as GPIO
import motor_control as motor
import data_receiver as receiver
from menu import Menu
import psutil
import json
import sys

SETTINGS_FILE = "motor_settings.json"

def cleanup_pins_and_processes():
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if proc.info['name'] == 'python3' and proc.info['pid'] != current_pid:
                os.kill(proc.info['pid'], signal.SIGTERM)
        except Exception:
            pass
    GPIO.cleanup()

def initialize_gpio_pins():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for dir_pin, step_pin in zip(motor.DIR_PINS, motor.STEP_PINS):
        GPIO.setup(dir_pin, GPIO.OUT)
        GPIO.setup(step_pin, GPIO.OUT)

def graceful_exit(signum, frame, menu):
    try:
        menu.cleanup()
    except Exception:
        pass
    try:
        motor.cleanup()
    except Exception:
        pass
    sys.exit(0)

cleanup_pins_and_processes()
time.sleep(2)
initialize_gpio_pins()

if os.path.exists(SETTINGS_FILE):
    with open(SETTINGS_FILE, "r") as f:
        motor_settings = json.load(f)
else:
    motor_settings = motor.MOTOR_SETTINGS
    with open(SETTINGS_FILE, "w") as f:
        json.dump(motor_settings, f, indent=4)
    motor.update_motor_settings(motor_settings)

motor.update_motor_settings(motor_settings)

akpp_center = motor.MOTOR_SETTINGS[3]["distance_D"]

if __name__ == "__main__":
    menu = Menu(motor_settings, akpp_center)

    receiver_thread = threading.Thread(target=receiver.receive_data, args=(motor,))
    receiver_thread.daemon = True
    receiver_thread.start()

    signal.signal(signal.SIGINT, lambda s, f: graceful_exit(s, f, menu))
    signal.signal(signal.SIGTERM, lambda s, f: graceful_exit(s, f, menu))

    menu_thread = threading.Thread(target=menu.run)
    menu_thread.daemon = True
    menu_thread.start()

    # по возможности повышаем приоритет (без вывода)
    try:
        os.system(f"sudo renice -n -5 -p {menu_thread.native_id} >/dev/null 2>&1")
    except Exception:
        pass

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    motor.cleanup()
