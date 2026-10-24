-- 027: senha provisória (troca obrigatória no 1º acesso) e consultas SQL do administrador
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS senha_provisoria boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS consultas_salvas (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nome        text NOT NULL,
  sql         text NOT NULL,
  criado_por  bigint REFERENCES usuarios(id),
  criado_em   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (nome)
);

CREATE TABLE IF NOT EXISTS consultas_log (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  usuario_id  bigint NOT NULL REFERENCES usuarios(id),
  sql         text NOT NULL,
  linhas      integer,
  erro        text,
  executado_em timestamptz NOT NULL DEFAULT now()
);

-- rotina "contratos" separada de "clientes": quem tinha liberação/bloqueio de clientes herda o mesmo para contratos
INSERT INTO permissoes (usuario_id, rotina, permitido)
SELECT usuario_id, 'contratos', permitido FROM permissoes WHERE rotina = 'clientes'
ON CONFLICT DO NOTHING;
-- aprovação de horas e cadastro de recursos passaram a ser exclusivos do administrador
DELETE FROM permissoes WHERE rotina IN ('aprovar_horas', 'equipe');

-- motivo da rejeição de horas (o recurso vê em "Minhas horas")
ALTER TABLE apontamentos ADD COLUMN IF NOT EXISTS motivo_rejeicao text;
