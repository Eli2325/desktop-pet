@echo off
echo ========================================
echo Checking files...
echo ========================================
echo.

if exist main.py (
    echo [OK] main.py
) else (
    echo [X] main.py MISSING!
)

if exist pet_core.py (
    echo [OK] pet_core.py
) else (
    echo [X] pet_core.py MISSING!
)

if exist settings_ui.py (
    echo [OK] settings_ui.py
) else (
    echo [X] settings_ui.py MISSING!
)

if exist logger.py (
    echo [OK] logger.py
) else (
    echo [X] logger.py MISSING!
)

if exist assets\icon.ico (
    echo [OK] assets\icon.ico
) else (
    echo [X] assets\icon.ico MISSING!
)

if exist assets\bubbles.json (
    echo [OK] assets\bubbles.json
) else (
    echo [X] assets\bubbles.json MISSING!
)

if exist assets\pet_settings.json (
    echo [OK] assets\pet_settings.json
) else (
    echo [X] assets\pet_settings.json MISSING!
)

if exist assets\app_map.json (
    echo [OK] assets\app_map.json
) else (
    echo [X] assets\app_map.json MISSING!
)

if exist assets\filters.json (
    echo [OK] assets\filters.json
) else (
    echo [X] assets\filters.json MISSING!
)

echo.
echo ========================================
echo Check completed!
echo ========================================
pause
