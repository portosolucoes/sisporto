#!/usr/bin/env bash
# =============================================================================
#  SisPorto – Publicação no Google Cloud (rodar no Cloud Shell, dentro da pasta
#  do repositório:  git clone https://github.com/portosolucoes/sisporto && cd sisporto)
#
#  1ª execução  : bash infra/gcp/publicar.sh            (cria banco, segredos e o serviço ~15 min)
#  Atualizações : bash infra/gcp/publicar.sh            (só republica o código ~4 min)
#
#  Custo: Cloud SQL db-f1-micro (~US$ 12/mês) + disco/backup (~US$ 3) + Cloud Run (cota gratuita)
# =============================================================================
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-porto-sisporto-2610}"
REGIAO="southamerica-east1"
INSTANCIA="sisporto-db"
BANCO="sisporto"
USUARIO_DB="sisporto"
SERVICO="sisporto"
ADMIN_EMAIL="contato@portosolucoes.com.br"

say(){ echo -e "\n\033[1;34m==> $*\033[0m"; }
gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
SA_RUN="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
CONEXAO="$PROJECT_ID:$REGIAO:$INSTANCIA"

say "Ativando APIs..."
gcloud services enable sqladmin.googleapis.com run.googleapis.com secretmanager.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com

# --- Banco de dados ---------------------------------------------------------
if ! gcloud sql instances describe "$INSTANCIA" >/dev/null 2>&1; then
  say "Criando o banco PostgreSQL (menor máquina, backup diário) – leva ~10 min..."
  gcloud sql instances create "$INSTANCIA" --database-version=POSTGRES_16 --edition=ENTERPRISE \
    --tier=db-f1-micro --region="$REGIAO" --storage-type=SSD --storage-size=10 \
    --no-storage-auto-increase --backup-start-time=06:00 --retained-backups-count=7 \
    --availability-type=zonal
fi
gcloud sql databases describe "$BANCO" --instance="$INSTANCIA" >/dev/null 2>&1 || \
  gcloud sql databases create "$BANCO" --instance="$INSTANCIA"

# --- Segredos ---------------------------------------------------------------
if ! gcloud secrets describe sisporto-database-url >/dev/null 2>&1; then
  say "Criando usuário do banco e segredos..."
  SENHA_DB=$(openssl rand -base64 30 | tr -dc 'A-Za-z0-9' | head -c 32)
  gcloud sql users create "$USUARIO_DB" --instance="$INSTANCIA" --password="$SENHA_DB"
  printf "host=/cloudsql/%s dbname=%s user=%s password=%s" "$CONEXAO" "$BANCO" "$USUARIO_DB" "$SENHA_DB" | \
    gcloud secrets create sisporto-database-url --data-file=-
  openssl rand -hex 32 | tr -d '\n' | gcloud secrets create sisporto-secret-key --data-file=-
fi
for S in sisporto-database-url sisporto-secret-key; do
  gcloud secrets add-iam-policy-binding "$S" --member="serviceAccount:$SA_RUN" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA_RUN" \
  --role=roles/cloudsql.client --condition=None >/dev/null

# --- Serviço ----------------------------------------------------------------
say "Publicando o sistema no Cloud Run (máx. 2 instâncias)..."
gcloud run deploy "$SERVICO" --source . --region="$REGIAO" --allow-unauthenticated \
  --add-cloudsql-instances="$CONEXAO" \
  --set-secrets="DATABASE_URL=sisporto-database-url:latest,SECRET_KEY=sisporto-secret-key:latest" \
  --max-instances=2 --min-instances=0 --memory=512Mi --cpu=1 --concurrency=40 --quiet

IMAGEM=$(gcloud run services describe "$SERVICO" --region="$REGIAO" \
  --format="value(spec.template.spec.containers[0].image)")

# --- Tarefas de manutenção (mesma imagem) -----------------------------------
tarefa(){  # nome, argumentos separados por vírgula
  gcloud run jobs deploy "$1" --image="$IMAGEM" --region="$REGIAO" \
    --set-cloudsql-instances="$CONEXAO" \
    --set-secrets="DATABASE_URL=sisporto-database-url:latest,SECRET_KEY=sisporto-secret-key:latest" \
    --command=flask --args="$2" --max-retries=0 --quiet >/dev/null
  gcloud run jobs execute "$1" --region="$REGIAO" --wait
}

say "Criando as tabelas (se ainda não existem)..."
tarefa sisporto-banco "--app,app,banco-criar" || echo "(tabelas já existiam – ok)"

if [[ "${PRIMEIRA_SENHA:-}" == "1" || ! -f "$HOME/.sisporto_admin_ok" ]]; then
  say "Gerando senha temporária do administrador $ADMIN_EMAIL..."
  tarefa sisporto-senha "--app,app,definir-senha,$ADMIN_EMAIL"
  sleep 5
  gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=sisporto-senha \
    AND textPayload:\"Senha temporária\"" --limit=1 --format="value(textPayload)" --freshness=10m
  touch "$HOME/.sisporto_admin_ok"
fi

URL=$(gcloud run services describe "$SERVICO" --region="$REGIAO" --format="value(status.url)")
say "PRONTO!"
echo "Sistema no ar: $URL"
echo "Entre com $ADMIN_EMAIL e a senha temporária acima, e troque a senha em 'Trocar senha'."
