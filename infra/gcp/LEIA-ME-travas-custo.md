# Google Cloud – Assinatura e travas de custo (SisPorto)

## 1. Assinatura (feita pelo Yuri)
1. Acesse https://console.cloud.google.com com `contato@portosolucoes.com.br`.
2. Clique em **Começar gratuitamente** e aceite os termos.
3. Tipo de conta **Empresa**: CNPJ, razão social e endereço.
4. Cartão de crédito (só verificação; na avaliação nada é cobrado automaticamente).
5. Moeda: **Real (BRL)**.

## 2. Criar as travas (5–8 min)
1. No console, clique no ícone **>_ (Ativar Cloud Shell)** no topo direito.
2. No Cloud Shell, menu **⋮ > Fazer upload** e envie `setup-travas-custo.sh`.
3. Rode: `bash setup-travas-custo.sh`
4. Se aparecer pedido de autorização, clique em **Autorizar**.

Ao final, você terá:

| Momento | O que acontece |
|---|---|
| 50%, 80%, 90% do teto | E-mail de aviso |
| Previsão indica estouro no mês | E-mail de aviso antecipado |
| 100% do teto | E-mail de estouro |
| Acima do teto | **E-mail a cada 24h** até aumentar o teto ou o mês virar |

Teto inicial: **R$ 50/mês** (fase de construção). Na produção, mudaremos para R$ 100.

## 3. Testar
O script imprime ao final um comando de **teste** (simula estouro → e-mail em ~10 min) e outro para **desfazer**.

## 4. Bloqueio automático (quando o sistema for publicado)
Quando o servidor do sistema (Cloud Run) existir, ativamos o **spend cap** em
Faturamento > Orçamentos e alertas: ao atingir o teto, o Cloud Run é bloqueado
sem apagar dados e só volta quando você clicar em **Lift spend cap**.
Também limitamos o servidor a no máximo 2 instâncias.

## Para aprovar mais gasto
Faturamento > **Orçamentos e alertas** > "Porto - Teto mensal SisPorto" > editar valor.
Somente a conta `contato@` (administrador de faturamento) pode fazer isso.

## Custo desta configuração
Praticamente zero: Pub/Sub, a função e o Monitoring ficam dentro das cotas gratuitas.
