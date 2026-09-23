"""
Suporte a múltiplos formatos de arquivo para verificação de integridade e
"recuperabilidade", além do fluxo específico de backups PostgreSQL com
restauração completa (src/executor.py, usado pelo verificador do TCC).

Este módulo é a base de uma ferramenta mais ampla ("Cofrya arquivos"),
separada do escopo acadêmico do TCC (que se restringe a backups lógicos de
um único banco PostgreSQL). Aqui, "recuperabilidade" é redefinida por tipo
de arquivo, já que a maioria não é um banco rodando em um servidor:

- CSV / JSON: o arquivo consegue ser reaberto e lido de ponta a ponta sem
  erro (parse bem-sucedido), e, se a pessoa informar quais colunas/chaves
  espera encontrar, essa estrutura é conferida.
- SQLite (.db/.sqlite/.sqlite3): além de abrir, roda PRAGMA integrity_check
  (verificação nativa do próprio SQLite) e confere tabelas esperadas.
- PostgreSQL (.dump/.backup, formato customizado do pg_dump): o índice do
  arquivo é lido com `pg_restore --list`, sem precisar de um servidor
  PostgreSQL rodando — não testa uma restauração completa, apenas se o
  arquivo é um arquivo pg_dump válido e legível, com as tabelas esperadas
  no índice.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Evidencia:
    teste: str
    aprovado: bool
    valor_esperado: Any
    valor_observado: Any
    detalhe: str = ""


@dataclass
class ResultadoVerificacaoArquivo:
    decisao: str  # aprovada | reprovada | inconclusiva
    evidencias: List[Evidencia] = field(default_factory=list)

    def adicionar(self, teste: str, aprovado: bool, esperado: Any, observado: Any, detalhe: str = "") -> None:
        self.evidencias.append(Evidencia(teste, aprovado, esperado, observado, detalhe))
        if not aprovado:
            self.decisao = "reprovada"


# --- Verificadores por tipo -------------------------------------------------

def verificar_csv(caminho: str, colunas_esperadas: Optional[List[str]] = None) -> ResultadoVerificacaoArquivo:
    resultado = ResultadoVerificacaoArquivo(decisao="aprovada")
    try:
        with open(caminho, "r", encoding="utf-8", newline="") as f:
            leitor = csv.reader(f, strict=True)
            cabecalho = next(leitor, None)
            if not cabecalho or not all(x.strip() for x in cabecalho):
                raise csv.Error("CSV vazio ou sem cabeçalho válido")
            n_linhas = 0
            for linha in leitor:
                if len(linha) != len(cabecalho):
                    raise csv.Error("Número de campos diferente do cabeçalho")
                n_linhas += 1
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        resultado.adicionar("abre_sem_erro", False, "arquivo legível como CSV", f"erro: {exc}")
        return resultado

    resultado.adicionar("abre_sem_erro", True, "arquivo legível como CSV", f"{n_linhas} linhas de dados")

    if colunas_esperadas:
        colunas_presentes = cabecalho or []
        faltantes = [c for c in colunas_esperadas if c not in colunas_presentes]
        resultado.adicionar(
            "colunas_esperadas", not faltantes, colunas_esperadas, colunas_presentes,
            detalhe=(f"faltando: {faltantes}" if faltantes else ""),
        )
    return resultado


def verificar_json(caminho: str, chaves_esperadas: Optional[List[str]] = None) -> ResultadoVerificacaoArquivo:
    resultado = ResultadoVerificacaoArquivo(decisao="aprovada")
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        resultado.adicionar("abre_sem_erro", False, "arquivo legível como JSON", f"erro: {exc}")
        return resultado

    resultado.adicionar("abre_sem_erro", True, "arquivo legível como JSON", type(dados).__name__)

    if chaves_esperadas:
        if isinstance(dados, dict):
            chaves_presentes = list(dados.keys())
        elif isinstance(dados, list) and dados and all(isinstance(x, dict) for x in dados):
            chaves_presentes = sorted(set.intersection(*(set(x) for x in dados)))
        else:
            chaves_presentes = []
        faltantes = [c for c in chaves_esperadas if c not in chaves_presentes]
        resultado.adicionar(
            "chaves_esperadas", not faltantes, chaves_esperadas, chaves_presentes,
            detalhe=(f"faltando: {faltantes}" if faltantes else ""),
        )
    return resultado


def verificar_sqlite(caminho: str, tabelas_esperadas: Optional[List[str]] = None) -> ResultadoVerificacaoArquivo:
    resultado = ResultadoVerificacaoArquivo(decisao="aprovada")
    conexao = None
    try:
        with open(caminho, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                raise ValueError("Cabeçalho SQLite ausente ou inválido")
        from pathlib import Path
        conexao = sqlite3.connect(Path(caminho).resolve().as_uri() + "?mode=ro", uri=True)
        status = conexao.execute("PRAGMA integrity_check").fetchall()
        ok = status == [("ok",)]
        resultado.adicionar("integrity_check", ok, "ok", status)
        if ok and tabelas_esperadas:
            tabelas_presentes = [x[0] for x in conexao.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            faltantes = [t for t in tabelas_esperadas if t not in tabelas_presentes]
            resultado.adicionar("tabelas_esperadas", not faltantes, tabelas_esperadas, tabelas_presentes)
    except (sqlite3.Error, OSError, ValueError) as exc:
        resultado.adicionar("abre_sem_erro", False, "banco SQLite legível", str(exc))
    finally:
        if conexao is not None:
            conexao.close()
    return resultado


def verificar_pg_dump(caminho: str, tabelas_esperadas: Optional[List[str]] = None) -> ResultadoVerificacaoArquivo:
    """Lê o índice do arquivo com `pg_restore --list`, sem precisar de um
    servidor PostgreSQL. Não substitui uma restauração completa (isso é o
    que o verificador específico do TCC faz, com Docker/Neon) — aqui só
    confirma que o arquivo é um dump válido e legível."""
    resultado = ResultadoVerificacaoArquivo(decisao="aprovada")
    try:
        r = subprocess.run(["pg_restore", "--list", caminho], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        resultado.adicionar("abre_sem_erro", False, "pg_restore disponível no servidor", "pg_restore não encontrado")
        resultado.decisao = "inconclusiva"
        return resultado
    except subprocess.TimeoutExpired:
        resultado.adicionar("abre_sem_erro", False, "leitura do índice em até 30s", "tempo esgotado")
        resultado.decisao = "inconclusiva"
        return resultado

    if r.returncode != 0:
        resultado.adicionar("abre_sem_erro", False, "arquivo lido por pg_restore --list", r.stderr.strip()[:500])
        return resultado

    resultado.adicionar(
        "abre_sem_erro", True, "arquivo lido por pg_restore --list",
        f"{len(r.stdout.splitlines())} entradas no índice",
    )

    if tabelas_esperadas:
        tabelas = set()
        for linha in r.stdout.splitlines():
            campos = linha.split()
            if len(campos) >= 6 and campos[3] == "TABLE" and campos[4] != "DATA":
                tabelas.add(campos[5])
        faltantes = [t for t in tabelas_esperadas if t not in tabelas]
        resultado.adicionar(
            "tabelas_no_indice", not faltantes, tabelas_esperadas,
            "presentes" if not faltantes else f"faltando: {faltantes}",
        )
    return resultado


TIPOS_SUPORTADOS = {
    ".csv": verificar_csv,
    ".json": verificar_json,
    ".db": verificar_sqlite,
    ".sqlite": verificar_sqlite,
    ".sqlite3": verificar_sqlite,
    ".dump": verificar_pg_dump,
    ".backup": verificar_pg_dump,
}


def detectar_tipo(nome_arquivo: str) -> Optional[str]:
    _, ext = os.path.splitext(nome_arquivo.lower())
    return ext if ext in TIPOS_SUPORTADOS else None


def verificar_conteudo(caminho: str, extensao: str, estrutura_esperada: Optional[List[str]] = None) -> ResultadoVerificacaoArquivo:
    funcao = TIPOS_SUPORTADOS.get(extensao)
    if funcao is None:
        resultado = ResultadoVerificacaoArquivo(decisao="reprovada")
        resultado.adicionar("tipo_suportado", False, sorted(TIPOS_SUPORTADOS.keys()), extensao)
        return resultado
    return funcao(caminho, estrutura_esperada)


# --- Fluxo completo: identidade + integridade (manifesto) + conteúdo -------

def verificar_arquivo_generico(
    caminho_arquivo: str,
    caminho_manifesto: Optional[str],
    chave_hmac: Optional[bytes],
    extensao: str,
    estrutura_esperada: Optional[List[str]] = None,
    idade_maxima_dias: int = 30,
) -> ResultadoVerificacaoArquivo:
    """Roda o fluxo completo: existência -> (se houver manifesto) autenticação
    HMAC, hash e idade -> conteúdo específico do tipo. Um manifesto é
    opcional: sem ele, roda só a checagem de conteúdo (útil pra uma
    conferência rápida sem ter gerado um manifesto antes)."""
    from . import manifest as manifesto_mod
    from datetime import datetime, timedelta, timezone

    resultado = ResultadoVerificacaoArquivo(decisao="aprovada")

    if not os.path.isfile(caminho_arquivo):
        resultado.adicionar("existe", False, "arquivo presente", "ausente")
        return resultado
    resultado.adicionar("existe", True, "arquivo presente", "presente")

    if caminho_manifesto and not chave_hmac:
        resultado.adicionar("autenticacao_manifesto", False, "chave fornecida", "chave ausente")
        resultado.decisao = "inconclusiva"
        return resultado
    if caminho_manifesto and chave_hmac:
        try:
            documento = manifesto_mod.carregar_documento_manifesto(caminho_manifesto)
            m = manifesto_mod.verificar_manifesto(documento, chave_hmac)
        except (manifesto_mod.ManifestoInvalido, manifesto_mod.AutenticacaoFalhou) as exc:
            resultado.adicionar("autenticacao_manifesto", False, "hmac válido", str(exc))
            return resultado
        resultado.adicionar("autenticacao_manifesto", True, "hmac válido", "hmac válido")

        integro = manifesto_mod.verificar_integridade_arquivo(caminho_arquivo, m)
        resultado.adicionar(
            "integridade_hash", integro, m.sha256, "confere" if integro else "não confere"
        )
        if not integro:
            return resultado

        idade = datetime.now(timezone.utc) - datetime.fromisoformat(m.instante_captura)
        dentro_da_politica = timedelta(0) <= idade <= timedelta(days=idade_maxima_dias)
        resultado.adicionar(
            "politica_idade_maxima", dentro_da_politica,
            f"<= {idade_maxima_dias} dias", f"{idade.days} dias",
        )
        if not dentro_da_politica:
            return resultado

    parcial = verificar_conteudo(caminho_arquivo, extensao, estrutura_esperada)
    resultado.evidencias.extend(parcial.evidencias)
    if parcial.decisao != "aprovada":
        resultado.decisao = parcial.decisao
    return resultado

