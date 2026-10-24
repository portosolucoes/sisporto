#!/usr/bin/env bash
# Grava o Client ID/Secret do login Google como segredos (sem aparecer na tela nem no histórico).
# Depois rode: bash infra/gcp/publicar.sh
set -euo pipefail
gcloud config set project "${PROJECT_ID:-porto-sisporto-2610}" >/dev/null
read -r -p "Cole o ID do cliente (termina em .apps.googleusercontent.com): " CID
read -r -s -p "Cole a Chave secreta do cliente (não aparece): " CSEC; echo
for par in "sisporto-google-client-id:$CID" "sisporto-google-client-secret:$CSEC"; do
  nome="${par%%:*}"; valor="${par#*:}"
  if gcloud secrets describe "$nome" >/dev/null 2>&1; then
    printf '%s' "$valor" | gcloud secrets versions add "$nome" --data-file=- >/dev/null
  else
    printf '%s' "$valor" | gcloud secrets create "$nome" --data-file=- >/dev/null
  fi
done
echo "Segredos gravados. Agora rode: bash infra/gcp/publicar.sh"
