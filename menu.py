import os
import json
import time
from PIL import Image, ImageDraw, ImageFont
from evdev import InputDevice, categorize, ecodes
import motor_control as motor
import numpy as np
import threading
import signal
from signal import SIGINT
import evdev
import math
import queue
import select

WIDTH, HEIGHT = 800, 480
BUTTON_RADIUS = 80
BUTTON_RADIUS_LARGE = 100
BUTTON_RADIUS_SMALL = int(BUTTON_RADIUS * 0.7)
STEP = 500
TOUCH_TOLERANCE = 20

MOTORS = ["Руль", "Газ", "Тормоз", "АКПП"]

BACKGROUND_COLOR = (50, 0, 100)
BUTTON_OUTLINE_COLOR = (255, 255, 255)
TEXT_COLOR = (255, 255, 255)

PARAMETERS_BY_MOTOR = [
    ["speed", "acceleration", "distance"],
    ["speed", "acceleration", "distance"],
    ["speed", "acceleration", "distance"],
    ["speed", "acceleration", "distance_R", "distance_D"]
]

PARAMETERS_RUS = {
    "speed": "Скорость",
    "acceleration": "Ускорение",
    "distance": "Дистанция",
    "distance_R": "Дистанция R",
    "distance_D": "Дистанция D"
}

SETTINGS_FILE = "motor_settings.json"
AKPP_FILE = "akpp_center.json"

class Menu:
    def __init__(self, motor_settings, akpp_center):
        self.motor_settings = motor_settings
        self.akpp_center = akpp_center
        self.BACKGROUND_COLOR = (50, 0, 100)
        self.TEXT_COLOR = (255, 255, 255)
        self.BUTTON_OUTLINE_COLOR = (255, 255, 255)
        self.current_motor_index = 0
        self.current_param_index = 0
        self.is_in_main_menu = True
        self.touch_device = self.find_touch_device()
        self.last_touch_time = 0
        self.touch_debounce = 0.1
        self.running = True
        self.touch_queue = queue.Queue()
        self.redraw_needed = threading.Event()
        self.redraw_needed.set()

        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        self.font_large = ImageFont.truetype(font_path, 50)
        self.font_medium = ImageFont.truetype(font_path, 30)
        self.font_small = ImageFont.truetype(font_path, 24)

        self.main_menu_image = self.create_main_menu_image()
        self.param_menu_images = {}
        self.update_screen(self.main_menu_image)

    def find_touch_device(self):
        try:
            devices = [InputDevice(path) for path in evdev.list_devices()]
            for device in devices:
                if "touchscreen" in device.name.lower() or "ft5x06" in device.name.lower():
                    device.grab()
                    return device
            if devices:
                devices[0].grab()
                return devices[0]
        except Exception:
            pass
        return None

    def create_main_menu_image(self):
        image = Image.new("RGB", (WIDTH, HEIGHT), self.BACKGROUND_COLOR)
        draw = ImageDraw.Draw(image)

        buttons = [
            {'name': 'Руль', 'x': 150, 'y': 150},
            {'name': 'Газ', 'x': WIDTH - 150, 'y': 150},
            {'name': 'Тормоз', 'x': 150, 'y': HEIGHT - 150},
            {'name': 'АКПП', 'x': WIDTH - 150, 'y': HEIGHT - 150}
        ]
        for btn in buttons:
            self.draw_metal_button(draw, btn['x'], btn['y'], BUTTON_RADIUS)
            self.draw_text(draw, btn['x'], btn['y'] - BUTTON_RADIUS - 20, btn['name'], self.font_medium)

        self.draw_metal_button(draw, WIDTH // 2, HEIGHT // 2, BUTTON_RADIUS_LARGE)
        self.draw_text(draw, WIDTH // 2, HEIGHT // 2 + BUTTON_RADIUS_LARGE + 20, "КАЛИБРОВКА", self.font_large)
        return image

    def create_parameter_menu_image(self, motor_index, param_index):
        image = Image.new("RGB", (WIDTH, HEIGHT), self.BACKGROUND_COLOR)
        draw = ImageDraw.Draw(image)

        motor_name = MOTORS[motor_index]
        param_key = PARAMETERS_BY_MOTOR[motor_index][param_index]
        param_value = self.motor_settings[motor_index].get(param_key, 0)

        self.draw_text(draw, WIDTH // 2, 30, f"{motor_name} - {PARAMETERS_RUS[param_key]}", self.font_large)
        self.draw_text(draw, WIDTH // 2, 130, f"Текущее значение: {param_value}", self.font_medium)

        self.draw_metal_button(draw, 150, 245, BUTTON_RADIUS_SMALL)
        self.draw_text(draw, 150, 245 - 35, "-", self.font_large)

        self.draw_metal_button(draw, WIDTH - 150, 245, BUTTON_RADIUS_SMALL)
        self.draw_text(draw, WIDTH - 150, 245 - 35, "+", self.font_large)

        self.draw_metal_button(draw, 100, HEIGHT - 100, BUTTON_RADIUS)
        self.draw_text(draw, 100, HEIGHT - 130, "<", self.font_large)

        self.draw_metal_button(draw, WIDTH - 100, HEIGHT - 100, BUTTON_RADIUS)
        self.draw_text(draw, WIDTH - 100, HEIGHT - 130, ">", self.font_large)

        self.draw_metal_button(draw, WIDTH // 2, HEIGHT - 125, BUTTON_RADIUS)
        self.draw_text(draw, WIDTH // 2, HEIGHT - 70, "Сохранить", self.font_small)

        return image

    def draw_motor_selection(self):
        self.is_in_main_menu = True
        self.update_screen(self.main_menu_image)

    def draw_metal_button(self, draw, x, y, radius):
        for i in range(radius):
            factor = i / radius
            color = (150 + int(80 * factor), 150 + int(80 * factor), 150 + int(80 * factor))
            draw.ellipse([x - radius + i, y - radius + i, x + radius - i, y + radius - i], fill=color)
        draw.ellipse([x - radius - 5, y - radius - 5, x + radius + 5, y + radius + 5],
                     outline=self.BUTTON_OUTLINE_COLOR, width=3)

    def draw_parameter_menu(self):
        self.is_in_main_menu = False
        key = (self.current_motor_index, self.current_param_index)
        if key not in self.param_menu_images:
            self.param_menu_images[key] = self.create_parameter_menu_image(*key)
        self.update_screen(self.param_menu_images[key])

    def is_touch_in_circle(self, x, y, center_x, center_y, radius):
        distance = math.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)
        return distance < (radius + TOUCH_TOLERANCE)

    def process_touch(self, x, y):
        current_time = time.time()
        if current_time - self.last_touch_time < self.touch_debounce:
            return
        self.last_touch_time = current_time

        if WIDTH != 800 or HEIGHT != 480:
            x = int(x * WIDTH / 800)
            y = int(y * HEIGHT / 480)

        if self.is_in_main_menu:
            buttons = [
                {'name': 'Руль', 'x': 150, 'y': 150, 'index': 0},
                {'name': 'Газ', 'x': WIDTH - 150, 'y': 150, 'index': 1},
                {'name': 'Тормоз', 'x': 150, 'y': HEIGHT - 150, 'index': 2},
                {'name': 'АКПП', 'x': WIDTH - 150, 'y': HEIGHT - 150, 'index': 3}
            ]
            for btn in buttons:
                if self.is_touch_in_circle(x, y, btn['x'], btn['y'], BUTTON_RADIUS):
                    self.current_motor_index = btn['index']
                    self.current_param_index = 0
                    self.draw_parameter_menu()
                    return

            if self.is_touch_in_circle(x, y, WIDTH // 2, HEIGHT // 2, BUTTON_RADIUS_LARGE):
                threading.Thread(target=self.start_calibration, daemon=True).start()
        else:
            if self.is_touch_in_circle(x, y, 150, 245, BUTTON_RADIUS_SMALL):
                self.adjust_parameter(-STEP)
            elif self.is_touch_in_circle(x, y, WIDTH - 150, 245, BUTTON_RADIUS_SMALL):
                self.adjust_parameter(STEP)
            elif self.is_touch_in_circle(x, y, 100, HEIGHT - 100, BUTTON_RADIUS):
                self.switch_parameter(-1)
            elif self.is_touch_in_circle(x, y, WIDTH - 100, HEIGHT - 100, BUTTON_RADIUS):
                self.switch_parameter(1)
            elif self.is_touch_in_circle(x, y, WIDTH // 2, HEIGHT - 125, BUTTON_RADIUS):
                self.save_settings()
                self.draw_motor_selection()

    def adjust_parameter(self, delta):
        param = PARAMETERS_BY_MOTOR[self.current_motor_index][self.current_param_index]
        current_value = self.motor_settings[self.current_motor_index].get(param, 0)
        current_value = max(0, current_value + delta)
        self.motor_settings[self.current_motor_index][param] = current_value
        self.apply_motor_settings()

        key = (self.current_motor_index, self.current_param_index)
        self.param_menu_images[key] = self.create_parameter_menu_image(*key)
        self.update_screen(self.param_menu_images[key])

    def switch_parameter(self, direction):
        self.current_param_index = (self.current_param_index + direction) % len(
            PARAMETERS_BY_MOTOR[self.current_motor_index])
        self.draw_parameter_menu()

    def start_calibration(self):
        motor.calibrate_motors()

    def save_settings(self):
        correct_motor_settings = []
        for i, motor_setting in enumerate(self.motor_settings):
            if i < 3:
                correct_motor_settings.append({
                    "speed": motor_setting["speed"],
                    "acceleration": motor_setting["acceleration"],
                    "distance": motor_setting.get("distance", 1000)
                })
            else:
                correct_motor_settings.append({
                    "speed": motor_setting["speed"],
                    "acceleration": motor_setting["acceleration"],
                    "distance_R": motor_setting.get("distance_R", 1000),
                    "distance_D": motor_setting.get("distance_D", 4000)
                })

        with open(SETTINGS_FILE, "w") as f:
            json.dump(correct_motor_settings, f, indent=4)

        self.motor_settings = correct_motor_settings
        self.apply_motor_settings()

    def apply_motor_settings(self):
        motor.update_motor_settings(self.motor_settings)
        if len(self.motor_settings) > 3:
            motor.akpp_center_value = self.akpp_center

    def cleanup(self):
        image = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
        self.update_screen(image)
        if self.touch_device:
            self.touch_device.ungrab()
        self.running = False

    def draw_text(self, draw, x, y, text, font):
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            w = bbox[2] - bbox[0]
            draw.text((x - w // 2, y), text, font=font, fill=self.TEXT_COLOR)
        except AttributeError:
            w, _ = draw.textsize(text, font=font)
            draw.text((x - w // 2, y), text, font=font, fill=self.TEXT_COLOR)

    def update_screen(self, image):
        try:
            fb_image = np.array(image.convert('RGB')).astype('uint16')
            fb_image = ((fb_image[:, :, 0] >> 3) << 11) | ((fb_image[:, :, 1] >> 2) << 5) | (fb_image[:, :, 2] >> 3)
            with open("/dev/fb0", "wb") as fb:
                fb.write(fb_image.tobytes())
        except Exception:
            pass

    def touch_listener_thread(self):
        last_x = None
        last_y = None

        while self.running and self.touch_device:
            try:
                events = self.touch_device.read()
                if events:
                    touch_detected = False
                    for event in events:
                        if event.type == ecodes.EV_ABS:
                            if event.code == ecodes.ABS_MT_POSITION_X:
                                last_x = event.value
                            elif event.code == ecodes.ABS_MT_POSITION_Y:
                                last_y = event.value
                        elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                            if event.value == 1:
                                touch_detected = True
                            elif event.value == 0:
                                last_x = None
                                last_y = None

                    if touch_detected and last_x is not None and last_y is not None:
                        self.touch_queue.put((last_x, last_y))
                        last_x = None
                        last_y = None
            except BlockingIOError:
                time.sleep(0.001)
            except Exception:
                time.sleep(0.1)

    def run(self):
        self.draw_motor_selection()
        if self.touch_device:
            threading.Thread(target=self.touch_listener_thread, daemon=True).start()

        while self.running:
            try:
                last_touch = None
                while not self.touch_queue.empty():
                    last_touch = self.touch_queue.get_nowait()
                if last_touch:
                    x, y = last_touch
                    self.process_touch(x, y)
                time.sleep(0.01)
            except Exception:
                pass

if __name__ == "__main__":
    def signal_handler(sig, frame):
        menu.save_settings()
        menu.cleanup()
        os._exit(0)

    signal.signal(SIGINT, signal_handler)

    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            motor_settings = json.load(f)
    else:
        motor_settings = motor.MOTOR_SETTINGS
        with open(SETTINGS_FILE, "w") as f:
            json.dump(motor_settings, f, indent=4)

    if os.path.exists(AKPP_FILE):
        with open(AKPP_FILE, "r") as f:
            akpp_center = json.load(f)["akpp_center_value"]
    else:
        akpp_center = 5000
        with open(AKPP_FILE, "w") as f:
            json.dump({"akpp_center_value": akpp_center}, f, indent=4)

    menu = Menu(motor_settings, akpp_center)
    menu_thread = threading.Thread(target=menu.run)
    menu_thread.daemon = True
    menu_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        menu.cleanup()
