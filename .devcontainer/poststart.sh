#!/bin/bash
set -euo pipefail
sudo rsyslogd 2>/tmp/rsyslogd-start.log || true
grep -q 'welcome.sh' /home/vscode/.bashrc || echo 'source /workspaces/.devcontainer/welcome.sh' >> /home/vscode/.bashrc
grep -q '.venv/bin/activate' /home/vscode/.bashrc || echo 'source ~/.venv/bin/activate 2>/dev/null' >> /home/vscode/.bashrc
# Welcome message
cat <<'EOF'
Welcome to the GRAYSPACE Dev Container!

Type Ctrl-Shift-` (backtick) to open a new terminal and get started.

EOF