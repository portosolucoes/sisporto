# SisPorto

Sistema de gestão da **Porto Soluções Corporativas**: help desk, portal do cliente,
projetos, apontamento de horas, contratos, faturamento e financeiro.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `db/` | Banco PostgreSQL (Cloud SQL): tabelas, regras de SLA, visões e dados iniciais |
| `db/tests/` | Testes das regras do banco |
| `infra/gcp/` | Configuração do Google Cloud (travas de custo) |

## Banco de dados

Aplicar em ordem:

```bash
psql -v ON_ERROR_STOP=1 -f db/001_schema.sql -f db/002_funcoes_sla.sql \
     -f db/003_views.sql -f db/010_dados_iniciais.sql
psql -v ON_ERROR_STOP=1 -f db/tests/teste_regras.sql   # roda em transação e desfaz
```

Regras automáticas no banco:
- Prazos de SLA em **horário útil** (seg–sex 8h–18h, feriados nacionais), por prioridade e contrato
- Pausa do SLA em "aguardando cliente" e retomada automática quando o cliente responde
- Primeira resposta registrada quando a equipe responde
- Histórico de status, auditoria de contratos, horas e usuários
- Saldo de banco de horas e excedente de franquia mensal

## Infra

- Google Cloud, projeto `porto-sisporto-2610`, região `southamerica-east1`
- Orçamento mensal com alertas e aviso diário após estouro (`infra/gcp/`)
