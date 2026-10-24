# SisPorto – histórico de versões

Toda versão informa o **orçamento**: custo mensal estimado, teto (R$ 200) e corte automático (R$ 400),
conforme `infra/gcp/orcamento.conf`. Avisos de custo e travas vão sempre para contato@portosolucoes.com.br.

## 1.7.0 — 24/10/2026 (Ajustes 02 + limpeza)
- **Orçamento:** custo estimado R$ 85/mês · teto R$ 200 · corte automático R$ 400 · impacto desta versão: **reduz** o crescimento do banco (anexos antigos e históricos saem sozinhos).
- Cliente: dados do cliente no topo; contratos e usuários abaixo. **Valores (R$) dos contratos só para quem tem acesso a Contratos** — técnicos veem apenas as horas.
- Chamado: **status, prioridade e responsável editáveis por toda a equipe**; trocar o **contrato** continua exigindo a permissão "Alterar o contrato vinculado aos chamados".
- Limpeza automática diária: anexos com mais de 90 dias já copiados ao Drive saem do banco (equipe abre pelo Drive; cliente é orientado a pedir cópia); e-mails enviados > 6 meses, consultas > 1 ano, auditoria > 2 anos.
- Limite de tentativas: login (8 erros por usuário ou 20 por origem em 15 min) e "esqueci minha senha" (10 por hora por origem).
- Travas: alerta de "sistema fora do ar" corrigido; script para logo se o Cloud Shell estiver sem login.

## 1.6.2 — 24/10/2026 (travas de custo v2)
- **Orçamento:** custo estimado R$ 85/mês · teto R$ 200 · corte automático R$ 400 · impacto desta versão: nenhum.
- Travas v2: teto R$ 200 com avisos, corte automático em R$ 400, alertas de disco e de sistema fora do ar, limpeza de versões antigas, cotas de VM/GPU zeradas, chaves de contas de serviço proibidas.
- Toda publicação confere as travas: sem a trava de corte, com valor diferente do aprovado ou com o sistema bloqueado por custo, a publicação não acontece.

## 1.6.1 — 24/10/2026
- Correção da publicação da 1.6.0 (pacote incompleto no servidor).
- **Conferência do pacote**: MANIFESTO.sha256 com a impressão digital de cada arquivo; a publicação para se algum arquivo não bater.
- **Verificação de rotas e telas no build**: se faltar algum módulo, a imagem não é gerada e a versão anterior continua no ar.
- **Publicação automática pelo GitHub Actions**: testes com PostgreSQL de verdade + publicação no Cloud Run a cada atualização; entrega por um único zip.

## 1.6.0 — 24/10/2026
- **Tickets por responsável**: quem não é administrador vê os chamados sem responsável e os atribuídos a si; os de outros recursos ficam ocultos (lista, painel, tela, anexos, cronômetro).
- **Avisos de saldo (80% e 100%)** por e-mail ao cliente (e-mail financeiro + usuários marcados "Avisos de saldo") e ao gestor da conta, e no Google Chat. Um aviso por nível e período.
- **Horas extras aprovadas pelo cliente**: a equipe solicita (tela Horas extras ou de dentro do chamado quando o saldo está acabando); o cliente aprova ou recusa no portal; aprovadas somam ao saldo na hora (no mensal fixo, só no mês). E-mails para os dois lados.
- **Respostas prontas** no chamado, com {cliente}, {solicitante}, {numero}, {titulo} e {recurso}.
- **Base de conhecimento**: artigos internos ou públicos, busca, "ajudou?", artigos relacionados no chamado, **Ajuda** no portal e sugestões automáticas enquanto o cliente digita o assunto do chamado.
- **Fechamento de período**: o administrador fecha um período de horas já aprovado; o banco passa a recusar inclusão, alteração ou exclusão de lançamentos nesse intervalo até a reabertura.
- Número da versão no rodapé do menu.

## 1.5.0 — 24/10/2026 (Ajustes 001)
- Contrato com itens (vários tipos de serviço), validações (vigência, vencimento 1–31, reajuste 1–12) e sugestões (valor mensal, horas liberadas). Mensal fixo com saldo por mês.
- Chamado: cronômetro acima dos detalhes, detalhes acompanham a caixa Atualizar, Atualizar acima da Agenda, permissão para alterar contrato/responsável/status/prioridade, bloqueio sem contrato com saldo.
- Cronômetros concorrentes no mesmo saldo, aviso a 10 minutos e parada no fim do saldo.
- E-mail ao recurso e ao aprovador na aprovação/rejeição de horas.
- Filtros e exportação Excel/PDF em Minhas horas, Aprovação, Apontamentos e Saldos.

## 1.4.0 — 24/10/2026
- Menu em pastas, contratos separados de clientes, aprovação de horas só pelo admin, privacidade entre usuários, consultas SQL, dicionário de dados em PDF, senha provisória, relatório analítico do cliente, dashboard gerencial, PDF nos relatórios.

## 1.3.0 — 23/10/2026
- Cronômetro no chamado, saldos de horas rateados, apontamentos pelo admin, administrador master.

## 1.2.0 / 1.1.0 / 1.0.0 — 23/10/2026
- Relatórios, cadastros completos, NPS, Google Chat/Agenda/Drive, Looker Studio, 2FA; celular, tipos de serviço, acessos por rotina, anexos; base do sistema (chamados, SLA, portal, horas, contratos).
