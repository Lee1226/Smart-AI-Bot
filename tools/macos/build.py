#!/usr/bin/env python3
"""Build the native app and bundled Python engine on a Mac with Xcode installed.

Run with a build venv containing pyinstaller + tools/adb-bridge-requirements.txt.
SAB_MAC_SIGN_IDENTITY optionally enables Developer ID signing (not notarization).
"""
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
work = root / 'build'
dist = root / 'dist'
app = dist / 'SAB Device Helper.app'
if platform.system() != 'Darwin':
    raise SystemExit('Build on macOS')
dist.mkdir(exist_ok=True)
identity = os.getenv('SAB_MAC_SIGN_IDENTITY', '-')
engine_sign_options = [] if identity == '-' else ['--codesign-identity', identity]
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
                '--name', 'sab-engine', '--paths', str(root.parent),
                *engine_sign_options,
                '--hidden-import', 'ios_bridge', '--hidden-import', 'ios_usbmux',
                '--hidden-import', 'hdc_bridge', '--hidden-import', 'device_host',
                '--collect-data', 'certifi', '--distpath', str(work / 'frozen'),
                '--workpath', str(work / 'pyinstaller'), '--specpath', str(work), str(root / 'engine.py')], check=True)
if app.exists():
    shutil.rmtree(app)
contents = app / 'Contents'
binary = contents / 'MacOS'
resources = contents / 'Resources'
binary.mkdir(parents=True)
resources.mkdir()
shutil.copytree(work / 'frozen/sab-engine', resources / 'engine', symlinks=True)
subprocess.run(['xcrun', 'swiftc', '-swift-version', '5', '-parse-as-library', '-O',
                '-target', platform.machine() + '-apple-macosx14.0',
                '-framework', 'SwiftUI', '-framework', 'AppKit', str(root / 'SABDeviceHelper.swift'),
                '-o', str(binary / 'SABDeviceHelper')], check=True)
info = dict(CFBundleExecutable='SABDeviceHelper', CFBundleIdentifier='io.github.rejigtian.smart-ai-bot.device-helper',
            CFBundleName='SAB Device Helper', CFBundleDisplayName='SAB 设备助手',
            CFBundleShortVersionString='0.2.0', CFBundleVersion='3', LSMinimumSystemVersion='14.0',
            NSHighResolutionCapable=True, CFBundlePackageType='APPL',
            CFBundleURLTypes=[dict(CFBundleURLName='SAB Mac Helper Pairing', CFBundleURLSchemes=['sab-helper'])])
with (contents / 'Info.plist').open('wb') as stream:
    plistlib.dump(info, stream)
sign_options = [] if identity == '-' else ['--options', 'runtime', '--timestamp']
subprocess.run(['codesign', '--force', '--deep', '--sign', identity, *sign_options, str(app)], check=True)
# A valid signature alone does not prove the frozen Python runtime can load.
subprocess.run([str(resources / 'engine/sab-engine'), '--self-check'], check=True)
archive = dist / ('SAB-Device-Helper-macOS-' + platform.machine() + '.zip')
subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(app), str(archive)], check=True)
print('App:', app)
print('Archive:', archive)
print('Developer ID notarization is a separate release step.' if identity == '-' else 'Signed; notarization remains a separate release step.')
