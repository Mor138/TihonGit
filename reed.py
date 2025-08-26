import os
import time
import signal
import threading
import RPi.GPIO as GPIO
import motor_control as motor
import data_receiver as receiver
from menu import Menu
import psutil
import pygame
import json
import sys

# Файлы настроек
SETTINGS_FILE = "motor_settings.json"

# Функция для очистки пинов и завершения процессов, кроме текущего
def cleanup_pins_and_processes():
    current_pid = os.getpid()

    # Завершение всех процессов Python, кроме текущего
    for proc in psutil.process_iter(['pid', 'name']):
        if proc.info['name'] == 'python3' and proc.info['pid'] != current_pid:
            os.kill(proc.info['pid'], signal.SIGTERM)

    # Освобождение всех GPIO пинов
    GPIO.cleanup()

# Функция для инициализации GPIO пинов
def initialize_gpio_pins():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    # Настройка пинов
    for dir_pin, step_pin in zip(motor.DIR_PINS, motor.STEP_PINS):
        GPIO.setup(dir_pin, GPIO.OUT)
        GPIO.setup(step_pin, GPIO.OUT)

# Обработчик завершения программы
def graceful_exit(signum, frame, menu):
    print("Завершение программы...")
    menu.cleanup()
    motor.cleanup()
    sys.exit(0)  # Используем sys.exit вместо os._exit

# Очистка и инициализация перед запуском основной программы
cleanup_pins_and_processes()
time.sleep(2)  # Задержка на 2 секунды перед инициализацией
initialize_gpio_pins()

# Загрузка настроек из файлов
if os.path.exists(SETTINGS_FILE):
    with open(SETTINGS_FILE, "r") as f:
        motor_settings = json.load(f)
    print("[INFO] Настройки моторов загружены из файла.")
else:
    motor_settings = motor.MOTOR_SETTINGS  # Используем настройки по умолчанию
    with open(SETTINGS_FILE, "w") as f:
        json.dump(motor_settings, f, indent=4)
    print("[INFO] Файл настроек моторов создан с дефолтными значениями.")
    motor.update_motor_settings(motor_settings)

# Применение загруженных настроек к motor.MOTOR_SETTINGS
for i in range(len(motor.MOTOR_SETTINGS)):
    motor.MOTOR_SETTINGS[i] = motor_settings[i]

akpp_center = motor.MOTOR_SETTINGS[3]["distance_D"]  # Используем значение из motor_settings.json

# Назначение обработчиков сигналов для завершения программы
if __name__ == "__main__":
    # Инициализация меню
    menu = Menu(motor_settings, akpp_center)
    
    # Запуск потока приема данных
    receiver_thread = threading.Thread(target=receiver.receive_data, args=(motor,))
    receiver_thread.daemon = True
    receiver_thread.start()

    # Назначение сигналов завершения программы для корректного завершения
    signal.signal(signal.SIGINT, lambda signum, frame: graceful_exit(signum, frame, menu))
    signal.signal(signal.SIGTERM, lambda signum, frame: graceful_exit(signum, frame, menu))

    # Запуск меню в отдельном потоке с повышенным приоритетом
    menu_thread = threading.Thread(target=menu.run)
    menu_thread.daemon = True
    menu_thread.start()
    
    # Устанавливаем более высокий приоритет для потока меню
    os.system(f"sudo renice -n -5 -p {menu_thread.native_id}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    # Очистка GPIO в конце программы
    motor.cleanup()
