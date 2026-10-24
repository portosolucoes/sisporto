-- =============================================================================
-- SisPorto – Migração 026: tipo do projeto/contrato, rateio de horas por chamado
-- e cronômetro de atendimento
-- =============================================================================
ALTER TABLE contratos ADD COLUMN IF NOT EXISTS tipo_servico_id bigint REFERENCES tipos_servico(id);
ALTER TABLE chamados  ADD COLUMN IF NOT EXISTS horas_alocadas numeric(7,2) CHECK (horas_alocadas >= 0);

CREATE TABLE IF NOT EXISTS cronometros (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chamado_id          bigint      NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  usuario_id          bigint      NOT NULL REFERENCES usuarios(id),
  segundos_acumulados integer     NOT NULL DEFAULT 0 CHECK (segundos_acumulados >= 0),
  rodando_desde       timestamptz,                 -- nulo = pausado
  criado_em           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (chamado_id, usuario_id)
);
