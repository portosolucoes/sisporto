-- =============================================================================
-- SisPorto – Migração 021: configurações gerais (logo da empresa etc.)
-- =============================================================================
CREATE TABLE IF NOT EXISTS configuracoes (
  chave          text PRIMARY KEY,
  texto          text,
  arquivo        bytea,
  tipo_mime      text,
  atualizado_em  timestamptz NOT NULL DEFAULT now()
);
