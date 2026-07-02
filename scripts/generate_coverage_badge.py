#!/usr/bin/env python3
"""Generate a coverage.svg badge from the .coverage file."""

import os
import sys

from coverage import Coverage
from coverage.exceptions import NoDataError

OUTPUT_DIR = "badges"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "coverage.svg")

c = Coverage()
c.load()
try:
    total = c.report()
except NoDataError:
    print("No .coverage data found — skipping badge generation.", file=sys.stderr)
    sys.exit(0)
pct = round(total)

color = (
    "#4c1" if pct >= 90 else "#dfb317" if pct >= 75 else "#e05d44"
)

svg = (
    f'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="20">'
    f'<rect width="120" height="20" fill="#24292e" rx="3"/>'
    f'<rect x="60" width="60" height="20" fill="{color}" rx="3"/>'
    f'<text x="30" y="14" fill="#fff" '
    f'font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">coverage</text>'
    f'<text x="90" y="14" fill="#fff" '
    f'font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">{pct}%</text>'
    f'</svg>'
)

os.makedirs(OUTPUT_DIR, exist_ok=True)
with open(OUTPUT_FILE, "w") as f:
    f.write(svg)

print(f"Coverage badge written: {pct}%")
