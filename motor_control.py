import RPi.GPIO as GPIO
import time
import os
import atexit

# ===== ПАРАМЕТРЫ =====
MOTOR_SETTINGS = [
    {"speed": 10000, "acceleration": 100, "distance": 15000},  # 0: Руль
    {"speed": 10000, "acceleration": 100, "distance": 10000},  # 1: Газ
    {"speed": 10000, "acceleration": 100, "distance": 10000},  # 2: Тормоз
    {"speed": 10000, "acceleration": 100, "distance_R": 4000, "distance_D": 7000}  # 3: АКПП
]

MAX_STEERING_SPEED = 25000
PULSES_PER_REVOLUTION = int(os.environ.get("PULSES_PER_REVOLUTION", 800))
HYSTERESIS = 50

# Реалистичные ограничения для userspace:
MIN_STEP_INTERVAL = 0.00020  # 200 мкс (≈5 кГц максимум)
STEP_PULSE_US     = 3        # ширина строба STEP в микросекундах (busy-wait)

DIR_PINS  = [26, 6, 0, 11]
STEP_PINS = [20, 12, 1, 8]
LIMIT_SWITCH_PINS = [None, 19, 13, None]

positions              = [0] * 4
target_positions       = [0] * 4
speeds                 = [0.0] * 4
last_step_time         = [time.time()] * 4
last_speed_update_time = [time.time()] * 4
step_intervals         = [float('inf')] * 4

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
for dir_pin, step_pin in zip(DIR_PINS, STEP_PINS):
    GPIO.setup(dir_pin, GPIO.OUT)
    GPIO.setup(step_pin, GPIO.OUT)

def setup_limit_switch_pins():
    for pin in LIMIT_SWITCH_PINS:
        if pin is not None:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

def update_motor_settings(new_settings):
    global MOTOR_SETTINGS, last_step_time, last_speed_update_time
    if new_settings and new_settings[0].get("speed", 0) > MAX_STEERING_SPEED:
        new_settings[0]["speed"] = MAX_STEERING_SPEED
    MOTOR_SETTINGS = new_settings
    now = time.time()
    for i in range(4):
        last_step_time[i] = now
        last_speed_update_time[i] = now
    update_step_intervals()

def get_motor_settings():
    return MOTOR_SETTINGS

# ===== ВСПОМОГАТЕЛЬНОЕ: быстрый микропаузер на ЦП =====
def _busy_wait_us(us: int):
    # минимальная точность на Pi в userspace — единицы микросекунд
    start = time.perf_counter()
    target = start + us / 1_000_000.0
    while time.perf_counter() < target:
        pass

# ===== ДИНАМИКА СКОРОСТИ =====
def update_step_intervals():
    global step_intervals, speeds, last_speed_update_time
    t = time.time()
    for i in range(4):
        target_speed = float(MOTOR_SETTINGS[i]["speed"])        # RPM
        acceleration = float(MOTOR_SETTINGS[i]["acceleration"]) # RPM/s

        dt = t - last_speed_update_time[i]
        if dt > 0:
            dv = acceleration * dt
            if speeds[i] < target_speed:
                speeds[i] = min(speeds[i] + dv, target_speed)
            elif speeds[i] > target_speed:
                speeds[i] = max(speeds[i] - dv, target_speed)
            last_speed_update_time[i] = t

        if speeds[i] > 0:
            pps = (speeds[i] * PULSES_PER_REVOLUTION) / 60.0
            step_intervals[i] = 1.0 / pps if pps > 0 else float('inf')
            if step_intervals[i] < MIN_STEP_INTERVAL:
                step_intervals[i] = MIN_STEP_INTERVAL
        else:
            step_intervals[i] = float('inf')

# ===== ШАГИ =====
def _do_step(i: int):
    GPIO.output(STEP_PINS[i], GPIO.HIGH)
    _busy_wait_us(STEP_PULSE_US)
    GPIO.output(STEP_PINS[i], GPIO.LOW)
    # без задержки на LOW: период задаётся step_intervals[i]

def move_motor(i: int):
    global positions, speeds, last_step_time

    max_distance = MOTOR_SETTINGS[i]["distance"] if i < 3 else MOTOR_SETTINGS[i]["distance_R"]

    if target_positions[i] > max_distance:
        target_positions[i] = max_distance
    elif target_positions[i] < -max_distance:
        target_positions[i] = -max_distance

    steps_to_move = target_positions[i] - positions[i]
    if abs(steps_to_move) < HYSTERESIS:
        speeds[i] = 0.0
        return False

    if i in [1, 2]:
        direction = GPIO.LOW if steps_to_move > 0 else GPIO.HIGH
    else:
        direction = GPIO.HIGH if steps_to_move > 0 else GPIO.LOW
    GPIO.output(DIR_PINS[i], direction)

    now = time.time()
    if now - last_step_time[i] >= step_intervals[i]:
        _do_step(i)
        positions[i] += 1 if steps_to_move > 0 else -1
        last_step_time[i] = now
        return True
    return False

def move_motor_akpp():
    i = 3
    max_R = MOTOR_SETTINGS[i]["distance_R"]
    max_D = MOTOR_SETTINGS[i]["distance_D"]

    if target_positions[i] < -max_R:
        target_positions[i] = -max_R
    elif target_positions[i] > max_D:
        target_positions[i] = max_D

    steps_to_move = target_positions[i] - positions[i]
    if abs(steps_to_move) < HYSTERESIS:
        speeds[i] = 0.0
        return False

    direction = GPIO.HIGH if steps_to_move > 0 else GPIO.LOW
    GPIO.output(DIR_PINS[i], direction)

    now = time.time()
    if now - last_step_time[i] >= step_intervals[i]:
        _do_step(i)
        positions[i] += 1 if steps_to_move > 0 else -1
        last_step_time[i] = now
        return True
    return False

def safety_mode():
    target_positions[0] = 0
    target_positions[1] = 0
    target_positions[2] = MOTOR_SETTINGS[2]["distance"]

def calibrate_motors():
    global positions, target_positions, speeds
    setup_limit_switch_pins()
    for motor_index in [1, 2]:
        direction_to_switch = GPIO.LOW
        GPIO.output(DIR_PINS[motor_index], direction_to_switch)
        limit_triggered = False
        max_steps = 10000
        step_count = 0
        while not limit_triggered and step_count < max_steps:
            if GPIO.input(LIMIT_SWITCH_PINS[motor_index]) == GPIO.LOW:
                limit_triggered = True
            else:
                _do_step(motor_index)
                step_count += 1
        if not limit_triggered:
            continue
        time.sleep(0.1)
        GPIO.output(DIR_PINS[motor_index], not direction_to_switch)
        for _ in range(100):
            _do_step(motor_index)
        positions[motor_index] = 0
        target_positions[motor_index] = 0
        speeds[motor_index] = 0.0

def cleanup():
    GPIO.cleanup()

atexit.register(cleanup)
