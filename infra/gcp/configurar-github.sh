#!/usr/bin/env bash
# =============================================================================
#  SisPorto – liga o GitHub ao Google Cloud para publicar sozinho (rodar UMA vez no Cloud Shell)
#
#  Cria uma "identidade federada" (Workload Identity Federation): o GitHub Actions do repositório
#  portosolucoes/sisporto (só a branch main) ganha permissão para publicar no Cloud Run.
#  Não existe chave/senha para guardar ou vazar: o Google confia apenas nesse repositório.
# =============================================================================
set -euo pipefail
PROJECT_ID="${PROJECT_ID:-porto-sisporto-2610}"
REPO="${REPO:-portosolucoes/sisporto}"
POOL="github"; PROVEDOR="sisporto"; SA_NOME="github-deploy"
gcloud config set project "$PROJECT_ID" >/dev/null
NUM=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
SA="$SA_NOME@$PROJECT_ID.iam.gserviceaccount.com"
SA_RUN="$NUM-compute@developer.gserviceaccount.com"
echo "==> APIs"
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com iam.googleapis.com cloudresourcemanager.googleapis.com >/dev/null

echo "==> Conta de publicação $SA"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NOME" --display-name="GitHub Actions – publica o SisPorto"
for PAPEL in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.writer roles/storage.admin \
             roles/logging.viewer roles/secretmanager.viewer roles/serviceusage.serviceUsageConsumer roles/browser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role="$PAPEL" \
    --condition=None >/dev/null
done
# pode "agir como" a conta que roda o sistema e o build
gcloud iam service-accounts add-iam-policy-binding "$SA_RUN" --member="serviceAccount:$SA" \
  --role=roles/iam.serviceAccountUser >/dev/null

echo "==> Identidade federada do GitHub"
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "$POOL" --location=global --display-name="GitHub"
gcloud iam workload-identity-pools providers describe "$PROVEDOR" --location=global \
    --workload-identity-pool="$POOL" >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc "$PROVEDOR" --location=global \
    --workload-identity-pool="$POOL" --display-name="Repositório SisPorto" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository=='$REPO' && assertion.ref=='refs/heads/main'"
# a conta recém-criada leva até ~1 min para valer no IAM: tenta de novo algumas vezes
for TENTATIVA in 1 2 3 4 5 6; do
  if gcloud iam service-accounts add-iam-policy-binding "$SA" --role=roles/iam.workloadIdentityUser \
      --member="principalSet://iam.googleapis.com/projects/$NUM/locations/global/workloadIdentityPools/$POOL/attribute.repository/$REPO" \
      >/dev/null 2>&1; then break; fi
  [[ $TENTATIVA == 6 ]] && { echo "Não consegui ligar o GitHub à conta $SA. Rode o script de novo em 1 minuto."; exit 1; }
  echo "   aguardando a conta nova propagar no Google ($TENTATIVA/6)..."; sleep 20
done

echo
echo "PRONTO. O GitHub ($REPO, branch main) já pode publicar o SisPorto."
echo "Provedor: projects/$NUM/locations/global/workloadIdentityPools/$POOL/providers/$PROVEDOR"
echo "Conta:    $SA"
