#!/bin/bash

python3 restore_capcut.py


ADB_PATH_WSL=/mnt/c/Users/rtackett/Downloads/platform-tools-latest-windows/platform-tools/adb.exe
"$ADB_PATH_WSL" shell am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file:///sdcard/DCIM
"$ADB_PATH_WSL" shell am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file:///sdcard/Pictures
"$ADB_PATH_WSL" shell am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file:///sdcard/Movies
"$ADB_PATH_WSL" shell am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file:///sdcard/Download



