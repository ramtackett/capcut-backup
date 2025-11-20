#!/usr/bin/env python3
import os
import subprocess
from typing import List

from dotenv import load_dotenv


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


# ----------------- ENV LOADING ----------------- #

def load_config():
    load_dotenv()

    adb_path = os.getenv("ADB_PATH_WSL")
    backup_root = os.getenv("BACKUP_ROOT_WSL")
    media_dirs_raw = os.getenv("PHONE_MEDIA_DIRS", "")

    if not adb_path:
        raise SystemExit("[ERROR] ADB_PATH_WSL not set in .env")
    if not backup_root:
        raise SystemExit("[ERROR] BACKUP_ROOT_WSL not set in .env")

    media_dirs = [m.strip() for m in media_dirs_raw.split(",") if m.strip()]

    return {
        "ADB_PATH": adb_path,
        "BACKUP_ROOT": backup_root,
        "PHONE_MEDIA_DIRS": media_dirs,
    }


# ----------------- ADB HELPERS ----------------- #

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
    Recursively find run directories that contain a 'media' folder.
    Returns a sorted list of paths.
    """
    run_dirs = []
    if not os.path.isdir(backup_root):
        print("[WARN] BACKUP_ROOT_WSL does not exist yet:", backup_root)
        return run_dirs

    for root, dirs, files in os.walk(backup_root):
        if "media" in dirs:
            run_dirs.append(os.path.join(root))  # root is the run dir
    run_dirs = sorted(set(run_dirs))
    return run_dirs


def choose_run_dir(run_dirs: List[str]) -> str:
    if not run_dirs:
        raise SystemExit("[ERROR] No backup runs found with a 'media' folder.")

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


def restore_media_dirs(adb_path: str, media_dirs: List[str], run_dir: str):
    """
    For each e.g. /sdcard/DCIM/Camera, we backed up into:
      <run_dir>/media/Camera/...

    We now push Camera back into /sdcard/DCIM/, Pictures into /sdcard/, etc.
    """
    media_parent_wsl = os.path.join(run_dir, "media")
    if not os.path.isdir(media_parent_wsl):
        print("[INFO] No 'media' folder in this run; skipping media restore.")
        return

    for phone_dir in media_dirs:
        phone_dir = phone_dir.rstrip("/")
        name = os.path.basename(phone_dir)  # e.g. Camera, Pictures, Movies, Download
        local_dir_wsl = os.path.join(media_parent_wsl, name)

        if not os.path.isdir(local_dir_wsl):
            print(f"[SKIP] Local media dir not found for {phone_dir}: {local_dir_wsl}")
            continue

        try:
            local_dir_win = wsl_to_win_path(local_dir_wsl)
        except ValueError as e:
            print(f"[PATH CONVERT FAIL] {e}")
            continue

        dest_parent = phone_dir.rsplit("/", 1)[0]  # e.g. /sdcard/DCIM
        print(f"[STEP] Restoring media {name} to {dest_parent} ...")
        # adb push <local_dir_win> <dest_parent>
        result = run_adb(adb_path, ["push", local_dir_win, dest_parent])
        if result.returncode != 0:
            print(f"[WARN] Restore of media {phone_dir} may have failed:")
            print(result.stderr.strip())
        else:
            print(f"[OK] Media restored for {phone_dir}")


def main():
    cfg = load_config()
    adb_path = cfg["ADB_PATH"]
    backup_root = cfg["BACKUP_ROOT"]
    media_dirs = cfg["PHONE_MEDIA_DIRS"]

    print("[CHECK] adb devices")
    devices = run_adb(adb_path, ["devices"]).stdout
    print(devices.strip())

    run_dirs = find_backup_runs(backup_root)
    run_dir = choose_run_dir(run_dirs)
    print("[INFO] Using backup run:", run_dir)

    if media_dirs:
        restore_media_dirs(adb_path, media_dirs, run_dir)
    else:
        print("[INFO] No PHONE_MEDIA_DIRS configured; nothing to restore.")

    print("[DONE] Media restore attempt complete. Check your DCIM/Pictures/etc. on the phone.")


if __name__ == "__main__":
    main()
