from flask import Flask, jsonify, send_file
import sqlite3
from datetime import datetime

app = Flask(__name__)

def get_db_connection():
    conn = sqlite3.connect('parking.db')
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return send_file('index.html')

@app.route('/api/entries', methods=['GET'])
def get_entries():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM entries WHERE exit_time = "" ORDER BY entry_time DESC')
    entries = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(entries)

@app.route('/api/exits', methods=['GET'])
def get_exits():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM entries WHERE exit_time != "" AND payment_status = 1 ORDER BY exit_time DESC')
    exits = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(exits)

@app.route('/api/payments', methods=['GET'])
def get_payments():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM entries WHERE payment_status = 1 AND due_payment IS NOT NULL ORDER BY exit_time DESC')
    payments = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(payments)

@app.route('/api/violations', methods=['GET'])
def get_violations():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM violations ORDER BY timestamp DESC')
    violations = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(violations)

if __name__ == '__main__':
    app.run(debug=True)