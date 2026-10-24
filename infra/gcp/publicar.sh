#!/usr/bin/env bash
# =============================================================================
#  SisPorto – Publicação no Google Cloud (rodar no Cloud Shell, dentro da pasta
#  do repositório:  git clone https://github.com/portosolucoes/sisporto && cd sisporto)
#
#  1ª execução  : bash infra/gcp/publicar.sh            (cria banco, segredos e o serviço ~15 min)
#  Atualizações : bash infra/gcp/publicar.sh            (só republica o código ~4 min)
#  Automático   : GitHub Actions (.github/workflows) roda este script com SO_ATUALIZAR=1
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
ADMIN_EMAIL="${ADMIN_EMAIL:-contato@portosolucoes.com.br}"

say(){ echo -e "\n\033[1;34m==> $*\033[0m"; }
cd "$(dirname "$0")/../.."   # raiz do repositório

# --- Conferência do pacote (evita publicar arquivos misturados de versões diferentes) ---
if [[ -f MANIFESTO.sha256 && "${IGNORAR_MANIFESTO:-}" != "1" ]]; then
  say "Conferindo os arquivos da versão $(sed -n 's/^VERSAO = "\(.*\)"/\1/p' app/__init__.py)..."
  if ! sha256sum --quiet -c MANIFESTO.sha256; then
    echo -e "\n\033[1;31mArquivos diferentes do pacote oficial (listados acima).\033[0m"
    echo "Provável upload incompleto. Envie o zip da versão de novo (ver infra/COMO-ATUALIZAR.md)."
    echo "Para publicar mesmo assim: IGNORAR_MANIFESTO=1 bash infra/gcp/publicar.sh"
    exit 1
  fi
fi
gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
SA_RUN="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
CONEXAO="$PROJECT_ID:$REGIAO:$INSTANCIA"

if [[ "${SO_ATUALIZAR:-}" != "1" ]]; then
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
gcloud secrets describe sisporto-integracao-token >/dev/null 2>&1 || \
  openssl rand -hex 24 | tr -d '\n' | gcloud secrets create sisporto-integracao-token --data-file=-
SEGREDOS="DATABASE_URL=sisporto-database-url:latest,SECRET_KEY=sisporto-secret-key:latest,INTEGRACAO_TOKEN=sisporto-integracao-token:latest"
LISTA_SEGREDOS="sisporto-database-url sisporto-secret-key sisporto-integracao-token"
# login Google da equipe (opcional – ver infra/gcp/LOGIN-GOOGLE.md)
if gcloud secrets describe sisporto-google-client-id >/dev/null 2>&1; then
  SEGREDOS="$SEGREDOS,GOOGLE_CLIENT_ID=sisporto-google-client-id:latest,GOOGLE_CLIENT_SECRET=sisporto-google-client-secret:latest"
  LISTA_SEGREDOS="$LISTA_SEGREDOS sisporto-google-client-id sisporto-google-client-secret"
fi
for S in $LISTA_SEGREDOS; do
  gcloud secrets add-iam-policy-binding "$S" --member="serviceAccount:$SA_RUN" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA_RUN" \
  --role=roles/cloudsql.client --condition=None >/dev/null
else
  # modo automático (GitHub Actions): banco e segredos já existem
  SEGREDOS="DATABASE_URL=sisporto-database-url:latest,SECRET_KEY=sisporto-secret-key:latest,INTEGRACAO_TOKEN=sisporto-integracao-token:latest"
  if gcloud secrets describe sisporto-google-client-id >/dev/null 2>&1; then
    SEGREDOS="$SEGREDOS,GOOGLE_CLIENT_ID=sisporto-google-client-id:latest,GOOGLE_CLIENT_SECRET=sisporto-google-client-secret:latest"
  fi
fi

# --- Serviço ----------------------------------------------------------------
say "Publicando o sistema no Cloud Run (máx. 2 instâncias)..."
gcloud run deploy "$SERVICO" --source . --region="$REGIAO" --allow-unauthenticated \
  --add-cloudsql-instances="$CONEXAO" \
  --set-secrets="$SEGREDOS" \
  --max-instances=2 --min-instances=0 --memory=512Mi --cpu=1 --concurrency=40 --quiet

IMAGEM=$(gcloud run services describe "$SERVICO" --region="$REGIAO" \
  --format="value(spec.template.spec.containers[0].image)")

# --- Tarefas de manutenção (mesma imagem) -----------------------------------
tarefa(){  # nome, argumentos separados por vírgula
  gcloud run jobs deploy "$1" --image="$IMAGEM" --region="$REGIAO" \
    --set-cloudsql-instances="$CONEXAO" \
    --set-secrets="$SEGREDOS" \
    --command=flask --args="$2" --max-retries=0 --quiet >/dev/null
  gcloud run jobs execute "$1" --region="$REGIAO" --wait
}

say "Criando/atualizando as tabelas do banco..."
tarefa sisporto-banco "--app,app,banco-atualizar"

GEROU_SENHA=0
if [[ "${PRIMEIRA_SENHA:-}" == "1" || ( "${SO_ATUALIZAR:-}" != "1" && ! -f "$HOME/.sisporto_admin_ok" ) ]]; then
  GEROU_SENHA=1
  say "Criando/corrigindo o administrador $ADMIN_EMAIL e gerando senha temporária..."
  tarefa sisporto-senha "--app,app,criar-admin,$ADMIN_EMAIL"
  sleep 5
  gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=sisporto-senha \
    AND textPayload:\"Senha temporária\"" --limit=1 --format="value(textPayload)" --freshness=10m
  touch "$HOME/.sisporto_admin_ok"
fi

URL=$(gcloud run services describe "$SERVICO" --region="$REGIAO" --format="value(status.url)")
VERSAO=$(sed -n 's/^VERSAO = "\(.*\)"/\1/p' app/__init__.py)
say "PRONTO! SisPorto $VERSAO publicado."
echo "Sistema no ar: $URL"
if [[ "$GEROU_SENHA" == "1" ]]; then
  echo "Entre com $ADMIN_EMAIL e a senha temporária acima, e troque a senha em 'Trocar senha'."
else
  echo "Atualização publicada. As senhas não mudaram: entre com a senha que você já usa."
  echo "(Esqueceu? Use 'Esqueci minha senha' na tela de login, ou rode: PRIMEIRA_SENHA=1 bash infra/gcp/publicar.sh)"
fi
