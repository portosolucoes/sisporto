"""Formatação, rótulos e pequenos utilitários."""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import secrets

from flask import render_template

TZ = ZoneInfo("America/Sao_Paulo")

ROTULOS = {
    "status": {
        "novo": "Novo", "em_atendimento": "Em atendimento", "aguardando_cliente": "Aguardando cliente",
        "resolvido": "Resolvido", "fechado": "Fechado",
    },
    "prioridade": {"critica": "Crítica", "alta": "Alta", "media": "Média", "baixa": "Baixa"},
    "sla": {
        "no_prazo": "No prazo", "em_risco": "Em risco", "estourado": "Estourado", "pausado": "Pausado",
        "cumprido": "Cumprido", "violado": "Violado", "sem_sla": "Sem SLA",
    },
    "tipo_contrato": {
        "mensal_fixo": "Mensal fixo", "banco_horas": "Banco de horas", "hora_avulsa": "Hora avulsa",
        "projeto": "Projeto", "proposta_avulsa": "Proposta avulsa",
    },
    "perfil": {"admin": "Administrador", "tecnico": "Técnico", "financeiro": "Financeiro", "cliente": "Usuário do cliente"},
    "origem": {"portal": "Portal", "email": "E-mail", "interno": "Interno"},
    "apontamento": {"pendente": "Pendente", "aprovado": "Aprovado", "rejeitado": "Rejeitado"},
}


def rotulo(valor, grupo):
    return ROTULOS.get(grupo, {}).get(valor, valor or "—")


def data(v):
    if not v:
        return "—"
    if isinstance(v, dt.datetime):
        v = v.astimezone(TZ).date()
    return v.strftime("%d/%m/%Y")


def data_hora(v):
    if not v:
        return "—"
    return v.astimezone(TZ).strftime("%d/%m/%Y %H:%M")


def horas(minutos):
    """90 -> '1h30'; 0/None -> '0h'."""
    m = int(minutos or 0)
    h, r = divmod(abs(m), 60)
    sinal = "-" if m < 0 else ""
    return f"{sinal}{h}h{r:02d}" if r else f"{sinal}{h}h"


def horas_dec(valor):
    """Decimal de horas (ex.: 18.50) -> '18h30'."""
    if valor is None:
        return "—"
    return horas(round(Decimal(valor) * 60))


def moeda(v):
    if v is None:
        return "—"
    s = f"{Decimal(v):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def parse_duracao(texto: str) -> int | None:
    """Aceita '1:30', '1h30', '1h', '45m', '45' (minutos) ou '1,5h'. Devolve minutos."""
    t = (texto or "").strip().lower().replace(" ", "")
    if not t:
        return None
    m = re.fullmatch(r"(\d+):(\d{1,2})", t) or re.fullmatch(r"(\d+)h(\d{1,2})?m?", t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2) or 0)
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)h", t)
    if m:
        return round(float(m.group(1).replace(",", ".")) * 60)
    m = re.fullmatch(r"(\d+)m?", t)
    if m:
        return int(m.group(1))
    return None


def parse_decimal(texto):
    t = (texto or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation:
        raise ValueError(f"Número inválido: {texto}")


def so_digitos(texto):
    return re.sub(r"\D", "", texto or "") or None


def cnpj_fmt(c):
    if not c or len(c) != 14:
        return c or "—"
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


def cnpj_valido(c: str) -> bool:
    if not c or len(c) != 14 or len(set(c)) == 1:
        return False
    def dv(base, pesos):
        s = sum(int(d) * p for d, p in zip(base, pesos)) % 11
        return "0" if s < 2 else str(11 - s)
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = dv(c[:12], p1)
    d2 = dv(c[:12] + d1, [6] + p1)
    return c[12:] == d1 + d2


def pagina_erro(titulo, mensagem):
    return render_template("erro.html", titulo=titulo, mensagem=mensagem)


def registrar_filtros(app):
    app.jinja_env.filters.update(
        data=data, data_hora=data_hora, horas=horas, horas_dec=horas_dec, moeda=moeda,
        rotulo=rotulo, cnpj=cnpj_fmt,
    )


ALFABETO_SENHA = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sem 0/O, 1/l/I


def senha_temporaria(n: int = 12) -> str:
    """Senha fácil de ler e digitar (sem caracteres que se confundem)."""
    return "".join(secrets.choice(ALFABETO_SENHA) for _ in range(n))


DOMINIOS_PUBLICOS = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.com.br", "outlook.com", "outlook.com.br", "live.com",
    "msn.com", "yahoo.com", "yahoo.com.br", "icloud.com", "me.com", "uol.com.br", "bol.com.br", "terra.com.br",
    "ig.com.br", "globo.com", "globomail.com", "zipmail.com.br", "proton.me", "protonmail.com", "aol.com",
}
RE_DOMINIO = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


def normalizar_dominio(texto):
    """'Contato@Empresa.com.br' / 'https://www.empresa.com.br/' -> 'empresa.com.br'. Vazio -> None."""
    t = (texto or "").strip().lower()
    if not t:
        return None
    if "@" in t:
        t = t.rsplit("@", 1)[1]
    t = re.sub(r"^[a-z]+://", "", t).split("/")[0].strip(". ")
    if t.startswith("www."):
        t = t[4:]
    if not RE_DOMINIO.match(t):
        raise ValueError(f"Domínio de e-mail inválido: '{texto}'. Use só o domínio, ex.: empresa.com.br")
    if t in DOMINIOS_PUBLICOS:
        raise ValueError(f"'{t}' é um provedor público de e-mail e não pode identificar um cliente. "
                         "Deixe o campo vazio e cadastre os usuários do cliente individualmente.")
    return t
