#!/bin/bash
# Grayspace Dev Container Claude Setup Script for macOS/Linux

echo ""
echo "  Grayspace Dev Container Claude Setup"
echo ""
echo "Select your AWS region:"
echo "  1) AWS Commercial (us-east-1)"
echo "  2) AWS GovCloud (us-gov-east-1)"
echo ""
read -p "Enter choice (1 or 2): " CHOICE

case "$CHOICE" in
    1)
        REGION="us-east-1"
        HAIKU="us.anthropic.claude-haiku-4-5-20251001-v1:0"
        SONNET=""
        SECTION="AWS COMMERCIAL"
        ;;
    2)
        REGION="us-gov-east-1"
        HAIKU="us-gov.anthropic.claude-3-haiku-20240307-v1:0"
        SONNET="us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0"
        SECTION="AWS GOVCLOUD"
        ;;
    *)
        echo "ERROR: Invalid choice."; exit 1 ;;
esac

echo ""
read -p "AWS Access Key ID: " KEY_ID
[ -z "$KEY_ID" ] && echo "ERROR: Access Key ID required." && exit 1

# Read secret with asterisk feedback
echo -n "AWS Secret Access Key: "
SECRET=""
while IFS= read -r -s -n1 char; do
    if [[ -z "$char" ]]; then  # Enter pressed
        break
    elif [[ "$char" == $'\x7f' || "$char" == $'\x08' ]]; then  # Backspace
        if [[ -n "$SECRET" ]]; then
            SECRET="${SECRET%?}"
            echo -ne '\b \b'
        fi
    else
        SECRET+="$char"
        echo -n '*'
    fi
done
echo ""
[ -z "$SECRET" ] && echo "ERROR: Secret Access Key required." && exit 1

OUTPUT="$(dirname "$0")/.devcontainer/claude.env"

cat > "$OUTPUT" << EOF
# Environment variables for Claude in Grayspace Dev Container

# GENERAL
CLAUDE_CODE_USE_BEDROCK=1

# $SECTION
AWS_REGION=$REGION
${SONNET:+ANTHROPIC_DEFAULT_SONNET_MODEL=$SONNET
}ANTHROPIC_DEFAULT_HAIKU_MODEL=$HAIKU
AWS_ACCESS_KEY_ID=$KEY_ID
AWS_SECRET_ACCESS_KEY=$SECRET
EOF

echo ""
echo "Created: .devcontainer/claude.env"
echo ""
echo "Next steps:"
echo "  If outside container: Open in VS Code → F1 → 'Dev Containers: Reopen in Container'"
echo "  If inside container:  F1 → 'Dev Containers: Rebuild Container'"
echo ""
