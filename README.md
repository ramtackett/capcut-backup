Backs up source media files for CapCut from Android USB connected phone to Windows 11 PC. 
Does not restore meta files or actual JSON used by CapCut. All edits in capcut must be done again manually.
Assumes Android Platform Tools from Google (Android developer site) already installed on Windows

USAGE: 
1. Enable Developer options (if not already):
* Settings → About phone
* Scroll to Build number
* Tap it 7 times
→ it should say “You are now a developer” (or similar).

2. Enable USB debugging:
* Settings → System → Developer options
* Find USB debugging
* Turn it ON

Plug the phone into your PC with a good data-capable cable
(some cables only charge; if in doubt, grab the one that came with the phone or a known data cable).

After plugging in:

Unlock the Pixel

Pull down the notification shade

Tap the USB notification, and set mode to File transfer (or “Transferring files / Android Auto”) – not “Charge only”.

---
Setup your .ENV variables:

\# Path to adb.exe as seen from WSL

\# Example if adb.exe is at C:\Android\platform-tools\adb.exe:

ADB_PATH_WSL=/mnt/c/Android/platform-tools/adb.exe

\# Where backups should be stored (WSL path).

\# Pick something OUTSIDE your Git repo so you don't commit video files.

BACKUP_ROOT_WSL=/mnt/c/Users/USER_NAME/Documents/CapCutBackups

\# Comma-separated list of media directories to back up (phone side)

\# Add/remove as needed.

PHONE_MEDIA_DIRS=/sdcard/DCIM/Camera,/sdcard/Pictures,/sdcard/Movies,/sdcard/Download

---
TO BACKUP: 

WSL -> python3 backup_capcut.py

* confirm files got backed up
* Edit files_to_delete_*.py to remove any file references you want preserved on the phone
* python3 files_to_delete_YYYYMMDD_HHMM.py # deletes files off of the Android phone

TO RESTORE: 

WSL -> restore.sh # chmod +x first

