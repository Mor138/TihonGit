import RPi.GPIO as GPIO
import time
import json
import os
import atexit

# Глобальная переменная для настроек моторов
MOTOR_SETTINGS = [
    {"speed": 10000, "acceleration": 100, "distance": 15000},  # Мотор 1 (Руль)
    {"speed": 10000, "acceleration": 100, "distance": 10000},  # Мотор 2 (Газ)
    {"speed": 10000, "acceleration": 100, "distance": 10000},  # Мотор 3 (Тормоз)
    {"speed": 10000, "acceleration": 100, "distance_R": 4000, "distance_D": 7000}    # Мотор 4 (АКПП)
]

# Максимально допустимая скорость для мотора руля (об/мин)
MAX_STEERING_SPEED = 15000

DIR_PINS = [26, 6, 0, 11]       # GPIO для DIR
STEP_PINS = [20, 12, 1, 8]      # GPIO для STEP
# Концевики только для газа (1) и тормоза (2)
LIMIT_SWITCH_PINS = [None, 19, 13, None]  # GPIO для концевиков

positions = [0] * 4
target_positions = [0] * 4
speeds = [0.0] * 4  # Изменено на float
last_step_time = [time.time()] * 4  # Инициализация текущим временем
step_intervals = [0.0] * 4  # Изменено на float

STEPS_PER_REVOLUTION = 200
HYSTERESIS = 50
MIN_STEP_INTERVAL = 0.0001  # Минимальный интервал между шагами (100 мкс)

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Настройка пинов для моторов
for dir_pin, step_pin in zip(DIR_PINS, STEP_PINS):
    GPIO.setup(dir_pin, GPIO.OUT)
    GPIO.setup(step_pin, GPIO.OUT)

def setup_limit_switch_pins():
    for pin in LIMIT_SWITCH_PINS:
        if pin is not None:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    print("[INFO] Пины концевиков настроены.")
    
def update_motor_settings(new_settings):
    """Обновление параметров моторов с ограничением скорости руля."""
    global MOTOR_SETTINGS

    # Ограничиваем скорость для мотора руля, чтобы избежать нестабильного поведения
    if new_settings and new_settings[0].get("speed", 0) > MAX_STEERING_SPEED:
        print(
            f"[WARNING] Скорость мотора руля превышает допустимое значение. "
            f"Ограничено {MAX_STEERING_SPEED} об/мин."
        )
        new_settings[0]["speed"] = MAX_STEERING_SPEED

    MOTOR_SETTINGS = new_settings

    # Сброс текущих скоростей при изменении настроек
    for i in range(4):
        speeds[i] = 0.0
    print("[MOTOR] Настройки моторов обновлены")

def update_step_intervals():
    global MOTOR_SETTINGS, speeds, step_intervals
    current_time = time.time()
    
    for i in range(4):
        # Рассчитываем целевую скорость (обороты в минуту)
        target_speed = MOTOR_SETTINGS[i]["speed"]
        
        # Рассчитываем ускорение (изменение скорости за секунду)
        acceleration = MOTOR_SETTINGS[i]["acceleration"]
        
        # Рассчитываем изменение скорости с учетом времени
        time_diff = current_time - last_step_time[i]
        if time_diff > 0:
            speed_diff = acceleration * time_diff
            if speeds[i] < target_speed:
                speeds[i] = min(speeds[i] + speed_diff, target_speed)
            elif speeds[i] > target_speed:
                speeds[i] = max(speeds[i] - speed_diff, target_speed)
        
        # Рассчитываем интервал между шагами
        if speeds[i] > 0:
            # Шагов в секунду = (об/мин * шагов/оборот) / 60
            steps_per_sec = (speeds[i] * STEPS_PER_REVOLUTION) / 60.0
            step_intervals[i] = 1.0 / steps_per_sec if steps_per_sec > 0 else float('inf')
            # Ограничиваем минимальный интервал
            step_intervals[i] = max(step_intervals[i], MIN_STEP_INTERVAL)
        else:
            step_intervals[i] = float('inf')

def move_motor(i):
    global MOTOR_SETTINGS, positions, target_positions, DIR_PINS, STEP_PINS
    global speeds, last_step_time, step_intervals
    
    # Рассчитываем максимальную дистанцию
    if i < 3:
        max_distance = MOTOR_SETTINGS[i]["distance"]
    else:
        max_distance = MOTOR_SETTINGS[i]["distance_R"]

    # Ограничиваем целевую позицию
    if target_positions[i] > max_distance:
        target_positions[i] = max_distance
    elif target_positions[i] < -max_distance:
        target_positions[i] = -max_distance

    # Рассчитываем необходимое движение
    steps_to_move = target_positions[i] - positions[i]
    if abs(steps_to_move) < HYSTERESIS:
        speeds[i] = 0  # Сбрасываем скорость, если движение не требуется
        return False

    # Определяем направление
    if i in [1, 2]:  # Для газа и тормоза
        direction = GPIO.LOW if steps_to_move > 0 else GPIO.HIGH
    else:  # Для руля и АКПП
        direction = GPIO.HIGH if steps_to_move > 0 else GPIO.LOW

    GPIO.output(DIR_PINS[i], direction)

    # Проверяем, пришло ли время для следующего шага
    current_time = time.time()
    if current_time - last_step_time[i] >= step_intervals[i]:
        # Выполняем шаг
        GPIO.output(STEP_PINS[i], GPIO.HIGH)
        # Минимальная задержка для драйвера шагового двигателя
        time.sleep(0.000001)  # 1 мкс
        GPIO.output(STEP_PINS[i], GPIO.LOW)
        
        # Обновляем позицию
        positions[i] += 1 if steps_to_move > 0 else -1
        last_step_time[i] = current_time
        
        return True
    
    return False
    
def get_motor_settings():
    global MOTOR_SETTINGS
    return MOTOR_SETTINGS    

def move_motor_akpp():
    i = 3  # Управление мотором АКПП
    max_distance_R = MOTOR_SETTINGS[i]["distance_R"]
    max_distance_D = MOTOR_SETTINGS[i]["distance_D"]
    
    # Ограничиваем целевую позицию
    if target_positions[i] < -max_distance_R:
        target_positions[i] = -max_distance_R
    elif target_positions[i] > max_distance_D:
        target_positions[i] = max_distance_D
        
    steps_to_move = target_positions[i] - positions[i]
    
    # Проверяем гистерезис
    if abs(steps_to_move) < HYSTERESIS:
        speeds[i] = 0  # Сбрасываем скорость
        return False

    # Определяем направление
    direction = GPIO.HIGH if steps_to_move > 0 else GPIO.LOW
    GPIO.output(DIR_PINS[i], direction)

    # Проверяем, пришло ли время для следующего шага
    current_time = time.time()
    if current_time - last_step_time[i] >= step_intervals[i]:
        # Выполняем шаг
        GPIO.output(STEP_PINS[i], GPIO.HIGH)
        time.sleep(0.000001)  # 1 мкс
        GPIO.output(STEP_PINS[i], GPIO.LOW)
        
        # Обновляем позицию
        positions[i] += 1 if steps_to_move > 0 else -1
        last_step_time[i] = current_time
        
        return True
    
    return False

def safety_mode():
    global MOTOR_SETTINGS, target_positions
    target_positions[0] = 0
    target_positions[1] = 0
    target_positions[2] = MOTOR_SETTINGS[2]["distance"]

def calibrate_motors():
    global MOTOR_SETTINGS, LIMIT_SWITCH_PINS, positions, target_positions
    print("[INFO] Начало калибровки газа и тормоза...")
    setup_limit_switch_pins()
    
    # Калибруем только газ (1) и тормоз (2)
    for motor_index in [1, 2]:
        print(f"[CALIB] Калибровка мотора {motor_index}")
        
        # Определяем направление движения к концевику
        direction_to_switch = GPIO.LOW  # Для газа и тормоза
        
        # Устанавливаем направление
        GPIO.output(DIR_PINS[motor_index], direction_to_switch)
        
        # Флаг срабатывания концевика
        limit_triggered = False
        max_steps = 10000  # Максимальное количество шагов
        step_count = 0
        
        while not limit_triggered and step_count < max_steps:
            if GPIO.input(LIMIT_SWITCH_PINS[motor_index]) == GPIO.LOW:
                limit_triggered = True
                print(f"[CALIB] Концевик {motor_index} сработал")
            else:
                # Делаем шаг
                GPIO.output(STEP_PINS[motor_index], GPIO.HIGH)
                time.sleep(0.0001)  # Уменьшено для ускорения
                GPIO.output(STEP_PINS[motor_index], GPIO.LOW)
                time.sleep(0.0001)
                step_count += 1
        
        if not limit_triggered:
            print(f"[ERROR] Мотор {motor_index} не достиг концевика за {max_steps} шагов")
            continue
        
        # Делаем паузу для стабилизации
        time.sleep(0.1)
        
        # Выходим из зоны срабатывания (100 шагов в обратном направлении)
        print(f"[CALIB] Выход из зоны концевика {motor_index}")
        GPIO.output(DIR_PINS[motor_index], not direction_to_switch)
        for _ in range(100):
            GPIO.output(STEP_PINS[motor_index], GPIO.HIGH)
            time.sleep(0.0001)  # Уменьшено
            GPIO.output(STEP_PINS[motor_index], GPIO.LOW)
            time.sleep(0.0001)
        
        # Сбрасываем позицию мотора
        positions[motor_index] = 0
        target_positions[motor_index] = 0
        speeds[motor_index] = 0  # Сброс скорости
        print(f"[CALIB] Мотор {motor_index} откалиброван. Позиция: 0")
    
    print("[INFO] Калибровка газа и тормоза завершена.")

def cleanup():
    print("[MOTOR] Очистка GPIO")
    GPIO.cleanup()

# Регистрация функции очистки при завершении
atexit.register(cleanup)