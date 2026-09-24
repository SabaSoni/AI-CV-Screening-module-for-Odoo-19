#!/usr/bin/env bash
# Installs Ollama (the free local AI runtime) and downloads the models this module uses.
# Linux / macOS.   Usage:  ./setup_ollama.sh [chat-model] [embedding-model]
# Example for better Georgian text (slower, ~8 GB):  ./setup_ollama.sh gemma3:12b
set -euo pipefail

CHAT_MODEL="${1:-gemma3:4b}"
EMBED_MODEL="${2:-bge-m3}"

if ! command -v ollama >/dev/null 2>&1; then
    echo "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# Wait for the service (the installer registers a systemd unit on Linux).
for _ in $(seq 1 30); do
    if curl -fs http://127.0.0.1:11434/api/version >/dev/null 2>&1; then break; fi
    sleep 2
done
if ! curl -fs http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    echo "Starting 'ollama serve' in the background..."
    nohup ollama serve >/tmp/ollama.log 2>&1 &
    sleep 5
fi

for model in "$CHAT_MODEL" "$EMBED_MODEL"; do
    for attempt in $(seq 1 20); do
        if ollama list | grep -q "^${model}"; then break; fi
        echo "Downloading ${model} (attempt ${attempt})..."
        ollama pull "$model" || sleep 20   # downloads resume after a dropped connection
    done
done

echo
ollama list
echo
echo "Done. In Odoo: Recruitment > Configuration > Settings > AI CV Screening > Test connection."
echo "If Odoo runs on another machine, start Ollama with OLLAMA_HOST=0.0.0.0 and put"
echo "http://<this-machine>:11434 into the 'Ollama URL' setting (keep it inside your network)."
