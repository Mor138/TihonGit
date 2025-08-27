import serial
import time

def receive_data(motor_control):
    try:
        uart = serial.Serial(
            port='/dev/ttyAMA0',   # <— было /dev/serial0
            baudrate=115200,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.01,
            write_timeout=0
        )
    except serial.SerialException:
        return

    buffer = bytearray()
    connection_lost = False
    first_run = True

    try:
        while True:
            data = uart.read(uart.in_waiting or 1)
            if data:
                buffer.extend(data)

                # разбор всех полных пакетов
                while len(buffer) >= 32:
                    # заголовок
                    if buffer[0] == 0x20 and buffer[1] == 0x40:
                        packet = buffer[:32]

                        # контрольная сумма
                        checksum = 0xFFFF
                        for b in packet[:-2]:
                            checksum -= b
                        checksum &= 0xFFFF
                        packet_checksum = packet[-2] | (packet[-1] << 8)

                        if checksum == packet_checksum:
                            # каналы
                            channels = []
                            for i in range(10):
                                lo = packet[2 + i*2]
                                hi = packet[3 + i*2]
                                channels.append(lo | (hi << 8))

                            signal_ok = channels[6] >= 800
                            if not signal_ok and not connection_lost:
                                motor_control.safety_mode()
                            connection_lost = not signal_ok

                            if signal_ok:
                                if first_run:
                                    motor_control.positions[0] = 0
                                    first_run = False
                                else:
                                    settings = motor_control.get_motor_settings()

                                    # Руль
                                    max_steer = settings[0]["distance"]
                                    motor_control.target_positions[0] = int((channels[0] - 1500) * (max_steer / 500))

                                    # Газ
                                    max_gas = settings[1]["distance"]
                                    if channels[1] > 1500:
                                        motor_control.target_positions[1] = int((channels[1] - 1500) * (max_gas / 500))
                                    else:
                                        motor_control.target_positions[1] = 0

                                    # Тормоз
                                    max_brake = settings[2]["distance"]
                                    motor_control.target_positions[2] = int((channels[2] - 1000) * (max_brake / 1000))

                                    # АКПП
                                    akpp_value = channels[5]
                                    if akpp_value < 1200:
                                        motor_control.target_positions[3] = -settings[3]["distance_R"]
                                    elif akpp_value > 1800:
                                        motor_control.target_positions[3] = settings[3]["distance_D"]
                                    else:
                                        motor_control.target_positions[3] = 0

                            # сдвиг буфера
                            del buffer[:32]
                            break
                        else:
                            del buffer[0]
                    else:
                        del buffer[0]
            else:
                time.sleep(0.001)

            # обновление моторов
            motor_control.update_step_intervals()
            for i in range(4):
                if i == 3:
                    motor_control.move_motor_akpp()
                else:
                    motor_control.move_motor(i)

    except KeyboardInterrupt:
        pass
    finally:
        try:
            uart.close()
        except Exception:
            pass
