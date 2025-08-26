import serial
import time

def receive_data(motor_control):
    try:
        uart = serial.Serial(
            port='/dev/serial0',
            baudrate=115200,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1  # Важно добавить timeout
        )
        print("[UART] Порт /dev/serial0 успешно открыт")
    except serial.SerialException as e:
        print(f"[UART ERROR] Ошибка открытия порта: {e}")
        return

    buffer = bytearray()
    connection_lost = False
    first_run = True

    try:
        while True:
            # Читаем все доступные данные
            data = uart.read(uart.in_waiting or 1)
            if data:
                buffer.extend(data)
                
                # Обрабатываем все полные пакеты в буфере
                while len(buffer) >= 32:
                    # Проверяем заголовок пакета
                    if buffer[0] == 0x20 and buffer[1] == 0x40:
                        packet = buffer[:32]
                        # Проверка контрольной суммы
                        checksum = 0xFFFF
                        for b in packet[:-2]:
                            checksum -= b
                        checksum &= 0xFFFF
                        
                        packet_checksum = packet[-2] | (packet[-1] << 8)
                        
                        if checksum == packet_checksum:
                            # Парсим каналы
                            channels = []
                            for i in range(10):
                                lo = packet[2 + i*2]
                                hi = packet[3 + i*2]
                                channels.append(lo | (hi << 8))
                            
                            signal_ok = channels[6] >= 800
                            if not signal_ok and not connection_lost:
                                print("Connection lost! (Signal below 800)")
                                motor_control.safety_mode()
                            elif signal_ok and connection_lost:
                                print("Connection OK (Signal above 800)")
                            connection_lost = not signal_ok
                            
                            if signal_ok:
                                if first_run:
                                    motor_control.positions[0] = 0
                                    first_run = False
                                else:
                                    # Получаем актуальные настройки
                                    settings = motor_control.get_motor_settings()
                                    
                                    # Рулевое управление (используем полную дистанцию)
                                    max_steer = settings[0]["distance"]
                                    motor_control.target_positions[0] = int((channels[0] - 1500) * (max_steer / 500))
                                    
                                    # Газ (используем дистанцию газа)
                                    max_gas = settings[1]["distance"]
                                    if channels[1] > 1500:
                                        motor_control.target_positions[1] = int((channels[1] - 1500) * (max_gas / 500))
                                    else:
                                        motor_control.target_positions[1] = 0
                                        
                                    # Тормоз (используем дистанцию тормоза)
                                    max_brake = settings[2]["distance"]
                                    motor_control.target_positions[2] = int((channels[2] - 1000) * (max_brake / 1000))
                                    
                                    # АКПП (используем специальные дистанции)
                                    akpp_value = channels[5]
                                    if akpp_value < 1200:
                                        motor_control.target_positions[3] = -settings[3]["distance_R"]
                                    elif akpp_value > 1800:
                                        motor_control.target_positions[3] = settings[3]["distance_D"]
                                    else:
                                        motor_control.target_positions[3] = 0
                                    
                            
                            # Удаляем обработанный пакет
                            del buffer[:32]
                            break
                        else:
                            # Неверная контрольная сумма - удаляем первый байт
                            del buffer[0]
                    else:
                        # Неверный заголовок - удаляем первый байт
                        del buffer[0]
            else:
                # Если данных нет, делаем небольшую паузу
                time.sleep(0.01)
                
            # Обновляем моторы
            motor_control.update_step_intervals()
            for i in range(4):
                if i == 3:
                    motor_control.move_motor_akpp()
                else:
                    motor_control.move_motor(i)

    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        uart.close()
