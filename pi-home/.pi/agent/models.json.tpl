{
  "providers": {
    "llama-local": {
        "baseUrl": "http://${GATEWAY_IP}:${LLAMA_PORT}/v1",
        "api": "openai-completions",
        "apiKey": "not-required",
        "models": [
            {
                "id": "${MODEL_ID}",
                "name": "${MODEL_ID}",
                "contextWindow": ${MODEL_CTX_WINDOW},
                "compactionThreshold": ${MODEL_COMPACTION_THRESHOLD},
                "maxTokens": ${MODEL_MAX_TOKENS},
                "options": {
                    "temperature": ${MODEL_TEMPERATURE},
                    "top_p": ${MODEL_TOP_P}
                }
            }
        ]
    }
  }
}