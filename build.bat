@echo off
chcp 65001 >nul
echo ========================================
echo Desktop Pet Packager
echo ========================================
echo.

echo [1/4] Cleaning old packaging artifacts...
if exist "dist_pack" rmdir /s /q dist_pack
if exist "build_pack" rmdir /s /q build_pack
echo Done.
echo.

echo [2/4] Building onedir package...
pyinstaller build_pet.spec --clean --distpath dist_pack --workpath build_pack
if errorlevel 1 (
    echo Build failed. Please check the error output above.
    pause
    exit /b 1
)
echo Done.
echo.

echo [3/4] Copying external assets for customization...
if not exist "dist_pack\DesktopPet\assets" mkdir "dist_pack\DesktopPet\assets"
xcopy /E /I /Y assets "dist_pack\DesktopPet\assets" >nul
echo Done.
echo.

echo [4/4] Writing package notes...
(
echo Desktop Pet package
echo.
echo Run:
echo Double-click "DesktopPet.exe"
echo.
echo Customize artwork:
echo 1. Open the assets folder next to the exe
echo 2. Replace GIF files with the same names (idle.gif, walk.gif, etc.)
echo 3. Restart the app
echo.
echo User config folder:
echo C:\Users\YourUserName\.desktop_pet
echo.
echo Feedback:
echo [Add your contact info here]
) > "dist_pack\DesktopPet\README_PACKAGE.txt"
echo Done.
echo.

echo ========================================
echo Build complete.
echo Output: dist_pack\DesktopPet\
echo ========================================
echo.
echo Press any key to open output folder...
pause >nul
explorer "dist_pack\DesktopPet"
