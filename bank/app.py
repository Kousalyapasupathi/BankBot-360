"""
Intelligent NLP-Based Core Banking Simulation System
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os, re, random, hashlib, secrets, threading, time, urllib.request, urllib.parse, io
from datetime import datetime, timedelta
from functools import wraps
import pandas as pd
from flask import Flask, render_template, request, jsonify, session, redirect
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
import warnings
warnings.filterwarnings('ignore')

app = Flask(__name__)
app.secret_key = "smartbank_secret_2024"

# ═══════════════════════════════════════════════════════════════════
#  SQL SERVER — PRIMARY (ONLY) DATABASE
#  Database name : SmartBankProDB
#  Server        : (localdb)\MSSQLLocalDB
#  Auth          : Windows Trusted Connection
# ═══════════════════════════════════════════════════════════════════

MSSQL_ENABLED = False
_mssql_conn   = None
_db_lock      = threading.Lock()

MSSQL_SERVER   = r"(localdb)\MSSQLLocalDB"
MSSQL_DATABASE = "SmartBankProDB"

# ── Try multiple ODBC drivers so it works on any machine ──────────
def _get_driver():
    import pyodbc
    preferred = [
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 13 for SQL Server",
        "SQL Server",
    ]
    available = pyodbc.drivers()
    for d in preferred:
        if d in available:
            return d
    raise RuntimeError(f"No SQL Server ODBC driver found. Install ODBC Driver 17.\nAvailable: {available}")

def _make_conn():
    import pyodbc
    driver = _get_driver()
    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={MSSQL_SERVER};"
        f"DATABASE={MSSQL_DATABASE};"
        "Trusted_Connection=yes;"
        "Connection Timeout=10;"
    )
    conn = pyodbc.connect(conn_str, autocommit=False)
    return conn

def _init_mssql():
    global MSSQL_ENABLED, _mssql_conn
    try:
        conn = _make_conn()
        MSSQL_ENABLED = True
        _mssql_conn = conn
        print(f"✅ SQL Server connected: {MSSQL_SERVER} / {MSSQL_DATABASE}")
        return conn
    except Exception as e:
        print(f"❌ SQL Server connection FAILED: {e}")
        print("   Make sure LocalDB is running:  sqllocaldb start MSSQLLocalDB")
        raise SystemExit(1)   # hard stop — no silent SQLite fallback

def _ensure_conn():
    """Return a live connection, reconnecting if needed."""
    global _mssql_conn
    try:
        _mssql_conn.cursor().execute("SELECT 1")
        return _mssql_conn
    except Exception:
        print("[DB] Reconnecting to SQL Server...")
        _mssql_conn = _make_conn()
        return _mssql_conn

# ── DB helpers (drop-in replacements for SQLite helpers) ──────────
class _MSSQLRow(dict):
    """Dict that also supports row['col'] and row.col access."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

def _row_from_cursor(cur, row_tuple):
    if row_tuple is None:
        return None
    cols = [d[0] for d in cur.description]
    return _MSSQLRow(zip(cols, row_tuple))

def db_exec(sql, args=(), commit=False):
    # Translate SQLite-style date functions to MSSQL equivalents
    sql = sql.replace("datetime('now')", "CONVERT(NVARCHAR,GETDATE(),120)")
    sql = sql.replace("datetime('now',", "DATEADD(SECOND,") \
             .replace("'-1 hour')", "-3600, GETDATE())")
    with _db_lock:
        conn = _ensure_conn()
        cur  = conn.cursor()
        try:
            cur.execute(sql, args if args else ())
            if commit:
                conn.commit()
                # Return last inserted id via @@IDENTITY
                try:
                    cur.execute("SELECT @@IDENTITY")
                    row = cur.fetchone()
                    return int(row[0]) if row and row[0] is not None else None
                except Exception:
                    return None
            return cur
        except Exception as e:
            try: conn.rollback()
            except: pass
            raise e

def db_one(sql, args=()):
    sql = sql.replace("datetime('now')", "CONVERT(NVARCHAR,GETDATE(),120)")
    sql = sql.replace("datetime('now',", "DATEADD(SECOND,") \
             .replace("'-1 hour')", "-3600, GETDATE())")
    # MSSQL uses TOP 1 instead of LIMIT 1
    sql = re.sub(r'\bLIMIT\s+1\b', '', sql, flags=re.IGNORECASE)
    with _db_lock:
        conn = _ensure_conn()
        cur  = conn.cursor()
        cur.execute(sql, args if args else ())
        row = cur.fetchone()
        return _row_from_cursor(cur, row)

def db_all(sql, args=()):
    sql = sql.replace("datetime('now')", "CONVERT(NVARCHAR,GETDATE(),120)")
    sql = sql.replace("datetime('now',", "DATEADD(SECOND,") \
             .replace("'-1 hour')", "-3600, GETDATE())")
    # Translate LIMIT N / LIMIT N OFFSET M → TOP N
    sql = re.sub(r'\bLIMIT\s+(\d+)\s+OFFSET\s+(\d+)\b',
                 lambda m: f'/* LIMIT {m.group(1)} OFFSET {m.group(2)} */', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bLIMIT\s+(\d+)\b',
                 lambda m: f'/* LIMIT {m.group(1)} */', sql, flags=re.IGNORECASE)
    with _db_lock:
        conn = _ensure_conn()
        cur  = conn.cursor()
        cur.execute(sql, args if args else ())
        rows = cur.fetchall()
        return [_row_from_cursor(cur, r) for r in rows]

def db_val(sql, args=()):
    row = db_one(sql, args)
    if row is None:
        return None
    return list(row.values())[0]

# ── INIT DB (SQL Server only) ──────────────────
def init_db():
    conn = _ensure_conn()
    cur  = conn.cursor()

    # Create all tables if they don't exist
    statements = [
        """
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='users' AND xtype='U')
        CREATE TABLE users (
            id              INT IDENTITY(1,1) PRIMARY KEY,
            account_number  NVARCHAR(20)  UNIQUE NOT NULL,
            name            NVARCHAR(200) NOT NULL,
            email           NVARCHAR(200) UNIQUE NOT NULL,
            phone           NVARCHAR(20)  DEFAULT '',
            password_hash   NVARCHAR(64)  NOT NULL,
            pin_hash        NVARCHAR(64)  DEFAULT '',
            balance         FLOAT         DEFAULT 0.0,
            account_type    NVARCHAR(20)  DEFAULT 'savings',
            is_admin        INT           DEFAULT 0,
            is_locked       INT           DEFAULT 0,
            failed_attempts INT           DEFAULT 0,
            created_at      NVARCHAR(30)  DEFAULT (CONVERT(NVARCHAR,GETDATE(),120)),
            last_login      NVARCHAR(30)  DEFAULT ''
        )
        """,
        """
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='transactions' AND xtype='U')
        CREATE TABLE transactions (
            id           INT IDENTITY(1,1) PRIMARY KEY,
            txn_id       NVARCHAR(30)  UNIQUE NOT NULL,
            from_account NVARCHAR(20)  NOT NULL,
            to_account   NVARCHAR(20)  NOT NULL,
            amount       FLOAT         NOT NULL,
            txn_type     NVARCHAR(30)  NOT NULL,
            description  NVARCHAR(200) DEFAULT '',
            status       NVARCHAR(20)  DEFAULT 'success',
            is_flagged   INT           DEFAULT 0,
            flag_reason  NVARCHAR(500) DEFAULT '',
            fraud_score  FLOAT         DEFAULT 0.0,
            created_at   NVARCHAR(30)  DEFAULT (CONVERT(NVARCHAR,GETDATE(),120))
        )
        """,
        """
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='chat_logs' AND xtype='U')
        CREATE TABLE chat_logs (
            id             INT IDENTITY(1,1) PRIMARY KEY,
            account_number NVARCHAR(20)  NOT NULL,
            message        NVARCHAR(MAX),
            response       NVARCHAR(MAX),
            intent         NVARCHAR(50),
            language       NVARCHAR(10),
            created_at     NVARCHAR(30)  DEFAULT (CONVERT(NVARCHAR,GETDATE(),120))
        )
        """,
        """
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='banker_requests' AND xtype='U')
        CREATE TABLE banker_requests (
            id             INT IDENTITY(1,1) PRIMARY KEY,
            account_number NVARCHAR(20)  NOT NULL,
            reason         NVARCHAR(MAX),
            status         NVARCHAR(20)  DEFAULT 'pending',
            banker_note    NVARCHAR(MAX) DEFAULT '',
            created_at     NVARCHAR(30)  DEFAULT (CONVERT(NVARCHAR,GETDATE(),120))
        )
        """,
        """
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='scheduled_transfers' AND xtype='U')
        CREATE TABLE scheduled_transfers (
            id             INT IDENTITY(1,1) PRIMARY KEY,
            from_account   NVARCHAR(20)  NOT NULL,
            to_account     NVARCHAR(20)  NOT NULL,
            amount         FLOAT         NOT NULL,
            scheduled_at   NVARCHAR(30)  NOT NULL,
            status         NVARCHAR(20)  DEFAULT 'pending',
            created_at     NVARCHAR(30)  DEFAULT (CONVERT(NVARCHAR,GETDATE(),120))
        )
        """,
        """
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='settings' AND xtype='U')
        CREATE TABLE settings (
            key_name   NVARCHAR(100) PRIMARY KEY,
            value      NVARCHAR(MAX)
        )
        """,
    ]
    for stmt in statements:
        cur.execute(stmt)
    conn.commit()

    # Ensure admin account exists
    pw_admin = hashlib.sha256("Admin@123".encode()).hexdigest()
    pin_h    = hashlib.sha256("1234".encode()).hexdigest()
    cur.execute("""
        IF NOT EXISTS (SELECT 1 FROM users WHERE account_number='10000')
        INSERT INTO users (account_number,name,email,phone,password_hash,pin_hash,balance,account_type,is_admin)
        VALUES ('10000','Admin User','admin@smartbank.com','9999999999',?,?,0,'admin',1)
    """, (pw_admin, pin_h))
    conn.commit()
    print("✅ SQL Server schema ready")

# ── NLP Training Data — embedded + CSV auto-export ────────────────
import csv as _csv_mod

# All training/test data is stored right here in the source file.
# On first run the app writes training_data.csv next to itself so you
# can open, inspect, or edit the data in Excel / any spreadsheet tool.
# The CSV is the ONE external data file — no separate generator script.

_EMBEDDED_DATA = [
    ("கணக்கு வரலாறு காட்டு", "view_history", "train"),
    ("display transaction history", "view_history", "train"),
    ("put money in my account", "deposit", "train"),
    ("can i see my transactions", "view_history", "train"),
    ("monthly summary", "analytics", "train"),
    ("last 10 transactions", "mini_statement", "train"),
    ("மாத செலவு சுருக்கம்", "analytics", "train"),
    ("show recent activity", "view_history", "train"),
    ("set up automatic transfer", "schedule_transfer", "train"),
    ("last three transactions", "mini_statement", "train"),
    ("என் பரிவர்த்தனைகள் காட்டு", "view_history", "train"),
    ("பரிமாற்றம் செய்", "transfer_money", "train"),
    ("வணக்கம்", "greeting", "train"),
    ("online transfer", "transfer_money", "train"),
    ("mini statement dikhao", "mini_statement", "train"),
    ("alvida", "goodbye", "train"),
    ("deposit cash", "deposit", "test"),
    ("show analytics", "analytics", "train"),
    ("move money to another account", "transfer_money", "train"),
    ("ok thanks", "goodbye", "train"),
    ("loan eligibility", "faq_loan", "train"),
    ("add funds", "deposit", "train"),
    ("बैंक का समय", "faq_timing", "train"),
    ("suspicious transaction hai", "fraud_alert", "test"),
    ("is bank open on sundays", "faq_timing", "train"),
    ("what is my current balance", "check_balance", "train"),
    ("mera balance kya hai", "check_balance", "train"),
    ("bank timings", "faq_timing", "train"),
    ("மோசடி பரிவர்த்தனை", "fraud_alert", "train"),
    ("kya aaj bank khula hai", "faq_timing", "train"),
    ("send payment", "transfer_money", "train"),
    ("greetings", "greeting", "train"),
    ("hi", "greeting", "train"),
    ("கணக்கிற்கு பணம் அனுப்பு", "transfer_money", "test"),
    ("do a bank transfer", "transfer_money", "train"),
    ("view my past transactions", "view_history", "train"),
    ("धन्यवाद", "goodbye", "train"),
    ("i want to remit money", "transfer_money", "train"),
    ("i would like to deposit", "deposit", "train"),
    ("show me how i spend my money", "analytics", "train"),
    ("kitna paisa hai", "check_balance", "train"),
    ("i need to deposit some cash", "deposit", "train"),
    ("what is the amount in my account", "check_balance", "train"),
    ("how much is in my account", "check_balance", "train"),
    ("please cancel my last transaction", "cancel_transfer", "train"),
    ("i need a loan", "faq_loan", "test"),
    ("future dated transfer", "schedule_transfer", "train"),
    ("मेरा बैलेंस क्या है", "check_balance", "train"),
    ("mujhe loan chahiye", "faq_loan", "train"),
    ("live agent", "connect_banker", "test"),
    ("send 1000 to account 10003", "transfer_money", "train"),
    ("speak to representative", "connect_banker", "train"),
    ("i want to pay", "transfer_money", "train"),
    ("hello", "greeting", "train"),
    ("check my account", "check_balance", "train"),
    ("done for now", "goodbye", "train"),
    ("reverse the last transfer", "cancel_transfer", "train"),
    ("धोखाधड़ी", "fraud_alert", "train"),
    ("expense analysis", "analytics", "train"),
    ("stop the last transfer", "cancel_transfer", "test"),
    ("balance kitna hai", "check_balance", "test"),
    ("cancel that transfer", "cancel_transfer", "test"),
    ("help", "greeting", "train"),
    ("education loan", "faq_loan", "train"),
    ("baad mein paise bhejna hai", "schedule_transfer", "train"),
    ("expense report", "analytics", "train"),
    ("bank representative se milao", "connect_banker", "train"),
    ("is the bank open today", "faq_timing", "train"),
    ("transfer money", "transfer_money", "test"),
    ("i got an unknown debit", "fraud_alert", "train"),
    ("பேலன்ஸ் எவ்வளவு", "check_balance", "train"),
    ("apply for loan", "faq_loan", "train"),
    ("payment history", "view_history", "train"),
    ("automatic transfer set karo", "schedule_transfer", "train"),
    ("என் கணக்கை ப்ளாக் செய்", "fraud_alert", "train"),
    ("how are you", "greeting", "train"),
    ("someone used my account without permission", "fraud_alert", "train"),
    ("meri transactions dikhao", "view_history", "train"),
    ("how much do i have in my bank", "check_balance", "train"),
    ("personal loan apply karna hai", "faq_loan", "train"),
    ("unauthorized transaction", "fraud_alert", "train"),
    ("தானியங்கி பரிமாற்றம்", "schedule_transfer", "train"),
    ("கடைசி 5 பரிவர்த்தனைகள்", "mini_statement", "test"),
    ("पैसे ट्रांसफर करें", "transfer_money", "test"),
    ("connect to agent", "connect_banker", "train"),
    ("analyze my spending", "analytics", "train"),
    ("set up a recurring transfer", "schedule_transfer", "test"),
    ("please connect me to an agent", "connect_banker", "train"),
    ("transfer cancel karo", "cancel_transfer", "train"),
    ("view my account statement", "view_history", "train"),
    ("logout", "goodbye", "train"),
    ("fraud detected", "fraud_alert", "train"),
    ("talk to banker", "connect_banker", "train"),
    ("suspicious activity on my account", "fraud_alert", "train"),
    ("இருப்பு காட்டு", "check_balance", "train"),
    ("i want to schedule a payment", "schedule_transfer", "train"),
    ("கடன் விண்ணப்பம்", "faq_loan", "train"),
    ("chhota statement", "mini_statement", "train"),
    ("what is my balance", "check_balance", "train"),
    ("do a transfer later", "schedule_transfer", "test"),
    ("என் கணக்கில் எவ்வளவு பணம் இருக்கிறது", "check_balance", "test"),
    ("hello bank", "greeting", "train"),
    ("மினி ஸ்டேட்மெண்ட்", "mini_statement", "train"),
    ("கடைசி பரிமாற்றத்தை ரத்து செய்", "cancel_transfer", "train"),
    ("reverse transaction", "cancel_transfer", "train"),
    ("my balance", "check_balance", "train"),
    ("பணம் போடு", "deposit", "train"),
    ("balance dikhao", "check_balance", "train"),
    ("show me my transaction history", "view_history", "test"),
    ("connect to support", "connect_banker", "train"),
    ("show mini statement", "mini_statement", "train"),
    ("fund transfer karo", "transfer_money", "train"),
    ("what is the balance in my account", "check_balance", "test"),
    ("காலை வணக்கம்", "greeting", "train"),
    ("fetch my balance", "check_balance", "train"),
    ("human support", "connect_banker", "test"),
    ("fund my account", "deposit", "train"),
    ("all transactions", "view_history", "train"),
    ("undo last transaction", "cancel_transfer", "test"),
    ("send money", "transfer_money", "train"),
    ("வங்கி கஸ்டமர் கேர் இணை", "connect_banker", "train"),
    ("i want to see my transaction history", "view_history", "train"),
    ("mujhe madad chahiye", "greeting", "test"),
    ("नमस्ते", "greeting", "test"),
    ("சேமிப்பு வட்டி", "faq_interest", "train"),
    ("check balance", "check_balance", "train"),
    ("past transactions", "view_history", "train"),
    ("automate money transfer", "schedule_transfer", "train"),
    ("i want to apply for a loan", "faq_loan", "train"),
    ("connect me to customer care", "connect_banker", "test"),
    ("bank opening time", "faq_timing", "train"),
    ("send money at a later date", "schedule_transfer", "train"),
    ("pichle transactions", "view_history", "test"),
    ("schedule money transfer", "schedule_transfer", "train"),
    ("plan a future transfer", "schedule_transfer", "train"),
    ("agent please", "connect_banker", "train"),
    ("when does branch open", "faq_timing", "train"),
    ("loan chahiye", "faq_loan", "train"),
    ("ok bye", "goodbye", "train"),
    ("என் பணம் எவ்வளவு", "check_balance", "train"),
    ("can i get a loan", "faq_loan", "train"),
    ("how much money do i have", "check_balance", "train"),
    ("unauthorized debit", "fraud_alert", "train"),
    ("can you tell me my balance", "check_balance", "train"),
    ("can i transfer money", "transfer_money", "train"),
    ("நான் உதவி வேண்டும்", "greeting", "train"),
    ("spending breakdown", "analytics", "train"),
    ("how much rupees do i have", "check_balance", "train"),
    ("theek hai bye", "goodbye", "train"),
    ("wire transfer", "transfer_money", "train"),
    ("when is the bank open", "faq_timing", "train"),
    ("good morning", "greeting", "train"),
    ("suspicious transaction", "fraud_alert", "train"),
    ("what is the interest rate", "faq_interest", "train"),
    ("cancel the transaction", "cancel_transfer", "train"),
    ("top up my account", "deposit", "train"),
    ("credit 5000 to my account", "deposit", "train"),
    ("வட்டி விகிதம்", "faq_interest", "train"),
    ("kitna kharch kiya", "analytics", "test"),
    ("bank hours", "faq_timing", "train"),
    ("personal loan", "faq_loan", "train"),
    ("where did i spend my money", "analytics", "test"),
    ("கணக்கில் பணம் சேர்", "deposit", "train"),
    ("how to get a loan", "faq_loan", "train"),
    ("சுருக்க அறிக்கை", "mini_statement", "train"),
    ("balance inquiry", "check_balance", "test"),
    ("view mini statement", "mini_statement", "train"),
    ("spending insights", "analytics", "train"),
    ("cancel last transfer", "cancel_transfer", "train"),
    ("வீட்டு கடன்", "faq_loan", "train"),
    ("balance status", "check_balance", "train"),
    ("expense tracker", "analytics", "test"),
    ("increase my balance", "deposit", "train"),
    ("can you help me", "greeting", "train"),
    ("neft transfer", "transfer_money", "train"),
    ("transfer to another account", "transfer_money", "train"),
    ("make a transfer", "transfer_money", "train"),
    ("बैलेंस चेक करें", "check_balance", "train"),
    ("account activity", "view_history", "train"),
    ("please show my balance", "check_balance", "train"),
    ("there is a fraud transaction", "fraud_alert", "train"),
    ("hi bot", "greeting", "test"),
    ("bye", "goodbye", "test"),
    ("வங்கி நேரம் என்ன", "faq_timing", "train"),
    ("hello there", "greeting", "train"),
    ("what time does the bank open", "faq_timing", "train"),
    ("balance add karo", "deposit", "train"),
    ("customer care", "connect_banker", "train"),
    ("पैसा भेजना है", "transfer_money", "train"),
    ("recent transactions", "view_history", "train"),
    ("transaction history dikhao", "view_history", "train"),
    ("show me all my transactions", "view_history", "train"),
    ("mujhe fraud report karna hai", "fraud_alert", "train"),
    ("நன்றி", "goodbye", "train"),
    ("transfer funds to my friend", "transfer_money", "train"),
    ("speak with a banker", "connect_banker", "test"),
    ("list all my transactions", "view_history", "train"),
    ("thanks a lot", "goodbye", "train"),
    ("cash deposit", "deposit", "train"),
    ("see you", "goodbye", "train"),
    ("revert transfer", "cancel_transfer", "train"),
    ("financial summary", "analytics", "train"),
    ("வரலாறு காட்டு", "view_history", "train"),
    ("quick statement", "mini_statement", "train"),
    ("schedule a transfer", "schedule_transfer", "train"),
    ("schedule transfer karo", "schedule_transfer", "train"),
    ("where did my money go", "view_history", "train"),
    ("how much did i spend", "analytics", "train"),
    ("home loan", "faq_loan", "train"),
    ("report fraud", "fraud_alert", "train"),
    ("remaining balance", "check_balance", "train"),
    ("savings account interest rate", "faq_interest", "train"),
    ("paisa jama karna hai", "deposit", "train"),
    ("spending report", "analytics", "train"),
    ("undo my last payment", "cancel_transfer", "train"),
    ("add money", "deposit", "train"),
    ("my money was stolen", "fraud_alert", "train"),
    ("car loan", "faq_loan", "train"),
    ("how much interest on savings", "faq_interest", "train"),
    ("fd rate", "faq_interest", "train"),
    ("someone stole money from my account", "fraud_alert", "train"),
    ("home loan kaise milega", "faq_loan", "test"),
    ("मिनी स्टेटमेंट", "mini_statement", "train"),
    ("आखिरी 5 लेनदेन", "mini_statement", "test"),
    ("thanks", "goodbye", "train"),
    ("loan requirements", "faq_loan", "train"),
    ("savings par kitna byaj milega", "faq_interest", "train"),
    ("pichla payment wapis karo", "cancel_transfer", "train"),
    ("balance check", "check_balance", "train"),
    ("ब्याज दर", "faq_interest", "train"),
    ("account balance batao", "check_balance", "train"),
    ("escalate to human", "connect_banker", "train"),
    ("namaste", "greeting", "train"),
    ("loan application", "faq_loan", "train"),
    ("show me my spending", "analytics", "train"),
    ("i want to deposit money", "deposit", "test"),
    ("add money to my account", "deposit", "train"),
    ("freeze my account", "fraud_alert", "train"),
    ("மனித உதவி வேண்டும்", "connect_banker", "train"),
    ("show my account statement", "view_history", "test"),
    ("show transaction history", "view_history", "train"),
    ("account mein paise bhejo", "transfer_money", "train"),
    ("unauthorized payment", "fraud_alert", "train"),
    ("display my balance", "check_balance", "train"),
    ("when does bank open", "faq_timing", "train"),
    ("exit", "goodbye", "test"),
    ("hi there", "greeting", "train"),
    ("சந்தேகமான பரிவர்த்தனை", "fraud_alert", "train"),
    ("scheduled payment", "schedule_transfer", "test"),
    ("available balance", "check_balance", "test"),
    ("okay bye", "goodbye", "train"),
    ("hey", "greeting", "train"),
    ("thank you", "goodbye", "train"),
    ("account ki history batao", "view_history", "train"),
    ("home loan interest rate", "faq_interest", "train"),
    ("my spending patterns", "analytics", "train"),
    ("illegal transaction", "fraud_alert", "train"),
    ("i want to send money", "transfer_money", "train"),
    ("talk to customer support", "connect_banker", "train"),
    ("ஒரு பரிவர்த்தனை திட்டமிடு", "schedule_transfer", "train"),
    ("show me my balance", "check_balance", "train"),
    ("transfer schedule karna hai", "schedule_transfer", "train"),
    ("credit my account", "deposit", "train"),
    ("i want to see my balance", "check_balance", "train"),
    ("வங்கி எப்போது திறக்கும்", "faq_timing", "test"),
    ("bank holidays", "faq_timing", "train"),
    ("bank transfer karna hai", "transfer_money", "train"),
    ("டெபாசிட் செய்", "deposit", "test"),
    ("help chahiye", "greeting", "train"),
    ("पैसे जमा करने हैं", "deposit", "train"),
    ("मेरे लेनदेन दिखाओ", "view_history", "test"),
    ("என் கணக்கில் திருட்டு நடந்தது", "fraud_alert", "train"),
    ("நிதி பகுப்பாய்வு", "analytics", "train"),
    ("show me the last 5 payments", "mini_statement", "test"),
    ("मेरे खाते में कितना पैसा है", "check_balance", "train"),
    ("பணம் மாற்று", "transfer_money", "train"),
    ("book a future payment", "schedule_transfer", "train"),
    ("goodbye", "goodbye", "test"),
    ("send funds", "transfer_money", "train"),
    ("mera last transfer cancel karo", "cancel_transfer", "train"),
    ("get me a human agent", "connect_banker", "train"),
    ("schedule payment for tomorrow", "schedule_transfer", "train"),
    ("mini statement", "mini_statement", "train"),
    ("help me", "greeting", "train"),
    ("bank kab khulta hai", "faq_timing", "test"),
    ("what are my last transactions", "mini_statement", "train"),
    ("brief transaction summary", "mini_statement", "train"),
    ("hello ji", "greeting", "train"),
    ("i want to reverse my payment", "cancel_transfer", "train"),
    ("i need to speak with someone", "connect_banker", "train"),
    ("last 5 transactions dikhao", "mini_statement", "train"),
    ("pay to account", "transfer_money", "train"),
    ("block my account", "fraud_alert", "train"),
    ("real agent chahiye", "connect_banker", "train"),
    ("current interest rates", "faq_interest", "train"),
    ("bank timing kya hai", "faq_timing", "train"),
    ("reverse last payment", "cancel_transfer", "train"),
    ("can i see last 5 transactions", "mini_statement", "train"),
    ("i need help from a person", "connect_banker", "train"),
    ("mera account hack ho gaya", "fraud_alert", "test"),
    ("customer care se connect karo", "connect_banker", "train"),
    ("show me last few transactions", "mini_statement", "train"),
    ("spending report chahiye", "analytics", "train"),
    ("recent 5 transactions", "mini_statement", "train"),
    ("my transactions", "view_history", "train"),
    ("போகிறேன்", "goodbye", "train"),
    ("சரி நன்றி", "goodbye", "train"),
    ("personal loan interest", "faq_interest", "train"),
    ("bank closing time", "faq_timing", "train"),
    ("does the bank work on saturday", "faq_timing", "train"),
    ("paisa bhejo", "transfer_money", "train"),
    ("i made a mistake please cancel", "cancel_transfer", "train"),
    ("खर्च विश्लेषण", "analytics", "train"),
    ("show my account balance", "check_balance", "test"),
    ("that's all", "goodbye", "train"),
    ("can you check my account balance", "check_balance", "train"),
    ("add balance", "deposit", "train"),
    ("पैसे भेजें", "transfer_money", "train"),
    ("start", "greeting", "test"),
    ("block account now", "fraud_alert", "train"),
    ("i am done", "goodbye", "train"),
    ("செலவு பகுப்பாய்வு", "analytics", "train"),
    ("ஹலோ", "greeting", "train"),
    ("i want to talk to someone", "connect_banker", "train"),
    ("short statement", "mini_statement", "train"),
    ("my account is compromised", "fraud_alert", "train"),
    ("agent se baat", "connect_banker", "train"),
    ("hey there", "greeting", "train"),
    ("கடன் வேண்டும்", "faq_loan", "test"),
    ("கடன் வட்டி", "faq_interest", "train"),
    ("जमा करें", "deposit", "train"),
    ("i need help", "greeting", "train"),
    ("how do i borrow money", "faq_loan", "train"),
    ("add 2000 to my balance", "deposit", "train"),
    ("can you show me my account history", "view_history", "train"),
    ("tell me my account balance", "check_balance", "train"),
    ("i want to report fraud", "fraud_alert", "test"),
    ("transaction undo karo", "cancel_transfer", "train"),
    ("what interest do i earn", "faq_interest", "train"),
    ("मेरा खर्च दिखाओ", "analytics", "train"),
    ("take care", "goodbye", "train"),
    ("bank working hours", "faq_timing", "train"),
    ("fixed deposit interest rate", "faq_interest", "train"),
    ("இருப்பு நிலை என்ன", "check_balance", "train"),
    ("deposit karo", "deposit", "train"),
    ("unknown transaction in my account", "fraud_alert", "test"),
    ("i want a banker", "connect_banker", "train"),
    ("please transfer money", "transfer_money", "train"),
    ("shukriya", "goodbye", "train"),
    ("vehicle loan", "faq_loan", "train"),
    ("transfer 500 rupees", "transfer_money", "train"),
    ("account mein paisa dalo", "deposit", "test"),
    ("last 5 transactions", "mini_statement", "train"),
    ("yeh transaction maine nahi kiya", "fraud_alert", "train"),
    ("transfer at a future date", "schedule_transfer", "train"),
    ("imps transfer", "transfer_money", "train"),
    ("send money to account number", "transfer_money", "train"),
    ("auto transfer", "schedule_transfer", "train"),
    ("i want to check my balance", "check_balance", "train"),
    ("account freeze karo", "fraud_alert", "train"),
    ("get my transaction records", "view_history", "train"),
    ("interest rate", "faq_interest", "train"),
    ("pay someone", "transfer_money", "train"),
    ("can i deposit money", "deposit", "train"),
    ("வங்கி நேரம்", "faq_timing", "test"),
    ("interest on fixed deposit", "faq_interest", "test"),
    ("mujhe insaan se baat karni hai", "connect_banker", "train"),
    ("i was scammed", "fraud_alert", "test"),
    ("பணம் அனுப்பு", "transfer_money", "train"),
    ("என் இருப்பு என்ன", "check_balance", "train"),
    ("வங்கி அதிகாரியிடம் பேசு", "connect_banker", "train"),
    ("fund transfer", "transfer_money", "train"),
    ("i want to transfer money", "transfer_money", "train"),
    ("deposit 1000", "deposit", "train"),
    ("transfer 5000 to 10002", "transfer_money", "test"),
    ("good afternoon", "greeting", "train"),
    ("i want human help", "connect_banker", "train"),
    ("can i cancel my transfer", "cancel_transfer", "train"),
    ("i did not make this transaction", "fraud_alert", "train"),
    ("show me my payment history", "view_history", "train"),
    ("interest rate kya hai", "faq_interest", "test"),
    ("deposit money", "deposit", "train"),
    ("loan interest", "faq_interest", "train"),
    ("make a payment", "transfer_money", "train"),
    ("account band karo", "fraud_alert", "train"),
    ("get my current balance", "check_balance", "train"),
    ("unknown debit from my account", "fraud_alert", "train"),
    ("show me mini statement", "mini_statement", "train"),
    ("how much can i spend", "check_balance", "train"),
    ("view history", "view_history", "train"),
    ("i want to speak to a real person", "connect_banker", "train"),
    ("பரிவர்த்தனை வரலாறு", "view_history", "train"),
    ("கடைசி பரிவர்த்தனை திரும்பப் பெறு", "cancel_transfer", "train"),
    ("good evening", "greeting", "train"),
    ("my account has been hacked", "fraud_alert", "train"),
    ("what is my bank balance right now", "check_balance", "train"),
    ("i want to cancel my last transfer", "cancel_transfer", "train"),
    ("transfer karo", "transfer_money", "test"),
    ("fd rate kya hai", "faq_interest", "train"),
    ("i want to know my balance", "check_balance", "train"),
    ("लेनदेन का इतिहास", "view_history", "train"),
]


def _ensure_csv():
    """Write training_data.csv from the embedded data if it doesn't exist yet."""
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'training_data.csv')
    if not os.path.exists(csv_path):
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = _csv_mod.writer(f)
            writer.writerow(['text', 'intent', 'split'])
            writer.writerows(_EMBEDDED_DATA)
        print(f"training_data.csv created at {csv_path}")
    return csv_path


def load_training_data():
    """Return (text, intent) pairs for split='train' rows."""
    csv_path = _ensure_csv()
    rows = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = _csv_mod.DictReader(f)
        for row in reader:
            text  = row.get('text', '').strip()
            label = row.get('intent', '').strip()
            split = row.get('split', 'train').strip()
            if text and label and split == 'train':
                rows.append((text, label))
    if not rows:
        print("No 'train' rows found in CSV — using embedded data.")
        rows = [(t, i) for t, i, s in _EMBEDDED_DATA if s == 'train']
    print(f"Loaded {len(rows)} training samples from training_data.csv")
    return rows

# ── SPOKEN NUMBER NORMALIZER ──────────────────
SPOKEN_NUMS = {
    'பூஜியம்': '0', 'சுழியம்': '0',
    'ஒன்று': '1', 'ஒன்னு': '1',
    'இரண்டு': '2', 'ரெண்டு': '2',
    'மூன்று': '3', 'மூணு': '3',
    'நான்கு': '4', 'நாலு': '4',
    'ஐந்து': '5', 'ஐஞ்சு': '5',
    'ஆறு': '6',
    'ஏழு': '7', 'ஏழ்': '7',
    'எட்டு': '8',
    'ஒன்பது': '9', 'ஒம்பது': '9',
    'शून्य': '0', 'जीरो': '0',
    'एक': '1', 'दो': '2', 'तीन': '3', 'चार': '4',
    'पांच': '5', 'पाँच': '5', 'छह': '6', 'छः': '6',
    'सात': '7', 'आठ': '8', 'नौ': '9',
    'zero': '0', 'oh': '0', 'one': '1', 'two': '2', 'three': '3',
    'four': '4', 'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
}

def normalize_spoken(text):
    t = re.sub(r'[\s\u00a0\u200b]+', ' ', text).strip()
    words = t.split()
    t = ' '.join(SPOKEN_NUMS.get(w, SPOKEN_NUMS.get(w.lower(), w)) for w in words)
    t = re.sub(r'(?:\b\d\b\s*){4,5}', lambda m: m.group(0).replace(' ', ''), t)
    return t


class NLPEngine:
    def __init__(self):
        TD = load_training_data()
        t, l = zip(*TD)
        self.pipe = Pipeline([
            ('tfidf', TfidfVectorizer(analyzer='char_wb', ngram_range=(2,4), max_features=8000, sublinear_tf=True)),
            ('clf',   LogisticRegression(max_iter=1000, C=5.0, solver='lbfgs'))
        ])
        self.pipe.fit(list(t), list(l))

    def predict(self, text):
        p = self.pipe.predict_proba([text.lower().strip()])[0]
        i = p.argmax()
        return self.pipe.classes_[i], float(p[i])

    def detect_lang(self, text):
        if re.findall(r'[\u0B80-\u0BFF]', text): return 'ta'
        if re.findall(r'[\u0900-\u097F]', text): return 'hi'
        return 'en'

    def extract_amount(self, text):
        t = re.sub(r'\b[1-9]\d{4}\b', '', text)
        t = re.sub(r'[Rr][Ss]\.?\s*', '', t)
        t = t.replace(',', '')
        m = re.findall(r'(?:₹\s*)?(\d+(?:\.\d+)?)', t)
        for v in m:
            if len(str(int(float(v)))) != 5:
                return float(v)
        return None

    def extract_account(self, text):
        m = re.search(r'\b([1-9]\d{4})\b', text)
        return m.group(1) if m else None

try:
    nlp = NLPEngine()
    print("NLP Engine loaded")
except Exception as e:
    print(f"NLP Engine failed: {e}")
    raise

# ── FRAUD ─────────────────────────────────────
def fraud_score(acc, amount, to_acc=None):
    score, reasons = 0.0, []
    if amount > 50000: score+=0.4; reasons.append("Very high amount")
    elif amount > 20000: score+=0.2; reasons.append("High amount")
    cnt = db_val("SELECT COUNT(*) FROM transactions WHERE from_account=? AND created_at>datetime('now','-1 hour')",(acc,)) or 0
    if cnt > 5: score+=0.3; reasons.append(f"High frequency: {cnt} txns/hour")
    if to_acc:
        prev = db_val("SELECT COUNT(*) FROM transactions WHERE from_account=? AND to_account=?",(acc,to_acc)) or 0
        if prev==0: score+=0.15; reasons.append("First-time recipient")
    h = datetime.now().hour
    if h>=23 or h<=5: score+=0.1; reasons.append("Odd-hours transaction")
    if amount>=10000 and amount%1000==0: score+=0.05; reasons.append("Round amount")
    return min(round(score,3),1.0), reasons

# ── HELPERS ───────────────────────────────────
def hp(pw): return hashlib.sha256(pw.encode()).hexdigest()

def gen_acc():
    while True:
        num = str(random.randint(10000, 99999))
        if not db_val("SELECT COUNT(*) FROM users WHERE account_number=?", (num,)):
            return num

def login_required(f):
    @wraps(f)
    def d(*a,**k):
        if 'account_number' not in session:
            return jsonify({'error':'Not authenticated','redirect':'/login'}), 401
        return f(*a,**k)
    return d

def admin_required(f):
    @wraps(f)
    def d(*a,**k):
        if not session.get('is_admin'):
            return jsonify({'error':'Admin only'}), 403
        return f(*a,**k)
    return d

# ── RESPONSES ─────────────────────────────────
R = {
 'en': {
  'greeting':"Hello {name}! 👋 Welcome to SmartBank. I can help with balance, transfers, history, and more!",
  'goodbye':"Thank you for using SmartBank, {name}! Have a great day! 👋",
  'balance':"💰 Your current balance is **₹{balance:,.2f}**\nAccount: {account_number} | Updated: {time}",
  'transfer_ask':"💸 Please enter the recipient account number:",
  'transfer_ask_acc':"✅ Got the account! Now please enter the amount to transfer:",
  'transfer_invalid_acc':"❌ Account not found. Please try again or cancel the transfer.",
  'transfer_ask_amt':"✅ Now please enter the amount to transfer:",
  'transfer_confirm':"⚠️ Please confirm transfer:\n👤 To: **{to_name}** ({to_account})\n💰 Amount: **₹{amount:,.2f}**\n\nSay **YES** to confirm or **NO** to cancel.",
  'transfer_confirm_yes':["yes","confirm","ok","okay","send","proceed","சரி","ஆமா","ஆமாம்","ஆம்","அனுப்பு","ha","haan","हाँ","हां","भेजो","हां भेजो"],
  'transfer_cancelled':"❌ Transfer cancelled. Your money is safe.",
  'transfer_done':"✅ Transfer successful!\nNew balance: ₹{balance:,.2f}",
  'transfer_fail':"❌ Insufficient balance. Available: ₹{balance:,.2f}",
  'transfer_flagged':"⚠️ Transaction flagged (Fraud Score: {score})\n{reasons}\nHeld for admin review.",
  'cancel_done':"↩️ Reversed ₹{amount:,.2f} transfer to {to_account}.\nBalance: ₹{balance:,.2f}",
  'cancel_none':"No recent transfer found to cancel.",
  'history':"📋 Your recent transactions:",
  'deposit_ask':"💵 How much would you like to deposit?",
  'deposit_done':"✅ ₹{amount:,.2f} deposited! New balance: ₹{balance:,.2f}",
  'mini_stmt':"🧾 Mini Statement (last 5 transactions):",
  'fraud_alert':"🚨 Fraud alert raised! Account secured. Our team will contact you. Ref: {txn_id}",
  'connect_banker':"📞 Banker request raised (ID: #{req_id}). You'll be contacted in 2-5 minutes.",
  'schedule_ask':"📅 Please provide the recipient account number, amount, and date-time (YYYY-MM-DD HH:MM):",
  'schedule_done':"✅ Scheduled! ₹{amount:,.2f} to {to_account} on {scheduled_at}",
  'analytics':"📊 Opening your analytics...",
  'faq_interest':"📈 Interest Rates:\n• Savings: 4% p.a.\n• FD (1yr): 7.5% p.a.\n• FD (2-3yr): 8% p.a.\n• Personal Loan: 12-18%\n• Home Loan: 8.5-9.5%",
  'faq_loan':"🏦 Loan Application:\n1. Visit branch or apply online\n2. Need: ID proof, income proof, 3-month statements\n3. Processing: 3-7 business days",
  'faq_timing':"🕐 Bank Hours:\n• Mon–Fri: 9:30AM–4:00PM\n• Saturday: 9:30AM–1:00PM\n• Sunday/Holidays: Closed\n• ATM & Net Banking: 24×7",
  'unknown':"I didn't understand that. I can help with:\n• Balance • Transfer • History • Deposit\n• Fraud alert • Banker support • Interest rates",
  'ctx_cancel':"No problem! Transfer cancelled. Is there anything else I can help you with?",
  'ctx_confused':"Please enter the recipient account number:",
  'ctx_confused_amt':"Please enter the amount to transfer:",
 },
 'ta': {
  'greeting':"வணக்கம் {name}! 👋 SmartBank-க்கு வரவேற்கிறோம். என்ன உதவி வேண்டும்?",
  'goodbye':"நன்றி {name}! SmartBank பயன்படுத்தியதற்கு நன்றி! 👋",
  'balance':"💰 உங்கள் இருப்பு: **₹{balance:,.2f}**\nகணக்கு: {account_number}",
  'transfer_ask':"💸 பெறுநர் கணக்கு எண்ணை உள்ளிடவும்:",
  'transfer_ask_acc':"✅ கணக்கு சரிபார்க்கப்பட்டது! அனுப்ப வேண்டிய தொகையை உள்ளிடவும்:",
  'transfer_invalid_acc':"❌ கணக்கு எண் கிடைக்கவில்லை. மீண்டும் முயற்சிக்கவும் அல்லது ரத்து செய்யவும்.",
  'transfer_ask_amt':"✅ கணக்கு கிடைத்தது! இப்போது அனுப்ப வேண்டிய தொகை சொல்லுங்கள்:",
  'transfer_confirm':"⚠️ உறுதிப்படுத்தவும்:\n👤 பெறுநர்: **{to_name}** ({to_account})\n💰 தொகை: **₹{amount:,.2f}**\n\n**ஆமா** என்று சொன்னால் அனுப்பும் | **வேண்டாம்** என்றால் ரத்து.",
  'transfer_cancelled':"❌ பரிமாற்றம் ரத்து. உங்கள் பணம் பாதுகாப்பாக உள்ளது.",
  'transfer_done':"✅ பரிமாற்றம் வெற்றி!\nபுதிய இருப்பு: ₹{balance:,.2f}",
  'transfer_fail':"❌ போதிய இருப்பு இல்லை. கிடைக்கக்கூடியது: ₹{balance:,.2f}",
  'cancel_done':"↩️ ₹{amount:,.2f} திரும்பப் பெறப்பட்டது. இருப்பு: ₹{balance:,.2f}",
  'cancel_none':"ரத்து செய்ய பரிமாற்றம் இல்லை.",
  'history':"📋 சமீபத்திய பரிவர்த்தனைகள்:",
  'deposit_ask':"💵 எவ்வளவு போட வேண்டும்?",
  'deposit_done':"✅ ₹{amount:,.2f} போடப்பட்டது! இருப்பு: ₹{balance:,.2f}",
  'mini_stmt':"🧾 சுருக்க அறிக்கை (கடைசி 5):",
  'fraud_alert':"🚨 மோசடி புகார் பதிவு. Ref: {txn_id}",
  'connect_banker':"📞 வங்கி அதிகாரியை இணைக்கிறோம். ID: #{req_id}",
  'faq_interest':"📈 வட்டி: சேமிப்பு 4% | FD 7.5% | கடன் 12-18%",
  'faq_timing':"🕐 நேரம்: திங்கள்-வெள்ளி 9:30-4:00 | சனி 9:30-1:00",
  'unknown':"புரியவில்லை. இருப்பு, பரிமாற்றம், வரலாறு கேளுங்கள்.",
  'ctx_cancel':"சரி! பரிமாற்றம் ரத்து செய்யப்பட்டது. வேறு என்ன உதவி வேண்டும்?",
  'ctx_confused':"பெறுநர் கணக்கு எண்ணை உள்ளிடவும்:",
  'ctx_confused_amt':"அனுப்ப வேண்டிய தொகையை உள்ளிடவும்:",
 },
 'hi': {
  'greeting':"नमस्ते {name}! 👋 SmartBank में स्वागत है। कैसे मदद करूँ?",
  'goodbye':"धन्यवाद {name}! SmartBank उपयोग के लिए शुक्रिया! 👋",
  'balance':"💰 आपका बैलेंस: **₹{balance:,.2f}**\nखाता: {account_number}",
  'transfer_ask':"💸 प्राप्तकर्ता खाता नंबर दर्ज करें:",
  'transfer_ask_acc':"✅ खाता सत्यापित हुआ! अब ट्रांसफर राशि दर्ज करें:",
  'transfer_invalid_acc':"❌ खाता नहीं मिला। कृपया फिर से प्रयास करें या रद्द करें।",
  'transfer_ask_amt':"✅ खाता मिला! अब ट्रांसफर राशि बताएं:",
  'transfer_confirm':"⚠️ पुष्टि करें:\n👤 प्राप्तकर्ता: **{to_name}** ({to_account})\n💰 राशि: **₹{amount:,.2f}**\n\n**हाँ** बोलें → भेजें | **नहीं** बोलें → रद्द करें।",
  'transfer_cancelled':"❌ ट्रांसफर रद्द। आपका पैसा सुरक्षित है।",
  'transfer_done':"✅ ट्रांसफर सफल!\nनया बैलेंस: ₹{balance:,.2f}",
  'transfer_fail':"❌ अपर्याप्त बैलेंस। उपलब्ध: ₹{balance:,.2f}",
  'cancel_done':"↩️ ₹{amount:,.2f} वापस किया। बैलेंस: ₹{balance:,.2f}",
  'cancel_none':"रद्द करने के लिए कोई ट्रांसफर नहीं।",
  'history':"📋 हाल के लेनदेन:",
  'deposit_ask':"💵 कितना जमा करना चाहते हैं?",
  'deposit_done':"✅ ₹{amount:,.2f} जमा! बैलेंस: ₹{balance:,.2f}",
  'mini_stmt':"🧾 मिनी स्टेटमेंट (अंतिम 5):",
  'fraud_alert':"🚨 धोखाधड़ी शिकायत दर्ज। Ref: {txn_id}",
  'connect_banker':"📞 बैंकर से जोड़ रहे हैं। ID: #{req_id}",
  'faq_interest':"📈 ब्याज: बचत 4% | FD 7.5% | लोन 12-18%",
  'faq_timing':"🕐 समय: सोम-शुक्र 9:30-4:00 | शनि 9:30-1:00",
  'unknown':"समझ नहीं आया। बैलेंस, ट्रांसफर, इतिहास पूछें।",
  'ctx_cancel':"ठीक है! ट्रांसफर रद्द हुआ। और क्या मदद चाहिए?",
  'ctx_confused':"प्राप्तकर्ता खाता नंबर दर्ज करें:",
  'ctx_confused_amt':"ट्रांसफर राशि दर्ज करें:",
 }
}

def say(lang, key, **kw):
    t = R.get(lang, R['en']).get(key, R['en'].get(key,''))
    try: return t.format(**kw)
    except: return t

# ── CHAT PROCESSOR ────────────────────────────
CONFUSION_PATTERNS = [
    r'தெரியல', r'தெரியவில்ல', r'புரியல', r'புரியவில்ல', r'வேண்டாம்',
    r'ரத்து', r'நிறுத்து', r'விடு', r'cancel', r'stop',
    r'नहीं पता', r'पता नहीं', r'नहीं', r'रद्द', r'छोड़ो', r'मत करो',
    r'nahi', r'nahin', r'chodo', r'band karo',
    r"don'?t know", r"no idea", r"not sure", r"i don'?t", r"dunno",
    r"never mind", r"nevermind", r"forget it", r"leave it", r"quit",
    r"exit", r"abort", r"nope", r"nah",
]

def is_confused(text):
    t = text.lower().strip()
    for pat in CONFUSION_PATTERNS:
        if re.search(pat, t, re.IGNORECASE):
            return True
    return False


def execute_transfer(acc, user, amount, to_acc, lang, out):
    to_user = db_one("SELECT * FROM users WHERE account_number=?", (to_acc,))
    if not to_user:
        out['message'] = f"❌ Account {to_acc} not found. Please check and try again."
        return
    if user['balance'] < amount:
        out['message'] = say(lang,'transfer_fail', balance=user['balance'])
        return
    fs, fr = fraud_score(acc, amount, to_acc)
    flagged = 1 if fs > 0.7 else 0
    txn_id = f"TXN{secrets.token_hex(4).upper()}"
    if flagged:
        db_exec("INSERT INTO transactions (txn_id,from_account,to_account,amount,txn_type,description,status,is_flagged,flag_reason,fraud_score) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (txn_id,acc,to_acc,amount,'transfer','NLP Transfer','held',1,"; ".join(fr),fs), commit=True)
        out['message'] = say(lang,'transfer_flagged', score=fs, reasons="\n".join(f"• {r}" for r in fr))
        out['type'] = 'fraud_warning'; out['data'] = {'fraud_score':fs,'reasons':fr}
    else:
        db_exec("UPDATE users SET balance=balance-? WHERE account_number=?", (amount, acc), commit=True)
        db_exec("UPDATE users SET balance=balance+? WHERE account_number=?", (amount, to_acc), commit=True)
        db_exec("INSERT INTO transactions (txn_id,from_account,to_account,amount,txn_type,description,fraud_score) VALUES (?,?,?,?,?,?,?)",
                (txn_id,acc,to_acc,amount,'transfer','NLP Transfer',fs), commit=True)
        user2 = db_one("SELECT * FROM users WHERE account_number=?", (acc,))
        out['message'] = say(lang,'transfer_done', txn_id=txn_id, amount=amount, to_account=to_acc, balance=user2['balance'])
        out['type'] = 'transfer'; out['data'] = {'txn_id':txn_id,'amount':amount,'to_account':to_acc,'new_balance':user2['balance']}

def do_transfer(acc, user, amount, to_acc, lang, out):
    to_user = db_one("SELECT * FROM users WHERE account_number=?", (to_acc,))
    if not to_user:
        out['message'] = f"❌ Account {to_acc} not found. Please check the account number."
        return
    if user['balance'] < amount:
        out['message'] = say(lang,'transfer_fail', balance=user['balance'])
        return
    session['pending_transfer'] = {'amount': amount, 'to_acc': to_acc}
    to_name = to_user['name']
    out['message'] = say(lang,'transfer_confirm', to_name=to_name, to_account=to_acc, amount=amount)
    out['type'] = 'confirm_transfer'
    out['data'] = {'to_name': to_name, 'to_account': to_acc, 'amount': amount}

def process_chat(message, acc, force_lang=''):
    message = normalize_spoken(message)
    lang = force_lang if force_lang in ('en','ta','hi') else nlp.detect_lang(message)
    intent, conf = nlp.predict(message)
    user = db_one("SELECT * FROM users WHERE account_number=?", (acc,))
    name = user['name'] if user else "Customer"
    if conf < 0.25: intent = 'unknown'
    out = {'intent':intent,'language':lang,'confidence':round(conf,3),'type':'text','data':None,'message':''}

    FRESH_INTENTS = {'greeting','goodbye','check_balance','view_history','mini_statement',
                     'analytics','faq_interest','faq_loan','faq_timing','fraud_alert',
                     'connect_banker','cancel_transfer'}

    ctx = session.get('chat_ctx', {})

    # ── If user says cancel/no while a transfer is pending confirmation,
    #    just drop the pending — do NOT reverse any real DB transaction.
    pending_check = session.get('pending_transfer')
    if pending_check and (intent == 'cancel_transfer' or is_confused(message)):
        session.pop('pending_transfer', None)
        session.pop('chat_ctx', None)
        out['message'] = say(lang, 'transfer_cancelled')
        try: db_exec("INSERT INTO chat_logs (account_number,message,response,intent,language) VALUES (?,?,?,?,?)",(acc,message,out['message'],'cancel_transfer',lang),commit=True)
        except: pass
        return out

    if intent in FRESH_INTENTS or is_confused(message):
        session.pop('chat_ctx', None)
        session.pop('pending_transfer', None)
        ctx = {}

    if ctx.get('intent') == 'deposit':
        amount = nlp.extract_amount(message)
        if amount:
            session.pop('chat_ctx', None)
            db_exec("UPDATE users SET balance=balance+? WHERE account_number=?", (amount, acc), commit=True)
            txn_id = f"TXN{secrets.token_hex(4).upper()}"
            db_exec("INSERT INTO transactions (txn_id,from_account,to_account,amount,txn_type,description) VALUES (?,?,?,?,?,?)",
                    (txn_id,acc,acc,amount,'deposit','NLP Deposit'), commit=True)
            user2 = db_one("SELECT * FROM users WHERE account_number=?", (acc,))
            out['message'] = say(lang,'deposit_done', amount=amount, balance=user2['balance'])
            out['type'] = 'transfer'
            out['data'] = {'new_balance': user2['balance']}
            return out
        else:
            out['message'] = say(lang,'deposit_ask')
            return out

    pending = session.get('pending_transfer')
    if pending:
        msg_lower = message.lower().strip()
        yes_words = ['yes','y','ok','okay','confirm','send','proceed','sure','yep','yeah',
                     'சரி','ஆமா','ஆமாம்','ஆம்','அனுப்பு','சரிதான்',
                     'haan','ha','han','हाँ','हां','भेजो','हाँ भेजो']
        no_words  = ['no','n','cancel','stop','nope','nah','dont',
                     'வேண்டாம்','ரத்து','நிறுத்து','வேண்டா',
                     'nahi','nahin','mat','रद्द','नहीं','मत']
        is_yes = any(w in msg_lower for w in yes_words)
        is_no  = any(w in msg_lower for w in no_words) or is_confused(message)
        if is_yes:
            session.pop('pending_transfer', None)
            session.pop('chat_ctx', None)
            execute_transfer(acc, user, pending['amount'], pending['to_acc'], lang, out)
            return out
        elif is_no:
            session.pop('pending_transfer', None)
            session.pop('chat_ctx', None)
            out['message'] = say(lang,'transfer_cancelled')
            return out
        else:
            to_user = db_one("SELECT * FROM users WHERE account_number=?", (pending['to_acc'],))
            to_name = to_user['name'] if to_user else pending['to_acc']
            out['message'] = say(lang,'transfer_confirm', to_name=to_name,
                                 to_account=pending['to_acc'], amount=pending['amount'])
            out['type'] = 'confirm_transfer'
            out['data'] = {'to_name':to_name,'to_account':pending['to_acc'],'amount':pending['amount']}
            return out

    if ctx.get('intent') == 'transfer_money':
        existing_acc = ctx.get('to_acc')
        if existing_acc:
            # Already have valid account — now collect amount
            amount = nlp.extract_amount(message)
            if amount:
                session.pop('chat_ctx', None)
                out['intent'] = 'transfer_money'
                do_transfer(acc, user, amount, existing_acc, lang, out)
            else:
                session['chat_ctx'] = {'intent':'transfer_money','amount':None,'to_acc':existing_acc}
                out['message'] = say(lang,'transfer_ask_acc'); out['type']='ask_amount'
            return out
        else:
            # Need account number first — extract and validate
            to_acc = nlp.extract_account(message)
            if to_acc:
                to_user = db_one("SELECT * FROM users WHERE account_number=?", (to_acc,))
                if not to_user:
                    session['chat_ctx'] = {'intent':'transfer_money','amount':None,'to_acc':None}
                    out['message'] = say(lang,'transfer_invalid_acc')
                    out['type'] = 'invalid_account'
                    return out
                # Valid — ask for amount
                session['chat_ctx'] = {'intent':'transfer_money','amount':None,'to_acc':to_acc}
                out['message'] = say(lang,'transfer_ask_acc'); out['type']='ask_amount'
            else:
                session['chat_ctx'] = {'intent':'transfer_money','amount':None,'to_acc':None}
                out['message'] = say(lang,'transfer_ask'); out['type']='ask_account'
            return out

    if intent not in ('unknown',):
        session.pop('chat_ctx', None)
        ctx = {}

    if intent=='greeting': out['message']=say(lang,'greeting',name=name)
    elif intent=='goodbye': out['message']=say(lang,'goodbye',name=name)
    elif intent=='check_balance':
        if user:
            out['message']=say(lang,'balance',balance=user['balance'],account_number=acc,time=datetime.now().strftime("%d %b %Y %H:%M"))
            out['type']='balance'; out['data']={'balance':user['balance'],'account':acc}
        else: out['message']="Account not found."
    elif intent=='transfer_money':
        # Always ask account number first, then amount
        to_acc = nlp.extract_account(message)
        if to_acc:
            to_user = db_one("SELECT * FROM users WHERE account_number=?", (to_acc,))
            if not to_user:
                session['chat_ctx'] = {'intent':'transfer_money','amount':None,'to_acc':None}
                out['message'] = say(lang,'transfer_invalid_acc')
                out['type'] = 'invalid_account'
            else:
                amount = nlp.extract_amount(message)
                if amount:
                    session.pop('chat_ctx', None)
                    do_transfer(acc, user, amount, to_acc, lang, out)
                else:
                    session['chat_ctx'] = {'intent':'transfer_money','amount':None,'to_acc':to_acc}
                    out['message'] = say(lang,'transfer_ask_acc'); out['type']='ask_amount'
        else:
            session['chat_ctx'] = {'intent':'transfer_money','amount':None,'to_acc':None}
            out['message'] = say(lang,'transfer_ask'); out['type']='ask_account'
    elif intent=='cancel_transfer':
        last=db_one("SELECT * FROM transactions WHERE from_account=? AND txn_type='transfer' AND status='success' ORDER BY created_at DESC LIMIT 1",(acc,))
        if not last: out['message']=say(lang,'cancel_none')
        else:
            db_exec("UPDATE users SET balance=balance+? WHERE account_number=?",(last['amount'],acc),commit=True)
            db_exec("UPDATE users SET balance=balance-? WHERE account_number=?",(last['amount'],last['to_account']),commit=True)
            db_exec("UPDATE transactions SET status='reversed' WHERE id=?",(last['id'],),commit=True)
            user=db_one("SELECT * FROM users WHERE account_number=?",(acc,))
            out['message']=say(lang,'cancel_done',amount=last['amount'],to_account=last['to_account'],balance=user['balance'])
    elif intent=='view_history':
        txns=db_all("SELECT * FROM transactions WHERE from_account=? OR to_account=? ORDER BY created_at DESC LIMIT 10",(acc,acc))
        out['message']=say(lang,'history'); out['type']='history'; out['data']=[dict(t) for t in txns]
    elif intent=='mini_statement':
        txns=db_all("SELECT * FROM transactions WHERE from_account=? OR to_account=? ORDER BY created_at DESC LIMIT 5",(acc,acc))
        out['message']=say(lang,'mini_stmt'); out['type']='mini_statement'; out['data']=[dict(t) for t in txns]
    elif intent=='deposit':
        amount=nlp.extract_amount(message)
        if not amount:
            session['chat_ctx'] = {'intent':'deposit'}
            out['message']=say(lang,'deposit_ask')
        else:
            db_exec("UPDATE users SET balance=balance+? WHERE account_number=?",(amount,acc),commit=True)
            txn_id=f"TXN{secrets.token_hex(4).upper()}"
            db_exec("INSERT INTO transactions (txn_id,from_account,to_account,amount,txn_type,description) VALUES (?,?,?,?,?,?)",(txn_id,acc,acc,amount,'deposit','NLP Deposit'),commit=True)
            user=db_one("SELECT * FROM users WHERE account_number=?",(acc,))
            out['message']=say(lang,'deposit_done',amount=amount,balance=user['balance'])
    elif intent=='fraud_alert':
        txn_id=f"FR{secrets.token_hex(3).upper()}"
        db_exec("UPDATE users SET is_locked=1 WHERE account_number=?",(acc,),commit=True)
        out['message']=say(lang,'fraud_alert',txn_id=txn_id)
    elif intent=='connect_banker':
        req_id=db_exec("INSERT INTO banker_requests (account_number,reason) VALUES (?,?)",(acc,message),commit=True)
        if MSSQL_ENABLED:
            _mssql_exec("INSERT INTO banker_requests (account_number,reason) VALUES (?,?)",(acc,message),commit=True)
        out['message']=say(lang,'connect_banker',req_id=req_id)
        out['type']='banker_connect'; out['data']={'request_id':req_id}
    elif intent=='schedule_transfer':
        amount=nlp.extract_amount(message); to_acc=nlp.extract_account(message)
        dm=re.search(r'(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)',message)
        if not amount or not to_acc or not dm: out['message']=say(lang,'schedule_ask')
        else:
            sched=dm.group(1)
            if len(sched)==10: sched+=" 09:00"
            db_exec("INSERT INTO scheduled_transfers (from_account,to_account,amount,scheduled_at) VALUES (?,?,?,?)",(acc,to_acc,amount,sched),commit=True)
            out['message']=say(lang,'schedule_done',amount=amount,to_account=to_acc,scheduled_at=sched)
    elif intent=='analytics': out['message']=say(lang,'analytics'); out['type']='analytics_redirect'
    elif intent=='faq_interest': out['message']=say(lang,'faq_interest')
    elif intent=='faq_loan': out['message']=say(lang,'faq_loan') if 'faq_loan' in R.get(lang,{}) else R['en']['faq_loan']
    elif intent=='faq_timing': out['message']=say(lang,'faq_timing')
    else: out['message']=say(lang,'unknown')

    try: db_exec("INSERT INTO chat_logs (account_number,message,response,intent,language) VALUES (?,?,?,?,?)",(acc,message,out['message'],intent,lang),commit=True)
    except: pass
    return out

# ── AUTH ROUTES ───────────────────────────────
@app.route('/')
def index():
    if 'account_number' in session:
        return redirect('/admin' if session.get('is_admin') else '/dashboard')
    return redirect('/login')

@app.route('/login')
def login_page(): return render_template('login.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json or {}
    acc  = data.get('account_number','').strip()
    pw   = data.get('password','')
    if not acc or not pw: return jsonify({'error':'Account number and password required'}),400
    user = db_one("SELECT * FROM users WHERE account_number=?",(acc,))
    if not user: return jsonify({'error':'Account not found. Check your account number.'}),404
    if user['is_locked']: return jsonify({'error':'Account locked. Contact admin.'}),403
    if user['password_hash']!=hp(pw):
        fa=(user['failed_attempts'] or 0)+1
        db_exec("UPDATE users SET failed_attempts=? WHERE account_number=?",(fa,acc),commit=True)
        if fa>=5:
            db_exec("UPDATE users SET is_locked=1 WHERE account_number=?",(acc,),commit=True)
            return jsonify({'error':'Too many attempts. Account locked.'}),403
        return jsonify({'error':f'Wrong password. {5-fa} attempts left.'}),401
    db_exec("UPDATE users SET failed_attempts=0, last_login=datetime('now') WHERE account_number=?",(acc,),commit=True)
    session.clear(); session['account_number']=acc; session['name']=user['name']; session['is_admin']=bool(user['is_admin'])
    return jsonify({'success':True,'is_admin':bool(user['is_admin']),'name':user['name']})

@app.route('/api/register', methods=['POST'])
def api_register():
    data=request.json or {}
    name=data.get('name','').strip(); email=data.get('email','').strip().lower()
    phone=data.get('phone','').strip(); pw=data.get('password',''); acc_type=data.get('account_type','savings')
    if not name: return jsonify({'error':'Full name is required'}),400
    if not email: return jsonify({'error':'Email is required'}),400
    if not pw or len(pw)<6: return jsonify({'error':'Password must be at least 6 characters'}),400

    try:
        existing = db_one("SELECT id FROM users WHERE email=?",(email,))
        if existing:
            return jsonify({'error':'Email already registered. Please login.'}),409

        acc_num = gen_acc()
        pin_h = hp("1234"); initial = 1000.0
        txn_id = f"TXN{secrets.token_hex(4).upper()}"

        db_exec(
            "INSERT INTO users (account_number,name,email,phone,password_hash,pin_hash,balance,account_type) VALUES (?,?,?,?,?,?,?,?)",
            (acc_num,name,email,phone,hp(pw),pin_h,initial,acc_type), commit=True)
        db_exec(
            "INSERT INTO transactions (txn_id,from_account,to_account,amount,txn_type,description) VALUES (?,?,?,?,?,?)",
            (txn_id,acc_num,acc_num,initial,'deposit','Welcome Bonus'), commit=True)

        saved = db_one("SELECT id,account_number,name FROM users WHERE account_number=?",(acc_num,))
        if not saved:
            return jsonify({'error':'Registration failed — database did not save. Please try again.'}),500

        print(f"✅ NEW USER REGISTERED: {acc_num} | {name} | {email}")
        return jsonify({'success':True,'account_number':acc_num,
                        'message':f'Account {acc_num} created! ₹1,000 welcome bonus credited. Your login: Account Number = {acc_num}, Password = what you set'})
    except Exception as e:
        print(f"❌ Register error: {e}")
        if 'UNIQUE' in str(e) or 'unique' in str(e): return jsonify({'error':'Email already registered'}),409
        return jsonify({'error':f'Registration error: {str(e)}'}),500

@app.route('/api/logout', methods=['POST'])
def api_logout(): session.clear(); return jsonify({'success':True})

# ── PAGE ROUTES ───────────────────────────────
@app.route('/dashboard')
def dashboard():
    if 'account_number' not in session: return redirect('/login')
    return render_template('dashboard.html',name=session.get('name'),account=session.get('account_number'))

@app.route('/admin')
def admin_page():
    if not session.get('is_admin'): return redirect('/login')
    return render_template('admin.html',name=session.get('name'))

# ── CUSTOMER APIs ─────────────────────────────
@app.route('/api/chat', methods=['POST'])
@login_required
def api_chat():
    body = request.json or {}
    msg  = body.get('message','').strip()
    force_lang = body.get('lang','').strip()
    if not msg: return jsonify({'error':'Empty message'}),400
    return jsonify(process_chat(msg, session['account_number'], force_lang=force_lang))

@app.route('/api/profile')
@login_required
def api_profile():
    u=db_one("SELECT account_number,name,email,phone,balance,account_type,created_at,last_login FROM users WHERE account_number=?",(session['account_number'],))
    return jsonify(dict(u))

@app.route('/api/transactions')
@login_required
def api_transactions():
    acc=session['account_number']; page=max(1,int(request.args.get('page',1))); limit=min(100,int(request.args.get('limit',20)))
    offset=(page-1)*limit
    txns=db_all(f"SELECT * FROM (SELECT ROW_NUMBER() OVER (ORDER BY created_at DESC) AS rn, * FROM transactions WHERE from_account=? OR to_account=?) t WHERE rn>{offset} AND rn<={offset+limit}",(acc,acc))
    total=db_val("SELECT COUNT(*) FROM transactions WHERE from_account=? OR to_account=?",(acc,acc)) or 0
    return jsonify({'transactions':[dict(t) for t in txns],'total':total,'page':page})

@app.route('/api/analytics')
@login_required
def api_analytics():
    acc=session['account_number']
    txns=db_all("SELECT * FROM transactions WHERE from_account=? OR to_account=? ORDER BY created_at DESC",(acc,acc))
    if not txns: return jsonify({'monthly':[],'category':[],'summary':{'total_sent':0,'total_received':0,'total_transactions':0,'flagged_transactions':0,'avg_transaction':0}})
    df=pd.DataFrame([dict(t) for t in txns]); df['created_at']=pd.to_datetime(df['created_at']); df['month']=df['created_at'].dt.strftime('%Y-%m')
    sent=df[df['from_account']==acc]; received=df[df['to_account']==acc]
    monthly=sent.groupby('month')['amount'].sum().reset_index(); monthly.columns=['month','amount']
    cat=sent.groupby('description')['amount'].sum().reset_index(); cat.columns=['category','amount']
    cat=cat.sort_values('amount',ascending=False).head(8)
    return jsonify({'monthly':monthly.to_dict('records'),'category':cat.to_dict('records'),'summary':{
        'total_sent':round(float(sent['amount'].sum()) if len(sent) else 0,2),
        'total_received':round(float(received['amount'].sum()) if len(received) else 0,2),
        'total_transactions':len(df),'flagged_transactions':len(df[df['is_flagged']==1]),
        'avg_transaction':round(float(df['amount'].mean()) if len(df) else 0,2)}})

@app.route('/api/scheduled')
@login_required
def api_scheduled():
    return jsonify([dict(r) for r in db_all("SELECT * FROM scheduled_transfers WHERE from_account=? ORDER BY scheduled_at DESC",(session['account_number'],))])

@app.route('/api/scheduled/<int:sid>/cancel', methods=['POST'])
@login_required
def cancel_scheduled(sid):
    s = db_one("SELECT * FROM scheduled_transfers WHERE id=? AND from_account=?", (sid, session['account_number']))
    if not s:
        return jsonify({'error': 'Not found'}), 404
    if s['status'] != 'pending':
        return jsonify({'error': 'Only pending transfers can be cancelled'}), 400
    db_exec("UPDATE scheduled_transfers SET status='cancelled' WHERE id=?", (sid,), commit=True)
    return jsonify({'success': True})

@app.route('/api/transfer', methods=['POST'])
@login_required
def api_transfer():
    data=request.json or {}; acc=session['account_number']
    to_acc=data.get('to_account','').strip().upper()
    try: amount=float(data.get('amount',0))
    except: return jsonify({'error':'Invalid amount'}),400
    desc=data.get('description','Online Transfer') or 'Online Transfer'
    if amount<=0: return jsonify({'error':'Amount must be > 0'}),400
    user=db_one("SELECT * FROM users WHERE account_number=?",(acc,)); to_user=db_one("SELECT * FROM users WHERE account_number=?",(to_acc,))
    if not to_user: return jsonify({'error':f'Account {to_acc} not found'}),404
    if user['balance']<amount: return jsonify({'error':f'Insufficient balance. Available: ₹{user["balance"]:,.2f}'}),400
    fs,fr=fraud_score(acc,amount,to_acc); flagged=1 if fs>0.7 else 0
    if not flagged:
        db_exec("UPDATE users SET balance=balance-? WHERE account_number=?",(amount,acc),commit=True)
        db_exec("UPDATE users SET balance=balance+? WHERE account_number=?",(amount,to_acc),commit=True)
    txn_id=f"TXN{secrets.token_hex(4).upper()}"
    db_exec("INSERT INTO transactions (txn_id,from_account,to_account,amount,txn_type,description,status,is_flagged,flag_reason,fraud_score) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (txn_id,acc,to_acc,amount,'transfer',desc,'held' if flagged else 'success',flagged,"; ".join(fr),fs),commit=True)
    user=db_one("SELECT * FROM users WHERE account_number=?",(acc,))
    return jsonify({'success':True,'txn_id':txn_id,'status':'held' if flagged else 'success','fraud_score':fs,'is_flagged':flagged,'reasons':fr,'new_balance':user['balance']})

@app.route('/api/deposit', methods=['POST'])
@login_required
def api_deposit():
    data=request.json or {}; acc=session['account_number']
    try: amount=float(data.get('amount',0))
    except: return jsonify({'error':'Invalid amount'}),400
    if amount<=0: return jsonify({'error':'Amount must be > 0'}),400
    db_exec("UPDATE users SET balance=balance+? WHERE account_number=?",(amount,acc),commit=True)
    txn_id=f"TXN{secrets.token_hex(4).upper()}"
    db_exec("INSERT INTO transactions (txn_id,from_account,to_account,amount,txn_type,description) VALUES (?,?,?,?,?,?)",(txn_id,acc,acc,amount,'deposit','Manual Deposit'),commit=True)
    user=db_one("SELECT * FROM users WHERE account_number=?",(acc,))
    return jsonify({'success':True,'txn_id':txn_id,'new_balance':user['balance']})

@app.route('/api/cancel-transfer-flow', methods=['POST'])
@login_required
def api_cancel_transfer_flow():
    """Cancel an in-progress transfer flow (no transaction touched). Returns friendly message."""
    session.pop('chat_ctx', None)
    session.pop('pending_transfer', None)
    lang = (request.json or {}).get('lang', 'en')
    msg = say(lang, 'ctx_cancel')
    return jsonify({'message': msg, 'type': 'text', 'intent': 'cancel_flow', 'language': lang})

@app.route('/api/retry-transfer-account', methods=['POST'])
@login_required
def api_retry_transfer_account():
    """Reset ctx so the next message will ask for account number again."""
    session.pop('pending_transfer', None)
    session['chat_ctx'] = {'intent': 'transfer_money', 'amount': None, 'to_acc': None}
    lang = (request.json or {}).get('lang', 'en')
    msg = say(lang, 'transfer_ask')
    return jsonify({'message': msg, 'type': 'ask_account', 'intent': 'transfer_money', 'language': lang})

@app.route('/api/banker-request', methods=['POST'])
@login_required
def api_banker_request():
    data=request.json or {}; acc=session['account_number']
    reason=data.get('reason','Customer requested banker assistance')
    req_id=db_exec("INSERT INTO banker_requests (account_number,reason) VALUES (?,?)",(acc,reason),commit=True)
    return jsonify({'success':True,'request_id':req_id,'message':f'Banker request #{req_id} submitted.'})

# ── ADMIN APIs ────────────────────────────────
@app.route('/api/admin/stats')
@login_required
@admin_required
def admin_stats():
    return jsonify({
        'stats': {
            'total_users':          db_val("SELECT COUNT(*) FROM users WHERE is_admin=0") or 0,
            'total_transactions':   db_val("SELECT COUNT(*) FROM transactions") or 0,
            'flagged_transactions': db_val("SELECT COUNT(*) FROM transactions WHERE is_flagged=1") or 0,
            'total_balance':        round(float(db_val("SELECT SUM(balance) FROM users WHERE is_admin=0") or 0),2),
            'total_deposits':       round(float(db_val("SELECT SUM(amount) FROM transactions WHERE txn_type='deposit'") or 0),2),
            'pending_banker':       db_val("SELECT COUNT(*) FROM banker_requests WHERE status='pending'") or 0,
            'locked_accounts':      db_val("SELECT COUNT(*) FROM users WHERE is_locked=1 AND is_admin=0") or 0,
            'new_today':            db_val("SELECT COUNT(*) FROM users WHERE CONVERT(DATE,created_at)=CONVERT(DATE,GETDATE()) AND is_admin=0") or 0,
        },
        'recent_transactions': [dict(t) for t in db_all("SELECT TOP 100 t.*,u.name as sender_name FROM transactions t LEFT JOIN users u ON t.from_account=u.account_number ORDER BY t.created_at DESC")],
        'users':               [dict(u) for u in db_all("SELECT id,account_number,name,email,phone,balance,account_type,is_locked,failed_attempts,created_at,last_login FROM users WHERE is_admin=0 ORDER BY id DESC")],
        'banker_requests':     [dict(r) for r in db_all("SELECT TOP 50 br.*,u.name as customer_name FROM banker_requests br LEFT JOIN users u ON br.account_number=u.account_number ORDER BY br.created_at DESC")],
        'chat_logs':           [dict(c) for c in db_all("SELECT TOP 60 cl.*,u.name as customer_name FROM chat_logs cl LEFT JOIN users u ON cl.account_number=u.account_number ORDER BY cl.created_at DESC")],
    })

@app.route('/api/admin/user/<acc>')
@login_required
@admin_required
def admin_get_user(acc):
    u=db_one("SELECT * FROM users WHERE account_number=?",(acc,))
    if not u: return jsonify({'error':'Not found'}),404
    txns=db_all("SELECT TOP 30 * FROM transactions WHERE from_account=? OR to_account=? ORDER BY created_at DESC",(acc,acc))
    return jsonify({'user':dict(u),'transactions':[dict(t) for t in txns]})

@app.route('/api/admin/user/<acc>/toggle-lock', methods=['POST'])
@login_required
@admin_required
def admin_toggle_lock(acc):
    u=db_one("SELECT is_locked FROM users WHERE account_number=?",(acc,))
    if not u: return jsonify({'error':'Not found'}),404
    new=0 if u['is_locked'] else 1
    db_exec("UPDATE users SET is_locked=?, failed_attempts=0 WHERE account_number=?",(new,acc),commit=True)
    return jsonify({'success':True,'locked':bool(new)})

@app.route('/api/admin/user/<acc>/reset-password', methods=['POST'])
@login_required
@admin_required
def admin_reset_pw(acc):
    new_pw=(request.json or {}).get('password','Pass@123')
    db_exec("UPDATE users SET password_hash=?, failed_attempts=0, is_locked=0 WHERE account_number=?",(hp(new_pw),acc),commit=True)
    return jsonify({'success':True,'message':f'Password reset. New password: {new_pw}'})

@app.route('/api/admin/user/<acc>/adjust-balance', methods=['POST'])
@login_required
@admin_required
def admin_adjust_bal(acc):
    data=request.json or {}
    try: amount=float(data.get('amount',0))
    except: return jsonify({'error':'Invalid amount'}),400
    action=data.get('action','credit')
    u=db_one("SELECT * FROM users WHERE account_number=?",(acc,))
    if not u: return jsonify({'error':'Not found'}),404
    if action=='debit' and u['balance']<amount: return jsonify({'error':'Insufficient balance'}),400
    if action=='credit': db_exec("UPDATE users SET balance=balance+? WHERE account_number=?",(amount,acc),commit=True)
    else: db_exec("UPDATE users SET balance=balance-? WHERE account_number=?",(amount,acc),commit=True)
    txn_id=f"ADM{secrets.token_hex(4).upper()}"
    db_exec("INSERT INTO transactions (txn_id,from_account,to_account,amount,txn_type,description) VALUES (?,?,?,?,?,?)",
            (txn_id,acc,acc,amount,'deposit' if action=='credit' else 'withdrawal',f'Admin {action.title()}'),commit=True)
    u=db_one("SELECT balance FROM users WHERE account_number=?",(acc,))
    return jsonify({'success':True,'new_balance':u['balance']})

@app.route('/api/admin/flag/<int:txn_id>', methods=['POST'])
@login_required
@admin_required
def admin_flag(txn_id):
    action=(request.json or {}).get('action','flag')
    if action=='approve':
        t=db_one("SELECT * FROM transactions WHERE id=?",(txn_id,))
        if t and t['status']=='held':
            db_exec("UPDATE users SET balance=balance-? WHERE account_number=?",(t['amount'],t['from_account']),commit=True)
            db_exec("UPDATE users SET balance=balance+? WHERE account_number=?",(t['amount'],t['to_account']),commit=True)
            db_exec("UPDATE transactions SET status='success',is_flagged=0 WHERE id=?",(txn_id,),commit=True)
    elif action=='reject': db_exec("UPDATE transactions SET status='rejected' WHERE id=?",(txn_id,),commit=True)
    elif action=='flag':   db_exec("UPDATE transactions SET is_flagged=1 WHERE id=?",(txn_id,),commit=True)
    return jsonify({'success':True})

@app.route('/api/admin/banker-request/<int:req_id>', methods=['POST'])
@login_required
@admin_required
def admin_handle_banker(req_id):
    data=request.json or {}
    db_exec("UPDATE banker_requests SET status=?, banker_note=? WHERE id=?",(data.get('status','resolved'),data.get('note',''),req_id),commit=True)
    return jsonify({'success':True})

@app.route('/api/admin/create-user', methods=['POST'])
@login_required
@admin_required
def admin_create_user():
    data=request.json or {}
    name=data.get('name','').strip(); email=data.get('email','').strip().lower()
    phone=data.get('phone','').strip(); pw=data.get('password','Pass@123')
    acc_type=data.get('account_type','savings'); balance=float(data.get('balance',0))
    if not name or not email: return jsonify({'error':'Name and email required'}),400
    if db_one("SELECT id FROM users WHERE email=?",(email,)): return jsonify({'error':'Email exists'}),409
    acc_num=gen_acc()
    db_exec("INSERT INTO users (account_number,name,email,phone,password_hash,pin_hash,balance,account_type) VALUES (?,?,?,?,?,?,?,?)",
            (acc_num,name,email,phone,hp(pw),hp("1234"),balance,acc_type),commit=True)
    if balance>0:
        db_exec("INSERT INTO transactions (txn_id,from_account,to_account,amount,txn_type,description) VALUES (?,?,?,?,?,?)",
                (f"ADM{secrets.token_hex(4).upper()}",acc_num,acc_num,balance,'deposit','Initial Deposit by Admin'),commit=True)
    print(f"✅ Admin created user: {acc_num} {name}")
    return jsonify({'success':True,'account_number':acc_num,'message':f'User {name} created as {acc_num}. Password: {pw}'})

@app.route('/api/admin/user/<acc>/delete', methods=['DELETE'])
@login_required
@admin_required
def admin_delete_user(acc):
    if acc == session.get('account_number'):
        return jsonify({'error': 'Cannot delete your own admin account.'}), 400
    user = db_one("SELECT * FROM users WHERE account_number=?", (acc,))
    if not user:
        return jsonify({'error': 'Account not found.'}), 404
    if user['is_admin']:
        return jsonify({'error': 'Cannot delete admin accounts.'}), 400
    try:
        db_exec("DELETE FROM transactions WHERE from_account=? OR to_account=?", (acc, acc), commit=True)
        db_exec("DELETE FROM chat_logs WHERE account_number=?", (acc,), commit=True)
        db_exec("DELETE FROM banker_requests WHERE account_number=?", (acc,), commit=True)
        db_exec("DELETE FROM scheduled_transfers WHERE from_account=? OR to_account=?", (acc, acc), commit=True)
        db_exec("DELETE FROM users WHERE account_number=?", (acc,), commit=True)
        print(f"🗑 DELETED user {acc} by admin {session.get('account_number')}")
        return jsonify({'success': True, 'message': f'Account {acc} deleted.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/scheduled')
@login_required
@admin_required
def admin_scheduled():
    return jsonify([dict(s) for s in db_all("SELECT TOP 100 * FROM scheduled_transfers ORDER BY created_at DESC")])

@app.route('/api/admin/stats/live')
@login_required
@admin_required
def admin_live_stats():
    return admin_stats()

@app.route('/api/tts')
def api_tts():
    text = request.args.get('text','').strip()[:300]
    lang = request.args.get('lang','en')
    if not text:
        return jsonify({'error':'No text'}), 400
    from flask import Response
    lang_map = {'ta':'ta', 'hi':'hi', 'en':'en-IN'}
    tl = lang_map.get(lang, 'en-IN')
    sources = [
        f'https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&tl={tl}&q={urllib.parse.quote(text)}',
        f'https://translate.googleapis.com/translate_tts?ie=UTF-8&client=gtx&tl={tl}&q={urllib.parse.quote(text)}',
    ]
    for url in sources:
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0',
                'Accept': 'audio/mpeg,audio/*',
                'Referer': 'https://translate.google.com/',
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                audio_data = resp.read()
            if len(audio_data) > 100:
                return Response(audio_data, mimetype='audio/mpeg',
                    headers={'Cache-Control':'no-cache','Access-Control-Allow-Origin':'*'})
        except Exception as e:
            continue
    return jsonify({'error': 'tts_unavailable', 'fallback': True}), 503

@app.route('/api/version')
def api_version():
    return jsonify({'version': 'v4.0-mssql', 'database': f'{MSSQL_SERVER}/{MSSQL_DATABASE}', 'mssql_enabled': MSSQL_ENABLED})

# ── SCHEDULER ─────────────────────────────────
def run_scheduler():
    while True:
        try:
            for s in db_all("SELECT * FROM scheduled_transfers WHERE status='pending' AND scheduled_at<=CONVERT(NVARCHAR,GETDATE(),120)"):
                u=db_one("SELECT * FROM users WHERE account_number=?",(s['from_account'],))
                if u and u['balance']>=s['amount']:
                    db_exec("UPDATE users SET balance=balance-? WHERE account_number=?",(s['amount'],s['from_account']),commit=True)
                    db_exec("UPDATE users SET balance=balance+? WHERE account_number=?",(s['amount'],s['to_account']),commit=True)
                    db_exec("INSERT INTO transactions (txn_id,from_account,to_account,amount,txn_type,description) VALUES (?,?,?,?,?,?)",
                            (f"SCH{secrets.token_hex(4).upper()}",s['from_account'],s['to_account'],s['amount'],'scheduled','Scheduled Transfer'),commit=True)
                    db_exec("UPDATE scheduled_transfers SET status='completed' WHERE id=?",(s['id'],),commit=True)
                else: db_exec("UPDATE scheduled_transfers SET status='failed' WHERE id=?",(s['id'],),commit=True)
        except Exception as e: print(f"Scheduler: {e}")
        time.sleep(60)

if __name__ == '__main__':
    print("="*50)
    print("🏦 Starting SmartBank...")
    _init_mssql()   # hard-stops if SQL Server is unreachable
    try:
        init_db()
        print("✅ Database OK")
    except Exception as e:
        print(f"❌ DB Error: {e}")
        import traceback; traceback.print_exc()
    try:
        threading.Thread(target=run_scheduler, daemon=True).start()
        print("✅ Scheduler OK")
    except Exception as e:
        print(f"⚠️  Scheduler skipped: {e}")
    print("🏦 SmartBank — http://localhost:5000")
    print("🔐 Admin: 10000 | Password: Admin@123")
    print(f"🗄  SQL Server: {MSSQL_SERVER} / {MSSQL_DATABASE}")
    print("="*50)
    app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)
