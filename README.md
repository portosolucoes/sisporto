# SisPorto

Sistema de gestão da **Porto Soluções Corporativas**: help desk com SLA, portal do cliente
com painel de horas, apontamento e aprovação de horas, clientes e contratos.
(Fase 1. Projetos, faturamento e financeiro vêm nas próximas fases.)

## O que já funciona (Fase 1)

| Equipe Porto | Portal do cliente |
|---|---|
| Painel: abertos, SLA estourado/em risco, fila sem responsável | Painel: horas do mês, saldo do banco de horas, franquia, gráfico por mês |
| Chamados: filtros, conversa, nota interna, status, prioridade, responsável | Abrir chamado, acompanhar, responder, reabrir |
| Prazos em horário útil (seg–sex 8h–18h, feriados), pausa em "aguardando cliente" | Avaliação do atendimento (1 a 5) |
| Lançamento de horas por chamado; aprovação pelo administrador | Relatório mensal de horas |
| Clientes, contratos (5 modalidades), contatos com acesso ao portal | |
| Equipe e perfis (admin, técnico, financeiro), configuração de SLA | |

Segurança: senhas com hash, proteção CSRF, cookies seguros, cada cliente só vê os próprios dados,
auditoria de contratos, horas e usuários no banco.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `app/` | Aplicação Flask (rotas, telas, regras) |
| `db/` | Banco PostgreSQL: tabelas, regras de SLA, visões, dados iniciais |
| `tests/` | Testes de ponta a ponta (16) |
| `infra/gcp/` | Travas de custo e script de publicação no Google Cloud |

## Publicar / atualizar (Cloud Shell)

```bash
git clone https://github.com/portosolucoes/sisporto.git && cd sisporto   # 1ª vez
git pull                                                                   # nas atualizações
bash infra/gcp/publicar.sh
```

## Rodar localmente

```bash
pip install -r requirements.txt
export DATABASE_URL="host=/tmp user=postgres dbname=sisporto" SECRET_KEY=dev COOKIE_SEGURO=0
flask --app app banco-criar
flask --app app demo            # opcional: dados de exemplo (senha demo-sisporto-2026)
flask --app app run
python -m unittest -v tests.test_app
```

## Comandos de manutenção

- `flask --app app banco-criar` – cria tabelas e dados iniciais numa base vazia
- `flask --app app definir-senha EMAIL` – gera senha temporária
- `flask --app app demo` – dados de exemplo (só teste)
