"""Testes de ponta a ponta do SisPorto (Flask test client + PostgreSQL real).

Rodar:  TEST_DATABASE_ADMIN="host=/tmp user=postgres dbname=postgres" python -m unittest -v
Cria e apaga a base 'sisporto_teste'.
"""
from __future__ import annotations

import os
import re
import unittest

from app import create_app, util
from app.db import _Conn
from app.demo import SENHA_DEMO

ADMIN_DSN = os.environ.get("TEST_DATABASE_ADMIN", "host=/tmp user=postgres dbname=postgres")
TEST_DSN = ADMIN_DSN.replace("dbname=postgres", "dbname=sisporto_teste")


def _admin_exec(sql):
    from app import db as dbmod
    if dbmod._DRIVER == "psycopg":
        import psycopg
        with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
            c.execute(sql)
    else:
        from app.pq import _lib
        conn = _lib.PQconnectdb(ADMIN_DSN.encode())
        res = _lib.PQexecParams(conn, sql.encode(), 0, None, None, None, None, 0)
        _lib.PQclear(res)
        _lib.PQfinish(conn)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _admin_exec("DROP DATABASE IF EXISTS sisporto_teste")
        _admin_exec("CREATE DATABASE sisporto_teste")
        cls.app = create_app({"DATABASE_URL": TEST_DSN, "TESTING": True, "SECRET_KEY": "teste",
                              "SESSION_COOKIE_SECURE": False})
        with cls.app.app_context():
            from app.cli import aplicar_sql
            from app.demo import carregar
            from app.db import db
            aplicar_sql()
            cls.ids = carregar()
            db().commit()

    @classmethod
    def tearDownClass(cls):
        from flask import g  # noqa: F401
        _admin_exec("DROP DATABASE IF EXISTS sisporto_teste")

    # --- ajudantes ----------------------------------------------------------
    def cliente_http(self, email=None):
        c = self.app.test_client()
        c.get("/entrar")
        if email:
            r = self.post(c, "/entrar", {"email": email, "senha": SENHA_DEMO})
            self.assertEqual(r.status_code, 302, r.data[:300])
        return c

    def post(self, c, url, data, **kw):
        with c.session_transaction() as s:
            token = s.get("csrf")
        return c.post(url, data={**data, "csrf": token}, **kw)

    def sql(self, query, params=()):
        conn = _Conn(TEST_DSN)
        try:
            rows = conn.all(query, params)
            conn.commit()
            return rows
        finally:
            conn.close()


class TestUtil(unittest.TestCase):
    def test_duracao(self):
        p = util.parse_duracao
        self.assertEqual(p("1:30"), 90)
        self.assertEqual(p("1h30"), 90)
        self.assertEqual(p("2h"), 120)
        self.assertEqual(p("45m"), 45)
        self.assertEqual(p("45"), 45)
        self.assertEqual(p("1,5h"), 90)
        self.assertIsNone(p("abc"))

    def test_formatos(self):
        self.assertEqual(util.horas(90), "1h30")
        self.assertEqual(util.horas(120), "2h")
        self.assertEqual(util.moeda(2500), "R$ 2.500,00")
        self.assertTrue(util.cnpj_valido("11222333000181"))
        self.assertFalse(util.cnpj_valido("11222333000182"))


class TestAcesso(Base):
    def test_login_errado(self):
        c = self.cliente_http()
        r = self.post(c, "/entrar", {"email": "maria@alfalog.com.br", "senha": "errada"})
        self.assertIn("incorretos".encode(), r.data)

    def test_csrf_obrigatorio(self):
        c = self.cliente_http()
        r = c.post("/entrar", data={"email": "maria@alfalog.com.br", "senha": SENHA_DEMO})
        self.assertEqual(r.status_code, 400)

    def test_sem_login_redireciona(self):
        r = self.app.test_client().get("/chamados")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/entrar", r.headers["Location"])

    def test_cliente_nao_acessa_equipe(self):
        c = self.cliente_http("maria@alfalog.com.br")
        self.assertEqual(c.get("/chamados").status_code, 403)
        self.assertEqual(c.get("/clientes").status_code, 403)

    def test_cliente_nao_ve_chamado_de_outro(self):
        num_beta = self.sql("SELECT numero FROM chamados WHERE cliente_id = %s LIMIT 1", [self.ids["beta"]])[0]["numero"]
        c = self.cliente_http("maria@alfalog.com.br")
        self.assertEqual(c.get(f"/portal/chamados/{num_beta}").status_code, 404)

    def test_cliente_nao_ve_nota_interna(self):
        num = self.sql("SELECT numero, id FROM chamados WHERE cliente_id = %s ORDER BY numero LIMIT 1",
                       [self.ids["alfa"]])[0]
        eq = self.cliente_http("ana@portosolucoes.com.br")
        self.post(eq, f"/chamados/{num['numero']}/responder", {"texto": "SEGREDO-INTERNO", "interna": "1"})
        cli = self.cliente_http("maria@alfalog.com.br")
        r = cli.get(f"/portal/chamados/{num['numero']}")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(b"SEGREDO-INTERNO", r.data)

    def test_tecnico_nao_administra(self):
        c = self.cliente_http("ana@portosolucoes.com.br")
        self.assertEqual(c.get("/equipe").status_code, 403)
        self.assertEqual(c.get("/clientes/novo").status_code, 403)


class TestTelas(Base):
    def test_telas_equipe(self):
        c = self.cliente_http("contato@portosolucoes.com.br")
        num = self.sql("SELECT numero FROM chamados LIMIT 1")[0]["numero"]
        for url in ["/painel", "/chamados", "/chamados?situacao=todos", "/chamados?busca=impressora",
                    "/chamados/novo", f"/chamados/{num}", "/clientes", f"/clientes/{self.ids['alfa']}",
                    f"/clientes/{self.ids['alfa']}/contratos/novo", f"/contratos/{self.ids['ct_alfa']}",
                    "/clientes/novo", "/equipe", "/horas", "/config/sla", "/conta/senha"]:
            with self.subTest(url=url):
                r = c.get(url)
                self.assertEqual(r.status_code, 200, f"{url}: {r.data[:500]!r}")

    def test_telas_portal(self):
        c = self.cliente_http("joao@clinicabeta.com.br")
        num = self.sql("SELECT numero FROM chamados WHERE cliente_id = %s LIMIT 1", [self.ids["beta"]])[0]["numero"]
        for url in ["/portal/", "/portal/chamados", "/portal/chamados?situacao=todos", "/portal/chamados/novo",
                    f"/portal/chamados/{num}", "/portal/horas"]:
            with self.subTest(url=url):
                r = c.get(url)
                self.assertEqual(r.status_code, 200, f"{url}: {r.data[:500]!r}")
        self.assertIn("restantes".encode(), c.get("/portal/").data)

    def test_saude(self):
        r = self.app.test_client().get("/saude")
        self.assertEqual(r.json["ok"], True)


class TestFluxo(Base):
    def test_ciclo_completo(self):
        # 1) cliente abre pelo portal
        cli = self.cliente_http("joao@clinicabeta.com.br")
        r = self.post(cli, "/portal/chamados/novo",
                      {"titulo": "Wi-Fi caiu", "descricao": "Recepção sem rede", "prioridade": "alta"})
        self.assertEqual(r.status_code, 302)
        numero = int(re.search(r"/chamados/(\d+)", r.headers["Location"]).group(1))
        ch = self.sql("SELECT * FROM chamados WHERE numero = %s", [numero])[0]
        self.assertEqual(ch["contrato_id"], self.ids["ct_beta"])        # contrato único herdado
        self.assertIsNotNone(ch["prazo_solucao"])                       # SLA calculado
        self.assertEqual(ch["origem"], "portal")

        # 2) técnico responde e pede informação ao cliente
        eq = self.cliente_http("ana@portosolucoes.com.br")
        self.post(eq, f"/chamados/{numero}/responder",
                  {"texto": "Qual o nome da rede?", "status_apos": "aguardando_cliente"})
        ch = self.sql("SELECT * FROM chamados WHERE numero = %s", [numero])[0]
        self.assertEqual(ch["status"], "aguardando_cliente")
        self.assertIsNotNone(ch["primeira_resposta_em"])
        self.assertEqual(ch["responsavel_id"], self.ids["tec"])       # quem respondeu assume

        # 3) cliente responde -> volta para atendimento
        self.post(cli, f"/portal/chamados/{numero}/responder", {"texto": "Rede RECEPCAO"})
        self.assertEqual(self.sql("SELECT status FROM chamados WHERE numero = %s", [numero])[0]["status"],
                         "em_atendimento")

        # 4) técnico lança 1h30 e resolve
        import datetime as dt
        hoje = dt.datetime.now(util.TZ).date().isoformat()
        r = self.post(eq, f"/chamados/{numero}/horas",
                      {"data": hoje, "duracao": "1:30", "descricao": "Reinício do AP", "faturavel": "1"})
        self.assertEqual(r.status_code, 302)
        self.post(eq, f"/chamados/{numero}/atualizar", {"status": "resolvido", "prioridade": "alta"})

        # 5) admin aprova -> saldo do banco cai
        antes = self.sql("SELECT horas_saldo FROM vw_saldo_banco_horas WHERE contrato_id = %s",
                         [self.ids["ct_beta"]])[0]["horas_saldo"]
        ap = self.sql("SELECT id FROM apontamentos WHERE chamado_id = %s", [ch["id"]])[0]["id"]
        adm = self.cliente_http("contato@portosolucoes.com.br")
        self.post(adm, "/horas/aprovar", {"ids": str(ap), "acao": "aprovado"})
        a = self.sql("SELECT status, aprovado_por FROM apontamentos WHERE id = %s", [ap])[0]
        self.assertEqual((a["status"], a["aprovado_por"]), ("aprovado", self.ids["adm"]))
        depois = self.sql("SELECT horas_saldo FROM vw_saldo_banco_horas WHERE contrato_id = %s",
                          [self.ids["ct_beta"]])[0]["horas_saldo"]
        self.assertEqual(antes, depois)  # pendente já consumia o saldo; aprovar não muda

        # 6) cliente avalia -> fechado
        self.post(cli, f"/portal/chamados/{numero}/avaliar", {"nota": "5", "comentario": "Rápido"})
        ch = self.sql("SELECT status, avaliacao FROM chamados WHERE numero = %s", [numero])[0]
        self.assertEqual((ch["status"], ch["avaliacao"]), ("fechado", 5))

        # auditoria registrou quem aprovou
        self.assertTrue(self.sql("""SELECT 1 FROM auditoria WHERE tabela = 'apontamentos' AND registro_id = %s
                                    AND acao = 'UPDATE' AND usuario_id = %s""", [ap, self.ids["adm"]]))

    def test_cadastro_cliente_e_contrato(self):
        adm = self.cliente_http("contato@portosolucoes.com.br")
        r = self.post(adm, "/clientes/novo", {"razao_social": "Gama ME", "cnpj": "11.222.333/0001-82"})
        self.assertIn("CNPJ inválido".encode(), r.data)
        r = self.post(adm, "/clientes/novo", {"razao_social": "Gama ME", "cnpj": "", "dominio_email": "gama.com"})
        self.assertEqual(r.status_code, 302)
        cid = int(r.headers["Location"].rstrip("/").split("/")[-1])
        r = self.post(adm, f"/clientes/{cid}/contratos/novo",
                      {"tipo": "banco_horas", "descricao": "Pacote 10h", "data_inicio": "2026-10-01"})
        self.assertIn("horas do pacote".encode(), r.data)
        r = self.post(adm, f"/clientes/{cid}/contratos/novo",
                      {"tipo": "banco_horas", "descricao": "Pacote 10h", "data_inicio": "2026-10-01",
                       "horas_pacote": "10", "valor_hora": "150,00"})
        self.assertEqual(r.status_code, 302)
        ct = self.sql("SELECT * FROM contratos WHERE cliente_id = %s", [cid])[0]
        self.assertEqual(str(ct["valor_hora"]), "150.00")
        # contato do cliente recebe senha temporária e consegue entrar
        r = self.post(adm, f"/clientes/{cid}/contatos", {"nome": "Paula", "email": "paula@gama.com"},
                      follow_redirects=True)
        senha = re.search(rb"Senha tempor\xc3\xa1ria: (\S+) ", r.data).group(1).decode()
        c = self.cliente_http()
        r = self.post(c, "/entrar", {"email": "paula@gama.com", "senha": senha})
        self.assertEqual(r.headers["Location"], "/portal/")

    def test_horas_invalidas(self):
        eq = self.cliente_http("ana@portosolucoes.com.br")
        num = self.sql("SELECT numero FROM chamados LIMIT 1")[0]["numero"]
        r = self.post(eq, f"/chamados/{num}/horas", {"data": "2099-01-01", "duracao": "1h", "descricao": "x"},
                      follow_redirects=True)
        self.assertIn("futura".encode(), r.data)
        r = self.post(eq, f"/chamados/{num}/horas", {"data": "2026-10-01", "duracao": "abc", "descricao": "x"},
                      follow_redirects=True)
        self.assertIn("Informe a dura".encode(), r.data)

    def test_trocar_senha(self):
        c = self.cliente_http("joao@clinicabeta.com.br")
        r = self.post(c, "/conta/senha", {"atual": SENHA_DEMO, "nova": "curta", "confirmacao": "curta"})
        self.assertIn("10 caracteres".encode(), r.data)


if __name__ == "__main__":
    unittest.main()
