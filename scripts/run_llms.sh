llama-server -hf unsloth/gemma-4-E2B-it-qat-GGUF:UD-Q4_K_XL --temp 1.0 --top-p 0.95 --top-k 64 --port 8001 --chat-template-kwargs '{"enable_thinking":false}' &
llama-server --embeddings -hf unsloth/embeddinggemma-300m-GGUF --port 8002
