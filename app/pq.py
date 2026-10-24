"""
Driver PostgreSQL mínimo sobre a libpq (ctypes).

Usado apenas quando o pacote `psycopg` não está instalado (ex.: ambiente de
desenvolvimento sem acesso ao PyPI). Em produção o Dockerfile instala o psycopg
e este módulo não é carregado.

Interface compatível com o subconjunto que o SisPorto usa: placeholders %s,
`%%` para '%' literal, linhas como dict, erros com `.sqlstate`.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import datetime as dt
import json
from decimal import Decimal

_lib = ctypes.CDLL(ctypes.util.find_library("pq") or "libpq.so.5")

_lib.PQconnectdb.restype = ctypes.c_void_p
_lib.PQconnectdb.argtypes = [ctypes.c_char_p]
_lib.PQstatus.argtypes = [ctypes.c_void_p]
_lib.PQerrorMessage.restype = ctypes.c_char_p
_lib.PQerrorMessage.argtypes = [ctypes.c_void_p]
_lib.PQfinish.argtypes = [ctypes.c_void_p]
_lib.PQexecParams.restype = ctypes.c_void_p
_lib.PQexecParams.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p,
                              ctypes.POINTER(ctypes.c_char_p), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
_lib.PQresultStatus.argtypes = [ctypes.c_void_p]
_lib.PQresultErrorMessage.restype = ctypes.c_char_p
_lib.PQresultErrorMessage.argtypes = [ctypes.c_void_p]
_lib.PQresultErrorField.restype = ctypes.c_char_p
_lib.PQresultErrorField.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQntuples.argtypes = [ctypes.c_void_p]
_lib.PQnfields.argtypes = [ctypes.c_void_p]
_lib.PQfname.restype = ctypes.c_char_p
_lib.PQfname.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQftype.restype = ctypes.c_uint
_lib.PQftype.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQgetvalue.restype = ctypes.c_char_p
_lib.PQgetvalue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_lib.PQgetisnull.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_lib.PQcmdTuples.restype = ctypes.c_char_p
_lib.PQcmdTuples.argtypes = [ctypes.c_void_p]
_lib.PQclear.argtypes = [ctypes.c_void_p]

_PGRES_COMMAND_OK, _PGRES_TUPLES_OK = 1, 2
_PG_DIAG_SQLSTATE = ord("C")


class Error(Exception):
    def __init__(self, msg: str, sqlstate: str | None = None):
        super().__init__(msg)
        self.sqlstate = sqlstate


class IntegrityError(Error):
    pass


def _ts(v: str) -> dt.datetime:
    # '2026-10-23 17:00:00-03' / '...+00' / com fração
    if len(v) >= 3 and v[-3] in "+-" and v[-2:].isdigit():
        v = v + ":00"
    return dt.datetime.fromisoformat(v)


_PARSERS = {
    16: lambda v: v == "t",
    20: int, 21: int, 23: int, 26: int,
    700: float, 701: float,
    1700: Decimal,
    1082: dt.date.fromisoformat,
    1083: dt.time.fromisoformat,
    1114: dt.datetime.fromisoformat,
    1184: _ts,
    114: json.loads, 3802: json.loads,
    17: lambda v: bytes.fromhex(v[2:]),  # bytea em formato hex (\\x...)
}


def _to_text(v) -> bytes | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return b"t" if v else b"f"
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat().encode()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return b"\\x" + bytes(v).hex().encode()
    if isinstance(v, (dict, list)):
        return json.dumps(v).encode()
    return str(v).encode()


def _convert_placeholders(sql: str) -> str:
    out, n, i = [], 0, 0
    while i < len(sql):
        c = sql[i]
        if c == "%" and i + 1 < len(sql):
            nxt = sql[i + 1]
            if nxt == "s":
                n += 1
                out.append(f"${n}")
                i += 2
                continue
            if nxt == "%":
                out.append("%")
                i += 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


class Connection:
    def __init__(self, dsn: str):
        self._c = _lib.PQconnectdb(dsn.encode())
        if _lib.PQstatus(self._c) != 0:
            msg = _lib.PQerrorMessage(self._c).decode(errors="replace")
            _lib.PQfinish(self._c)
            self._c = None
            raise Error(msg)
        self._in_tx = False

    def _run(self, sql: str, params=()):
        vals = [_to_text(p) for p in params]
        arr = (ctypes.c_char_p * len(vals))(*vals) if vals else None
        res = _lib.PQexecParams(self._c, sql.encode(), len(vals), None, arr, None, None, 0)
        if not res:
            raise Error(_lib.PQerrorMessage(self._c).decode(errors="replace"))
        st = _lib.PQresultStatus(res)
        if st not in (_PGRES_COMMAND_OK, _PGRES_TUPLES_OK):
            msg = _lib.PQresultErrorMessage(res).decode(errors="replace").strip()
            code = _lib.PQresultErrorField(res, _PG_DIAG_SQLSTATE)
            code = code.decode() if code else None
            _lib.PQclear(res)
            raise (IntegrityError if code and code.startswith("23") else Error)(msg, code)
        return res

    def execute(self, sql: str, params=()):
        """Executa e devolve (linhas: list[dict], linhas_afetadas: int)."""
        if not self._in_tx:
            _lib.PQclear(self._run("BEGIN"))
            self._in_tx = True
        res = self._run(_convert_placeholders(sql), params)
        try:
            nf, nt = _lib.PQnfields(res), _lib.PQntuples(res)
            cols = [(_lib.PQfname(res, j).decode(), _PARSERS.get(_lib.PQftype(res, j))) for j in range(nf)]
            rows = []
            for i in range(nt):
                row = {}
                for j, (name, parse) in enumerate(cols):
                    if _lib.PQgetisnull(res, i, j):
                        row[name] = None
                    else:
                        v = _lib.PQgetvalue(res, i, j).decode()
                        row[name] = parse(v) if parse else v
                rows.append(row)
            affected = _lib.PQcmdTuples(res).decode()
            return rows, int(affected) if affected.isdigit() else 0
        finally:
            _lib.PQclear(res)

    def commit(self):
        if self._in_tx:
            _lib.PQclear(self._run("COMMIT"))
            self._in_tx = False

    def rollback(self):
        if self._in_tx:
            try:
                _lib.PQclear(self._run("ROLLBACK"))
            finally:
                self._in_tx = False

    def close(self):
        if self._c:
            _lib.PQfinish(self._c)
            self._c = None
