# Chat templates

Testing with Gemma 4 revealed that Pi Coding Agent benefits from excplicit chat template definitions as models aren't guaranteed to having them built-in.

"--jinja",
"--chat-template-file", "llama-server/chat-templates/gemma-4-26B-A4B-it/chat_template.jinja"

# System RAM cache

System RAM cache makes sense to enable. Default is 8192GB.

"--cache-ram", 8192,

# Cache Quantization

Setting cache quantization makes the token generation much slower on unified memory architecture (tested on M2 Max 64GB unified RAM).
They should be only used when running on discrete GPU VRAM instead of unified memory (Apple, AMD Ryzen AI Halo etc.).

"--cache-type-k", "q8_0",
"--cache-type-v", "q8_0",
