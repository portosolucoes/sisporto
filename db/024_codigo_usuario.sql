-- =============================================================================
-- SisPorto – Migração 024: código de usuário como chave (R0001 recursos,
-- C0001 usuários de clientes). O e-mail deixa de ser único no sistema todo:
-- é único entre os recursos e, por cliente, entre os usuários daquele cliente.
-- =============================================================================
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS codigo text;
UPDATE usuarios SET codigo = CASE WHEN perfil = 'cliente' THEN 'C' ELSE 'R' END || lpad(id::text, 4, '0')
 WHERE codigo IS NULL;
ALTER TABLE usuarios ALTER COLUMN codigo SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_usuarios_codigo ON usuarios (upper(codigo));

CREATE OR REPLACE FUNCTION tg_usuario_codigo() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.codigo IS NULL OR NEW.codigo = '' THEN
    NEW.codigo := CASE WHEN NEW.perfil = 'cliente' THEN 'C' ELSE 'R' END || lpad(NEW.id::text, 4, '0');
  END IF;
  NEW.codigo := upper(NEW.codigo);
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS usuarios_codigo ON usuarios;
CREATE TRIGGER usuarios_codigo BEFORE INSERT ON usuarios FOR EACH ROW EXECUTE FUNCTION tg_usuario_codigo();

ALTER TABLE usuarios DROP CONSTRAINT IF EXISTS usuarios_email_key;
CREATE UNIQUE INDEX IF NOT EXISTS ux_usuarios_email_recurso ON usuarios (email) WHERE perfil <> 'cliente';
CREATE UNIQUE INDEX IF NOT EXISTS ux_usuarios_email_cliente ON usuarios (cliente_id, email) WHERE perfil = 'cliente';
CREATE INDEX IF NOT EXISTS ix_usuarios_email ON usuarios (email);
