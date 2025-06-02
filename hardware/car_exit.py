import platform
import cv2
from ultralytics import YOLO
import pytesseract
import os
import time
import serial
import serial.tools.list_ports
import sqlite3
from collections import Counter
from datetime import datetime

# Load YOLOv8 model
model = YOLO('../model_dev/runs/detect/train/weights/best.pt')

# Configurations
DB_FILE = 'parking.db'
MAX_DISTANCE = 20     # cm
MIN_DISTANCE = 0      # cm
CAPTURE_THRESHOLD = 6 # number of consistent reads
GATE_OPEN_TIME = 10   # seconds
BUZZER_ON_TIME = 5    # seconds
EXIT_WINDOW = 5       # minutes

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

# Check for valid paid exit
def handle_exit(plate_number):
    cursor.execute('''
        SELECT no, entry_time, exit_time, payment_status
        FROM entries
        WHERE car_plate = ? AND exit_time != '' AND payment_status = 1
        ORDER BY exit_time DESC
    ''', (plate_number,))
    valid_entries = []
    for row in cursor:
        try:
            exit_time = datetime.strptime(row['exit_time'], '%Y-%m-%d %H:%M:%S')
            time_diff = (datetime.now() - exit_time).total_seconds() / 60
            if time_diff <= EXIT_WINDOW:
                valid_entries.append((exit_time, row))
        except Exception as e:
            print(f"[ERROR] Invalid exit_time for {plate_number}: {e}")
    return valid_entries

# Log exit in entries table
def log_exit(plate_number):
    cursor.execute('''
        SELECT no, entry_time
        FROM entries
        WHERE car_plate = ? AND exit_time = '' AND payment_status = 0
        ORDER BY entry_time DESC
        LIMIT 1
    ''', (plate_number,))
    row = cursor.fetchone()
    if not row:
        return False, "No active entry found"
    
    entry_time = datetime.strptime(row['entry_time'], '%Y-%m-%d %H:%M:%S')
    duration_hours = (datetime.now() - entry_time).total_seconds() / 3600
    due_payment = round(duration_hours * 1.0, 2)  # $1/hour, adjust as needed
    
    cursor.execute('''
        UPDATE entries
        SET exit_time = ?, due_payment = ?, payment_status = 1
        WHERE no = ?
    ''', (
        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        due_payment,
        row['no']
    ))
    conn.commit()
    print(f"[EXIT] Logged exit for {plate_number}, payment: ${due_payment}")
    return True, "Valid exit"

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

# Read distance from Arduino
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
    arduino.flush()
else:
    print("[ERROR] Arduino not detected.")

# Initialize Webcam
cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("[ERROR] Cannot open camera.")
    exit(1)
cv2.namedWindow('Exit Webcam Feed', cv2.WINDOW_NORMAL)
cv2.namedWindow('Plate', cv2.WINDOW_NORMAL)
cv2.namedWindow('Processed', cv2.WINDOW_NORMAL)
cv2.resizeWindow('Exit Webcam Feed', 800, 600)

# State variables
plate_buffer = []
gate_open_until = 0
buzzer_on_until = 0
gate_is_open = False
buzzer_is_on = False

print("[EXIT SYSTEM] Ready. Press 'q' to quit.")

try:
    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] Frame capture failed.")
            break

        current_time = time.time()

        # Handle gate closing
        if gate_is_open and current_time >= gate_open_until:
            if arduino:
                arduino.flush()
                arduino.write(b'0')
                if wait_for_arduino_response(arduino, "[GATE] Closed"):
                    print("[GATE] Closing gate (sent '0')")
                gate_is_open = False

        # Handle buzzer stopping
        if buzzer_is_on and current_time >= buzzer_on_until:
            if arduino:
                arduino.flush()
                arduino.write(b'0')
                if wait_for_arduino_response(arduino, "[ALERT] Cleared"):
                    print("[ALERT] Buzzer stopped (sent '0')")
                buzzer_is_on = False

        # Get distance
        distance = read_distance(arduino) or (MAX_DISTANCE - 1)
        annotated = frame.copy()

        if MIN_DISTANCE <= distance <= MAX_DISTANCE and not (gate_is_open or buzzer_is_on):
            results = model(frame)[0]
            annotated = results.plot()

            for box in results.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                plate_img = frame[y1:y2, x1:x2]

                # Preprocess image for OCR
                gray = cv2.cvtColor(plate_img, cv2.COLOR_BGR2GRAY)
                blur = cv2.GaussianBlur(gray, (5,5), 0)
                thresh = cv2.threshold(blur, 0, 255,
                                       cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

                plate_text = pytesseract.image_to_string(
                    thresh,
                    config='--psm 8 --oem 3 '
                           '-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
                ).strip().replace(' ', '')

                if 'RA' in plate_text:
                    start_idx = plate_text.find('RA')
                    plate_candidate = plate_text[start_idx:start_idx+7]
                    if len(plate_candidate) == 7:
                        prefix, digits, suffix = plate_candidate[:3], plate_candidate[3:6], plate_candidate[6]
                        if prefix.isalpha() and digits.isdigit() and suffix.isalpha():
                            plate_buffer.append(plate_candidate)

                # Process plate buffer
                if len(plate_buffer) >= CAPTURE_THRESHOLD:
                    most_common = Counter(plate_buffer).most_common(1)[0][0]
                    plate_buffer.clear()

                    # Check for existing paid exit
                    valid_entries = handle_exit(most_common)
                    if valid_entries:
                        print(f"[ACCESS GRANTED] Paid exit found for {most_common}")
                        if arduino:
                            arduino.flush()
                            arduino.write(b'1')
                            if wait_for_arduino_response(arduino, "[GATE] Opened"):
                                print("[GATE] Opening gate (sent '1')")
                            gate_open_until = current_time + GATE_OPEN_TIME
                            gate_is_open = True
                    else:
                        # Try logging a new exit
                        success, reason = log_exit(most_common)
                        if success:
                            print(f"[ACCESS GRANTED] Exit recorded for {most_common}")
                            if arduino:
                                arduino.flush()
                                arduino.write(b'1')
                                if wait_for_arduino_response(arduino, "[GATE] Opened"):
                                    print("[GATE] Opening gate (sent '1')")
                                gate_open_until = current_time + GATE_OPEN_TIME
                                gate_is_open = True
                        else:
                            print(f"[ACCESS DENIED] Exit not allowed for {most_common}")
                            log_violation(most_common, "Exit", reason)
                            if arduino:
                                arduino.flush()
                                arduino.write(b'2')
                                if wait_for_arduino_response(arduino, "[ALERT] Unpaid vehicle detected"):
                                    print("[ALERT] Buzzer triggered (sent '2')")
                                buzzer_on_until = current_time + BUZZER_ON_TIME
                                buzzer_is_on = True

                # Show plates
                cv2.imshow('Plate', plate_img)
                cv2.imshow('Processed', thresh)

        # Show annotated frame
        cv2.imshow('Exit Webcam Feed', annotated)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
finally:
    cap.release()
    if arduino:
        if gate_is_open or buzzer_is_on:
            arduino.write(b'0')
            time.sleep(0.1)
        arduino.close()
    conn.close()
    cv2.destroyAllWindows()