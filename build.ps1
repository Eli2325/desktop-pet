# PowerShell packaging script
$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Desktop Pet Packager" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. Clean old artifacts
Write-Host "[1/4] Cleaning old packaging artifacts..." -ForegroundColor Yellow
if (Test-Path "dist_pack") { Remove-Item -Recurse -Force "dist_pack" }
if (Test-Path "build_pack") { Remove-Item -Recurse -Force "build_pack" }
Write-Host "Done." -ForegroundColor Green
Write-Host ""

# 2. Build onedir package
Write-Host "[2/4] Building onedir package..." -ForegroundColor Yellow
pyinstaller build_pet.spec --clean --distpath dist_pack --workpath build_pack
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed. Please check the error output above." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "Done." -ForegroundColor Green
Write-Host ""

# 3. Copy external assets for customization
Write-Host "[3/4] Copying external assets for customization..." -ForegroundColor Yellow
if (-not (Test-Path "dist_pack\DesktopPet\assets")) {
    New-Item -ItemType Directory -Path "dist_pack\DesktopPet\assets" | Out-Null
}
Copy-Item -Path "assets\*" -Destination "dist_pack\DesktopPet\assets" -Recurse -Force
Write-Host "Done." -ForegroundColor Green
Write-Host ""

# 4. Write package notes
Write-Host "[4/4] Writing package notes..." -ForegroundColor Yellow
$readme = @"
Desktop Pet package

Run:
Double-click "DesktopPet.exe"

Customize artwork:
1. Open the assets folder next to the exe
2. Replace GIF files with the same names (idle.gif, walk.gif, etc.)
3. Restart the app

User config folder:
C:\Users\YourUserName\.desktop_pet

[Add your contact info here]
"@
$readme | Out-File -FilePath "dist_pack\DesktopPet\README_PACKAGE.txt" -Encoding UTF8
Write-Host "Done." -ForegroundColor Green
Write-Host ""

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Build complete." -ForegroundColor Green
Write-Host "Output: dist_pack\DesktopPet\" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Open output folder
Write-Host "Opening output folder..." -ForegroundColor Yellow
Start-Process "dist_pack\DesktopPet"

Read-Host "Press Enter to exit"
