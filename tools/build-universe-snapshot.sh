#!/bin/bash
# Build and publish the universe-snapshot zipapp for the collector box.
#
# The box is a 512 MB t4g.nano with no third-party Python. All bundled modules
# are stdlib-only (enforced below), so `python3 universe-snapshot.pyz` just runs.
# Shipping one file mirrors how the collector binary deploys: upload to the
# artifacts prefix, and the next scheduled start picks it up.
# NO_UPLOAD=1 builds the .pyz only (no AWS call).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUCKET="${1:-marketdata-047915756052}"
REGION="${AWS_DEFAULT_REGION:-ap-southeast-3}"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

mkdir -p "$BUILD/screener"
touch "$BUILD/screener/__init__.py"
for m in emitten universe_snapshot sb_http pit_recorder pit_tables pit_store; do
  cp "$ROOT/research/screener/$m.py" "$BUILD/screener/"
done

# Fail the build rather than ship something that cannot import on the box.
python3 - "$BUILD" <<'PY'
import ast, sys, pathlib
root = pathlib.Path(sys.argv[1])
std = set(sys.stdlib_module_names) | {"screener"}
bad = []
for f in root.rglob("*.py"):
    t = ast.parse(f.read_text())
    mods = {n.module.split(".")[0] for n in ast.walk(t) if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name.split(".")[0] for n in ast.walk(t) if isinstance(n, ast.Import) for a in n.names}
    bad += [(f.name, m) for m in sorted(mods - std)]
if bad:
    sys.exit(f"third-party imports would break the box: {bad}")
print("stdlib-only check: OK")
PY

cat > "$BUILD/__main__.py" <<'MAIN'
from screener.universe_snapshot import main
main()
MAIN

OUT="$ROOT/tools/universe-snapshot.pyz"
python3 -m zipapp "$BUILD" -o "$OUT" -p "/usr/bin/env python3"
echo "built $OUT ($(wc -c < "$OUT") bytes)"

if [ "${NO_UPLOAD:-}" = "1" ]; then
  echo "NO_UPLOAD=1: built only, nothing uploaded"
  exit 0
fi

aws s3 cp "$OUT" "s3://$BUCKET/artifacts/universe-snapshot.pyz" --region "$REGION"
echo "published to s3://$BUCKET/artifacts/universe-snapshot.pyz"
