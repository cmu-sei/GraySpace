#!/bin/bash
#
# Configure persistent bash history across devcontainer rebuilds
#
# This script should be run as root to fix volume mount ownership

# Ensure the history directory has correct ownership
# The volume mount is created as root, so we need sudo to fix it
if [ -w /home/vscode/.history ]; then
    mkdir -p /home/vscode/.history
    touch /home/vscode/.history/.bash_history
else
    sudo chown -R vscode:vscode /home/vscode/.history
    touch /home/vscode/.history/.bash_history
fi

# Create or update bash history configuration
BASHRC_MARKER="# Persistent history configuration"
HISTORY_CONFIG="
$BASHRC_MARKER
export HISTFILE=/home/vscode/.history/.bash_history
export HISTSIZE=10000
export HISTFILESIZE=20000
export HISTCONTROL=ignoreboth:erasedups
# Save and reload history after each command for multi-terminal sync
export PROMPT_COMMAND=\"history -a; history -n\"
"

# Remove old configuration if it exists
if grep -q "$BASHRC_MARKER" /home/vscode/.bashrc; then
    # Remove from marker to the end of the history config block
    sed -i "/$BASHRC_MARKER/,/^$/d" /home/vscode/.bashrc
fi

# Append new configuration
echo "$HISTORY_CONFIG" >> /home/vscode/.bashrc

echo "Bash history configured to persist in /home/vscode/.history/"
