#!/usr/bin/env python3
import argparse
import os
import subprocess
from datetime import datetime
from typing import List

from dotenv import load_dotenv

import fnmatch

# ----------------- ENV LOADING ----------------- #

def should_ignore_download_file(path: str, ignore_patterns: List[str]) -> bool:
    """
    Returns True if the file path matches any ignore pattern.
    Patterns use Unix shell matching: *.zip, junk*, etc.
    """
    filename = os.path.basename(path)
    for pat in ignore_patterns:
        if fnmatch.fnmatch(filename, pat):
            return True
    return False



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

import shlex

def shell_escape(path: str) -> str:
    """
    Escape an Android shell path safely for use in rm commands.
    Uses shlex.quote() which safely handles spaces, quotes, parentheses, etc.
    """
    return shlex.quote(path)

def generate_delete_script(media_files: List[str]) -> None:
    """
    Generate a timestamped Python script that safely deletes phone files
    using adb shell rm, with proper shell escaping.
    """
    if not media_files:
        print("[INFO] No media files collected; delete script will not be generated.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"files_to_delete_{timestamp}.py"
    script_path = os.path.join(os.getcwd(), filename)

    lines = []
    lines.append("import os")
    lines.append("import subprocess")
    lines.append("from dotenv import load_dotenv")
    lines.append("")
    lines.append("load_dotenv()")
    lines.append("ADB_PATH = os.getenv('ADB_PATH_WSL')")
    lines.append("")
    lines.append("if not ADB_PATH:")
    lines.append("    raise SystemExit('[ERROR] ADB_PATH_WSL not set in .env')")
    lines.append("")
    lines.append("PHONE_FILES = [")
    for p in media_files:
        lines.append(f"    r\"{p}\",")
    lines.append("]")
    lines.append("")
    lines.append("def run_adb(args):")
    lines.append("    cmd = [ADB_PATH] + args")
    lines.append("    return subprocess.run(cmd, text=True)")
    lines.append("")
    lines.append("def main():")
    lines.append("    import shlex")
    lines.append("    for path in PHONE_FILES:")
    lines.append("        esc = shlex.quote(path)")  # SAFE
    lines.append("        print(f'Deleting {path} ...')")
    lines.append("        run_adb(['shell', f\"rm -f {esc}\"])")
    lines.append("")
    lines.append("if __name__ == '__main__':")
    lines.append("    confirm = input('Type DELETE to remove these media files from the phone: ')")
    lines.append("    if confirm.strip() == 'DELETE':")
    lines.append("        main()")
    lines.append("    else:")
    lines.append("        print('Aborted; no deletions performed.')")
    lines.append("")

    with open(script_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[GENERATED] Delete script: {script_path}")
    print("           (Run this later *after* verifying your backup.)")

def backup_portodb_dbs(adb_path: str, portodb_dir: str | None, run_dir: str) -> None:
    """
    Back up PortoDB SQLite databases from the phone using adb.

    By default, we expect the databases to live under something like:
      /sdcard/Android/data/com.portofarina.portodb/files/PortoDB

    We copy that directory into:
      <run_dir>/portodb/PortoDB/...

    If PORTODB_DB_DIR is not set in .env, this is a no-op.
    """
    if not portodb_dir:
        print("[INFO] PORTODB_DB_DIR not set; skipping PortoDB backup.")
        return

    phone_dir = portodb_dir.rstrip("/")
    dest_parent_wsl = os.path.join(run_dir, "portodb")
    os.makedirs(dest_parent_wsl, exist_ok=True)

    try:
        dest_parent_win = wsl_to_win_path(dest_parent_wsl)
    except ValueError as e:
        print(f"[PATH CONVERT FAIL] {e}")
        return

    print(f"[STEP] Backing up PortoDB SQLite DBs from {phone_dir} ...")

    # Simple approach: pull the whole folder.
    # adb pull /sdcard/Android/data/com.portofarina.portodb/files/PortoDB  <run_dir>/portodb
    result = run_adb(adb_path, ["pull", phone_dir, dest_parent_win])
    if result.returncode != 0:
        print("[WARN] PortoDB backup may have failed:")
        print(result.stderr.strip())
    else:
        print(f"[OK] PortoDB DBs backed up under {dest_parent_wsl}")


def load_config():
    load_dotenv()

    adb_path = os.getenv("ADB_PATH_WSL")
    backup_root = os.getenv("BACKUP_ROOT_WSL")
    phone_capcut_dir = os.getenv("PHONE_CAPCUT_DIR", "/sdcard/Android/data/com.lemon.lvoverseas")
    media_dirs_raw = os.getenv("PHONE_MEDIA_DIRS", "")
    portodb_dir = os.getenv("PORTODB_DB_DIR")

    if not adb_path:
        raise SystemExit("[ERROR] ADB_PATH_WSL not set in .env")
    if not backup_root:
        raise SystemExit("[ERROR] BACKUP_ROOT_WSL not set in .env")

    media_dirs = [m.strip() for m in media_dirs_raw.split(",") if m.strip()]

    ignore_raw = os.getenv("DOWNLOAD_IGNORE_PATTERNS", "")
    ignore_patterns = [p.strip() for p in ignore_raw.split(",") if p.strip()]


    return {
        "ADB_PATH": adb_path,
        "BACKUP_ROOT": backup_root,
        "PHONE_CAPCUT_DIR": phone_capcut_dir.rstrip("/"),
        "PHONE_MEDIA_DIRS": media_dirs,
        "DOWNLOAD_IGNORE_PATTERNS": ignore_patterns,
	"PORTODB_DB_DIR": portodb_dir,
    }

# ----------------- ADB HELPERS ----------------- #

def run_adb(adb_path: str, args: List[str], check: bool = False) -> subprocess.CompletedProcess:
    cmd = [adb_path] + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=check)
    except FileNotFoundError:
        raise SystemExit(
            f"[ERROR] adb executable not found at {adb_path}\n"
            "Check ADB_PATH_WSL in your .env and verify the path in WSL matches where adb.exe lives on Windows."
        )

def adb_shell(adb_path: str, cmd: str) -> str:
    result = run_adb(adb_path, ["shell", cmd])
    if result.stderr.strip():
        print("[ADB STDERR]", result.stderr.strip())
    return result.stdout

def list_media_files_for_delete(adb_path: str, media_dirs: List[str]) -> List[str]:
    """
    For each media dir (e.g. /sdcard/DCIM/Camera), use adb shell find
    to list all files to include in the delete script.
    Returns a list of full phone paths.
    """
    all_files: List[str] = []

    for phone_dir in media_dirs:
        phone_dir = phone_dir.rstrip("/")
        if not phone_dir:
            continue

        print(f"[SCAN] Listing files for delete under {phone_dir} ...")
        out = adb_shell(adb_path, f"find {phone_dir} -type f 2>/dev/null")
        for line in out.splitlines():
            p = line.strip()
            if p:
                all_files.append(p)

    print(f"[INFO] Collected {len(all_files)} media files for potential deletion.")
    return all_files


# ----------------- BACKUP LOGIC ----------------- #

def create_run_directory(backup_root: str) -> str:
    now = datetime.now()
    date_yyyy = f"{now.year:04d}"
    date_mm = f"{now.month:02d}"
    date_dd = f"{now.day:02d}"
    run_timestamp = now.strftime("%H%M")  # 1902, etc.

    run_dir = os.path.join(backup_root, date_yyyy, date_mm, date_dd, run_timestamp)
    os.makedirs(run_dir, exist_ok=True)

    print(f"[RUN] Backup root for this run: {run_dir}")
    return run_dir

def backup_capcut_data(adb_path: str, phone_capcut_dir: str, run_dir: str):
    """
    Pull /sdcard/Android/data/com.lemon.lvoverseas into:
      <run_dir>/capcut_app/com.lemon.lvoverseas/...
    """
    capcut_parent_wsl = os.path.join(run_dir, "capcut_app")
    os.makedirs(capcut_parent_wsl, exist_ok=True)

    try:
        capcut_parent_win = wsl_to_win_path(capcut_parent_wsl)
    except ValueError as e:
        print(f"[PATH CONVERT FAIL] {e}")
        return

    print(f"[STEP] Backing up CapCut app data from {phone_capcut_dir} ...")
    # adb pull <phone_capcut_dir> <capcut_parent_win>
    result = run_adb(adb_path, ["pull", phone_capcut_dir, capcut_parent_win])
    if result.returncode != 0:
        print("[WARN] CapCut data backup may have failed:")
        print(result.stderr.strip())
    else:
        print("[OK] CapCut data backed up to", capcut_parent_wsl)

def backup_media_dirs(adb_path: str, media_dirs: List[str], run_dir: str):
    """
    For each e.g. /sdcard/DCIM/Camera, pulls into:
      <run_dir>/media/Camera/...
    """
    media_parent_wsl = os.path.join(run_dir, "media")
    os.makedirs(media_parent_wsl, exist_ok=True)

    try:
        media_parent_win = wsl_to_win_path(media_parent_wsl)
    except ValueError as e:
        print(f"[PATH CONVERT FAIL] {e}")
        return

    for phone_dir in media_dirs:
        phone_dir = phone_dir.rstrip("/")
        name = os.path.basename(phone_dir)
        if not name:
            print(f"[SKIP] Invalid media dir: {phone_dir}")
            continue

        print(f"[STEP] Backing up media from {phone_dir} ...")
        # adb pull /sdcard/DCIM/Camera  <run_dir>/media (Windows path)
        result = run_adb(adb_path, ["pull", phone_dir, media_parent_win])
        if result.returncode != 0:
            print(f"[WARN] Media backup may have failed for {phone_dir}:")
            print(result.stderr.strip())
        else:
            print(f"[OK] Media from {phone_dir} backed up under {media_parent_wsl}")


def main():
    # ---------------------------
    # Command-line parameter
    # ---------------------------
    parser = argparse.ArgumentParser(description="Android backup script")
    parser.add_argument(
        "--skip-portodb",
        action="store_true",
        help="Skip backing up PortoDB SQLite databases"
    )
    args = parser.parse_args()

    cfg = load_config()
    adb_path = cfg["ADB_PATH"]
    backup_root = cfg["BACKUP_ROOT"]
    phone_capcut_dir = cfg["PHONE_CAPCUT_DIR"]
    media_dirs = cfg["PHONE_MEDIA_DIRS"]
    portodb_dir = cfg["PORTODB_DB_DIR"]

    print("[CHECK] adb devices")
    devices = run_adb(adb_path, ["devices"]).stdout
    print(devices.strip())
    if "device" not in devices.splitlines()[-1:]:
        print("[WARN] No connected/authorized device detected. Make sure USB debugging is on and allowed.")

    run_dir = create_run_directory(backup_root)

    # backup_capcut_data(adb_path, phone_capcut_dir, run_dir)
    if not args.skip_portodb:
        backup_portodb_dbs(adb_path, portodb_dir, run_dir)
    else:
        print("[INFO] --skip-portodb enabled; skipping PortoDB backup.")
 


    if media_dirs:
        backup_media_dirs(adb_path, media_dirs, run_dir)
    else:
        print("[INFO] No PHONE_MEDIA_DIRS specified; skipping media backup.")
        media_dirs = []

    # NEW: PortoDB SQLite databases
    backup_portodb_dbs(adb_path, portodb_dir, run_dir)

    # NEW: build delete script for original media on phone
    if media_dirs:
        print("[STEP] Collecting media file list for delete script...")
        media_files = list_media_files_for_delete(adb_path, media_dirs)
        generate_delete_script(media_files)
    else:
        print("[INFO] No media dirs configured; skipping delete script generation.")

    print("[DONE] backup complete.")
    print("       Run directory:", run_dir)

if __name__ == "__main__":
    main()
