"""Acesso ao banco: uma conexão/transação por requisição.

Produção: psycopg 3. Desenvolvimento sem PyPI: app.pq (libpq via ctypes).
Uso nas rotas:
    db().all("select ... where id = %s", [id])  -> list[dict]
    db().one(...)                                -> dict | None
    db().val(...)                                -> primeiro valor | None
    db().run(...)                                -> linhas afetadas
"""
from __future__ import annotations

from flask import current_app, g

try:  # pragma: no cover - depende do ambiente
    import psycopg
    from psycopg.rows import dict_row

    IntegrityError = psycopg.IntegrityError
    DatabaseError = psycopg.Error
    _DRIVER = "psycopg"
except ImportError:  # pragma: no cover
    from . import pq

    IntegrityError = pq.IntegrityError
    DatabaseError = pq.Error
    _DRIVER = "libpq-ctypes"


class _Conn:
    def __init__(self, dsn: str):
        if _DRIVER == "psycopg":
            self._c = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
        else:
            self._c = pq.Connection(dsn)
        self.run("SET TIME ZONE 'America/Sao_Paulo'")

    def _exec(self, sql, params):
        if _DRIVER == "psycopg":
            # sempre com lista: '%%' vira '%' nos dois drivers
            cur = self._c.execute(sql, list(params or []))
            rows = cur.fetchall() if cur.description else []
            return rows, cur.rowcount
        return self._c.execute(sql, params or ())

    def all(self, sql, params=()):
        return self._exec(sql, params)[0]

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def val(self, sql, params=()):
        row = self.one(sql, params)
        return next(iter(row.values())) if row else None

    def run(self, sql, params=()):
        return self._exec(sql, params)[1]

    _uid = None

    def set_usuario(self, usuario_id):
        """Grava o usuário da sessão para auditoria/histórico (vale até o fim da transação)."""
        self._uid = usuario_id
        self.val("SELECT set_config('sisporto.usuario_id', %s, true)", [str(usuario_id or "")])

    def commit(self):
        self._c.commit()

    def rollback(self, reidentificar=True):
        self._c.rollback()
        if reidentificar and self._uid:
            self.set_usuario(self._uid)

    def close(self):
        self._c.close()


def db() -> _Conn:
    if "db" not in g:
        g.db = _Conn(current_app.config["DATABASE_URL"])
        user = g.get("usuario")
        if user:
            g.db.set_usuario(user["id"])
    return g.db


def confirmar(response):
    """after_request: grava a transação ANTES de a resposta sair."""
    conn = g.get("db")
    if conn is not None and response.status_code < 400:
        conn.commit()
    return response


def fechar(exc=None):
    """teardown: desfaz o que não foi gravado e fecha a conexão."""
    conn = g.pop("db", None)
    if conn is None:
        return
    try:
        conn.rollback(reidentificar=False)
    finally:
        conn.close()
