import sqlite3, os, tempfile, shutil
from pathlib import Path

local = os.environ.get('LOCALAPPDATA', '')
src = Path(local) / 'Google' / 'Chrome' / 'User Data' / 'Default' / 'Network' / 'Cookies'
print(f'Source: {src}')
print(f'Exists: {src.exists()}')

if src.exists():
    tmp = Path(tempfile.mkdtemp()) / 'Cookies'

    # Use Volume Shadow Copy to bypass Chrome's exclusive lock
    try:
        from shadowcopy import shadow_copy
        with shadow_copy(str(src)) as shadow_path:
            print(f'Shadow path: {shadow_path}')
            shutil.copy2(shadow_path, tmp)
            print(f'Method: shadowcopy (VSS)')
    except Exception as e:
        print(f'shadowcopy failed: {e}')
        tmp = None

    if tmp and tmp.exists():
        print(f'Backup size: {tmp.stat().st_size} bytes')
        conn = sqlite3.connect(str(tmp))
        rows = conn.execute(
            "SELECT name, length(encrypted_value) FROM cookies "
            "WHERE host_key = '.linkedin.com' AND name = 'li_at'"
        ).fetchall()
        conn.close()
        print(f'li_at rows: {rows}')
        shutil.rmtree(tmp.parent, ignore_errors=True)
else:
    print('Chrome Cookies file not found')
