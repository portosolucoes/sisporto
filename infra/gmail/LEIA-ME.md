# Script do suporte@ (e-mail, Agenda, Drive, Chat, NPS e BI)

Um pequeno script do Google (Apps Script) roda **na conta suporte@portosolucoes.com.br** e, a cada 5 minutos:

1. **E-mails recebidos** → o SisPorto:
   - remetente cadastrado como contato, ou com o domínio do cliente → **vira chamado**
   - resposta com `[#número]` no assunto → **vira mensagem do chamado** (e reabre se estava resolvido)
   - remetente desconhecido → rótulo **SisPorto/Revisar** e tela *E-mails não processados*
2. **Notificações** do sistema (chamado aberto, resposta, resolvido, link de senha, NPS), com resposta ao suporte@.
3. **Google Agenda**: visitas/reuniões marcadas no chamado viram eventos com convite; cancelar no sistema apaga o evento.
4. **Google Drive**: anexos de até 20 MB enviados pelo sistema são copiados para
   *SisPorto – Chamados / cliente / #chamado*. Arquivos maiores o recurso sobe manualmente e cola o link no chamado.
5. **Tarefas**: alertas de SLA (em risco / estourado) e chamados críticos no **Google Chat**; NPS mensal.
6. **1 vez por dia**: atualiza a planilha **SisPorto BI** (abas apontamentos, contratos, chamados) – fonte do Looker Studio.

## Instalação ou atualização (10 min)

1. No Cloud Shell, pegue o código de integração:
   ```bash
   gcloud secrets versions access latest --secret=sisporto-integracao-token; echo
   ```
2. Entre em **script.google.com** logado como **suporte@** → abra o projeto *SisPorto E-mail*
   (ou **Novo projeto** na primeira vez).
3. Apague o conteúdo, cole o arquivo `sisporto-email.gs` inteiro e salve.
4. Só na primeira vez – **Configurações do projeto** → **Propriedades do script**:
   - `SISPORTO_URL` = endereço do sistema, sem barra no fim
   - `SISPORTO_TOKEN` = o código do passo 1
5. Escolha a função **instalar** → **Executar** → autorize (agora pede também Agenda, Drive e Planilhas).
6. Teste: de um e-mail de cliente cadastrado, escreva para suporte@ e aguarde até 5 minutos.

## Google Chat (alertas)

No espaço do Chat: nome do espaço → **Apps e integrações** → **Webhooks** → criar → copie a URL.
No SisPorto: **Configurações → Integrações** → cole em *Webhook do Google Chat* → **Testar**.
(Não envie essa URL por chat/e-mail: quem a tiver pode postar no espaço.)

## Looker Studio (painéis)

1. Depois do primeiro dia, a planilha **SisPorto BI** aparece no Drive do suporte@.
   Compartilhe-a (leitura) com quem vai montar os painéis.
2. **lookerstudio.google.com** → *Criar* → *Fonte de dados* → **Planilhas Google** → SisPorto BI →
   aba `apontamentos` (repita para `contratos` e `chamados`).
3. Monte os gráficos (horas por cliente/tipo/recurso, custo × receita, SLA, NPS) e compartilhe o relatório.

## Dicas

- Clientes sem acesso ao portal: preencha o **Domínio de e-mail** no cadastro do cliente
  (ex.: `alfalog.com.br`) – qualquer pessoa desse domínio pode abrir chamado por e-mail.
- Erros aparecem em script.google.com → **Execuções**.
