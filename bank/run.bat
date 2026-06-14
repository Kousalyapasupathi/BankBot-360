@echo off
echo ============================================
echo   SmartBank - Starting with SQL Server
echo ============================================

sqllocaldb start MSSQLLocalDB

sqlcmd -S "(localdb)\MSSQLLocalDB" -Q "IF NOT EXISTS (SELECT name FROM sys.databases WHERE name='SmartBankProDB') CREATE DATABASE SmartBankProDB" -b
IF %ERRORLEVEL% NEQ 0 (
    echo ERROR: Could not create database. Is LocalDB running?
    pause
    exit /b 1
)

echo Database SmartBankProDB is ready.
python app.py
pause
