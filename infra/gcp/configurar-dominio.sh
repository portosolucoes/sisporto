#!/usr/bin/env bash
# =============================================================================
#  SisPorto – domínio próprio  https://sistema.portosolucoes.com.br
#  (Firebase Hosting na frente do Cloud Run; o Cloud Run de São Paulo não aceita domínio direto)
#
#  Rodar no Cloud Shell, dentro de ~/sisporto:
#     bash infra/gcp/configurar-dominio.sh            -> liga o domínio e mostra os registros DNS
#     bash infra/gcp/configurar-dominio.sh situacao   -> confere DNS/certificado (rodar quando quiser)
#     bash infra/gcp/configurar-dominio.sh concluir   -> quando o domínio abrir: links e monitor no domínio
#
#  Custo: Firebase Hosting grátis até 10 GB guardados e 360 MB/dia de tráfego; acima disso
#  US$ 0,15/GB. Certificado https grátis. Para o volume da Porto: R$ 0 a R$ 5/mês.
#  As travas de custo continuam valendo: o corte automático tira o sistema do ar também pelo domínio.
# =============================================================================
set -uo pipefail
PROJECT_ID="porto-sisporto-2610"; REGIAO="southamerica-east1"; SERVICO="sisporto"
DOMINIO="sistema.portosolucoes.com.br"; SITE="$PROJECT_ID"
API="https://firebasehosting.googleapis.com/v1beta1"
say(){ echo -e "\n\033[1;34m==> $*\033[0m"; }
erro(){ echo -e "\033[1;31m$*\033[0m"; exit 1; }

gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q . || erro "Cloud Shell sem login: rode  gcloud auth login"
gcloud config set project "$PROJECT_ID" >/dev/null
TOKEN=$(gcloud auth print-access-token)
chamar(){  # método url [json]
  curl -sS -X "$1" "$2" -H "Authorization: Bearer $TOKEN" -H "x-goog-user-project: $PROJECT_ID" \
    -H "Content-Type: application/json" ${3:+-d "$3"}
}
campo(){ python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1" 2>/dev/null; }

mostrar_dns(){
  chamar GET "$API/projects/$PROJECT_ID/sites/$SITE/customDomains/$DOMINIO" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if "error" in d:
    print("   erro:", d["error"].get("message")); sys.exit(0)
print("   Domínio:", d.get("hostState", "?"), "| Propriedade:", d.get("ownershipState", "?"),
      "| Certificado https:", (d.get("cert") or {}).get("state", "?"))
regs = set()
def junta(bloco):
    for conj in (bloco or {}).get("desired", []):
        for r in conj.get("records", []):
            if r.get("requiredAction", "ADD") != "NONE":
                regs.add((r.get("requiredAction", "ADD"), r.get("type", "?"), r.get("domainName", "").rstrip("."), r.get("rdata", "")))
junta(d.get("requiredDnsUpdates"))
junta(((d.get("cert") or {}).get("verification") or {}).get("dns"))
if regs:
    print("\n   CADASTRE NO REGISTRO.BR (portosolucoes.com.br > DNS > Editar zona):")
    print("   %-8s %-6s %-45s %s" % ("AÇÃO", "TIPO", "NOME", "VALOR"))
    for a, t, n, v in sorted(regs, key=lambda x: x[0] != "REMOVE"):
        nome = n[:-len(".portosolucoes.com.br")] if n.endswith(".portosolucoes.com.br") else n
        print("   %-8s %-6s %-45s %s" % ("APAGAR" if a == "REMOVE" else "INCLUIR", t, nome, v))
    print("\n   (no Registro.br o NOME é só a parte antes de .portosolucoes.com.br, ex.: sistema)")
else:
    print("   Nenhum registro pendente.")
'
}

case "${1:-}" in
situacao)
  say "Situação de $DOMINIO"; mostrar_dns
  echo; curl -s -o /dev/null -w "   https://$DOMINIO/saude -> HTTP %{http_code}\n" --max-time 15 "https://$DOMINIO/saude" \
    || echo "   https://$DOMINIO ainda não responde (DNS/certificado em andamento)"
  exit 0;;
concluir)
  say "Conferindo https://$DOMINIO ..."
  curl -sf --max-time 20 "https://$DOMINIO/saude" | grep -q '"ok":true' \
    || erro "O domínio ainda não responde com https. Rode  bash infra/gcp/configurar-dominio.sh situacao  e tente mais tarde."
  say "Links do sistema (e-mails, login Google) passam a usar https://$DOMINIO"
  gcloud run services update "$SERVICO" --region="$REGIAO" --update-env-vars="URL_PUBLICA=https://$DOMINIO" --quiet >/dev/null \
    && echo "   ok"
  say "Monitor de 'sistema fora do ar' também no domínio"
  if ! gcloud monitoring uptime list-configs --format="value(displayName)" 2>/dev/null | grep -qxF "SisPorto no domínio"; then
    gcloud monitoring uptime create "SisPorto no domínio" --resource-type=uptime-url \
      --resource-labels="host=$DOMINIO,project_id=$PROJECT_ID" --path="/saude" --protocol=https --period=15 >/dev/null \
      && echo "   verificação a cada 15 min em https://$DOMINIO/saude"
  else echo "   já existe"; fi
  cat <<FIM

PRONTO: https://$DOMINIO
  Equipe:   https://$DOMINIO/entrar
  Clientes: https://$DOMINIO/portal
Se usa "Entrar com Google": no console (APIs e serviços > Credenciais > cliente OAuth do SisPorto)
inclua em "URIs de redirecionamento autorizados":  https://$DOMINIO/entrar/google/retorno
FIM
  exit 0;;
esac

# --- 1. Firebase no projeto ---------------------------------------------------------------------
say "Ativando o Firebase Hosting no projeto $PROJECT_ID"
gcloud services enable firebase.googleapis.com firebasehosting.googleapis.com >/dev/null || erro "Não consegui ativar as APIs do Firebase."
if ! chamar GET "https://firebase.googleapis.com/v1beta1/projects/$PROJECT_ID" | grep -q '"projectId"'; then
  R=$(chamar POST "https://firebase.googleapis.com/v1beta1/projects/$PROJECT_ID:addFirebase" '{}')
  if echo "$R" | grep -q '"error"'; then
    echo "$R" | campo 'd["error"]["message"]'
    erro "Abra https://console.firebase.google.com > 'Criar um projeto' > 'Adicionar Firebase a um projeto do Google Cloud' > escolha $PROJECT_ID (sem Analytics). Depois rode este script de novo."
  fi
  echo "   aguardando o Firebase..."; sleep 30
fi
echo "   ok"

# --- 2. Site de hospedagem ----------------------------------------------------------------------
if ! chamar GET "$API/projects/$PROJECT_ID/sites/$SITE" | grep -q '"name"'; then
  chamar POST "$API/projects/$PROJECT_ID/sites?siteId=$SITE" '{}' | grep -q '"name"' || erro "Não consegui criar o site de hospedagem."
fi

# --- 3. Todo acesso vai para o SisPorto no Cloud Run ---------------------------------------------
say "Ligando o Firebase Hosting ao serviço $SERVICO ($REGIAO)"
CFG="{\"config\":{\"rewrites\":[{\"glob\":\"**\",\"run\":{\"serviceId\":\"$SERVICO\",\"region\":\"$REGIAO\"}}]}}"
R=$(chamar POST "$API/sites/$SITE/versions" "$CFG")
VERSAO=$(echo "$R" | campo 'd["name"]')
if [[ -z "$VERSAO" ]]; then
  echo "$R" | campo 'd["error"]["message"]'
  erro "O Firebase recusou a ligação com o Cloud Run (veja a mensagem acima). Nada foi alterado no sistema. Me envie a mensagem."
fi
chamar PATCH "$API/$VERSAO?update_mask=status" '{"status":"FINALIZED"}' | grep -q FINALIZED || erro "Falha ao finalizar a versão."
chamar POST "$API/sites/$SITE/releases?versionName=$VERSAO" '{}' | grep -q '"name"' || erro "Falha ao publicar."
sleep 5
curl -sf --max-time 30 "https://$SITE.web.app/saude" | grep -q '"ok":true' \
  && echo "   ok: https://$SITE.web.app já abre o SisPorto" \
  || echo "   publicado (o endereço https://$SITE.web.app pode levar 1–2 min para responder)"

# --- 4. Domínio próprio --------------------------------------------------------------------------
say "Registrando o domínio $DOMINIO"
if ! chamar GET "$API/projects/$PROJECT_ID/sites/$SITE/customDomains/$DOMINIO" | grep -q '"name"'; then
  R=$(chamar POST "$API/projects/$PROJECT_ID/sites/$SITE/customDomains?customDomainId=$DOMINIO" '{}')
  echo "$R" | grep -q '"error"' && { echo "$R" | campo 'd["error"]["message"]'; erro "Não consegui registrar o domínio."; }
fi
echo "   aguardando o Google preparar os registros DNS (até 5 min)..."
for _ in $(seq 1 30); do
  chamar GET "$API/projects/$PROJECT_ID/sites/$SITE/customDomains/$DOMINIO" | grep -q '"rdata"' && break
  sleep 10
done
mostrar_dns
cat <<FIM

PRÓXIMOS PASSOS
 1. Cadastre os registros acima no Registro.br (só eles; o site e os e-mails de portosolucoes.com.br não mudam).
 2. Confira de vez em quando:   bash infra/gcp/configurar-dominio.sh situacao
    (DNS: minutos a algumas horas; certificado https: até 24 h depois do DNS)
 3. Quando https://$DOMINIO abrir:   bash infra/gcp/configurar-dominio.sh concluir
Enquanto isso o sistema continua normal em $(gcloud run services describe "$SERVICO" --region="$REGIAO" --format="value(status.url)")
FIM
