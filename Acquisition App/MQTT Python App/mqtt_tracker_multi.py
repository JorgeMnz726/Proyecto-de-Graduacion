import paho.mqtt.client as mqtt
import time
import json
import threading
from collections import deque
from datetime import datetime
import pandas as pd
import numpy as np
import tkinter as tk
from tkinter import filedialog
import os

# Parámetros de conexión
BROKER_ADDRESS = "192.168.50.200"
PORT = 1880
KEEP_ALIVE = 180

# IDs de las masas disponibles
AVAILABLE_MASSES = ["76", "77", "78", "79"]
GND_IDENTIFIER = "80"  # Referencia GND

# Variables de configuración
num_masses = 1  # Número de masas a trackear (1-4)
use_relative = True  # True: relativo a GND, False: absoluto

# Variables globales para almacenar datos
data_lock = threading.Lock()

# Datos de cada masa (76, 77, 78, 79)
masses_data = {}
for mass_id in AVAILABLE_MASSES:
    masses_data[mass_id] = {
        'time': deque(maxlen=10000),
        'x': deque(maxlen=10000),
        'y': deque(maxlen=10000),
        'z': deque(maxlen=10000),
        'roll': deque(maxlen=10000),
        'pitch': deque(maxlen=10000),
        'yaw': deque(maxlen=10000)
    }

# Datos del GND (80)
gnd_data = {
    'time': deque(maxlen=10000),
    'x': deque(maxlen=10000),
    'y': deque(maxlen=10000),
    'z': deque(maxlen=10000),
    'roll': deque(maxlen=10000),
    'pitch': deque(maxlen=10000),
    'yaw': deque(maxlen=10000)
}

is_recording = False
start_time = None
xlsx_filename = None

# Callbacks
def on_connect(client, userdata, flags, rc):
    """Callback cuando el cliente se conecta al broker"""
    if rc == 0:
        print("Conexión exitosa al broker MQTT")
        print(f"  Broker: tcp://{BROKER_ADDRESS}")
        print(f"  Puerto: {PORT}")
        print(f"  Keep Alive: {KEEP_ALIVE} segundos")
        
        # Suscribirse al topic mocap/mechanical
        client.subscribe("mocap/mechanical")
        print("Suscrito al topic: mocap/mechanical")
    else:
        print(f"Error de conexión. Código: {rc}")

def on_disconnect(client, userdata, rc):
    """Callback cuando se desconecta"""
    if rc != 0:
        print("Desconexión inesperada del broker")
    else:
        print("Desconectado del broker")

def on_message(client, userdata, msg):
    """Callback cuando se recibe un mensaje"""
    global start_time
    
    if not is_recording:
        return
    
    try:
        # Parsear el JSON del payload
        payload_str = msg.payload.decode()
        data = json.loads(payload_str)
        
        identifier = data.get("identifier")
        
        # Procesar masas seleccionadas o GND
        selected_masses = AVAILABLE_MASSES[:num_masses]
        
        if identifier in selected_masses or identifier == GND_IDENTIFIER:
            # Extraer posición y orientación
            pose = data.get('payload', {}).get('pose', {})
            position = pose.get('position', {})
            rotation = pose.get('rotation', {})
            
            x = position.get('x', 0)
            y = position.get('y', 0)
            z = position.get('z', 0)
            
            # Extraer ángulos de Euler (en radianes)
            roll = rotation.get('x', 0)   # roll (rotación en X)
            pitch = rotation.get('y', 0)  # pitch (rotación en Y)
            yaw = rotation.get('z', 0)    # yaw (rotación en Z)
            
            # Almacenar datos con timestamp
            current_time = time.time()
            if start_time is None:
                start_time = current_time
            
            t = current_time - start_time
            
            with data_lock:
                if identifier in selected_masses:
                    masses_data[identifier]['time'].append(t)
                    masses_data[identifier]['x'].append(x)
                    masses_data[identifier]['y'].append(y)
                    masses_data[identifier]['z'].append(z)
                    masses_data[identifier]['roll'].append(roll)
                    masses_data[identifier]['pitch'].append(pitch)
                    masses_data[identifier]['yaw'].append(yaw)
                elif identifier == GND_IDENTIFIER:
                    gnd_data['time'].append(t)
                    gnd_data['x'].append(x)
                    gnd_data['y'].append(y)
                    gnd_data['z'].append(z)
                    gnd_data['roll'].append(roll)
                    gnd_data['pitch'].append(pitch)
                    gnd_data['yaw'].append(yaw)
        
    except json.JSONDecodeError:
        print(f"Error al parsear JSON del mensaje")
    except Exception as e:
        print(f"Error procesando mensaje: {e}")

def on_publish(client, userdata, mid):
    """Callback cuando se publica un mensaje"""
    print(f"Mensaje publicado (ID: {mid})")

def make_transform_matrix(x, y, z, roll, pitch, yaw):
    """
    Crea matriz de transformación homogénea 4x4
    T = transl * Rx * Ry * Rz (orden XYZ de Euler)
    """
    # Matriz de rotación alrededor de X (roll)
    Rx = np.array([
        [1, 0, 0],
        [0, np.cos(roll), -np.sin(roll)],
        [0, np.sin(roll), np.cos(roll)]
    ])
    
    # Matriz de rotación alrededor de Y (pitch)
    Ry = np.array([
        [np.cos(pitch), 0, np.sin(pitch)],
        [0, 1, 0],
        [-np.sin(pitch), 0, np.cos(pitch)]
    ])
    
    # Matriz de rotación alrededor de Z (yaw)
    Rz = np.array([
        [np.cos(yaw), -np.sin(yaw), 0],
        [np.sin(yaw), np.cos(yaw), 0],
        [0, 0, 1]
    ])
    
    # Rotación compuesta: R = Rz * Ry * Rx
    R = Rz @ Ry @ Rx
    
    # Matriz de transformación homogénea
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = [x, y, z]
    
    return T

def process_mass_relative(mass_id, gnd_times, gnd_x, gnd_y, gnd_z, gnd_roll, gnd_pitch, gnd_yaw):
    """
    Procesa una masa en coordenadas relativas a GND usando HTMs
    """
    with data_lock:
        mass_times = list(masses_data[mass_id]['time'])
        mass_x = list(masses_data[mass_id]['x'])
        mass_y = list(masses_data[mass_id]['y'])
        mass_z = list(masses_data[mass_id]['z'])
        mass_roll = list(masses_data[mass_id]['roll'])
        mass_pitch = list(masses_data[mass_id]['pitch'])
        mass_yaw = list(masses_data[mass_id]['yaw'])
    
    if len(mass_times) == 0:
        return [], [], []
    
    # Interpolar datos de GND para alinear con tiempos de la masa
    gnd_x_interp = np.interp(mass_times, gnd_times, gnd_x)
    gnd_y_interp = np.interp(mass_times, gnd_times, gnd_y)
    gnd_z_interp = np.interp(mass_times, gnd_times, gnd_z)
    gnd_roll_interp = np.interp(mass_times, gnd_times, gnd_roll)
    gnd_pitch_interp = np.interp(mass_times, gnd_times, gnd_pitch)
    gnd_yaw_interp = np.interp(mass_times, gnd_times, gnd_yaw)
    
    # Arrays para coordenadas relativas
    rel_x = []
    rel_y = []
    rel_z = []
    
    # Procesar cada punto en el tiempo
    for i in range(len(mass_times)):
        # Verificar datos válidos de GND
        if (np.isnan(gnd_x_interp[i]) or np.isnan(gnd_y_interp[i]) or 
            np.isnan(gnd_z_interp[i]) or np.isnan(gnd_roll_interp[i]) or 
            np.isnan(gnd_pitch_interp[i]) or np.isnan(gnd_yaw_interp[i])):
            rel_x.append(np.nan)
            rel_y.append(np.nan)
            rel_z.append(np.nan)
            continue
        
        # Verificar datos válidos de la masa
        if (np.isnan(mass_x[i]) or np.isnan(mass_y[i]) or np.isnan(mass_z[i])):
            rel_x.append(np.nan)
            rel_y.append(np.nan)
            rel_z.append(np.nan)
            continue
        
        # T_GND: transformación del marco GND en el marco global (Robotat)
        T_gnd = make_transform_matrix(
            gnd_x_interp[i], gnd_y_interp[i], gnd_z_interp[i],
            gnd_roll_interp[i], gnd_pitch_interp[i], gnd_yaw_interp[i]
        )
        
        # T_Mass: transformación de la masa en el marco global (Robotat)
        T_mass = make_transform_matrix(
            mass_x[i], mass_y[i], mass_z[i],
            mass_roll[i], mass_pitch[i], mass_yaw[i]
        )
        
        # T_rel = inv(T_GND) * T_Mass
        T_rel = np.linalg.inv(T_gnd) @ T_mass
        
        # Extraer posición relativa (invertir X como en MATLAB)
        rel_x.append(-T_rel[0, 3])
        rel_y.append(T_rel[1, 3])
        rel_z.append(T_rel[2, 3])
    
    return rel_x, rel_y, rel_z

def process_all_masses():
    """
    Procesa todas las masas seleccionadas (absolutas o relativas)
    Retorna un diccionario con los datos procesados
    """
    selected_masses = AVAILABLE_MASSES[:num_masses]
    
    # Encontrar el vector de tiempo común (usar la primera masa con datos)
    common_times = None
    with data_lock:
        for mass_id in selected_masses:
            if len(masses_data[mass_id]['time']) > 0:
                common_times = list(masses_data[mass_id]['time'])
                break
    
    if common_times is None:
        print("No hay datos para procesar")
        return None
    
    result = {'Tiempo_s': common_times}
    
    # ===== MODO ABSOLUTO =====
    if not use_relative:
        print(f"Procesando {len(selected_masses)} masa(s) en modo ABSOLUTO...")
        with data_lock:
            for i, mass_id in enumerate(selected_masses):
                mass_num = i + 1
                if len(masses_data[mass_id]['time']) > 0:
                    # Interpolar para alinear con common_times
                    x_interp = np.interp(common_times, 
                                        list(masses_data[mass_id]['time']),
                                        list(masses_data[mass_id]['x']))
                    y_interp = np.interp(common_times,
                                        list(masses_data[mass_id]['time']),
                                        list(masses_data[mass_id]['y']))
                    z_interp = np.interp(common_times,
                                        list(masses_data[mass_id]['time']),
                                        list(masses_data[mass_id]['z']))
                    
                    result[f'M{mass_num}_X'] = x_interp
                    result[f'M{mass_num}_Y'] = y_interp
                    result[f'M{mass_num}_Z'] = z_interp
                else:
                    result[f'M{mass_num}_X'] = [np.nan] * len(common_times)
                    result[f'M{mass_num}_Y'] = [np.nan] * len(common_times)
                    result[f'M{mass_num}_Z'] = [np.nan] * len(common_times)
        
        return result
    
    # ===== MODO RELATIVO =====
    with data_lock:
        gnd_times = list(gnd_data['time'])
        gnd_x = list(gnd_data['x'])
        gnd_y = list(gnd_data['y'])
        gnd_z = list(gnd_data['z'])
        gnd_roll = list(gnd_data['roll'])
        gnd_pitch = list(gnd_data['pitch'])
        gnd_yaw = list(gnd_data['yaw'])
    
    if len(gnd_times) == 0:
        print("Advertencia: No hay datos de GND. Guardando en modo ABSOLUTO.")
        # Recursivamente llamar en modo absoluto
        temp_use_relative = use_relative
        globals()['use_relative'] = False
        result = process_all_masses()
        globals()['use_relative'] = temp_use_relative
        return result
    
    print(f"Procesando {len(selected_masses)} masa(s) en modo RELATIVO a GND...")
    
    for i, mass_id in enumerate(selected_masses):
        mass_num = i + 1
        rel_x, rel_y, rel_z = process_mass_relative(
            mass_id, gnd_times, gnd_x, gnd_y, gnd_z, gnd_roll, gnd_pitch, gnd_yaw
        )
        
        if len(rel_x) > 0:
            # Interpolar para alinear con common_times
            mass_times_local = list(masses_data[mass_id]['time'])
            x_interp = np.interp(common_times, mass_times_local, rel_x)
            y_interp = np.interp(common_times, mass_times_local, rel_y)
            z_interp = np.interp(common_times, mass_times_local, rel_z)
            
            result[f'M{mass_num}_X'] = x_interp
            result[f'M{mass_num}_Y'] = y_interp
            result[f'M{mass_num}_Z'] = z_interp
        else:
            result[f'M{mass_num}_X'] = [np.nan] * len(common_times)
            result[f'M{mass_num}_Y'] = [np.nan] * len(common_times)
            result[f'M{mass_num}_Z'] = [np.nan] * len(common_times)
    
    return result

def select_save_location():
    """
    Abre un diálogo para seleccionar ubicación y nombre del archivo
    Retorna la ruta completa o None si se cancela
    """
    try:
        # Crear ventana temporal oculta para el diálogo
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        
        # Generar nombre sugerido
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        coord_type = "rel_GND" if use_relative else "abs"
        default_name = f"robotat_{num_masses}masas_{coord_type}_{timestamp}.xlsx"
        
        # Abrir diálogo de guardado
        file_path = filedialog.asksaveasfilename(
            title="Guardar datos como...",
            defaultextension=".xlsx",
            initialfile=default_name,
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")]
        )
        
        root.destroy()
        
        # Si el usuario canceló, retornar None
        if not file_path:
            return None
        
        return file_path
        
    except Exception as e:
        print(f"Error al abrir diálogo de guardado: {e}")
        # Si falla el diálogo, usar nombre por defecto
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        coord_type = "rel_GND" if use_relative else "abs"
        return f"robotat_{num_masses}masas_{coord_type}_{timestamp}.xlsx"

def save_to_xlsx():
    """Guarda los datos procesados en un archivo .xlsx con selección de ubicación"""
    global xlsx_filename
    
    try:
        # Procesar todas las masas
        processed_data = process_all_masses()
        
        if processed_data is None or len(processed_data['Tiempo_s']) == 0:
            print("No hay datos para guardar")
            return
        
        # Abrir diálogo para seleccionar ubicación y nombre
        print("\nAbriendo diálogo para guardar archivo...")
        xlsx_filename = select_save_location()
        
        # Si el usuario canceló, no guardar
        if xlsx_filename is None:
            print("Guardado cancelado por el usuario.")
            return
        
        # Crear DataFrame
        df = pd.DataFrame(processed_data)
        
        # Guardar en Excel
        df.to_excel(xlsx_filename, index=False, engine='openpyxl')
        
        # Mostrar información
        num_points = len(processed_data['Tiempo_s'])
        duration = processed_data['Tiempo_s'][-1] if num_points > 0 else 0
        
        print(f"\nDatos guardados exitosamente!")
        print(f"  Archivo: {xlsx_filename}")
        print(f"  Masas: {num_masses} ({', '.join(AVAILABLE_MASSES[:num_masses])})")
        print(f"  Puntos registrados: {num_points}")
        print(f"  Duración: {duration:.2f} segundos")
        if num_points > 0 and duration > 0:
            print(f"  Frecuencia promedio: {num_points/duration:.2f} Hz")
        print(f"  Modo: {'RELATIVO a GND' if use_relative else 'ABSOLUTO'}")
        
    except Exception as e:
        print(f"Error al guardar XLSX: {e}")
        import traceback
        traceback.print_exc()

def record_data():
    """Función para grabar datos en tiempo real"""
    global is_recording, start_time
    
    selected_masses = AVAILABLE_MASSES[:num_masses]
    
    print(f"\nIniciando grabación de datos...")
    print(f"  Masas: {', '.join(selected_masses)}")
    print(f"  GND: {GND_IDENTIFIER}")
    print(f"  Modo: {'RELATIVO a GND' if use_relative else 'ABSOLUTO'}")
    print("Presiona '2' para detener la grabación")
    
    # Limpiar datos anteriores
    with data_lock:
        for mass_id in AVAILABLE_MASSES:
            masses_data[mass_id]['time'].clear()
            masses_data[mass_id]['x'].clear()
            masses_data[mass_id]['y'].clear()
            masses_data[mass_id]['z'].clear()
            masses_data[mass_id]['roll'].clear()
            masses_data[mass_id]['pitch'].clear()
            masses_data[mass_id]['yaw'].clear()
        
        gnd_data['time'].clear()
        gnd_data['x'].clear()
        gnd_data['y'].clear()
        gnd_data['z'].clear()
        gnd_data['roll'].clear()
        gnd_data['pitch'].clear()
        gnd_data['yaw'].clear()
    
    start_time = None
    last_counts = [0] * num_masses
    last_gnd_count = 0
    
    try:
        while is_recording:
            with data_lock:
                counts = [len(masses_data[mid]['time']) for mid in selected_masses]
                gnd_count = len(gnd_data['time'])
                
                # Mostrar progreso cada 100 puntos de la primera masa
                if counts[0] > last_counts[0] and counts[0] % 100 == 0:
                    if len(masses_data[selected_masses[0]]['time']) > 0:
                        duration = masses_data[selected_masses[0]]['time'][-1]
                        freq = counts[0] / duration if duration > 0 else 0
                        counts_str = '/'.join(map(str, counts))
                        print(f"Grabando... [{counts_str}] | GND:{gnd_count} | T:{duration:.2f}s | Freq:{freq:.2f}Hz")
                    last_counts = counts.copy()
                    last_gnd_count = gnd_count
            
            time.sleep(0.1)
        
    except Exception as e:
        print(f"Error durante la grabación: {e}")
    finally:
        is_recording = False
        print("\nGrabación detenida. Procesando datos...")
        save_to_xlsx()
        print_menu()

def select_num_masses():
    """Permite seleccionar el número de masas"""
    global num_masses
    try:
        print("\n--- Selección de número de masas ---")
        print("Masas disponibles: 76, 77, 78, 79")
        choice = input("Ingresa el número de masas a trackear (1-4): ").strip()
        n = int(choice)
        if 1 <= n <= 4:
            num_masses = n
            print(f"Configurado: {num_masses} masa(s) - {', '.join(AVAILABLE_MASSES[:num_masses])}")
        else:
            print("Número inválido. Debe ser entre 1 y 4.")
    except ValueError:
        print("Entrada inválida. Usa un número del 1 al 4.")
    print_menu()

def select_coordinate_type():
    """Permite seleccionar el tipo de coordenadas"""
    global use_relative
    print("\n--- Selección de tipo de coordenadas ---")
    print("1. Relativas a GND (transformaciones homogéneas)")
    print("2. Absolutas (sin transformación)")
    choice = input("Selecciona (1 o 2): ").strip()
    
    if choice == '1':
        use_relative = True
        print("Configurado: Coordenadas RELATIVAS a GND")
    elif choice == '2':
        use_relative = False
        print("Configurado: Coordenadas ABSOLUTAS")
    else:
        print("Opción inválida.")
    print_menu()

def print_menu():
    """Imprime el menú de opciones"""
    print("\n" + "="*50)
    print("MQTT TRACKER - MULTI MASA")
    print("="*50)
    print(f"Configuración actual:")
    print(f"  Masas: {num_masses} ({', '.join(AVAILABLE_MASSES[:num_masses])})")
    print(f"  Modo: {'RELATIVO a GND' if use_relative else 'ABSOLUTO'}")
    print("-"*50)
    print("Comandos:")
    print("  1 - Iniciar grabación")
    print("  2 - Detener grabación")
    print("  3 - Seleccionar número de masas (1-4)")
    print("  4 - Seleccionar tipo de coordenadas")
    print("  Ctrl+C - Salir")
    print("="*50)

def user_input_thread():
    """Hilo para leer entrada del usuario"""
    global is_recording
    while True:
        try:
            user_input = input()
            if user_input.strip() == '1':
                if not is_recording:
                    is_recording = True
                    record_thread = threading.Thread(target=record_data, daemon=True)
                    record_thread.start()
                else:
                    print("Ya hay una grabación en progreso")
            elif user_input.strip() == '2':
                if is_recording:
                    is_recording = False
                    print("Deteniendo grabación...")
                else:
                    print("No hay grabación en progreso")
            elif user_input.strip() == '3':
                if not is_recording:
                    select_num_masses()
                else:
                    print("No se puede cambiar configuración durante la grabación")
            elif user_input.strip() == '4':
                if not is_recording:
                    select_coordinate_type()
                else:
                    print("No se puede cambiar configuración durante la grabación")
            else:
                if user_input.strip() != '':
                    print("Comando no reconocido")
        except EOFError:
            break
        except Exception as e:
            print(f"Error: {e}")

# Crear cliente MQTT
client = mqtt.Client(client_id="python_mqtt_tracker_multi")

# Asignar callbacks
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message
client.on_publish = on_publish

try:
    # Conectar al broker
    print("Conectando al broker MQTT...")
    client.connect(BROKER_ADDRESS, PORT, KEEP_ALIVE)
    
    # Iniciar el loop en segundo plano
    client.loop_start()
    
    # Iniciar hilo para leer entrada del usuario
    input_thread = threading.Thread(target=user_input_thread, daemon=True)
    input_thread.start()
    
    # Mostrar menú inicial
    print("\nSistema listo!")
    print_menu()
    
    while True:
        time.sleep(1)
        
except KeyboardInterrupt:
    print("\n\nCerrando conexión...")
    client.loop_stop()
    client.disconnect()
    print("Programa terminado")
    
except Exception as e:
    print(f"Error: {e}")
    client.loop_stop()
    client.disconnect()
