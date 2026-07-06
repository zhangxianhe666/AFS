#!/bin/bash
# ============================================================
# AFS DMG 构建脚本 (macOS)
# 使用 PyInstaller 打包为 .app，再用 hdiutil 生成 .dmg
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
pip3 install -r "$AFS_DIR/requirements.txt" pyinstaller --quiet

# 2. 清理旧产物
echo "[2/5] 清理旧产物..."
rm -rf "$DIST_DIR" "$BUILD_DIR"

# 3. PyInstaller 打包
echo "[3/5] PyInstaller 打包..."
cd "$AFS_DIR"
pyinstaller \
    --name="$APP_NAME" \
    --onefile \
    --windowed \
    --add-data "templates:templates" \
    --add-data "static:static" \
    --add-data "gateway.py:." \
    --hidden-import flask \
    --hidden-import requests \
    --hidden-import gateway \
    --clean \
    app.py

# 4. 创建 .app bundle
echo "[4/5] 创建 .app bundle..."
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

cp "$DIST_DIR/$APP_NAME" "$APP_BUNDLE/Contents/MacOS/$APP_NAME"
chmod +x "$APP_BUNDLE/Contents/MacOS/$APP_NAME"

# Info.plist
cat > "$APP_BUNDLE/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>AFS</string>
    <key>CFBundleDisplayName</key>
    <string>AI Fusion Server</string>
    <key>CFBundleIdentifier</key>
    <string>com.afs.gateway</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleExecutable</key>
    <string>AFS</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

# 5. 生成 DMG
echo "[5/5] 生成 DMG..."
DMG_PATH="$DIST_DIR/$DMG_NAME.dmg"
rm -f "$DMG_PATH"

# 创建临时目录用于 DMG 内容
TMP_DMG="$BUILD_DIR/dmg_contents"
rm -rf "$TMP_DMG"
mkdir -p "$TMP_DMG"
cp -R "$APP_BUNDLE" "$TMP_DMG/"
# 创建 Applications 快捷方式
ln -s /Applications "$TMP_DMG/Applications"

hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$TMP_DMG" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

rm -rf "$TMP_DMG"

echo ""
echo "============================================"
echo "  构建完成"
echo "============================================"
echo ""
echo "  DMG:  $DMG_PATH"
echo "  App:  $APP_BUNDLE"
echo ""
echo "  双击 DMG 安装，或将 App 拖入 Applications"
echo ""