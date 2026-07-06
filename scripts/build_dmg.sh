#!/bin/bash
# ============================================================
# AFS DMG 构建脚本 (macOS)
# 方式1: 直接运行可执行  ./AFS
# 方式2: AppleScript 外壳 AFS.app（双击打开终端+浏览器）
# ============================================================
set -e

AFS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$AFS_DIR/dist"
BUILD_DIR="$AFS_DIR/build"
APP_NAME="AFS"
DMG_NAME="AFS-Installer"

echo "============================================"
echo "  ⚡ AFS DMG 构建"
echo "============================================"
echo ""

# 1. 安装依赖
echo "[1/5] 安装 Python 依赖..."
pip3 install -r "$AFS_DIR/requirements.txt" pyinstaller --quiet 2>&1 | tail -1

# 2. 清理
echo "[2/5] 清理旧产物..."
rm -rf "$DIST_DIR" "$BUILD_DIR" "$AFS_DIR/$APP_NAME.spec" 2>/dev/null

# 3. PyInstaller
echo "[3/5] PyInstaller 打包..."
cd "$AFS_DIR"
python3 -m PyInstaller \
    --name="$APP_NAME" \
    --onefile \
    --console \
    --add-data "templates:templates" \
    --add-data "static:static" \
    --hidden-import flask \
    --hidden-import requests \
    --hidden-import gateway \
    --clean \
    app.py

echo "[3/5] 打包完成 → $DIST_DIR/$APP_NAME"

# 4. 创建 AppleScript 外壳（双击打开终端+浏览器）
echo "[4/5] 创建启动外壳..."
LAUNCHER_DIR="$DIST_DIR/LauncherScript.app"
rm -rf "$LAUNCHER_DIR"
mkdir -p "$LAUNCHER_DIR/Contents/MacOS"
mkdir -p "$LAUNCHER_DIR/Contents/Resources"

# AppleScript：打开终端运行 AFS，然后打开浏览器
cat > "$LAUNCHER_DIR/Contents/MacOS/LauncherScript" << 'SCRIPT'
#!/usr/bin/osascript

-- 获取当前 .app 所在目录
set appPath to POSIX path of (path to me as string)
set binPath to text 1 thru -2 of (do shell script "dirname " & quoted form of appPath)
set afsBin to binPath & "/AFS"

-- 在后台启动 AFS
do shell script afsBin & " > /tmp/afs.log 2>&1 &"

-- 等 2 秒让服务就绪
delay 2

-- 打开浏览器
do shell script "open http://127.0.0.1:8081/"

-- 显示通知
display notification "AFS 已启动，浏览器已打开管理界面" with title "⚡ AFS" subtitle "服务运行在 http://127.0.0.1:8081"

-- 打开终端显示日志
tell application "Terminal"
    activate
    do script "echo '╔══════════════════════════════╗'; echo '║  ⚡ AFS — AI Fusion Server  ║'; echo '╠══════════════════════════════╣'; echo '║  管理界面: http://127.0.0.1:8081  ║'; echo '║  退出: Ctrl+C 或关闭此窗口        ║'; echo '╚══════════════════════════════╝'; echo ''; tail -f /tmp/afs.log"
end tell
SCRIPT

chmod +x "$LAUNCHER_DIR/Contents/MacOS/LauncherScript"

cat > "$LAUNCHER_DIR/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>AFS</string>
    <key>CFBundleDisplayName</key>
    <string>AFS — AI Fusion Server</string>
    <key>CFBundleIdentifier</key>
    <string>com.afs.launcher</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleExecutable</key>
    <string>LauncherScript</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

# 5. DMG
echo "[5/5] 生成 DMG..."
DMG_PATH="$DIST_DIR/$DMG_NAME.dmg"
rm -f "$DMG_PATH"

TMP_DMG="$BUILD_DIR/dmg_contents"
rm -rf "$TMP_DMG"
mkdir -p "$TMP_DMG"

# 放入 AFS 可执行文件
cp "$DIST_DIR/$APP_NAME" "$TMP_DMG/"
# 放入启动外壳
cp -R "$LAUNCHER_DIR" "$TMP_DMG/"

# Applications 快捷方式
ln -s /Applications "$TMP_DMG/Applications"

# DMG 背景说明
cat > "$TMP_DMG/README.txt" << 'README'
╔═══════════════════════════════════════════╗
║           ⚡  AFS 安装说明                ║
╠═══════════════════════════════════════════╣
║                                           ║
║  方式一（推荐）：双击 AFS.app             ║
║    → 自动打开浏览器管理界面              ║
║    → 终端显示实时日志                    ║
║                                           ║
║  方式二：将 AFS.app 拖入 Applications    ║
║    以后从启动台打开                      ║
║                                           ║
║  管理界面: http://127.0.0.1:8081/        ║
║  停止: 关闭终端窗口 或 Ctrl+C            ║
║                                           ║
╚═══════════════════════════════════════════╝
README

hdiutil create \
    -volname "AFS" \
    -srcfolder "$TMP_DMG" \
    -ov \
    -format UDZO \
    "$DMG_PATH" 2>&1 | tail -1

rm -rf "$TMP_DMG"

echo ""
echo "============================================"
echo "  构建完成"
echo "============================================"
echo ""
echo "  DMG:  $DMG_PATH"
echo ""
echo "  双击 DMG 后:"
echo "    → 双击 AFS.app（启动服务 + 打开浏览器）"
echo "    或 → 终端运行 ./AFS"
echo ""