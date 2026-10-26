#!/usr/bin/env bash
# Libera o SisPorto depois do corte automático de custo.
#   bash infra/gcp/liberar-sistema.sh        -> libera e NÃO corta de novo até o fim do mês (você aprovou o gasto)
#   bash infra/gcp/liberar-sistema.sh teste  -> libera depois de um teste (o corte continua valendo)
set -euo pipefail
PROJECT_ID="porto-sisporto-2610"; REGIAO="southamerica-east1"
gcloud config set project "$PROJECT_ID" >/dev/null
if [[ "${1:-}" == "teste" ]]; then
  gcloud pubsub topics publish orcamento-alertas --message='{"costAmount":0,"budgetAmount":400,"currencyCode":"BRL"}' >/dev/null
  MES=""
else
  MES=$(date +%Y-%m)
fi
gcloud run services update porto-monitor-orcamento --region="$REGIAO" --update-env-vars="LIBERADO_MES=$MES" --quiet >/dev/null
gcloud run services add-iam-policy-binding sisporto --region="$REGIAO" --member=allUsers --role=roles/run.invoker >/dev/null
echo "SisPorto liberado.${MES:+ O corte fica suspenso até o fim de $MES.}"
