import sqlite3
import csv

# Connect to SQLite database (creates file if it doesn't exist)
conn = sqlite3.connect('parking.db')
cursor = conn.cursor()

# Create entries table
cursor.execute('''
    CREATE TABLE IF NOT EXISTS entries (
        no INTEGER PRIMARY KEY,
        entry_time TEXT,
        exit_time TEXT,
        car_plate TEXT,
        due_payment REAL,
        payment_status INTEGER
    )
''')

# Create violations table
cursor.execute('''
    CREATE TABLE IF NOT EXISTS violations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        car_plate TEXT,
        gate_location TEXT,
        reason TEXT
    )
''')

# Migrate db.csv
with open('./db.csv', 'r', newline='') as f:
    reader = csv.DictReader(f)
    for row in reader:
        cursor.execute('''
            INSERT OR IGNORE INTO entries (no, entry_time, exit_time, car_plate, due_payment, payment_status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            int(row['no']),
            row['entry_time'],
            row['exit_time'],
            row['car_plate'],
            float(row['due payment']) if row['due payment'] else None,
            int(row['payment status'])
        ))

# Migrate violations.csv
with open('./violations.csv', 'r', newline='') as f:
    reader = csv.DictReader(f)
    for row in reader:
        cursor.execute('''
            INSERT INTO violations (timestamp, car_plate, gate_location, reason)
            VALUES (?, ?, ?, ?)
        ''', (
            row['timestamp'],
            row['car_plate'],
            row['gate_location'],
            row['reason']
        ))

# Commit changes and close connection
conn.commit()
conn.close()
print("Data migration completed successfully.")