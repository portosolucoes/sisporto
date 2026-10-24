#!/usr/bin/env bash
# Gera o pacote de entrega: MANIFESTO.sha256 (impressão digital de cada arquivo) + zip da versão.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSAO=$(sed -n 's/^VERSAO = "\(.*\)"/\1/p' app/__init__.py)
git ls-files | grep -v -e '^MANIFESTO.sha256$' -e '^\.github/' | sort | xargs sha256sum > MANIFESTO.sha256
git add MANIFESTO.sha256
git diff --cached --quiet || git -c user.name=Claude -c user.email=noreply@anthropic.com commit -qm "Manifesto da versão $VERSAO"
git archive --format=zip -o "${1:-sisporto-v$VERSAO.zip}" HEAD
echo "sisporto-v$VERSAO.zip"
