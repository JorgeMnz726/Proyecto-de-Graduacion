''' 
App MQTT para grabar datos de posiciones y orientaciones de masas mecánicas
Diseñada para trabajar con sistema de captura de movimientos y broker MQTT
Parte de trabajo de graduación para grado de Licenciatura en Ingeniería Mecatrónica

Jorge Muñiz - Noviembre de 2025
'''

import paho.mqtt.client as mqtt
import time
import json
import threading
from collections import deque
from datetime import datetime
import pandas as pd
import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
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

# Variables globales para almacenar datos sin conflicto de threading
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

# Datos de GND (80)
gnd_data = {
    'time': deque(maxlen=10000),
    'x': deque(maxlen=10000),
    'y': deque(maxlen=10000),
    'z': deque(maxlen=10000),
    'roll': deque(maxlen=10000),
    'pitch': deque(maxlen=10000),
    'yaw': deque(maxlen=10000)
}

# Estado de la grabación
is_recording = False
start_time = None
xlsx_filename = None
is_connected = False

# GUI elements
root = None
status_label = None
recording_btn = None
connect_btn = None
config_frame = None
log_text = None
masses_var = None
coord_var = None
masses_combo = None
coord_combo = None

def log_message(message):
    """Añade un mensaje al log de la GUI"""
    if log_text:
        log_text.insert(tk.END, message + "\n")
        log_text.see(tk.END)
    else:
        print(message)

# Callbacks
def on_connect(client, userdata, flags, rc):
    """Callback cuando el cliente se conecta al broker"""
    global is_connected
    if rc == 0:
        log_message("Conexión exitosa al broker MQTT")
        log_message(f"  Broker: tcp://{BROKER_ADDRESS}")
        log_message(f"  Puerto: {PORT}")
        log_message(f"  Keep Alive: {KEEP_ALIVE} segundos")
        
        # Suscribirse al topic mocap/mechanical
        client.subscribe("mocap/mechanical")
        log_message("Suscrito al topic: mocap/mechanical")
        is_connected = True
        update_status("Conectado - Listo", "green")
        if connect_btn:
            connect_btn.config(text="🔗 Desconectar", bg="#ff8844")
    else:
        log_message(f"Error de conexión. Código: {rc}")
        is_connected = False
        update_status("Error de conexión", "red")
        if connect_btn:
            connect_btn.config(text="🔗 Conectar", bg="#4488ff")

def on_disconnect(client, userdata, rc):
    """Callback cuando se desconecta"""
    global is_connected
    is_connected = False
    if rc != 0:
        log_message("Desconexión inesperada del broker")
        update_status("Desconectado", "red")
    else:
        log_message("Desconectado del broker")
        update_status("Desconectado", "orange")
    if connect_btn:
        connect_btn.config(text="🔗 Conectar", bg="#4488ff")

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
        log_message(f"Error al parsear JSON del mensaje")
    except Exception as e:
        log_message(f"Error procesando mensaje: {e}")

def on_publish(client, userdata, mid):
    """Callback cuando se publica un mensaje"""
    log_message(f"Mensaje publicado (ID: {mid})")

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
        log_message("No hay datos para procesar")
        return None
    
    result = {'Tiempo_s': common_times}
    
    # ===== MODO ABSOLUTO =====
    if not use_relative:
        log_message(f"Procesando {len(selected_masses)} masa(s) en modo ABSOLUTO...")
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
        log_message("Advertencia: No hay datos de GND. Guardando en modo ABSOLUTO.")
        # Recursivamente llamar en modo absoluto
        temp_use_relative = use_relative
        globals()['use_relative'] = False
        result = process_all_masses()
        globals()['use_relative'] = temp_use_relative
        return result
    
    log_message(f"Procesando {len(selected_masses)} masa(s) en modo RELATIVO a GND...")
    
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
        log_message(f"Error al abrir diálogo de guardado: {e}")
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
            log_message("No hay datos para guardar")
            return
        
        # Abrir diálogo para seleccionar ubicación y nombre
        log_message("\nAbriendo diálogo para guardar archivo...")
        xlsx_filename = select_save_location()
        
        # Si el usuario canceló, no guardar
        if xlsx_filename is None:
            log_message("Guardado cancelado por el usuario.")
            return
        
        # Crear DataFrame
        df = pd.DataFrame(processed_data)
        
        # Guardar en Excel
        df.to_excel(xlsx_filename, index=False, engine='openpyxl')
        
        # Mostrar información
        num_points = len(processed_data['Tiempo_s'])
        duration = processed_data['Tiempo_s'][-1] if num_points > 0 else 0
        
        log_message(f"\nDatos guardados exitosamente!")
        log_message(f"  Archivo: {xlsx_filename}")
        log_message(f"  Masas: {num_masses} ({', '.join(AVAILABLE_MASSES[:num_masses])})")
        log_message(f"  Puntos registrados: {num_points}")
        log_message(f"  Duración: {duration:.2f} segundos")
        if num_points > 0 and duration > 0:
            log_message(f"  Frecuencia promedio: {num_points/duration:.2f} Hz")
        log_message(f"  Modo: {'RELATIVO a GND' if use_relative else 'ABSOLUTO'}")
        
    except Exception as e:
        log_message(f"Error al guardar XLSX: {e}")
        import traceback
        traceback.print_exc()

def record_data():
    """Función para grabar datos en tiempo real"""
    global start_time, is_recording
    
    selected_masses = AVAILABLE_MASSES[:num_masses]
    
    log_message(f"\nIniciando grabación de datos...")
    log_message(f"  Masas: {', '.join(selected_masses)}")
    log_message(f"  GND: {GND_IDENTIFIER}")
    log_message(f"  Modo: {'RELATIVO a GND' if use_relative else 'ABSOLUTO'}")
    update_status("Grabando...", "red")
    
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
    
    

    #! Pochi mapache estuvo aquí 
    # ?    /\_/\ 
    # ?   (=ºº=)       Machape
    # ?   /|   |\ |
    # ?   \|___|/-'
    # ~  IG@pochi_mapache 


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
                        log_message(f"Grabando... [{counts_str}] | GND:{gnd_count} | T:{duration:.2f}s | Freq:{freq:.2f}Hz")
                    last_counts = counts.copy()
                    last_gnd_count = gnd_count
            
            time.sleep(0.1)
        
        log_message("Saliendo del bucle de grabación...")
        
    except Exception as e:
        log_message(f"Error durante la grabación: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Asegurarse de que is_recording esté en False
        is_recording = False
        
        log_message("\nGrabación detenida. Procesando datos...")
        save_to_xlsx()
        
        # Restaurar UI
        update_status("Conectado - Listo", "green")
        if recording_btn:
            recording_btn.config(text="⬤ Iniciar Grabación", bg="#44ff44")
        if masses_combo:
            masses_combo.config(state='readonly')
        if coord_combo:
            coord_combo.config(state='readonly')
        if connect_btn:
            connect_btn.config(state='normal')

def update_status(text, color):
    """Actualiza el estado en la GUI"""
    if status_label:
        status_label.config(text=f"Estado: {text}", foreground=color)

def toggle_recording():
    """Inicia o detiene la grabación"""
    global is_recording
    
    if not is_connected:
        log_message("Error: Debes conectarte al broker MQTT primero")
        return
    
    if not is_recording:
        is_recording = True
        recording_btn.config(text="⬛ Detener Grabación", bg="#ff4444")
        # Deshabilitar controles de configuración
        if masses_combo:
            masses_combo.config(state='disabled')
        if coord_combo:
            coord_combo.config(state='disabled')
        if connect_btn:
            connect_btn.config(state='disabled')
        record_thread = threading.Thread(target=record_data, daemon=True)
        record_thread.start()
    else:
        is_recording = False
        log_message("Deteniendo grabación...")

def update_num_masses():
    """Actualiza el número de masas desde el combobox"""
    global num_masses
    num_masses = int(masses_var.get())
    log_message(f"Configurado: {num_masses} masa(s) - {', '.join(AVAILABLE_MASSES[:num_masses])}")

def update_coord_type():
    """Actualiza el tipo de coordenadas desde el combobox"""
    global use_relative
    use_relative = (coord_var.get() == "Relativo a GND")
    log_message(f"Configurado: Coordenadas {'RELATIVAS a GND' if use_relative else 'ABSOLUTAS'}")

def toggle_connection():
    """Conecta o desconecta del broker MQTT"""
    global is_connected
    
    if not is_connected:
        # Intentar conectar
        connect_btn.config(text="⏳ Conectando...", state='disabled', bg="#ffaa44")
        update_status("Conectando...", "yellow")
        log_message(f"Conectando al broker MQTT en {BROKER_ADDRESS}:{PORT}...")
        
        def connect_thread():
            try:
                client.connect(BROKER_ADDRESS, PORT, KEEP_ALIVE)
                client.loop_start()
            except Exception as e:
                log_message(f"Error al conectar: {e}")
                update_status("Error de conexión", "red")
                if connect_btn:
                    connect_btn.config(text="🔗 Conectar", state='normal', bg="#4488ff")
        
        threading.Thread(target=connect_thread, daemon=True).start()
    else:
        # Desconectar
        if is_recording:
            log_message("No se puede desconectar durante una grabación")
            return
        
        log_message("Desconectando del broker...")
        try:
            client.loop_stop()
            client.disconnect()
        except Exception as e:
            log_message(f"Error al desconectar: {e}")
        
        is_connected = False
        update_status("Desconectado", "orange")
        connect_btn.config(text="🔗 Conectar", bg="#4488ff")

def create_gui():
    """Crea la interfaz gráfica"""
    global root, status_label, recording_btn, connect_btn, config_frame, log_text, masses_var, coord_var, masses_combo, coord_combo
    
    root = tk.Tk()
    root.title("MQTT Tracker - Multi Masa")
    root.geometry("700x600")
    root.resizable(True, True)
    
    # Frame superior - Título y estado
    header_frame = tk.Frame(root, bg="#2c3e50", pady=10)
    header_frame.pack(fill=tk.X)
    
    title_label = tk.Label(header_frame, text="MQTT TRACKER - MULTI MASA", 
                          font=("Arial", 16, "bold"), bg="#2c3e50", fg="white")
    title_label.pack()
    
    status_label = tk.Label(header_frame, text="Estado: Desconectado", 
                           font=("Arial", 10), bg="#2c3e50", fg="orange")
    status_label.pack()
    
    # Frame de configuración
    config_outer_frame = tk.LabelFrame(root, text="⚙ Configuración", 
                                       font=("Arial", 11, "bold"), padx=10, pady=10)
    config_outer_frame.pack(fill=tk.X, padx=10, pady=10)
    
    config_frame = tk.Frame(config_outer_frame)
    config_frame.pack()
    
    # Selector de número de masas
    masses_frame = tk.Frame(config_frame)
    masses_frame.grid(row=0, column=0, padx=10, pady=5, sticky=tk.W)
    
    tk.Label(masses_frame, text="Número de masas:", font=("Arial", 10)).pack(side=tk.LEFT, padx=5)
    masses_var = tk.StringVar(value=str(num_masses))
    masses_combo = ttk.Combobox(masses_frame, textvariable=masses_var, 
                               values=["1", "2", "3", "4"], state="readonly", width=10)
    masses_combo.pack(side=tk.LEFT, padx=5)
    masses_combo.bind("<<ComboboxSelected>>", lambda e: update_num_masses())
    
    tk.Label(masses_frame, text="(76, 77, 78, 79)", font=("Arial", 8), fg="gray").pack(side=tk.LEFT)
    
    # Selector de tipo de coordenadas
    coord_frame = tk.Frame(config_frame)
    coord_frame.grid(row=1, column=0, padx=10, pady=5, sticky=tk.W)
    
    tk.Label(coord_frame, text="Tipo de coordenadas:", font=("Arial", 10)).pack(side=tk.LEFT, padx=5)
    coord_var = tk.StringVar(value="Relativo a GND" if use_relative else "Absoluto")
    coord_combo = ttk.Combobox(coord_frame, textvariable=coord_var, 
                              values=["Relativo a GND", "Absoluto"], state="readonly", width=15)
    coord_combo.pack(side=tk.LEFT, padx=5)
    coord_combo.bind("<<ComboboxSelected>>", lambda e: update_coord_type())
    
    # Frame de control
    control_frame = tk.Frame(root, pady=10)
    control_frame.pack(fill=tk.X, padx=10)
    
    # Botón de conexión
    connect_btn = tk.Button(control_frame, text="🔗 Conectar", 
                           font=("Arial", 11, "bold"), bg="#4488ff", fg="white",
                           command=toggle_connection, height=1)
    connect_btn.pack(fill=tk.X, padx=20, pady=(0, 10))
    
    # Botón de grabación
    recording_btn = tk.Button(control_frame, text="⬤ Iniciar Grabación", 
                             font=("Arial", 12, "bold"), bg="#44ff44", fg="black",
                             command=toggle_recording, height=2)
    recording_btn.pack(fill=tk.X, padx=20)
    
    # Frame de log
    log_frame = tk.LabelFrame(root, text="📋 Log de eventos", 
                             font=("Arial", 11, "bold"), padx=10, pady=10)
    log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    
    log_text = scrolledtext.ScrolledText(log_frame, height=15, font=("Consolas", 9),
                                        bg="#1e1e1e", fg="#00ff00")
    log_text.pack(fill=tk.BOTH, expand=True)
    
    # Info footer
    info_frame = tk.Frame(root, bg="#ecf0f1", pady=5)
    info_frame.pack(fill=tk.X)
    
    info_label = tk.Label(info_frame, text=f"Broker: {BROKER_ADDRESS}:{PORT} | GND ID: {GND_IDENTIFIER}", 
                         font=("Arial", 8), bg="#ecf0f1", fg="#34495e")
    info_label.pack()
    
    log_message("Sistema iniciado. Presiona 'Conectar' para comenzar.")
    
    return root

def run_gui():
    """Ejecuta el bucle principal de la GUI"""
    root.mainloop()

def on_closing():
    """Maneja el cierre de la aplicación"""
    global is_recording, is_connected
    if is_recording:
        is_recording = False
        time.sleep(0.5)
    
    if is_connected:
        log_message("\nCerrando conexión...")
        client.loop_stop()
        client.disconnect()
        time.sleep(0.3)
    
    root.destroy()
    log_message("Programa terminado")

# Crear cliente MQTT
client = mqtt.Client(client_id="python_mqtt_tracker_multi", callback_api_version=mqtt.CallbackAPIVersion.VERSION1)

# Asignar callbacks
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message
client.on_publish = on_publish

try:
    # Crear GUI
    root = create_gui()
    
    # Configurar el cierre de ventana
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    # Ejecutar GUI (no conectar automáticamente)
    run_gui()
    
except KeyboardInterrupt:
    if is_connected:
        log_message("\n\nCerrando conexión...")
        client.loop_stop()
        client.disconnect()
    log_message("Programa terminado")
    
except Exception as e:
    log_message(f"Error: {e}")
    if is_connected:
        client.loop_stop()
        client.disconnect()
