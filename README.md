# 🏦 SmartBank AI — Intelligent NLP-Based Core Banking Simulation System

> A multilingual banking chatbot powered by NLP (TF-IDF + Logistic Regression), featuring voice I/O, fraud detection, scheduled transfers, and a dual Admin/Customer portal. Built with Flask and SQL Server (LocalDB).

---

## 📌 Project Overview

**SmartBank AI** (also referred to as **BankBot 360**) is an academic project that simulates a real-world core banking system with an intelligent conversational interface. Users can perform banking operations — checking balances, transferring funds, viewing history, and more — through natural language in **English, Tamil, and Hindi**.

### Key Highlights

- 🤖 **NLP Chatbot** — Intent classification using TF-IDF vectorizer + Logistic Regression pipeline trained on 600+ samples
- 🌐 **Multilingual** — Supports English, Tamil (தமிழ்), and Hindi (हिन्दी)
- 🔊 **Voice I/O** — Web Speech API for speech-to-text; Google TTS proxy for text-to-speech
- 🔐 **Fraud Detection** — Rule-based scoring system that flags suspicious transactions
- ⏰ **Scheduled Transfers** — Background scheduler executes pending transfers automatically
- 🛡️ **Admin Portal** — Full admin dashboard for user management, transaction monitoring, and fraud review
- 🗄️ **SQL Server** — Uses Microsoft SQL Server LocalDB as the primary database (no SQLite fallback)

---

## 🗂️ Project Structure

```
bank/
├── app.py                  # Main Flask application (NLP, routes, DB logic)
├── training_data.csv       # NLP training dataset (600+ samples, 3 languages, 15 intents)
├── run.bat                 # Windows startup script (starts LocalDB + runs app)
└── templates/
    ├── login.html          # Login / Register page
    ├── dashboard.html      # Customer chatbot portal
    └── admin.html          # Admin management dashboard
```

---

## ⚙️ Prerequisites

| Requirement | Details |
|---|---|
| **Python** | 3.9 or higher |
| **SQL Server LocalDB** | Comes with Visual Studio or SQL Server Express |
| **ODBC Driver** | ODBC Driver 17 for SQL Server (recommended) |
| **sqlcmd** | SQL Server command-line tool (for `run.bat`) |

### Python Dependencies

Install all required packages:

```bash
pip install flask pandas scikit-learn pyodbc
```

---

## 🚀 Getting Started

### 1. Start the Application (Windows)

Double-click `run.bat` or run it from the command prompt:

```bat
run.bat
```

This script will:
1. Start the `MSSQLLocalDB` instance
2. Create the `SmartBankProDB` database if it doesn't exist
3. Launch the Flask application

### 2. Manual Start (if `run.bat` fails)

```bash
# Step 1: Start LocalDB
sqllocaldb start MSSQLLocalDB

# Step 2: Create the database
sqlcmd -S "(localdb)\MSSQLLocalDB" -Q "IF NOT EXISTS (SELECT name FROM sys.databases WHERE name='SmartBankProDB') CREATE DATABASE SmartBankProDB"

# Step 3: Run the app
python app.py
```

### 3. Access the Application

Open your browser and go to:

```
http://localhost:5000
```

---

## 🔐 Default Credentials

| Role | Account Number | Password |
|---|---|---|
| **Admin** | `10000` | `Admin@123` |
| **Customer** | Register via UI | Set during registration |

> Default PIN for admin-created accounts: `1234`

---

## 🗄️ Database Configuration

- **Server:** `(localdb)\MSSQLLocalDB`
- **Database:** `SmartBankProDB`
- **Auth:** Windows Trusted Connection (no username/password needed)
- **Driver:** Auto-detected from ODBC Driver 17 → 13 → SQL Server

### Tables Created Automatically on First Run

| Table | Purpose |
|---|---|
| `users` | Account holders and admin accounts |
| `transactions` | All financial transactions |
| `scheduled_transfers` | Pending/completed future transfers |
| `chat_logs` | Chatbot conversation history |
| `banker_requests` | Customer escalation requests to human banker |

---

## 🤖 NLP Model

The chatbot uses a **scikit-learn pipeline**:

```
TF-IDF Vectorizer  →  Logistic Regression  →  Intent Label
```

- **Training Data:** `training_data.csv` — 600+ samples across 15 intents in 3 languages
- **Languages:** English, Tamil, Hindi
- **Model is trained at startup** — no pre-saved model file required

### Supported Intents

| Intent | Example Phrases |
|---|---|
| `check_balance` | "What is my balance?", "mera balance kya hai" |
| `transfer_money` | "Send ₹500 to account", "பரிமாற்றம் செய்" |
| `view_history` | "Show transactions", "கணக்கு வரலாறு காட்டு" |
| `mini_statement` | "Last 5 transactions", "mini statement dikhao" |
| `deposit` | "Add funds", "deposit cash" |
| `schedule_transfer` | "Set up automatic transfer" |
| `analytics` | "Monthly summary", "மாத செலவு சுருக்கம்" |
| `fraud_alert` | "Suspicious transaction", "மோசடி பரிவர்த்தனை" |
| `faq_loan` | "Loan eligibility", "interest rate" |
| `faq_timing` | "Bank timings", "बैंक का समय" |
| `greeting` | "Hello", "வணக்கம்" |
| `goodbye` | "Thanks, bye", "alvida" |

---

## 🔊 Voice Features

- **Speech-to-Text:** Browser Web Speech API (`continuous: true` mode with silence timer)
- **Text-to-Speech:** Google TTS proxied through `/api/tts` endpoint to bypass CORS
  - Supports `ta` (Tamil), `hi` (Hindi), `en` (English/India)

---

## 🛡️ Fraud Detection

A rule-based scoring system flags transactions based on:
- Unusually large transfer amounts
- High-frequency transactions in a short window
- Transfers to new/unrecognized accounts
- Transactions outside normal banking hours

Flagged transactions are held for admin review before processing.

---

## ⏰ Scheduled Transfers

A background daemon thread (`run_scheduler`) checks every **60 seconds** for pending scheduled transfers and executes them when their scheduled time is reached. Failed transfers (e.g., insufficient balance) are marked as `failed`.

---

## 🖥️ Admin Portal Features

Accessible at `/admin` after logging in with an admin account:

- View all users and balances
- Lock / unlock accounts
- Reset passwords
- Manually credit or debit balances
- View and flag suspicious transactions
- Approve or reject held transactions
- Manage scheduled transfers
- Handle customer banker requests
- Create or delete user accounts

---

## 📡 API Endpoints (Key Routes)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/login` | User login |
| `POST` | `/api/register` | New user registration |
| `POST` | `/api/chat` | Send message to chatbot |
| `GET` | `/api/balance` | Get account balance |
| `GET` | `/api/transactions` | Transaction history |
| `POST` | `/api/transfer` | Fund transfer |
| `POST` | `/api/schedule` | Schedule a future transfer |
| `GET` | `/api/tts` | Text-to-speech proxy |
| `GET` | `/api/version` | App version and DB info |
| `GET` | `/api/admin/stats` | Admin dashboard statistics |

---

## 🐛 Troubleshooting

**LocalDB not starting?**
```bash
sqllocaldb info MSSQLLocalDB
sqllocaldb start MSSQLLocalDB
```

**No ODBC driver found?**
Download and install [ODBC Driver 17 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server).

**Port 5000 already in use?**
Edit the last line in `app.py`:
```python
app.run(debug=False, host='0.0.0.0', port=5001)
```

**Speech recognition not working?**
Ensure you are using a Chromium-based browser (Chrome/Edge) and have microphone permission granted.

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask |
| NLP | scikit-learn (TF-IDF + Logistic Regression) |
| Database | Microsoft SQL Server LocalDB (pyodbc) |
| Frontend | HTML5, CSS3, Vanilla JavaScript |
| Voice | Web Speech API, Google TTS (proxy) |
| Scheduler | Python `threading` (daemon thread) |

---

## 👨‍💻 Academic Context

This project was developed as part of an **MCA (Master of Computer Applications)** academic program. It demonstrates the integration of:
- Natural Language Processing for intent classification
- Full-stack web development with Flask
- Relational database design and management
- Real-time voice interaction in a web app
- Rule-based AI for fraud detection

---

## 📄 License

This project is intended for **academic and educational purposes only**. Not for production use.
