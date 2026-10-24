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


class TestEmail(Base):
    TOKEN = "tok-teste"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app.config["INTEGRACAO_TOKEN"] = cls.TOKEN

    def chamar(self, rota, dados=None, token=None):
        return self.app.test_client().post(f"/integracao/email/{rota}", json=dados or {},
                                           headers={"X-Token": token or self.TOKEN})

    def test_token_obrigatorio(self):
        self.assertEqual(self.chamar("saida", token="errado").status_code, 403)

    def test_limpar_resposta(self):
        from app.email_integracao import limpar_resposta
        t = "Ok, funcionou!\n\nEm qui., 23 de out. de 2026 às 10:00, Suporte <suporte@x.com> escreveu:\n> antigo"
        self.assertEqual(limpar_resposta(t), "Ok, funcionou!")

    def test_fluxo_email(self):
        # cliente conhecido pelo domínio abre chamado por e-mail
        r = self.chamar("entrada", {"message_id": "m1", "thread_id": "t1", "de": "Carla@AlfaLog.com.br",
                                    "assunto": "Sem acesso à VPN", "texto": "Não conecto desde ontem."})
        self.assertEqual(r.json["acao"], "novo")
        numero = r.json["numero"]
        ch = self.sql("SELECT * FROM chamados WHERE numero = %s", [numero])[0]
        self.assertEqual((ch["origem"], ch["solicitante_email"], ch["cliente_id"]),
                         ("email", "carla@alfalog.com.br", self.ids["alfa"]))
        # duplicado é ignorado
        self.assertEqual(self.chamar("entrada", {"message_id": "m1", "de": "carla@alfalog.com.br"}).json["acao"],
                         "duplicado")
        # confirmação foi enfileirada e sai pela fila
        fila = self.chamar("saida").json["emails"]
        conf = [e for e in fila if e["para"] == "carla@alfalog.com.br"]
        self.assertTrue(conf and f"[#{numero}]" in conf[0]["assunto"])
        self.assertEqual(self.chamar("enviados", {"ids": [e["id"] for e in fila]}).json["confirmados"], len(fila))
        self.assertEqual(self.chamar("saida").json["emails"], [])
        # técnico responde no sistema -> notificação por e-mail
        eq = self.cliente_http("ana@portosolucoes.com.br")
        self.post(eq, f"/chamados/{numero}/responder", {"texto": "Reinicie o cliente VPN, por favor.",
                                                        "status_apos": "aguardando_cliente"})
        fila = self.chamar("saida").json["emails"]
        self.assertEqual(len(fila), 1)
        self.assertIn("Reinicie o cliente VPN", fila[0]["corpo"])
        self.chamar("enviados", {"ids": [fila[0]["id"]]})
        # cliente responde por e-mail (assunto com [#n]) -> vira mensagem e retoma atendimento
        r = self.chamar("entrada", {"message_id": "m2", "thread_id": "t1", "de": "carla@alfalog.com.br",
                                    "assunto": f"Re: [#{numero}] Sem acesso à VPN",
                                    "texto": "Funcionou!\n\nOn Thu, Oct 23, 2026 Suporte wrote:\n> Reinicie"})
        self.assertEqual(r.json["acao"], "resposta")
        msgs = self.sql("SELECT texto FROM chamado_interacoes c JOIN chamados x ON x.id = c.chamado_id "
                        "WHERE x.numero = %s ORDER BY c.id", [numero])
        self.assertEqual(msgs[-1]["texto"], "Funcionou!")
        self.assertEqual(self.sql("SELECT status FROM chamados WHERE numero = %s", [numero])[0]["status"],
                         "em_atendimento")
        # nota interna não gera e-mail
        self.post(eq, f"/chamados/{numero}/responder", {"texto": "nota", "interna": "1"})
        self.assertEqual(self.chamar("saida").json["emails"], [])

    def test_remetente_desconhecido(self):
        r = self.chamar("entrada", {"message_id": "x9", "de": "spam@qualquer.com", "assunto": "Oferta",
                                    "texto": "compre"})
        self.assertEqual(r.json["acao"], "recusado")
        adm = self.cliente_http("contato@portosolucoes.com.br")
        self.assertIn(b"spam@qualquer.com", adm.get("/emails-recusados").data)

    def test_intruso_nao_responde_chamado_alheio(self):
        num = self.sql("SELECT numero FROM chamados WHERE cliente_id = %s LIMIT 1", [self.ids["beta"]])[0]["numero"]
        r = self.chamar("entrada", {"message_id": "x10", "de": "maria@alfalog.com.br",
                                    "assunto": f"Re: [#{num}]", "texto": "oi"})
        self.assertEqual(r.json["acao"], "recusado")


class TestImportacao(Base):
    def planilha(self, clientes=(), contratos=(), contatos=()):
        import io
        from openpyxl import load_workbook
        from app.importacao import gerar_modelo
        wb = load_workbook(io.BytesIO(gerar_modelo()))
        for aba, linhas in (("Clientes", clientes), ("Contratos", contratos), ("Contatos", contatos)):
            ws = wb[aba]
            for col in range(1, ws.max_column + 1):
                ws.cell(row=2, column=col).value = None
            for i, l in enumerate(linhas, start=2):
                for j, v in enumerate(l, start=1):
                    ws.cell(row=i, column=j, value=v)
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        return buf

    def enviar(self, arquivo):
        adm = self.cliente_http("contato@portosolucoes.com.br")
        return self.post(adm, "/importar", {"planilha": (arquivo, "clientes.xlsx")},
                         content_type="multipart/form-data")

    def test_modelo(self):
        adm = self.cliente_http("contato@portosolucoes.com.br")
        r = adm.get("/importar/modelo.xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data[:2] == b"PK")
        self.assertEqual(adm.get("/importar").status_code, 200)

    def test_importacao_ok(self):
        r = self.enviar(self.planilha(
            clientes=[("Delta Engenharia Ltda", "Delta", "33.445.566/0001-86", None, None, "delta.eng.br", None)],
            contratos=[("Delta", "Banco de horas", "Pacote 20h", None, None, "150,00", 20, None, "01/10/2026"),
                       ("Alfa Logística", "Hora avulsa", "Avulso", None, None, 200, None, None, "2026-10-01")],
            contatos=[("33445566000186", "Pedro", "pedro@delta.eng.br", None)]))
        self.assertIn("Importação concluída".encode(), r.data, r.data[-1500:])
        cid = self.sql("SELECT id FROM clientes WHERE cnpj = '33445566000186'")[0]["id"]
        self.assertEqual(self.sql("SELECT count(*) AS n FROM contratos WHERE cliente_id = %s", [cid])[0]["n"], 1)
        self.assertEqual(self.sql("SELECT horas_pacote FROM contratos WHERE cliente_id = %s", [cid])[0]["horas_pacote"], 20)
        self.assertTrue(self.sql("SELECT 1 FROM contratos WHERE cliente_id = %s AND tipo = 'hora_avulsa'",
                                 [self.ids["alfa"]]))
        self.assertTrue(self.sql("SELECT 1 FROM usuarios WHERE email = 'pedro@delta.eng.br' AND cliente_id = %s", [cid]))

    def test_importacao_com_erro_nao_grava(self):
        r = self.enviar(self.planilha(
            clientes=[("Ômega SA", "Ômega", None, None, None, None, None)],
            contratos=[("Ômega", "Mensal fixo", "Suporte", None, None, None, None, None, "01/10/2026")],
            contatos=[("Inexistente", "Zé", "ze@x.com", None)]))
        self.assertIn("Nada foi gravado".encode(), r.data)
        self.assertIn("preencha Valor mensal".encode(), r.data)
        self.assertIn("Inexistente".encode(), r.data)
        self.assertFalse(self.sql("SELECT 1 FROM clientes WHERE razao_social = 'Ômega SA'"))


class TestMigracoes(Base):
    def test_atualizar_idempotente(self):
        with self.app.app_context():
            from app.cli import atualizar_banco
            self.assertEqual(atualizar_banco(), [])
        self.assertTrue(self.sql("SELECT 1 FROM schema_migracoes WHERE arquivo = '020_email.sql'"))


class TestPoliticasSLA(Base):
    def test_politica_por_contrato(self):
        adm = self.cliente_http("contato@portosolucoes.com.br")
        prazos = {"critica_resposta": "30m", "critica_solucao": "2h", "alta_resposta": "1h", "alta_solucao": "4h",
                  "media_resposta": "2h", "media_solucao": "10h", "baixa_resposta": "4h", "baixa_solucao": "20h"}
        r = self.post(adm, "/config/sla", {"acao": "criar", "nome": "Premium", **prazos}, follow_redirects=True)
        self.assertIn("Premium".encode(), r.data)
        pid = self.sql("SELECT id FROM sla_politicas WHERE nome = 'Premium'")[0]["id"]
        self.assertEqual(self.sql("SELECT minutos_solucao FROM sla_regras WHERE politica_id = %s AND prioridade = 'critica'",
                                  [pid])[0]["minutos_solucao"], 120)
        # erro: solução menor que resposta
        r = self.post(adm, "/config/sla", {"acao": "criar", "nome": "Ruim", **{**prazos, "alta_solucao": "30m"}},
                      follow_redirects=True)
        self.assertIn("não pode ser menor".encode(), r.data)
        # contrato da Beta passa a usar Premium
        ct = self.sql("SELECT * FROM contratos WHERE id = %s", [self.ids["ct_beta"]])[0]
        dados = {k: ("" if ct[k] is None else str(ct[k])) for k in
                 ["tipo", "descricao", "valor_mensal", "franquia_horas", "valor_hora", "horas_pacote", "valor_total",
                  "data_inicio", "data_fim", "dia_vencimento", "indice_reajuste", "mes_reajuste"]}
        self.post(adm, f"/contratos/{self.ids['ct_beta']}", {**dados, "ativo": "1", "sla_politica_id": str(pid)})
        self.assertEqual(self.sql("SELECT sla_politica_id FROM contratos WHERE id = %s", [self.ids["ct_beta"]])[0]
                         ["sla_politica_id"], pid)
        # política em uso não pode ser excluída
        r = self.post(adm, "/config/sla", {"acao": "excluir", "politica_id": str(pid)}, follow_redirects=True)
        self.assertIn("em uso".encode(), r.data)
        # chamado crítico da Beta usa 2h úteis (Premium) e não 4h (padrão)
        cli = self.cliente_http("joao@clinicabeta.com.br")
        r = self.post(cli, "/portal/chamados/novo", {"titulo": "Servidor fora", "descricao": "x", "prioridade": "critica"})
        num = int(r.headers["Location"].rstrip("/").split("/")[-1])
        c = self.sql("""SELECT fn_minutos_uteis_entre(criado_em, prazo_solucao) AS m,
                               fn_somar_minutos_uteis(criado_em, 120) = prazo_solucao AS ok
                          FROM chamados WHERE numero = %s""", [num])[0]
        self.assertTrue(c["ok"])


class TestLogo(Base):
    def test_logo_padrao_e_troca(self):
        import io
        c = self.app.test_client()
        r = c.get("/marca/logo")
        self.assertEqual((r.status_code, r.mimetype), (200, "image/png"))
        padrao = r.data
        adm = self.cliente_http("contato@portosolucoes.com.br")
        self.assertEqual(adm.get("/config/marca").status_code, 200)
        r = self.post(adm, "/config/marca", {"logo": (io.BytesIO(b"<svg>x</svg>"), "x.svg")},
                      content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("Formato não aceito".encode(), r.data)
        novo = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
        self.post(adm, "/config/marca", {"logo": (io.BytesIO(novo), "logo.png")}, content_type="multipart/form-data")
        self.assertEqual(c.get("/marca/logo").data, novo)
        self.post(adm, "/config/marca", {"acao": "restaurar"})
        self.assertEqual(c.get("/marca/logo").data, padrao)
        # técnico não troca logo
        tec = self.cliente_http("ana@portosolucoes.com.br")
        self.assertEqual(tec.get("/config/marca").status_code, 403)


class TestCriarAdmin(Base):
    def test_criar_admin(self):
        r = self.app.test_cli_runner().invoke(args=["criar-admin", "Novo.Admin@portosolucoes.com.br", "--nome", "Novo"])
        self.assertEqual(r.exit_code, 0, r.output)
        senha = r.output.strip().split(": ")[-1]
        self.assertEqual(len(senha), 12)
        c = self.cliente_http()
        r = self.post(c, "/entrar", {"email": "novo.admin@portosolucoes.com.br", "senha": f" {senha} "})
        self.assertEqual(r.headers["Location"], "/painel")
        # reaplicar em usuário existente (ex.: cliente) vira admin ativo
        r = self.app.test_cli_runner().invoke(args=["criar-admin", "maria@alfalog.com.br"])
        u = self.sql("SELECT perfil, cliente_id, ativo FROM usuarios WHERE email = 'maria@alfalog.com.br'")[0]
        self.assertEqual((u["perfil"], u["cliente_id"], u["ativo"]), ("admin", None, True))
