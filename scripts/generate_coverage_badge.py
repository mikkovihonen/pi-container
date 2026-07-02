#!/usr/bin/env python3
"""Generate a coverage.svg badge from the .coverage file."""

import os
import sys

from coverage import Coverage
from coverage.exceptions import NoDataError

OUTPUT_DIR = "badges"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "coverage.svg")

# Shields.io-style badge dimensions (2/3 label, 1/3 value)
WIDTH = 114
LEFT_WIDTH = 76
RIGHT_WIDTH = 38
HEIGHT = 20

# Text content
LEFT_TEXT = "coverage"

# Text positions (centered in each section, scaled by 10 to match font-size 110)
LEFT_X = 380  # center of 76px section (76/2 * 10)
RIGHT_X = 950  # center of right section ((76 + 38/2) * 10)

# Text lengths tuned for visual centering (from original badge style)
LEFT_TEXT_LENGTH = 530
RIGHT_TEXT_LENGTH = 280


def generate_badge(coverage_pct: int) -> str:
    """Generate Shields.io-style coverage badge SVG."""
    # Determine right section color based on coverage
    if coverage_pct >= 90:
        right_color = "#4c1"  # bright green
    elif coverage_pct >= 75:
        right_color = "#dfb317"  # yellow
    else:
        right_color = "#e05d44"  # red

    coverage_str = f"{coverage_pct}%"

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="coverage: {coverage_str}">
  <title>coverage: {coverage_str}</title>
  <filter id="blur"><feGaussianBlur stdDeviation="16"/></filter>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r"><rect width="{WIDTH}" height="{HEIGHT}" rx="3"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{LEFT_WIDTH}" height="{HEIGHT}" fill="#555"/>
    <rect x="{LEFT_WIDTH}" width="{RIGHT_WIDTH}" height="{HEIGHT}" fill="{right_color}"/>
    <rect width="{WIDTH}" height="{HEIGHT}" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" font-size="110">
    <g transform="scale(.1)">
      <g aria-hidden="true" fill="#010101">
        <text x="{LEFT_X}" y="150" fill-opacity=".8" filter="url(#blur)">{LEFT_TEXT}</text>
        <text x="{LEFT_X}" y="150" fill-opacity=".3" textLength="{LEFT_TEXT_LENGTH}">{LEFT_TEXT}</text>
      </g>
      <text x="{LEFT_X}" y="140" textLength="{LEFT_TEXT_LENGTH}">{LEFT_TEXT}</text>
    </g>
    <g transform="scale(.1)">
      <g aria-hidden="true" fill="#010101">
        <text x="{RIGHT_X}" y="150" fill-opacity=".8" filter="url(#blur)">{coverage_str}</text>
        <text x="{RIGHT_X}" y="150" fill-opacity=".3" textLength="{RIGHT_TEXT_LENGTH}">{coverage_str}</text>
      </g>
      <text x="{RIGHT_X}" y="140" textLength="{RIGHT_TEXT_LENGTH}">{coverage_str}</text>
    </g>
  </g>
</svg>"""

    return svg


def main():
    c = Coverage()
    c.load()
    try:
        total = c.report()
    except NoDataError:
        print("No .coverage data found — skipping badge generation.", file=sys.stderr)
        sys.exit(0)

    pct = round(total)
    svg = generate_badge(pct)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        f.write(svg)

    print(f"Coverage badge written: {pct}%")


if __name__ == "__main__":
    main()
