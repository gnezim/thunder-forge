import sys
from pathlib import Path

# Make thunder_admin importable for tests
admin_path = str(Path(__file__).parent.parent / "admin")
if admin_path not in sys.path:
    sys.path.insert(0, admin_path)
