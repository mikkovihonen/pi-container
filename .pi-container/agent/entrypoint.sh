#!/bin/bash
uv venv --python 3.14.6
source .venv/bin/activate
UV_LINK_MODE=copy uv sync