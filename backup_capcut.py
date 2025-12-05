#!/usr/bin/env python3
import os
import subprocess
import argparse
import shlex
import fnmatch
import json
import hashlib
from datetime import datetime
from typing import List, Optional, Dict

from dotenv import load_dotenv


# ----------------- PATH HELPERS ----------------- #

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

    phone_capcut_dir = os.getenv(
        "PHONE_CAPCUT_DIR",
        "/sdcard/Android/data/com.lemon.lvoverseas"
    )

    media_dirs_raw = os.getenv("PHONE_MEDIA_DIRS", "")
    media_dirs = [m.strip() for m in media_dirs_raw.split(",") if m.strip()]

    portodb_dir_raw = os.getenv("PORTODB_DB_DIR", "").strip()
    portodb_dir: Optional[str] = portodb_dir_raw or None

    download_ignore_raw = os.getenv("DOWNLOAD_IGNORE_PATTERNS", "")
    download_ignore_patterns = [
        p.strip() for p in download_ignore_raw.split(",") if p.strip()
    ]

    if not adb_path:
        raise SystemExit("[ERROR] ADB_PATH_WSL not set in .env")
    if not backup_root:
        raise SystemExit("[ERROR] BACKUP_ROOT_WSL not set in .env")

    return {
        "ADB_PATH": adb_path,
        "BACKUP_ROOT": backup_root,
        "PHONE_CAPCUT_DIR": phone_capcut_dir.rstrip("/"),
        "PHONE_MEDIA_DIRS": media_dirs,
        "PORTODB_DB_DIR": portodb_dir,
        "DOWNLOAD_IGNORE_PATTERNS": download_ignore_patterns,
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


# ----------------- BACKUP HELPERS ----------------- #

def create_run_directory(backup_root: str) -> str:
    now = datetime.now()
    yyyy = f"{now.year:04d}"
    mm = f"{now.month:02d}"
    dd = f"{now.day:02d}"
    run_ts = now.strftime("%H%M")

    run_dir = os.path.join(backup_root, yyyy, mm, dd, run_ts)
    os.makedirs(run_dir, exist_ok=True)
    print(f"[RUN] Backup root for this run: {run_dir}")
    return run_dir


def backup_capcut_data(adb_path: str, phone_capcut_dir: str, run_dir: str) -> None:
    """
    Pull /sdcard/Android/data/com.lemon.lvoverseas into:
      <run_dir>/capcut_app/com.lemon.lvoverseas/...
    """
    if not phone_capcut_dir:
        print("[INFO] PHONE_CAPCUT_DIR not set; skipping CapCut app data backup.")
        return

    capcut_parent_wsl = os.path.join(run_dir, "capcut_app")
    os.makedirs(capcut_parent_wsl, exist_ok=True)

    try:
        capcut_parent_win = wsl_to_win_path(capcut_parent_wsl)
    except ValueError as e:
        print(f"[PATH CONVERT FAIL] {e}")
        return

    print(f"[STEP] Backing up CapCut app data from {phone_capcut_dir} ...")
    result = run_adb(adb_path, ["pull", phone_capcut_dir, capcut_parent_win])
    if result.returncode != 0:
        print("[WARN] CapCut data backup may have failed:")
        print(result.stderr.strip())
    else:
        print("[OK] CapCut data backed up to", capcut_parent_wsl)


def should_ignore_download_file(path: str, ignore_patterns: List[str]) -> bool:
    """
    Returns True if the basename of 'path' matches any ignore pattern.
    """
    filename = os.path.basename(path)
    for pat in ignore_patterns:
        if fnmatch.fnmatch(filename, pat):
            return True
    return False


def backup_media_dirs(
    adb_path: str,
    media_dirs: List[str],
    run_dir: str,
    download_ignore_patterns: List[str]
) -> None:
    """
    For each media dir (e.g. /sdcard/DCIM/Camera, /sdcard/Pictures, /sdcard/Download),
    back up contents into:
        <run_dir>/media/<Name>/...

    - Camera/Pictures/Movies/etc. use bulk adb pull.
    - Download is treated specially: we honor DOWNLOAD_IGNORE_PATTERNS
      and skip matching files.
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

        # --- Special handling for Download --- #
        if name.lower() == "download" and download_ignore_patterns:
            print(f"[STEP] Backing up filtered Download media from {phone_dir} ...")
            out = adb_shell(adb_path, f"find {phone_dir} -type f 2>/dev/null")
            all_files = [line.strip() for line in out.splitlines() if line.strip()]

            files_to_copy = [
                f for f in all_files
                if not should_ignore_download_file(f, download_ignore_patterns)
            ]
            ignored_files = [f for f in all_files if f not in files_to_copy]

            print(f"[DOWNLOAD] {len(all_files)} files total, "
                  f"{len(files_to_copy)} after ignore rules.")
            if ignored_files:
                print("[DOWNLOAD] Ignoring:")
                for f in ignored_files:
                    print("   ", f)

            for f in files_to_copy:
                # Preserve subdirectory structure under Download
                if f.startswith(phone_dir + "/"):
                    rel = f[len(phone_dir) + 1:]
                else:
                    rel = os.path.basename(f)

                rel_dir = os.path.dirname(rel)
                filename = os.path.basename(rel)

                dest_dir_wsl = os.path.join(media_parent_wsl, "Download", rel_dir)
                os.makedirs(dest_dir_wsl, exist_ok=True)

                try:
                    dest_dir_win = wsl_to_win_path(dest_dir_wsl)
                except ValueError as e:
                    print(f"[PATH CONVERT FAIL] {e}")
                    continue

                print(f"[COPY] {f} -> {dest_dir_win}")
                result = run_adb(adb_path, ["pull", f, dest_dir_win])
                if result.returncode != 0:
                    print(f"[WARN] Failed to copy {f}: {result.stderr.strip()}")

            continue  # Skip bulk pull for Download

        # --- Default bulk handling for other media dirs --- #
        print(f"[STEP] Backing up media from {phone_dir} ...")
        result = run_adb(adb_path, ["pull", phone_dir, media_parent_win])
        if result.returncode != 0:
            print("[WARN] Media backup may have failed for {phone_dir}:")
            print(result.stderr.strip())
        else:
            print(f"[OK] Media from {phone_dir} backed up under {media_parent_wsl}")


def list_media_files_for_delete(adb_path: str, media_dirs: List[str]) -> List[str]:
    """
    For each media dir, list all files to include in the delete script.
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

    lines: List[str] = []
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
    lines.append("        esc = shlex.quote(path)")
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


# ----------------- PORTODB SHA + DEDUPE HELPERS ----------------- #

def compute_sha256(path: str) -> str:
    """
    Compute SHA256 for a file in a streaming-safe way.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def write_sha256_file(portodb_root: str) -> Dict[str, str]:
    """
    Walk portodb_root and write SHA256.txt into that directory.
    Returns a dict: relative_path_from_portodb_root -> sha256.
    """
    sha_map: Dict[str, str] = {}
    sha_file_path = os.path.join(portodb_root, "SHA256.txt")

    lines: List[str] = []

    for root, dirs, files in os.walk(portodb_root):
        for name in files:
            full_path = os.path.join(root, name)

            # Don't hash our own SHA file if re-run
            if os.path.abspath(full_path) == os.path.abspath(sha_file_path):
                continue

            rel_path = os.path.relpath(full_path, portodb_root)
            sha256 = compute_sha256(full_path)
            sha_map[rel_path] = sha256
            lines.append(f"{sha256}  {rel_path}")

    with open(sha_file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))

    print(f"[OK] SHA256.txt written with {len(sha_map)} entries at {sha_file_path}")
    return sha_map


def load_portodb_log(log_path: str) -> Dict[str, str]:
    if not os.path.exists(log_path):
        return {}
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        return {}
    except Exception as e:
        print(f"[WARN] Could not read existing PortoDB SHA log: {e}")
        return {}


def save_portodb_log(log_path: str, data: Dict[str, str]) -> None:
    tmp_path = log_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, log_path)
    print(f"[OK] PortoDB SHA log updated at {log_path}")


def generate_portodb_delete_script(files_to_delete: List[str]) -> None:
    """
    Generate a timestamped Python script that deletes *destination* PortoDB
    files whose SHA256 has not changed since last export.
    """
    if not files_to_delete:
        print("[INFO] No unchanged PortoDB files this run; no delete script generated.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"delete_unchanged_portodb_{timestamp}.py"
    script_path = os.path.join(os.getcwd(), filename)

    lines: List[str] = []
    lines.append("import os")
    lines.append("")
    lines.append("FILES_TO_DELETE = [")
    for p in files_to_delete:
        lines.append(f"    r\"{p}\",")
    lines.append("]")
    lines.append("")
    lines.append("def main():")
    lines.append("    for path in FILES_TO_DELETE:")
    lines.append("        if os.path.exists(path):")
    lines.append("            print(f'Removing {path} ...')")
    lines.append("            try:")
    lines.append("                os.remove(path)")
    lines.append("            except Exception as e:")
    lines.append("                print(f'  [WARN] Failed to remove {path}: {e}')")
    lines.append("        else:")
    lines.append("            print(f'Skipping {path}; does not exist.')")
    lines.append("")
    lines.append("if __name__ == '__main__':")
    lines.append("    confirm = input('Type DELETE PORTODB to remove unchanged destination PortoDB files: ')")
    lines.append("    if confirm.strip() == 'DELETE PORTODB':")
    lines.append("        main()")
    lines.append("    else:")
    lines.append("        print('Aborted; no deletions performed.')")
    lines.append("")

    with open(script_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[GENERATED] PortoDB dedupe delete script: {script_path}")
    print("           (Run this to remove unchanged destination PortoDB files for this run.)")


def process_portodb_hashes_and_dedupe(portodb_root: str, backup_root: str) -> None:
    """
    After PortoDB pull into portodb_root, generate SHA256.txt,
    update the running log, and create a delete script for unchanged files.
    """
    if not os.path.isdir(portodb_root):
        print(f"[INFO] PortoDB backup directory not found at {portodb_root}; skipping SHA/dedupe.")
        return

    # 1. Generate SHA256.txt for this run
    current_map = write_sha256_file(portodb_root)

    # 2. Load running log
    log_path = os.path.join(backup_root, "portodb_sha_log.json")
    old_log = load_portodb_log(log_path)

    # 3. Determine which files are unchanged from last run
    # Key: relative path from portodb_root (e.g. "PortoDB/mydb.sqlite")
    # We track per relative path across runs.
    unchanged_abs_paths: List[str] = []
    new_log = dict(old_log)  # carry forward any existing entries

    for rel_path, sha in current_map.items():
        prev_sha = old_log.get(rel_path)
        if prev_sha is not None and prev_sha == sha:
            # unchanged file; mark this *new* copy for deletion
            abs_path = os.path.join(portodb_root, rel_path)
            unchanged_abs_paths.append(abs_path)

        # update log with current hash
        new_log[rel_path] = sha

    # 4. Save updated log
    save_portodb_log(log_path, new_log)

    # 5. Generate per-run delete script for unchanged files
    if unchanged_abs_paths:
        print(f"[INFO] {len(unchanged_abs_paths)} PortoDB files unchanged since last export.")
    generate_portodb_delete_script(unchanged_abs_paths)


# ----------------- PORTODB BACKUP ----------------- #

def backup_portodb_dbs(adb_path: str, portodb_dir: Optional[str], run_dir: str, backup_root: str) -> None:
    """
    Back up PortoDB SQLite databases from the phone using adb.

    Expected default location (set in .env):
      /sdcard/Android/data/com.portofarina.portodb/files/PortoDB

    They are copied into:
      <run_dir>/portodb/PortoDB/...

    After backup, we:
      - generate SHA256.txt for all files under <run_dir>/portodb
      - maintain a running SHA log under BACKUP_ROOT
      - create a per-run delete script for unchanged destination files
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
    result = run_adb(adb_path, ["pull", phone_dir, dest_parent_win])
    if result.returncode != 0:
        print("[WARN] PortoDB backup may have failed:")
        print(result.stderr.strip())
        # If backup failed, don't try to hash/dedupe
        return

    print(f"[OK] PortoDB DBs backed up under {dest_parent_wsl}")

    # Now run SHA256 + dedupe logic on the backed-up tree
    process_portodb_hashes_and_dedupe(dest_parent_wsl, backup_root)


# ----------------- MAIN ----------------- #

def main():
    # CLI args
    parser = argparse.ArgumentParser(description="Android media + CapCut + PortoDB backup script")
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
    download_ignore_patterns = cfg["DOWNLOAD_IGNORE_PATTERNS"]

    print("[CHECK] adb devices")
    devices = run_adb(adb_path, ["devices"]).stdout
    print(devices.strip())
    # don't bail hard if no device line format is weird; just warn
    if not any(line.strip().endswith("device") for line in devices.splitlines()[1:]):
        print("[WARN] No connected/authorized device detected. Make sure USB debugging is on and allowed.")

    run_dir = create_run_directory(backup_root)

    # CapCut external data (optional)
    # backup_capcut_data(adb_path, phone_capcut_dir, run_dir)

    # Media backup
    if media_dirs:
        backup_media_dirs(adb_path, media_dirs, run_dir, download_ignore_patterns)
    else:
        print("[INFO] No PHONE_MEDIA_DIRS specified; skipping media backup.")

    # PortoDB (optional, controlled by CLI + env)
    if not args.skip_portodb:
        backup_portodb_dbs(adb_path, portodb_dir, run_dir, backup_root)
    else:
        print("[INFO] --skip-portodb flag enabled; skipping PortoDB backup.")

    # Delete script (for media only, on the phone)
    if media_dirs:
        print("[STEP] Collecting media file list for delete script...")
        media_files = list_media_files_for_delete(adb_path, media_dirs)
        generate_delete_script(media_files)
    else:
        print("[INFO] No media dirs configured; skipping delete script generation.")

    print("[DONE] Backup complete.")
    print("       Run directory:", run_dir)


if __name__ == "__main__":
    main()
