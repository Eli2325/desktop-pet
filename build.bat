@echo off
chcp 65001 >nul
echo ========================================
echo 桌面宠物打包工具
echo ========================================
echo.

echo [1/4] 清理旧的打包文件...
if exist "dist" rmdir /s /q dist
if exist "build" rmdir /s /q build
echo 完成！
echo.

echo [2/4] 开始打包...
pyinstaller build_pet.spec --clean
if errorlevel 1 (
    echo 打包失败！请检查错误信息。
    pause
    exit /b 1
)
echo 完成！
echo.

echo [3/4] 复制assets到exe同目录...
xcopy /E /I /Y assets "dist\桌面宠物\assets"
echo 完成！
echo.

echo [4/4] 创建使用说明...
(
echo 桌面宠物 v1.0
echo.
echo 运行方式：
echo 双击 "桌面宠物.exe" 即可运行
echo.
echo 更换外观：
echo 1. 打开 assets 文件夹
echo 2. 用同名GIF文件替换即可（如 idle.gif、walk.gif 等）
echo 3. 重启桌宠生效
echo.
echo 配置文件位置：
echo C:\Users\你的用户名\.desktop_pet
echo.
echo 问题反馈：
echo [在这里填写你的联系方式或GitHub链接]
) > "dist\桌面宠物\使用说明.txt"
echo 完成！
echo.

echo ========================================
echo 打包完成！
echo 输出目录：dist\桌面宠物\
echo ========================================
echo.
echo 按任意键打开输出目录...
pause >nul
explorer "dist\桌面宠物"
