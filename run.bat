@echo off
chcp 65001 >nul
cd /d "%~dp0"
python cockroach_pet.py
pause
