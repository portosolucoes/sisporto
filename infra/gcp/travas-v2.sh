#!/usr/bin/env bash
# =============================================================================
#  SisPorto – Travas de custo v2  (rodar no Cloud Shell:  bash travas-v2.sh)
#
#  1. Teto mensal R$ 200 com avisos em 50% / 80% / 90% / 100% + previsão de estouro
#  2. CORTE AUTOMÁTICO em R$ 400: o sistema sai do ar (nada é apagado) e chega e-mail
#  3. Alertas de disco do banco (70% e 85%) e de sistema fora do ar
#  4. Guarda só as 5 últimas versões montadas (Artifact Registry)
#  5. Cotas de máquinas virtuais e GPU zeradas (proteção contra invasão/mineração)
#  6. Proíbe criar chaves de contas de serviço
#  Pode ser rodado de novo: só ajusta o que for preciso.
# =============================================================================
set -uo pipefail
source "$(dirname "$0")/orcamento.conf"
EMAIL="$EMAIL_NOTIFICACOES"; TETO="$TETO_BRL"; CORTE="$CORTE_BRL"
PROJECT_ID="porto-sisporto-2610"; REGIAO="southamerica-east1"; SERVICO="sisporto"
TOPICO="orcamento-alertas"; FUNCAO="porto-monitor-orcamento"; SA_NOME="porto-orcamento-fn"
ORC_NOME="Porto - Teto mensal SisPorto"
M_TETO="custom.googleapis.com/porto/billing/gasto_sobre_teto"
M_CORTE="custom.googleapis.com/porto/billing/corte_ativo"
say(){ echo -e "\n\033[1;34m==> $*\033[0m"; }
aviso(){ echo -e "\033[1;33m   ! $*\033[0m"; }
if [[ -z "$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null)" ]]; then
  echo -e "\033[1;31mO Cloud Shell perdeu o login do Google. Rode: gcloud auth login   e depois este script de novo.\033[0m"; exit 1
fi
gcloud config set project "$PROJECT_ID" >/dev/null
NUM=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
SA="$SA_NOME@$PROJECT_ID.iam.gserviceaccount.com"
BILLING=$(gcloud billing projects describe "$PROJECT_ID" --format="value(billingAccountName.basename())")
gcloud services enable billingbudgets.googleapis.com monitoring.googleapis.com cloudfunctions.googleapis.com \
  pubsub.googleapis.com artifactregistry.googleapis.com cloudquotas.googleapis.com orgpolicy.googleapis.com >/dev/null
CANAL=$(gcloud beta monitoring channels list --filter="type=\"email\" AND labels.email_address=\"$EMAIL\"" \
  --format="value(name)" | head -n1)

# --- 1. Teto R$ 200 -----------------------------------------------------------------------------
say "Teto mensal: R\$ $TETO"
ORC=$(gcloud billing budgets list --billing-account="$BILLING" --filter="displayName=\"$ORC_NOME\"" \
  --format="value(name)" | head -n1)
if [[ -n "$ORC" ]]; then
  gcloud billing budgets update "$ORC" --billing-account="$BILLING" --budget-amount="${TETO}BRL" >/dev/null \
    && echo "   orçamento atualizado para R\$ $TETO (avisos em R\$ 100, 160, 180, 200 e previsão)"
else
  gcloud billing budgets create --billing-account="$BILLING" --display-name="$ORC_NOME" \
    --budget-amount="${TETO}BRL" --filter-projects="projects/$PROJECT_ID" \
    --threshold-rule=percent=0.50 --threshold-rule=percent=0.80 --threshold-rule=percent=0.90 \
    --threshold-rule=percent=1.00 --threshold-rule=percent=1.00,basis=forecasted-spend \
    --notifications-rule-pubsub-topic="projects/$PROJECT_ID/topics/$TOPICO" \
    --notifications-rule-monitoring-notification-channels="$CANAL" >/dev/null
fi

# --- 2. Corte automático em R$ 400 --------------------------------------------------------------
say "Corte automático em R\$ $CORTE"
gcloud run services add-iam-policy-binding "$SERVICO" --region="$REGIAO" --member="serviceAccount:$SA" \
  --role=roles/run.admin >/dev/null || aviso "não consegui dar à função permissão de bloquear o sistema"
curl -sS -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" -H "Content-Type: application/json" \
  "https://monitoring.googleapis.com/v3/projects/$PROJECT_ID/metricDescriptors" \
  -d "{\"type\":\"$M_CORTE\",\"metricKind\":\"GAUGE\",\"valueType\":\"DOUBLE\",\"displayName\":\"Porto - corte ativo\"}" >/dev/null
DIR=$(mktemp -d)
cat > "$DIR/main.py" <<'PY'
import base64, datetime, json, os, time
import functions_framework, google.auth
from google.auth.transport.requests import AuthorizedSession
from google.cloud import monitoring_v3

P, R, S = os.environ["PROJECT_ID"], os.environ["REGIAO"], os.environ["SERVICO"]
CORTE = float(os.environ.get("CORTE_BRL", "400"))
mon = monitoring_v3.MetricServiceClient()

def metrica(tipo, valor):
    s = monitoring_v3.TimeSeries(); s.metric.type = tipo; s.resource.type = "global"
    s.resource.labels["project_id"] = P
    now = time.time()
    s.points = [monitoring_v3.Point({"interval": {"end_time": {"seconds": int(now)}}, "value": {"double_value": valor}})]
    mon.create_time_series(name=f"projects/{P}", time_series=[s])

def bloquear():
    """Tira o acesso público do Cloud Run (allUsers): o sistema responde 403, nada é apagado."""
    cred, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    http = AuthorizedSession(cred)
    url = f"https://run.googleapis.com/v2/projects/{P}/locations/{R}/services/{S}"
    pol = http.get(url + ":getIamPolicy").json()
    antes = json.dumps(pol)
    for b in pol.get("bindings", []):
        if b.get("role") == "roles/run.invoker":
            b["members"] = [m for m in b.get("members", []) if m != "allUsers"]
    pol["bindings"] = [b for b in pol.get("bindings", []) if b.get("members")]
    if json.dumps(pol) != antes:
        http.post(url + ":setIamPolicy", json={"policy": pol}).raise_for_status()
        return True
    return False

@functions_framework.cloud_event
def on_budget(event):
    d = json.loads(base64.b64decode(event.data["message"]["data"]).decode("utf-8"))
    gasto, teto = float(d.get("costAmount", 0) or 0), float(d.get("budgetAmount", 0) or 0)
    metrica(os.environ["METRIC_TYPE"], gasto / teto if teto else 0.0)
    mes = datetime.date.today().strftime("%Y-%m")
    liberado = os.environ.get("LIBERADO_MES", "") == mes
    corte = gasto >= CORTE and not liberado
    bloqueou = bloquear() if corte else False
    metrica(os.environ["METRIC_CORTE"], 1.0 if corte else 0.0)
    print(json.dumps({"gasto": gasto, "teto": teto, "corte": CORTE, "bloqueou_agora": bloqueou, "liberado_mes": liberado}))
PY
printf 'functions-framework==3.*\ngoogle-cloud-monitoring==2.*\ngoogle-auth==2.*\nrequests==2.*\n' > "$DIR/requirements.txt"
gcloud functions deploy "$FUNCAO" --gen2 --region="$REGIAO" --runtime=python312 --source="$DIR" \
  --entry-point=on_budget --trigger-topic="$TOPICO" --service-account="$SA" --trigger-service-account="$SA" \
  --set-env-vars="PROJECT_ID=$PROJECT_ID,METRIC_TYPE=$M_TETO,METRIC_CORTE=$M_CORTE,REGIAO=$REGIAO,SERVICO=$SERVICO,CORTE_BRL=$CORTE,LIBERADO_MES=" \
  --memory=256Mi --max-instances=1 --quiet >/dev/null && echo "   função de corte publicada"

politica(){  # nome, filtro, comparação, limite, texto, [alinhador]
  gcloud alpha monitoring policies list --format='value(displayName)' | grep -qxF "$1" && return
  cat > "$DIR/p.json" <<JSON
{"displayName":"$1","combiner":"OR","conditions":[{"displayName":"$1","conditionThreshold":{"filter":"$2",
 "comparison":"$3","thresholdValue":$4,"duration":"300s","aggregations":[{"alignmentPeriod":"300s","perSeriesAligner":"${6:-ALIGN_MAX}"}],
 "evaluationMissingData":"EVALUATION_MISSING_DATA_NO_OP"}}],"notificationChannels":["$CANAL"],
 "alertStrategy":{"notificationChannelStrategy":[{"notificationChannelNames":["$CANAL"],"renotifyInterval":"86400s"}]},
 "documentation":{"mimeType":"text/markdown","content":"$5"}}
JSON
  gcloud alpha monitoring policies create --policy-from-file="$DIR/p.json" >/dev/null && echo "   alerta: $1"
}
politica "Porto - SISTEMA BLOQUEADO PELO CORTE DE CUSTO" "metric.type=\\\"$M_CORTE\\\" AND resource.type=\\\"global\\\"" \
  COMPARISON_GT 0.5 "O gasto do mês passou de R\$ $CORTE e o SisPorto foi tirado do ar (dados preservados). Para liberar: no Cloud Shell, cd ~/sisporto && bash infra/gcp/liberar-sistema.sh"

# --- 3. Disco do banco e sistema fora do ar -----------------------------------------------------
say "Alertas de disco do banco"
F="metric.type=\\\"cloudsql.googleapis.com/database/disk/utilization\\\" AND resource.type=\\\"cloudsql_database\\\""
politica "Porto - Disco do banco acima de 70%" "$F" COMPARISON_GT 0.70 "O disco do banco passou de 70%. Avise o Claude para revisar a limpeza ou aumentar o disco (custo ~US\$ 2 por 10 GB)."
politica "Porto - Disco do banco acima de 85% (URGENTE)" "$F" COMPARISON_GT 0.85 "URGENTE: com o disco cheio o sistema para. Aumente o disco: Cloud SQL > sisporto-db > Editar > Armazenamento."
say "Monitor de sistema fora do ar"
HOST=$(gcloud run services describe "$SERVICO" --region="$REGIAO" --format="value(status.url)" | sed 's#https://##')
if ! gcloud monitoring uptime list-configs --format="value(displayName)" 2>/dev/null | grep -qxF "SisPorto no ar"; then
  gcloud monitoring uptime create "SisPorto no ar" --resource-type=uptime-url \
    --resource-labels="host=$HOST,project_id=$PROJECT_ID" --path="/saude" --protocol=https --period=15 >/dev/null \
    && echo "   verificação a cada 15 min em https://$HOST/saude" || aviso "monitor de disponibilidade não criado"
fi
politica "Porto - SisPorto fora do ar" "metric.type=\\\"monitoring.googleapis.com/uptime_check/check_passed\\\" AND resource.type=\\\"uptime_url\\\"" \
  COMPARISON_LT 0.5 "A verificação automática não conseguiu abrir o SisPorto. Se foi o corte de custo, chegou outro e-mail avisando." ALIGN_FRACTION_TRUE

# --- 4. Só as 5 últimas versões montadas ----------------------------------------------------------
say "Limpeza das versões antigas (Artifact Registry)"
cat > "$DIR/limpeza.json" <<'JSON'
[{"name":"manter-5-ultimas","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":5}},
 {"name":"apagar-antigas","action":{"type":"Delete"},"condition":{"tagState":"any","olderThan":"604800s"}}]
JSON
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy --location="$REGIAO" \
  --policy="$DIR/limpeza.json" --no-dry-run >/dev/null && echo "   ok" || aviso "repositório de versões não encontrado (normal se ainda não houve publicação)"

# --- 5. Cotas de máquinas e GPU zeradas -----------------------------------------------------------
say "Cotas de máquinas virtuais e GPU = 0"
for Q in GPUS-ALL-REGIONS-per-project CPUS-ALL-REGIONS-per-project; do
  gcloud beta quotas preferences create --service=compute.googleapis.com --quota-id="$Q" --preferred-value=0 \
    --project="$PROJECT_ID" --preference-id="porto-zero-${Q,,}" --justification="SisPorto nao usa VMs" >/dev/null 2>&1 \
    && echo "   $Q = 0" || aviso "cota $Q: ajuste manual em IAM e administrador > Cotas (ver documentação)"
done

# --- 6. Proibir chaves de contas de serviço -------------------------------------------------------
say "Proibir chaves de contas de serviço"
gcloud resource-manager org-policies enable-enforce iam.disableServiceAccountKeyCreation --project="$PROJECT_ID" >/dev/null 2>&1 \
  && echo "   ok" || aviso "sem permissão de política da organização: aplique no console (ver documentação)"

say "PRONTO"
cat <<FIM
Teto R\$ $TETO (avisos em 100/160/180/200 + previsão) · corte automático em R\$ $CORTE · e-mails para $EMAIL

TESTAR O CORTE (o sistema sai do ar em ~1 min):
  gcloud pubsub topics publish $TOPICO --message='{"costAmount":450,"budgetAmount":$TETO,"currencyCode":"BRL"}'
LIBERAR DEPOIS DO TESTE:
  cd ~/sisporto && bash infra/gcp/liberar-sistema.sh teste
FIM
