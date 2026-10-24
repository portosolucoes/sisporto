# Chamados por e-mail (suporte@portosolucoes.com.br)

Como funciona: um pequeno script do Google (Apps Script) roda **na conta que recebe os e-mails do
suporte@** e, a cada 5 minutos:

- lê os e-mails novos enviados ao suporte@ e manda para o SisPorto:
  - remetente cadastrado como contato, ou com o domínio do cliente → **vira chamado**
  - resposta com `[#número]` no assunto → **vira mensagem do chamado** (e reabre se estava resolvido)
  - remetente desconhecido → fica no rótulo **SisPorto/Revisar** e aparece em *E-mails não processados*
- envia as notificações do sistema (chamado aberto, nova resposta da equipe, chamado resolvido),
  com resposta direcionada ao suporte@.

## Instalação (10 min, uma vez)

1. No Cloud Shell, pegue o código de integração:
   ```bash
   gcloud secrets versions access latest --secret=sisporto-integracao-token; echo
   ```
2. Entre em **script.google.com** logado como **suporte@portosolucoes.com.br** (é uma caixa própria)
   → **Novo projeto**.
3. Apague o conteúdo, cole o arquivo `sisporto-email.gs` e salve (nome: *SisPorto E-mail*).
4. **Configurações do projeto** (engrenagem) → **Propriedades do script** → adicione:
   - `SISPORTO_URL` = endereço do sistema, sem barra no fim
   - `SISPORTO_TOKEN` = o código do passo 1
5. Volte ao editor, escolha a função **instalar** e clique em **Executar** → autorize.
6. Teste: de um e-mail de cliente cadastrado, escreva para suporte@ e aguarde até 5 minutos.

Para clientes cujos funcionários não têm acesso ao portal, preencha o **Domínio de e-mail** no
cadastro do cliente (ex.: `alfalog.com.br`): qualquer pessoa desse domínio pode abrir chamado por e-mail.
