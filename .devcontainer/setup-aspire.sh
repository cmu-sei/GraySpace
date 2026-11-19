#!/bin/bash
# The Aspire CLI, which the Aspire VS Code extension uses to run the AppHost on F5 (aspire/README.md).
set -euo pipefail

version=17.0.0
if dotnet tool list --global | grep -qi '^aspire.cli '; then
    dotnet tool update --global Aspire.Cli --version "$version" > /dev/null
else
    dotnet tool install --global Aspire.Cli --version "$version" > /dev/null
fi
echo "Aspire CLI $(aspire --version 2>/dev/null | cut -d+ -f1)"
