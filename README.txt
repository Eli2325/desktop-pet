==============================================
    桌面宠物 Desktop Pet - 使用说明
==============================================

【打包步骤】
1. 确保所有文件在同一目录：
   - main.py
   - pet_core.py
   - settings_ui.py
   - assets/ (包含所有GIF和图标)
   - bubbles.json
   - pet_settings.json
   - app_map.json
   - filters.json
   - build.bat

2. 双击运行 build.bat
   - 会自动安装PyInstaller
   - 打包成单个exe文件
   - 生成在 dist\DesktopPet.exe

【使用方法】
1. 运行 dist\DesktopPet.exe
2. 桌宠会出现在屏幕上
3. 右键托盘图标 → 设置

【配置文件位置】
C:\Users\你的用户名\.desktop_pet\
├── bubbles.json        (气泡文案)
├── pet_settings.json   (宠物设置)
├── app_map.json        (应用映射)
└── filters.json        (过滤规则)

【自定义素材】
想替换GIF动画？
1. 在 DesktopPet.exe 旁边创建 custom_assets\ 文件夹
2. 放入你的GIF文件（文件名必须一致）

必需的GIF文件：
├── idle.gif            (待机)
├── idle2.gif           (待机变化)
├── walk.gif            (走路)
├── drag.gif            (拖拽)
├── fall.gif            (下落)
├── wall_slide.gif      (贴墙)
├── ceiling_hang.gif    (挂天花板)
├── sleep_day.gif       (白天睡觉)
└── sleep_night.gif     (夜晚睡觉)

可选的GIF文件：
├── poke.gif            (戳地面)
├── poke_wall.gif       (戳墙)
├── poke_ceiling.gif    (戳天花板)
├── poke_sleep.gif      (戳睡觉)
└── headpat.gif         (摸头)

【GIF规格要求】
- 格式：GIF动画
- 建议尺寸：128x128 或 256x256
- 背景：透明
- 帧数：15-30帧最佳
- 帧率：10-30 FPS

【分享给别人】
方式1：只分享exe
- 把 dist\DesktopPet.exe 发给别人
- 双击运行即可
- 配置和素材都是默认的

方式2：分享完整包（推荐）
- 创建文件夹 DesktopPet\
- 放入：
  ├── DesktopPet.exe
  ├── README.txt (这个文件)
  └── custom_assets\ (可选：你的自定义GIF)
- 打包成zip分享

【常见问题】
Q: 双击exe没反应？
A: 检查是否被杀毒软件拦截，添加信任

Q: 如何恢复默认设置？
A: 删除 C:\Users\你的用户名\.desktop_pet\ 文件夹

Q: 如何替换文案？
A: 右键托盘图标 → 设置 → Text Pool标签页

Q: 如何添加识别的应用？
A: 右键托盘图标 → 设置 → App Mapping标签页

Q: 如何完全卸载？
A: 1. 右键托盘图标 → Exit
   2. 删除 DesktopPet.exe
   3. 删除 C:\Users\你的用户名\.desktop_pet\

【快捷键】
Ctrl + 鼠标滚轮      缩放大小
Ctrl + 拖拽边缘      缩放大小
单击                 戳一下
长按                 摸头
拖拽                 移动位置

【开发者信息】
基于 PyQt6 开发
使用 PyInstaller 打包

==============================================
