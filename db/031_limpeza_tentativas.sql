-- 031 (versão 1.7): limite de tentativas (login, recuperação de senha) e controle da limpeza automática
CREATE TABLE IF NOT EXISTS tentativas (
  id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chave  text NOT NULL,
  em     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tentativas_chave_em ON tentativas (chave, em);
COMMENT ON TABLE tentativas IS 'Tentativas recentes (login com erro, pedidos de senha) por origem, para barrar robôs. Limpa sozinha.';
ALTER TABLE chamado_anexos ADD COLUMN IF NOT EXISTS arquivado_em timestamptz;
COMMENT ON COLUMN chamado_anexos.arquivado_em IS 'Quando o arquivo saiu do banco (fica só no Google Drive).';
