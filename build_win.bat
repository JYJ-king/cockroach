@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] 安装依赖...
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [2/3] 用 PyInstaller 打包 exe...
python -m PyInstaller --noconfirm cockroach_pet_win.spec
if errorlevel 1 goto :fail

echo [3/3] 完成
echo.
echo 可执行文件: dist\cockroach_pet.exe
echo 命令行运行: dist\cockroach_pet.exe
echo 源码运行:   python cockroach_pet.py
echo.
pause
exit /b 0

:fail
echo 打包失败，请检查上方报错。
pause
exit /b 1
