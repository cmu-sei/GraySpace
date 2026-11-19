#!/bin/bash
# Interactive .environment setup from environment.template
set -euo pipefail

TEMPLATE="${1:?Usage: setup-env.sh <template> <output>}"
OUTPUT="${2:?Usage: setup-env.sh <template> <output>}"

if [ -f "$OUTPUT" ]; then
    echo ".environment already exists, skipping setup."
    exit 0
fi

WORK="${OUTPUT}.tmp"
cp "$TEMPLATE" "$WORK"
trap 'rm -f "$WORK"' EXIT

echo "Setting up .environment from template..."
echo "Press Enter to keep the default value shown in [brackets]."
echo "Fields marked (required) cannot be left blank."
echo ""

# Ask which Proxmox auth method to use and comment out the other
while true; do
    printf "Proxmox authentication method — (1) API token or (2) username/password? [1]: "
    read -r auth_choice < /dev/tty
    auth_choice="${auth_choice:-1}"
    if [[ "$auth_choice" == "1" || "$auth_choice" == "2" ]]; then
        break
    fi
    echo "  Please enter 1 or 2."
done

if [[ "$auth_choice" == "1" ]]; then
    # Comment out username/password lines
    sed -i 's/^export PROXMOX_VE_USERNAME=/#export PROXMOX_VE_USERNAME=/' "$WORK"
    sed -i 's/^export PROXMOX_VE_PASSWORD=/#export PROXMOX_VE_PASSWORD=/' "$WORK"
else
    # Comment out API token line
    sed -i 's/^export PROXMOX_VE_API_TOKEN=/#export PROXMOX_VE_API_TOKEN=/' "$WORK"
fi
# A proxy only when the site has one: its lines stay commented out otherwise
printf "Do guests reach the internet through a proxy? (y/N): "
read -r proxy_choice < /dev/tty
if [[ "$proxy_choice" =~ ^[Yy] ]]; then
    sed -i -E 's/^# export (TF_VAR_(no_)?proxy=)/export \1/' "$WORK"
fi

echo ""

prev_comment=""
skip_crucible=false
lineno=0
while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))

    # Track comment lines as descriptions for the next export
    if [[ "$line" =~ ^#\  ]]; then
        prev_comment="${line#\# }"
        continue
    fi

    # Skip non-export lines and commented-out exports
    if [[ ! "$line" =~ ^export\  ]] || [[ "$line" =~ ^#export ]]; then
        prev_comment=""
        continue
    fi

    # Skip crucible-specific vars if crucible is not being configured
    if $skip_crucible && [[ "$line" =~ ^export\ TF_VAR_crucible_ ]]; then
        prev_comment=""
        continue
    fi

    # Extract variable name and current value
    VAR_NAME="${line#export }"
    VAR_NAME="${VAR_NAME%%=*}"
    current_value="${line#*=}"
    # Strip surrounding quotes (single or double)
    current_value="${current_value#[\"\']}"
    current_value="${current_value%[\"\']}"

    # Skip lines with variable expansion (e.g. $VAR references)
    if [[ "$current_value" == *'$'* ]]; then
        prev_comment=""
        continue
    fi

    # Display description if available
    if [ -n "$prev_comment" ]; then
        echo "$prev_comment"
    fi

    # Prompt loop — re-prompt if required and left blank
    is_required=false
    if [ -z "$current_value" ]; then
        is_required=true
    fi

    while true; do
        if $is_required; then
            printf "  %s (required): " "$VAR_NAME"
        else
            printf "  %s [%s]: " "$VAR_NAME" "$current_value"
        fi
        read -r answer < /dev/tty

        if [ -n "$answer" ]; then
            new_value="$answer"
        elif $is_required; then
            echo "  This field cannot be blank."
            continue
        else
            new_value="$current_value"
        fi
        break
    done

    # A random API token on request
    if [[ "$VAR_NAME" == "GRAYSPACE_API_TOKEN" && "$new_value" == "generate" ]]; then
        new_value="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
        echo "  Generated a random GRAYSPACE_API_TOKEN"
    fi

    # Use single quotes for variables whose values may contain special characters
    SINGLE_QUOTE_VARS="TF_VAR_crucible_client_scopes"
    quote='"'
    if [[ " $SINGLE_QUOTE_VARS " == *" $VAR_NAME "* ]]; then
        quote="'"
    fi

    # Replace the line in the output file using awk (safe with special characters)
    awk -v n="$lineno" -v val="$new_value" -v var="$VAR_NAME" -v q="$quote" \
        'NR==n { print "export " var "=" q val q; next } { print }' \
        "$WORK" > "${WORK}.swap" && mv "${WORK}.swap" "$WORK"

    # If crucible is not being configured, comment out all other crucible vars
    if [[ "$VAR_NAME" == "TF_VAR_configure_crucible" && "$new_value" == "false" ]]; then
        skip_crucible=true
        sed -i '/^export TF_VAR_crucible_/s/^export/#export/' "$WORK"
    fi

    prev_comment=""
done < "$WORK"

mv "$WORK" "$OUTPUT"
trap - EXIT
echo ""
echo "Setup complete! Review .environment before running 'task apply'."
