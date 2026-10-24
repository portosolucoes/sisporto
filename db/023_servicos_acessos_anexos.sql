-- =============================================================================
-- SisPorto – Migração 023: tipos de serviço, horas por contrato, acessos por
-- rotina e anexos de chamados
-- =============================================================================

-- Tipos de serviço (editáveis) -------------------------------------------------
CREATE TABLE IF NOT EXISTS tipos_servico (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nome       text    NOT NULL UNIQUE,
  ativo      boolean NOT NULL DEFAULT true,
  ordem      smallint NOT NULL DEFAULT 0,
  criado_em  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO tipos_servico (nome, ordem) VALUES
  ('Help desk', 1), ('Desenvolvimento', 2), ('Infraestrutura', 3), ('Consultoria', 4)
ON CONFLICT (nome) DO NOTHING;

ALTER TABLE apontamentos ADD COLUMN IF NOT EXISTS tipo_servico_id bigint REFERENCES tipos_servico(id);
UPDATE apontamentos SET tipo_servico_id = (SELECT id FROM tipos_servico WHERE nome = 'Help desk')
 WHERE tipo_servico_id IS NULL;

-- Horas podem ser lançadas direto no contrato (projeto/suporte), sem chamado
ALTER TABLE apontamentos DROP CONSTRAINT IF EXISTS ck_apontamento_origem;
ALTER TABLE apontamentos ADD CONSTRAINT ck_apontamento_origem
  CHECK (chamado_id IS NOT NULL OR tarefa_id IS NOT NULL OR contrato_id IS NOT NULL);

-- Horas liberadas: qualquer contrato com horas_pacote (banco de horas, projeto...)
CREATE OR REPLACE VIEW vw_saldo_banco_horas AS
SELECT ct.id                                         AS contrato_id,
       ct.cliente_id,
       ct.descricao,
       ct.horas_pacote,
       ct.data_inicio,
       ct.data_fim                                   AS validade,
       round(coalesce(sum(a.minutos), 0) / 60.0, 2)  AS horas_consumidas,
       round(ct.horas_pacote - coalesce(sum(a.minutos), 0) / 60.0, 2) AS horas_saldo,
       round(100 * coalesce(sum(a.minutos), 0) / 60.0 / ct.horas_pacote, 1) AS pct_consumido
  FROM contratos ct
  LEFT JOIN apontamentos a
         ON a.contrato_id = ct.id AND a.faturavel AND a.status <> 'rejeitado'
 WHERE ct.horas_pacote IS NOT NULL
 GROUP BY ct.id;

-- Acessos por rotina (exceções ao padrão do perfil) ---------------------------
CREATE TABLE IF NOT EXISTS permissoes (
  usuario_id  bigint  NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  rotina      text    NOT NULL,
  permitido   boolean NOT NULL,
  PRIMARY KEY (usuario_id, rotina)
);

-- Anexos guardados no banco (só compactados, até 20 MB) -----------------------
ALTER TABLE chamado_anexos ALTER COLUMN caminho_storage DROP NOT NULL;
ALTER TABLE chamado_anexos ADD COLUMN IF NOT EXISTS arquivo bytea;
