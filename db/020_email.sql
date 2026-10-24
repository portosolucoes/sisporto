-- =============================================================================
-- SisPorto – Migração 020: chamados por e-mail e fila de notificações
-- =============================================================================
ALTER TABLE chamados ADD COLUMN IF NOT EXISTS email_message_id text UNIQUE;

CREATE TABLE IF NOT EXISTS emails_saida (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chamado_id  bigint      REFERENCES chamados(id) ON DELETE SET NULL,
  para        citext      NOT NULL,
  assunto     text        NOT NULL,
  corpo       text        NOT NULL,
  evento      text        NOT NULL,          -- aberto, resposta, resolvido
  criado_em   timestamptz NOT NULL DEFAULT now(),
  enviado_em  timestamptz
);
CREATE INDEX IF NOT EXISTS ix_emails_pendentes ON emails_saida (criado_em) WHERE enviado_em IS NULL;

-- E-mails recebidos que não puderam virar chamado (remetente desconhecido etc.)
CREATE TABLE IF NOT EXISTS emails_recusados (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  message_id  text        UNIQUE,
  de          citext      NOT NULL,
  assunto     text,
  motivo      text        NOT NULL,
  recebido_em timestamptz NOT NULL DEFAULT now()
);
