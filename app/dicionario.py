"""Dicionário de dados: lê a estrutura real do banco (tabelas, colunas, chaves, comentários)."""
from __future__ import annotations

from .db import db


def ler() -> dict:
    tabelas = db().all("""
        SELECT c.relname AS nome, CASE c.relkind WHEN 'v' THEN 'visão' ELSE 'tabela' END AS tipo,
               obj_description(c.oid, 'pg_class') AS descricao,
               CASE WHEN c.relkind = 'r' THEN c.reltuples::bigint END AS linhas_estimadas
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v')
         ORDER BY c.relkind, c.relname""")
    colunas = db().all("""
        SELECT c.relname AS tabela, a.attnum AS ordem, a.attname AS coluna,
               format_type(a.atttypid, a.atttypmod) AS tipo, NOT a.attnotnull AS nulo,
               pg_get_expr(d.adbin, d.adrelid) AS padrao, col_description(c.oid, a.attnum) AS descricao,
               a.attidentity <> '' AS identidade
          FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
         WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v') AND a.attnum > 0 AND NOT a.attisdropped
         ORDER BY c.relname, a.attnum""")
    restricoes = db().all("""
        SELECT c.relname AS tabela, k.conname AS nome, k.contype AS tipo, pg_get_constraintdef(k.oid) AS definicao,
               (SELECT string_agg(att.attname::text, ',' ORDER BY att.attnum) FROM pg_attribute att
                 WHERE att.attrelid = k.conrelid AND att.attnum = ANY(k.conkey)) AS colunas,
               fc.relname AS referencia
          FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          LEFT JOIN pg_class fc ON fc.oid = k.confrelid
         WHERE n.nspname = 'public' ORDER BY c.relname, k.contype, k.conname""")
    indices = db().all("""SELECT tablename AS tabela, indexname AS nome, indexdef AS definicao
                            FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1, 2""")
    tipos = db().all("""SELECT t.typname AS nome, string_agg(e.enumlabel, ', ' ORDER BY e.enumsortorder) AS valores
                          FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid GROUP BY 1 ORDER BY 1""")
    por_tab = {t["nome"]: {**t, "colunas": [], "pk": set(), "fk": {}, "unicas": [], "checks": [], "indices": []}
               for t in tabelas}
    for c in colunas:
        if c["tabela"] in por_tab:
            por_tab[c["tabela"]]["colunas"].append(c)
    for r in restricoes:
        t = por_tab.get(r["tabela"])
        if not t:
            continue
        cols = (r["colunas"] or "").split(",") if r["colunas"] else []
        if r["tipo"] == "p":
            t["pk"].update(cols)
        elif r["tipo"] == "f":
            for col in cols:
                t["fk"][col] = r["referencia"]
        elif r["tipo"] == "u":
            t["unicas"].append(", ".join(cols))
        elif r["tipo"] == "c":
            t["checks"].append(r["definicao"])
    for i in indices:
        if i["tabela"] in por_tab:
            por_tab[i["tabela"]]["indices"].append(i)
    return {"tabelas": list(por_tab.values()), "tipos": tipos}


def estrutura_pdf(d: dict) -> dict:
    """Converte para o formato de app.pdf.gerar."""
    tabelas = [{"titulo": "Visão geral", "larguras": [22, 8, 70],
                "colunas": [("nome", "Objeto", "texto"), ("tipo", "Tipo", "texto"), ("descricao", "Descrição", "texto")],
                "linhas": d["tabelas"]},
               {"titulo": "Tipos enumerados (valores permitidos)", "larguras": [25, 75],
                "colunas": [("nome", "Tipo", "texto"), ("valores", "Valores", "texto")], "linhas": d["tipos"]}]
    for t in d["tabelas"]:
        linhas = []
        for c in t["colunas"]:
            chave = []
            if c["coluna"] in t["pk"]:
                chave.append("PK")
            if c["coluna"] in t["fk"]:
                chave.append(f"FK → {t['fk'][c['coluna']]}")
            linhas.append({"coluna": c["coluna"], "tipo": c["tipo"], "chave": " ".join(chave),
                           "nulo": "sim" if c["nulo"] else "não",
                           "padrao": "identidade" if c["identidade"] else (c["padrao"] or ""),
                           "descricao": c["descricao"] or ""})
        extra = []
        if t["unicas"]:
            extra.append("Únicos: " + "; ".join(t["unicas"]))
        if t["checks"]:
            extra.append("Regras: " + "; ".join(t["checks"]))
        tabelas.append({"titulo": f"{t['tipo'].capitalize()} {t['nome']} — {t['descricao'] or 'sem descrição'}",
                        "obs": "  ·  ".join(extra),
                        "larguras": [16, 15, 12, 5, 14, 38],
                        "colunas": [("coluna", "Coluna", "texto"), ("tipo", "Tipo", "texto"),
                                    ("chave", "Chave", "texto"), ("nulo", "Nulo", "texto"),
                                    ("padrao", "Padrão", "texto"), ("descricao", "Descrição", "texto")],
                        "linhas": linhas})
    n_tab = sum(1 for t in d["tabelas"] if t["tipo"] == "tabela")
    return {"cards": [("Tabelas", n_tab, "num"), ("Visões", len(d["tabelas"]) - n_tab, "num"),
                      ("Colunas", sum(len(t["colunas"]) for t in d["tabelas"]), "num"),
                      ("Tipos enumerados", len(d["tipos"]), "num")],
            "tabelas": tabelas,
            "nota": "Gerado automaticamente a partir da estrutura do banco PostgreSQL do SisPorto "
                    "(comentários em db/028_comentarios.sql). PK = chave primária; FK = chave estrangeira."}
