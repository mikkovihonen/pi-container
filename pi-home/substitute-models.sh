#!/usr/bin/env sh
set -eu

TEMPLATE="/home/pi/.pi/agent/models.json.tpl"
OUTPUT="/home/pi/.pi/agent/models.json"

# Defaults — all overridable via --env in container run
LLAMA_PORT="${LLAMA_PORT:?LLAMA_PORT must be set}"
MODEL_ID="${MODEL_ID:?MODEL_ID must be set}"
GATEWAY_IP="${GATEWAY_IP:?Gateway ip not resolved}"

MODEL_CTX_WINDOW="${MODEL_CTX_WINDOW:-131072}"
MODEL_COMPACTION_THRESHOLD="${MODEL_COMPACTION_THRESHOLD:-128000}"
MODEL_MAX_TOKENS="${MODEL_MAX_TOKENS:-8192}"
MODEL_TEMPERATURE="${MODEL_TEMPERATURE:-0.2}"
MODEL_TOP_P="${MODEL_TOP_P:-0.95}"

for var in MODEL_CTX_WINDOW MODEL_COMPACTION_THRESHOLD MODEL_MAX_TOKENS MODEL_TEMPERATURE MODEL_TOP_P GATEWAY_IP; do
  eval "val=\${$var}"
  case "$val" in
    ''|*[!0-9.]*) echo "ERROR: $var='$val' is not a number" >&2; exit 1 ;;
  esac
done

if [ "${UPDATE_MODEL:-0}" = "1" ]; then
  envsubst '${LLAMA_PORT}${MODEL_ID}${MODEL_CTX_WINDOW}${MODEL_COMPACTION_THRESHOLD}${MODEL_MAX_TOKENS}${MODEL_TEMPERATURE}${MODEL_TOP_P}${GATEWAY_IP}' \
    < "$TEMPLATE" > "$OUTPUT"
else
  echo "Skipping models.json update (UPDATE_MODEL is not 1)."
fi