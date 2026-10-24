#!/usr/bin/env bash
# =============================================================================
#  Porto Soluções – SisPorto
#  Travas de custo no Google Cloud
#
#  O que este script faz:
#   1. Cria o projeto do sistema e liga à conta de faturamento
#   2. Cria um ORÇAMENTO mensal (teto) com avisos ANTES do estouro:
#        50%, 80%, 90%, 100% do gasto real + aviso de PREVISÃO de estouro
#   3. Cria um aviso DIÁRIO por e-mail enquanto o gasto estiver ACIMA do teto
#        (orçamento -> Pub/Sub -> função -> métrica -> alerta que repete a cada 24h)
#
#  Como usar: abra o Cloud Shell (ícone >_ no topo do console), envie este
#  arquivo (menu ⋮ > Fazer upload) e rode:
#        bash setup-travas-custo.sh
#
#  Nada aqui apaga dados nem desliga o faturamento.
# =============================================================================
set -euo pipefail

# ----------------------------- AJUSTE AQUI -----------------------------------
EMAIL_ALERTA="contato@portosolucoes.com.br"
TETO_BRL=50            # teto mensal na fase de construção (mude p/ 100 depois)
TETO_USD=10            # usado só se a conta de faturamento estiver em dólar
REGIAO="southamerica-east1"   # São Paulo
PROJECT_ID="${PROJECT_ID:-porto-sisporto-$(date +%y%m)}"
# -----------------------------------------------------------------------------

TOPICO="orcamento-alertas"
FUNCAO="porto-monitor-orcamento"
SA_NOME="porto-orcamento-fn"
METRICA="custom.googleapis.com/porto/billing/gasto_sobre_teto"
ORC_NOME="Porto - Teto mensal SisPorto"

say(){ echo -e "\n\033[1;34m==> $*\033[0m"; }

# --- 0. Conta de faturamento -------------------------------------------------
say "Procurando a conta de faturamento ativa..."
BILLING_ACCOUNT=$(gcloud billing accounts list --filter="open=true" \
  --format="value(name.basename())" | head -n1)
if [[ -z "$BILLING_ACCOUNT" ]]; then
  echo "Nenhuma conta de faturamento ativa encontrada. Conclua a assinatura primeiro."; exit 1
fi
MOEDA=$(gcloud billing accounts describe "$BILLING_ACCOUNT" \
  --format="value(currencyCode)" 2>/dev/null || true)
MOEDA=${MOEDA:-BRL}
if [[ "$MOEDA" == "BRL" ]]; then VALOR_ORC="${TETO_BRL}BRL"; else VALOR_ORC="${TETO_USD}${MOEDA}"; fi
echo "Conta: $BILLING_ACCOUNT | Moeda: $MOEDA | Teto: $VALOR_ORC"

# --- 1. Projeto ---------------------------------------------------------------
say "Criando/usando o projeto $PROJECT_ID..."
if ! gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud projects create "$PROJECT_ID" --name="SisPorto"
fi
gcloud config set project "$PROJECT_ID" >/dev/null
gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT" >/dev/null
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

say "Ativando as APIs necessárias (leva 1-2 min)..."
gcloud services enable \
  cloudbilling.googleapis.com billingbudgets.googleapis.com pubsub.googleapis.com \
  monitoring.googleapis.com cloudfunctions.googleapis.com run.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com eventarc.googleapis.com \
  compute.googleapis.com logging.googleapis.com

# --- 2. Canal de e-mail -------------------------------------------------------
say "Configurando canal de e-mail $EMAIL_ALERTA..."
CANAL=$(gcloud beta monitoring channels list \
  --filter="type=\"email\" AND labels.email_address=\"$EMAIL_ALERTA\"" \
  --format="value(name)" | head -n1)
if [[ -z "$CANAL" ]]; then
  CANAL=$(gcloud beta monitoring channels create --display-name="Porto - $EMAIL_ALERTA" \
    --type=email --channel-labels=email_address="$EMAIL_ALERTA" --format="value(name)")
fi
echo "Canal: $CANAL"

# --- 3. Tópico Pub/Sub --------------------------------------------------------
say "Criando tópico $TOPICO..."
gcloud pubsub topics describe "$TOPICO" >/dev/null 2>&1 || gcloud pubsub topics create "$TOPICO"

# --- 4. Orçamento com avisos ANTES do estouro ---------------------------------
say "Criando orçamento '$ORC_NOME'..."
EXISTE=$(gcloud billing budgets list --billing-account="$BILLING_ACCOUNT" \
  --billing-project="$PROJECT_ID" \
  --filter="displayName=\"$ORC_NOME\"" --format="value(name)" | head -n1 || true)
if [[ -z "$EXISTE" ]]; then
  gcloud billing budgets create --billing-project="$PROJECT_ID" \
    --billing-account="$BILLING_ACCOUNT" \
    --display-name="$ORC_NOME" \
    --budget-amount="$VALOR_ORC" \
    --filter-projects="projects/$PROJECT_ID" \
    --threshold-rule=percent=0.50 \
    --threshold-rule=percent=0.80 \
    --threshold-rule=percent=0.90 \
    --threshold-rule=percent=1.00 \
    --threshold-rule=percent=1.00,basis=forecasted-spend \
    --notifications-rule-pubsub-topic="projects/$PROJECT_ID/topics/$TOPICO" \
    --notifications-rule-monitoring-notification-channels="$CANAL"
else
  echo "Orçamento já existe: $EXISTE (não alterado)"
fi

# --- 5. Função que transforma o aviso do orçamento em métrica -----------------
say "Preparando permissões da função..."
SA_EMAIL="$SA_NOME@$PROJECT_ID.iam.gserviceaccount.com"
gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NOME" --display-name="Monitor de orçamento"
for ROLE in roles/monitoring.metricWriter roles/run.invoker roles/eventarc.eventReceiver; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA_EMAIL" \
    --role="$ROLE" --condition=None >/dev/null
done
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.builder" --condition=None >/dev/null || true
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:service-$PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" --condition=None >/dev/null 2>&1 || true

say "Criando a métrica personalizada..."
curl -sS -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  "https://monitoring.googleapis.com/v3/projects/$PROJECT_ID/metricDescriptors" \
  -d "{\"type\":\"$METRICA\",\"metricKind\":\"GAUGE\",\"valueType\":\"DOUBLE\",\"unit\":\"1\",
       \"displayName\":\"Porto - gasto / teto\",
       \"description\":\"Gasto do mês dividido pelo teto do orçamento (1.0 = 100%)\"}" >/dev/null

DIR=$(mktemp -d)
cat > "$DIR/main.py" <<'PY'
import base64, json, os, time
import functions_framework
from google.cloud import monitoring_v3

PROJECT = os.environ["PROJECT_ID"]
METRIC = os.environ["METRIC_TYPE"]
client = monitoring_v3.MetricServiceClient()

@functions_framework.cloud_event
def on_budget(event):
    data = json.loads(base64.b64decode(event.data["message"]["data"]).decode("utf-8"))
    cost = float(data.get("costAmount", 0) or 0)
    budget = float(data.get("budgetAmount", 0) or 0)
    ratio = cost / budget if budget > 0 else 0.0

    series = monitoring_v3.TimeSeries()
    series.metric.type = METRIC
    series.resource.type = "global"
    series.resource.labels["project_id"] = PROJECT
    now = time.time()
    interval = monitoring_v3.TimeInterval(
        {"end_time": {"seconds": int(now), "nanos": int((now - int(now)) * 1e9)}})
    series.points = [monitoring_v3.Point({"interval": interval, "value": {"double_value": ratio}})]
    client.create_time_series(name=f"projects/{PROJECT}", time_series=[series])
    print(json.dumps({"gasto": cost, "teto": budget, "proporcao": round(ratio, 3),
                      "moeda": data.get("currencyCode")}))
PY
cat > "$DIR/requirements.txt" <<'TXT'
functions-framework==3.*
google-cloud-monitoring==2.*
TXT

say "Aguardando 60s para as permissões propagarem..."
sleep 60

say "Publicando a função $FUNCAO (leva 2-4 min)..."
gcloud functions deploy "$FUNCAO" --gen2 --region="$REGIAO" --runtime=python312 \
  --source="$DIR" --entry-point=on_budget --trigger-topic="$TOPICO" \
  --service-account="$SA_EMAIL" --trigger-service-account="$SA_EMAIL" \
  --set-env-vars="PROJECT_ID=$PROJECT_ID,METRIC_TYPE=$METRICA" \
  --memory=256Mi --max-instances=1 --quiet

# --- 6. Alerta DIÁRIO enquanto estiver acima do teto ------------------------------
say "Criando o alerta diário de estouro..."
cat > "$DIR/politica.json" <<JSON
{
  "displayName": "Porto - GASTO ACIMA DO TETO (aviso diário)",
  "combiner": "OR",
  "conditions": [{
    "displayName": "Gasto do mês >= 100% do teto",
    "conditionThreshold": {
      "filter": "metric.type=\"$METRICA\" AND resource.type=\"global\"",
      "comparison": "COMPARISON_GT",
      "thresholdValue": 0.999,
      "duration": "60s",
      "aggregations": [{"alignmentPeriod": "3600s", "perSeriesAligner": "ALIGN_MAX"}],
      "evaluationMissingData": "EVALUATION_MISSING_DATA_NO_OP",
      "trigger": {"count": 1}
    }
  }],
  "alertStrategy": {
    "autoClose": "604800s",
    "notificationChannelStrategy": [{
      "notificationChannelNames": ["$CANAL"],
      "renotifyInterval": "86400s"
    }]
  },
  "notificationChannels": ["$CANAL"],
  "documentation": {
    "mimeType": "text/markdown",
    "content": "O gasto do projeto $PROJECT_ID passou do teto aprovado ($VALOR_ORC). O valor mostrado é gasto/teto (ex.: 1.15 = 115%).\n\nEste e-mail se repete **a cada 24h** até você agir:\n1. Ver gastos: https://console.cloud.google.com/billing/$BILLING_ACCOUNT/reports?project=$PROJECT_ID\n2. Para APROVAR mais gasto: aumente o valor do orçamento em Faturamento > Orçamentos e alertas.\n3. Se houver bloqueio (spend cap) no Cloud Run, clique em 'Lift spend cap' para liberar."
  }
}
JSON
if ! gcloud alpha monitoring policies list --format='value(displayName)' \
      | grep -qxF "Porto - GASTO ACIMA DO TETO (aviso diário)"; then
  gcloud alpha monitoring policies create --policy-from-file="$DIR/politica.json" >/dev/null
fi

say "PRONTO!"
cat <<FIM

Projeto ............ $PROJECT_ID
Teto mensal ........ $VALOR_ORC
Avisos antes ....... 50%, 80%, 90%, 100% e previsão de estouro -> $EMAIL_ALERTA
Aviso após estouro . e-mail a cada 24h enquanto o gasto estiver acima do teto

TESTE (simula um estouro; chega um e-mail em ~5-10 min):
  gcloud pubsub topics publish $TOPICO --project=$PROJECT_ID \\
    --message='{"costAmount":60,"budgetAmount":50,"currencyCode":"BRL"}'

DESFAZER O TESTE (encerra o alerta em até ~1h):
  gcloud pubsub topics publish $TOPICO --project=$PROJECT_ID \\
    --message='{"costAmount":0,"budgetAmount":50,"currencyCode":"BRL"}'
FIM
