-- =============================================================================
-- SisPorto – Migração 022: recuperação de senha por e-mail
-- =============================================================================
CREATE TABLE IF NOT EXISTS senha_tokens (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  usuario_id  bigint      NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  token_hash  text        NOT NULL UNIQUE,      -- sha256 do token; o token em si só vai no e-mail
  criado_em   timestamptz NOT NULL DEFAULT now(),
  expira_em   timestamptz NOT NULL,
  usado_em    timestamptz,
  ip          text
);
CREATE INDEX IF NOT EXISTS ix_senha_tokens_usuario ON senha_tokens (usuario_id, criado_em);
