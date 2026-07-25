from pathlib import Path

env = Path(".env").read_text(encoding="utf-8")
for line in env.splitlines():
    line = line.strip()
    if "ELEVEN" in line.upper() and "=" in line:
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        print(f"Key name  : [{k.strip()}]")
        print(f"Key length: {len(v)}")
        print(f"First 8   : {v[:8]}...")
        print(f"Last 4    : ...{v[-4:]}")
        print(f"Has spaces: {' ' in v}")
        print(f"Repr[0:3] : {repr(v[:3])}")
