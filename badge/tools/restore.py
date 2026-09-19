"""Restore the badge from a full 4MB flash backup — the un-brick button.

  python restore.py                      # restore the pristine backup
  python restore.py current              # restore the current-state backup
  python restore.py path/to/image.bin    # restore a specific image

To enter download mode if the badge won't respond normally: hold START (GPIO9,
the BOOT strap) while plugging in USB, then run this. Port defaults to COM5;
override with BADGE_PORT. Needs esptool (in the scratch venv used for the dumps).
"""
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP = os.path.join(HERE, "..", "backup")
PORT = os.environ.get("BADGE_PORT", "COM5")

# sha256 of each 4MB image, checked before flashing so a truncated file can't brick the badge.
KNOWN = {
    "badge_pristine_full_4MB.bin": "6db8f20fd6ed259e964ffe9fd76749b68cabe5e8bb78d35fc11f024e096bfbfa",
    "badge_current_full_4MB.bin": "18b9d8ed1371107257bd3c1bf06d9c3013b56b6f2cf050ff3bbee0f237b8ad71",
}

arg = sys.argv[1] if len(sys.argv) > 1 else "pristine"
name = {"pristine": "badge_pristine_full_4MB.bin", "current": "badge_current_full_4MB.bin"}.get(arg, arg)
path = name if os.path.isabs(name) or os.sep in name else os.path.join(BACKUP, name)

data = open(path, "rb").read()
digest = hashlib.sha256(data).hexdigest()
if len(data) != 0x400000:
    sys.exit(f"refusing: {path} is {len(data)} bytes, expected 4194304")
base = os.path.basename(path)
if base in KNOWN and KNOWN[base] != digest:
    sys.exit(f"refusing: {base} sha256 {digest} does not match the known-good hash")

try:
    import esptool  # noqa: F401
except ImportError:
    sys.exit("esptool not installed for this Python. Run: python -m pip install esptool")

print(f"restoring {path}\n  sha256 {digest}\n  to {PORT} — do not unplug until it finishes")
subprocess.run([sys.executable, "-m", "esptool", "--port", PORT, "--baud", "921600",
                "write-flash", "0x0", path], check=True)
