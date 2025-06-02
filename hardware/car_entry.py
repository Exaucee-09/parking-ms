import platform
import cv2
from ultralytics import YOLO
import pytesseract
import os
import time
import serial
import sqlite3
from collections import Counter
from datetime import datetime

# Load YOLOv8 model
model = YOLO('../model_dev/runs/detect/train/weights/best.pt')

# Configurations
SAVE_DIR = 'plates'
DB_FILE = 'parking.db'
ENTRY_COOLDOWN = 300  # seconds
MAX_DISTANCE = 20     # cm
MIN_DISTANCE = 0      # cm
CAPTURE_THRESHOLD = 6 # number of consistent reads before logging
GATE_OPEN_TIME = 10   # seconds

# Ensure plates directory exists
os.makedirs(SAVE_DIR, exist_ok=True)

# SQLite connection (single instance)
conn = sqlite3.connect(DB_FILE)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Log violation to violations table
def log_violation(plate_number, gate_location, reason):
    cursor.execute('''
        INSERT INTO violations (timestamp, car_plate, gate_location, reason)
        VALUES (?, ?, ?, ?)
    ''', (
        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        plate_number,
        gate_location,
        reason
    ))
    conn.commit()
    print(f"[LOGGED] Violation for {plate_number} at {gate_location}: {reason}")

# Check for existing unpaid entry in database
def has_unpaid_record(plate):
    cursor.execute('SELECT 1 FROM entries WHERE car_plate = ? AND payment_status = 0', (plate,))
    return cursor.fetchone() is not None

# Get next entry number
def get_next_entry_no():
    cursor.execute('SELECT COALESCE(MAX(no), 0) + 1 AS next_no FROM entries')
    return cursor.fetchone()['next_no']

# Auto-detect Arduino Serial Port
def detect_arduino_port():
    for port in serial.tools.list_ports.comports():
        dev = port.device
        if platform.system() == 'Linux' and 'ttyACM' in dev:
            return dev
        if platform.system() == 'Darwin' and ('usbmodem' in dev or 'usbserial' in dev):
            return dev
        if platform.system() == 'Windows' and 'COM' in dev:
            return dev
    return None

# Read distance from Arduino (returns float or None)
def read_distance(arduino):
    if not arduino or arduino.in_waiting == 0:
        return None
    try:
        val = arduino.readline().decode('utf-8').strip()
        return float(val)
    except (UnicodeDecodeError, ValueError):
        return None

# Wait for Arduino response
def wait_for_arduino_response(arduino, expected, timeout=2.0):
    start_time = time.time()
    while time.time() - start_time < timeout:
        if arduino.in_waiting:
            response = arduino.readline().decode('utf-8').strip()
            if expected in response:
                return True
        time.sleep(0.01)
    return False

# Initialize Arduino
arduino_port = detect_arduino_port()
arduino = None
if arduino_port:
    print(f"[CONNECTED] Arduino on {arduino_port}")
    arduino = serial.Serial(arduino_port, 115200, timeout=1)  # Increased baud rate
    time.sleep(2)
    arduino.flush()  # Clear serial buffer
else:
    print("[ERROR] Arduino not detected.")

# Initialize Webcam and Windows
cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("[ERROR] Cannot open camera.")
    exit(1)
cv2.namedWindow('Webcam Feed', cv2.WINDOW_NORMAL)
cv2.namedWindow('Plate', cv2.WINDOW_NORMAL)
cv2.namedWindow('Processed', cv2.WINDOW_NORMAL)
cv2.resizeWindow('Webcam Feed', 800, 600)

# State variables
plate_buffer = []
last_saved_plate = None
last_entry_time = 0
gate_open_until = 0
gate_is_open = False

print("[SYSTEM] Ready. Press 'q' to exit.")

try:
    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] Frame capture failed.")
            break

        # Get distance reading, default to safe value
        distance = read_distance(arduino) or (MAX_DISTANCE - 1)
        annotated = frame.copy()

        # Handle gate closing
        current_time = time.time()
        if gate_is_open and current_time >= gate_open_until:
            if arduino:
                arduino.flush()
                arduino.write(b'0')
                if wait_for_arduino_response(arduino, "[GATE] Closed"):
                    print("[GATE] Closing gate (sent '0')")
                gate_is_open = False

        if MIN_DISTANCE <= distance <= MAX_DISTANCE:
            results = model(frame)[0]
            annotated = results.plot()

            for box in results.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                plate_img = frame[y1:y2, x1:x2]

                # OCR preprocess
                gray = cv2.cvtColor(plate_img, cv2.COLOR_BGR2GRAY)
                blur = cv2.GaussianBlur(gray, (5,5), 0)
                thresh = cv2.threshold(blur, 0, 255,
                                       cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

                text = pytesseract.image_to_string(
                    thresh,
                    config='--psm 8 --oem 3 '
                           '-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
                ).strip().replace(' ', '')

                # Validate Rwandan format RAxxxA
                if text.startswith('RA') and len(text) >= 7:
                    plate = text[:7]
                    pr, dg, su = plate[:3], plate[3:6], plate[6]
                    if pr.isalpha() and dg.isdigit() and su.isalpha():
                        plate_buffer.append(plate)

                # Once buffer is full, decide
                if len(plate_buffer) >= CAPTURE_THRESHOLD and not gate_is_open:
                    common = Counter(plate_buffer).most_common(1)[0][0]
                    now = time.time()

                    # Check for unpaid record
                    if has_unpaid_record(common):
                        print(f"[ACCESS DENIED] Unpaid record exists for {common}")
                        log_violation(common, "Entry", "Unpaid entry attempt")
                    else:
                        # Apply cooldown logic
                        if common != last_saved_plate or (now - last_entry_time) > ENTRY_COOLDOWN:
                            entry_count = get_next_entry_no()
                            cursor.execute('''
                                INSERT INTO entries (no, entry_time, exit_time, car_plate, due_payment, payment_status)
                                VALUES (?, ?, ?, ?, ?, ?)
                            ''', (
                                entry_count,
                                time.strftime('%Y-%m-%d %H:%M:%S'),
                                '',
                                common,
                                None,
                                0
                            ))
                            conn.commit()
                            print(f"[NEW] Logged plate {common}")

                            # Gate actuation
                            if arduino:
                                arduino.flush()
                                arduino.write(b'1')
                                if wait_for_arduino_response(arduino, "[GATE] Opened"):
                                    print("[GATE] Opening gate (sent '1')")
                                gate_open_until = time.time() + GATE_OPEN_TIME
                                gate_is_open = True

                            last_saved_plate = common
                            last_entry_time = now
                        else:
                            print(f"[SKIPPED] Cooldown: {common}")

                    plate_buffer.clear()

                # Show previews
                cv2.imshow('Plate', plate_img)
                cv2.imshow('Processed', thresh)

        # Display feed
        cv2.imshow('Webcam Feed', annotated)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
finally:
    cap.release()
    if arduino:
        if gate_is_open:
            arduino.write(b'0')
            time.sleep(0.1)
        arduino.close()
    conn.close()
    cv2.destroyAllWindows()