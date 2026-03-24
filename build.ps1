# PowerShell 打包脚本
$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "桌面宠物打包工具" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. 清理旧文件
Write-Host "[1/4] 清理旧的打包文件..." -ForegroundColor Yellow
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
Write-Host "完成！" -ForegroundColor Green
Write-Host ""

# 2. 打包
Write-Host "[2/4] 开始打包..." -ForegroundColor Yellow
pyinstaller build_pet.spec --clean
if ($LASTEXITCODE -ne 0) {
    Write-Host "打包失败！请检查错误信息。" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}
Write-Host "完成！" -ForegroundColor Green
Write-Host ""

# 3. 复制assets
Write-Host "[3/4] 复制assets到exe同目录..." -ForegroundColor Yellow
Copy-Item -Path "assets" -Destination "dist\桌面宠物\assets" -Recurse -Force
Write-Host "完成！" -ForegroundColor Green
Write-Host ""

# 4. 创建使用说明
Write-Host "[4/4] 创建使用说明..." -ForegroundColor Yellow
$readme = @"
桌面宠物 v1.0

运行方式：
双击 "桌面宠物.exe" 即可运行

更换外观：
1. 打开 assets 文件夹
2. 用同名GIF文件替换即可（如 idle.gif、walk.gif 等）
3. 重启桌宠生效

配置文件位置：
C:\Users\你的用户名\.desktop_pet

问题反馈：
[在这里填写你的联系方式或GitHub链接]
"@
$readme | Out-File -FilePath "dist\桌面宠物\使用说明.txt" -Encoding UTF8
Write-Host "完成！" -ForegroundColor Green
Write-Host ""

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "打包完成！" -ForegroundColor Green
Write-Host "输出目录：dist\桌面宠物\" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 打开输出目录
Write-Host "正在打开输出目录..." -ForegroundColor Yellow
Start-Process "dist\桌面宠物"

Read-Host "按回车键退出"
