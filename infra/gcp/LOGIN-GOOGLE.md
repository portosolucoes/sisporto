# Login com Google para a equipe (@portosolucoes.com.br)

Com isto, a equipe entra no SisPorto clicando em **Entrar com Google**, sem senha própria.
Só contas do domínio portosolucoes.com.br **e** cadastradas em *Equipe* no sistema conseguem entrar.

1. Abra **console.cloud.google.com/auth/overview?project=porto-sisporto-2610**
   (menu: APIs e serviços → Google Auth Platform) e clique em **Começar / Get started**:
   - Nome do app: `SisPorto` · E-mail de suporte: `contato@portosolucoes.com.br`
   - Público (Audience): **Interno** – só usuários do seu Workspace
   - Contato: `contato@portosolucoes.com.br` → aceite e **Criar**
2. Vá em **Clientes (Clients) → Criar cliente**:
   - Tipo: **Aplicativo da Web** · Nome: `SisPorto`
   - **URIs de redirecionamento autorizados** → adicionar:
     `https://<ENDEREÇO DO SISTEMA>/entrar/google/retorno`
     (o endereço que o publicar.sh mostrou, ex.: https://sisporto-xxxx.southamerica-east1.run.app)
   - **Criar**. Aparecem o *ID do cliente* e a *Chave secreta*. **Não mande no chat.**
3. No Cloud Shell, dentro da pasta sisporto:
   ```bash
   git pull
   bash infra/gcp/configurar-google.sh     # cole o ID e a chave quando pedir
   bash infra/gcp/publicar.sh
   ```
4. No sistema, em **Equipe**, cadastre cada pessoa com o e-mail Google dela.

Quando mudarmos para `sistema.portosolucoes.com.br`, inclua também
`https://sistema.portosolucoes.com.br/entrar/google/retorno` no passo 2.
