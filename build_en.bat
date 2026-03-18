@echo off
chcp 65001 >nul
cls
echo ========================================
echo Desktop Pet Build Tool
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing PyInstaller...
    pip install pyinstaller
)

echo [Step 1] Cleaning old build files...
if exist build rd /s /q build
if exist dist rd /s /q dist
if exist *.spec del /q *.spec

echo [Step 2] Building executable...
echo.

pyinstaller --name=DesktopPet --windowed --onedir --icon=assets/icon.ico --add-data "assets;assets" --hidden-import=logger main.py

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed!
    pause
    exit /b 1
)

echo.
echo [Step 3] Copying assets folder...
xcopy /E /I /Y assets dist\DesktopPet\assets

echo.
echo [Step 4] Build completed!
echo.
echo Output: dist\DesktopPet\
echo.
echo ========================================
echo To distribute:
echo 1. Compress dist\DesktopPet folder
echo 2. Share the ZIP file
echo ========================================
echo.
pause
