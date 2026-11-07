#!/bin/sh
# Build the Ollama models the AI Q&A uses (services/chat_assistant.py):
#   qwen3-1.7b-nothink  (default)  qwen3-0.6b-nothink (fallback)
# Each is the base model's weights plus the current Qwen3 chat template with the
# think block forced empty (this Ollama build ignores "think": false) and 2 threads.
# The template comes from qwen3:0.6b because the older qwen3:1.7b import renders the
# system prompt twice, which defeats Ollama's prompt cache (~30 s extra per question).
# No sudo needed. Usage: sh scripts/create_assistant_model.sh
set -eu
ollama pull qwen3:0.6b
ollama list | grep -q '^qwen3:1.7b ' || ollama pull qwen3:1.7b
TMP=$(mktemp -d)
ollama show qwen3:0.6b --template > "$TMP/template.txt"
python3 - "$TMP" <<'EOF'
import sys
tmp = sys.argv[1]
t = open(f"{tmp}/template.txt", encoding="utf-8").read()
old = "{{ if and $.IsThinkSet (not $.Think) -}}\n<think>\n\n</think>\n\n{{ end -}}"
if old not in t:
    sys.exit("unexpected qwen3 template: think block condition not found")
t = t.replace(old, "<think>\n\n</think>\n\n")
for base, name in (("qwen3:0.6b", "0.6b"), ("qwen3:1.7b", "1.7b")):
    with open(f"{tmp}/Modelfile.{name}", "w", encoding="utf-8") as f:
        f.write(f'FROM {base}\nPARAMETER num_thread 2\nTEMPLATE """{t}"""\n')
EOF
ollama create qwen3-0.6b-nothink -f "$TMP/Modelfile.0.6b"
ollama create qwen3-1.7b-nothink -f "$TMP/Modelfile.1.7b"
rm -rf "$TMP"
ollama list | grep nothink
