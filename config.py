# config.py
import os
import pymysql.cursors
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

# --- WHATSAPP CONFIGURATION ---
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

# --- DATABASE CONFIGURATION ---
# Hierarchy Database
DB_HIERARCHY_CONFIG = {
    "host": os.getenv("DB_HIERARCHY_HOST"),
    "user": os.getenv("DB_HIERARCHY_USER"),
    "password": os.getenv("DB_HIERARCHY_PASSWORD"),
    "db": os.getenv("DB_HIERARCHY_DB"),
    "port": int(os.getenv("DB_HIERARCHY_PORT")),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

# Attendance Database
DB_ATTENDANCE_CONFIG = {
    'host': os.getenv("DB_ATTENDANCE_HOST"),
    'port': int(os.getenv("DB_ATTENDANCE_PORT")),
    'user': os.getenv("DB_ATTENDANCE_USER"),
    'password': os.getenv("DB_ATTENDANCE_PASSWORD"),
    'db': os.getenv("DB_ATTENDANCE_DB"),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}