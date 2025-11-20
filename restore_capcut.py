#!/usr/bin/env python3
import os
import subprocess
from typing import List

from dotenv import load_dotenv

# ----------------- ENV LOADING ----------------- #

def load_config():
    load_dotenv()

    adb_path = os.getenv("ADB_PATH_WSL")
    backup_root = os.getenv("BACKUP_ROOT_WSL")
    phone_capcut_dir = os.getenv("PHONE_CAPCUT_DIR", "/sdcard/Android/data/com.lemon.lvoverseas")
    media_dirs_raw = os.getenv("PHONE_MEDIA_DIRS", "")

    if not adb_path:
        raise SystemExit("[ERROR] ADB_PATH_WSL not set in .env")
    if not backup_root:
        raise SystemExit("[ERROR] BACKUP_ROOT_WSL not set in .env")

    media_dirs = [m.strip() for m in media_dirs_raw.split(",") if m.strip()]

    return {
        "ADB_PATH": adb_path,
        "BACKUP_ROOT": backup_root,
        "PHONE_CAPCUT_DIR": phone_capcut_dir.rstrip("/"),
        "PHONE_MEDIA_DIRS": media_dirs,
    }

# ----------------- ADB HELPERS ----------------- #

def wsl_to_win_path(wsl_path: str) -> str:
    """
    Convert a WSL path like /mnt/c/Users/rtackett/Documents/CapCutBackups
    to a Windows path like C:\\Users\\rtackett\\Documents\\CapCutBackups.
    """
    wsl_path = os.path.normpath(wsl_path)
    if not wsl_path.startswith("/mnt/"):
        raise ValueError(f"Cannot convert non-/mnt path to Windows path: {wsl_path}")

    parts = wsl_path.split("/")
    # ['', 'mnt', 'c', 'Users', 'rtackett', 'Documents', 'CapCutBackups', ...]
    if len(parts) < 4:
        raise ValueError(f"Unexpected WSL path format: {wsl_path}")

    drive_letter = parts[2].upper()  # 'c' -> 'C'
    rest = parts[3:]                 # ['Users','rtackett',...]
    return drive_letter + ":\\" + "\\".join(rest)


def run_adb(adb_path: str, args: List[str], check: bool = False) -> subprocess.CompletedProcess:
    cmd = [adb_path] + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=check)
    except FileNotFoundError:
        raise SystemExit(
            f"[ERROR] adb executable not found at {adb_path}\n"
            "Check ADB_PATH_WSL in your .env."
        )

# ----------------- RESTORE LOGIC ----------------- #

def find_backup_runs(backup_root: str) -> List[str]:
    """
    Recursively find run directories that contain a 'capcut_app' folder.
    Returns a sorted list of paths.
    """
    run_dirs = []
    if not os.path.isdir(backup_root):
        print("[WARN] BACKUP_ROOT_WSL does not exist yet:", backup_root)
        return run_dirs

    for root, dirs, files in os.walk(backup_root):
        if "capcut_app" in dirs:
            run_dirs.append(os.path.join(root))  # root is the run dir
    run_dirs = sorted(set(run_dirs))
    return run_dirs

def choose_run_dir(run_dirs: List[str]) -> str:
    if not run_dirs:
        raise SystemExit("[ERROR] No backup runs found with a capcut_app folder.")

    print("Available backup runs:")
    for i, path in enumerate(run_dirs):
        print(f"  [{i}] {path}")

    while True:
        choice = input("Enter the index of the run you want to restore from: ")
        try:
            idx = int(choice)
            if 0 <= idx < len(run_dirs):
                return run_dirs[idx]
        except ValueError:
            pass
        print("Invalid choice. Try again.")

def restore_capcut_data(adb_path: str, phone_capcut_dir: str, run_dir: str):
    """
    We backed up CapCut as:
      <run_dir>/capcut_app/com.lemon.lvoverseas/...

    We now push that back to:
      parent of PHONE_CAPCUT_DIR (e.g. /sdcard/Android/data)
    """
    capcut_parent = os.path.join(run_dir, "capcut_app")
    capcut_basename = os.path.basename(phone_capcut_dir)  # com.lemon.lvoverseas
    local_capcut_dir = os.path.join(capcut_parent, capcut_basename)

    if not os.path.isdir(local_capcut_dir):
        print("[WARN] CapCut local directory not found:", local_capcut_dir)
        return

    dest_parent = phone_capcut_dir.rsplit("/", 1)[0]  # e.g. /sdcard/Android/data

    print(f"[STEP] Restoring CapCut data to {dest_parent} ...")

    # Convert local WSL path to Windows for adb.exe
    try:
        local_capcut_dir_win = wsl_to_win_path(local_capcut_dir)
    except ValueError as e:
        print(f"[PATH CONVERT FAIL] {e}")
        return    

    # adb push <local_capcut_dir_win> <dest_parent>
    result = run_adb(adb_path, ["push", local_capcut_dir_win, dest_parent])
    if result.returncode != 0:
        print("[WARN] Restore of CapCut data may have failed:")
        print(result.stderr.strip())
    else:
        print("[OK] CapCut data restored.")

def restore_media_dirs(adb_path: str, media_dirs: List[str], run_dir: str):
    """
    For each e.g. /sdcard/DCIM/Camera, we backed up into:
      <run_dir>/media/Camera/...

    We now push Camera back into /sdcard/DCIM/
    """
    media_parent = os.path.join(run_dir, "media")
    if not os.path.isdir(media_parent):
        print("[INFO] No 'media' folder in this run; skipping media restore.")
        return

    for phone_dir in media_dirs:
        phone_dir = phone_dir.rstrip("/")
        name = os.path.basename(phone_dir)  # e.g. Camera, Pictures
        local_dir = os.path.join(media_parent, name)

        if not os.path.isdir(local_dir):
            print(f"[SKIP] Local media dir not found for {phone_dir}: {local_dir}")
            continue

        try:
            local_dir_win = wsl_to_win_path(local_dir)
        except ValueError as e:
            print(f"[PATH CONVERT FAIL] {e}")
            continue

        dest_parent = phone_dir.rsplit("/", 1)[0]  # e.g. /sdcard/DCIM
        print(f"[STEP] Restoring media {name} to {dest_parent} ...")
        # adb push <local_dir_win> <dest_parent>
        result = run_adb(adb_path, ["push", local_dir_win, dest_parent])

def main():
    cfg = load_config()
    adb_path = cfg["ADB_PATH"]
    backup_root = cfg["BACKUP_ROOT"]
    phone_capcut_dir = cfg["PHONE_CAPCUT_DIR"]
    media_dirs = cfg["PHONE_MEDIA_DIRS"]

    print("[CHECK] adb devices")
    devices = run_adb(adb_path, ["devices"]).stdout
    print(devices.strip())

    run_dirs = find_backup_runs(backup_root)
    run_dir = choose_run_dir(run_dirs)
    print("[INFO] Using backup run:", run_dir)

    restore_capcut_data(adb_path, phone_capcut_dir, run_dir)
    if media_dirs:
        restore_media_dirs(adb_path, media_dirs, run_dir)

    print("[DONE] Restore attempt complete. Open CapCut and verify your projects.")

if __name__ == "__main__":
    main()
