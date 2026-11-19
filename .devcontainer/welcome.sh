#!/bin/bash
#
# Devcontainer terminal welcome screen for GraySpace
#
# Sourced from .bashrc - displays once per terminal session

# Guard: only show in interactive shells
[[ $- != *i* ]] && return
# Guard: only show once per shell (not in nested shells)
[ -n "$GRAYSPACE_WELCOMED" ] && return
export GRAYSPACE_WELCOMED=1

BOLD='\033[1m'
DIM='\033[2m'
CYAN='\033[36m'
GREEN='\033[32m'
YELLOW='\033[33m'
RED='\033[31m'
RESET='\033[0m'

echo ""
echo -e "${CYAN}${BOLD}   ___                ___"
echo -e "  / __|_ _ __ _ _  _/ __|_ __  __ _ __ ___"
echo -e " | (_ | '_/ _\` | || \\__ \\ '_ \\/ _\` / _/ -_)"
echo -e "  \\___|_| \\__,_|\\_, |___/ .__/\\__,_\\__\\___|"
echo -e "                |__/    |_|${RESET}"
echo -e " ${DIM}Internet Backbone Simulation & Modeling${RESET}"
echo ""

print_status() {
    local branch
    branch=$(git -C /workspaces branch --show-current 2>/dev/null)
    if [ -n "$branch" ]; then
        echo -e "  ${GREEN}ok${RESET}  Git branch: ${BOLD}${branch}${RESET}"
    else
        echo -e "  ${RED}--${RESET}  Git: not a repository"
    fi

    if test -f /workspaces/.environment; then
        echo -e "  ${GREEN}ok${RESET}  .environment file found"
    else
        echo -e "  ${YELLOW}--${RESET}  .environment missing ${DIM}(run: task env)${RESET}"
    fi

    if test -f /workspaces/terraform/terraform.tfvars.json; then
        echo -e "  ${GREEN}ok${RESET}  terraform.tfvars.json generated"
    else
        echo -e "  ${YELLOW}--${RESET}  terraform.tfvars.json not found ${DIM}(run: task parse)${RESET}"
    fi

    if test -d /home/vscode/.venv; then
        echo -e "  ${GREEN}ok${RESET}  Python venv available"
    else
        echo -e "  ${RED}--${RESET}  Python venv missing"
    fi
}

print_status
echo ""

# --- Quick Reference ---
echo -e " ${BOLD}Quick Reference${RESET}"
echo -e "  ${CYAN}task env${RESET}        Setup environment variables"
echo -e "  ${CYAN}task parse${RESET}      Generate tfvars from the topology CSVs"
echo -e "  ${CYAN}task plan${RESET}       Plan Terraform changes"
echo -e "  ${CYAN}task deploy${RESET}     Apply infra + configure"
echo -e "  ${CYAN}task destroy${RESET}    Tear down all resources"
echo -e "  ${CYAN}task --list${RESET}     Show all available tasks"
echo ""
