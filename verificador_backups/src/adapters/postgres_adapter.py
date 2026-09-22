"""
Adaptador PostgreSQL (seção 5.4 do TCC).

Encapsula pg_dump (para gerar cópias no gerador/laboratório) e pg_restore
(para a restauração isolada no ambiente temporário). Todos os comandos são
chamados como lista de argumentos ao processo — nunca concatenação de string
de shell a partir de entrada não confiável.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Optional

from .docker_adapter import InstanciaTemporaria


def _ambiente_conexao(instancia: InstanciaTemporaria) -> dict:
    """Monta o ambiente do subprocesso, incluindo PGSSLMODE quando a
    instância vem do adaptador Neon (que exige TLS), sem afetar o Docker
    local (que normalmente não tem TLS configurado)."""
    ambiente = os.environ.copy()
    ambiente["PGPASSWORD"] = instancia.senha
    if getattr(instancia, "imagem", "") == "neon":
        ambiente["PGSSLMODE"] = "require"
    return ambiente


@dataclass
class ResultadoRestauracao:
    codigo_saida: int
    stdout: str
    stderr: str
    duracao_s: float


def criar_backup_customizado(
    dsn_origem: str,
    caminho_saida: str,
    timeout_s: int = 300,
) -> None:
    """Gera um backup lógico em formato customizado via pg_dump."""
    cmd = [
        "pg_dump",
        "--format=custom",
        "--file", caminho_saida,
        dsn_origem,
    ]
    resultado = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if resultado.returncode != 0:
        raise RuntimeError(f"pg_dump falhou: {resultado.stderr.strip()}")


def restaurar(
    caminho_backup: str,
    instancia: InstanciaTemporaria,
    timeout_s: int = 600,
) -> ResultadoRestauracao:
    """Executa pg_restore --exit-on-error contra a instância temporária.

    A opção --exit-on-error interrompe o processo ao primeiro erro de comando
    SQL, evitando que uma restauração parcial seja interpretada como sucesso
    (documentação do pg_restore, citada na seção 5.4 do TCC).
    """
    import time

    ambiente = _ambiente_conexao(instancia)

    cmd = [
        "pg_restore",
        "--exit-on-error",
        "--no-owner",
        "--host", instancia.host,
        "--port", str(instancia.porta),
        "--username", instancia.usuario,
        "--dbname", instancia.banco,
        caminho_backup,
    ]

    inicio = time.monotonic()
    resultado = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout_s, env=ambiente
    )
    duracao = time.monotonic() - inicio

    return ResultadoRestauracao(
        codigo_saida=resultado.returncode,
        stdout=resultado.stdout,
        stderr=resultado.stderr,
        duracao_s=duracao,
    )


def preparar_dependencias(
    instancia: InstanciaTemporaria,
    papeis_necessarios: Optional[list] = None,
    extensoes_necessarias: Optional[list] = None,
    timeout_s: int = 60,
) -> ResultadoRestauracao:
    """Cria papéis/extensões declarados como dependências antes da restauração.

    Usado para os cenários em que uma dependência é deliberadamente omitida
    (C5): esta função simplesmente não é chamada (ou é chamada com uma lista
    incompleta) para reproduzir a falha.
    """
    import time

    papeis_necessarios = papeis_necessarios or []
    extensoes_necessarias = extensoes_necessarias or []

    comandos_sql = []
    for papel in papeis_necessarios:
        comandos_sql.append(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{papel}') "
            f"THEN CREATE ROLE {papel}; END IF; END $$;"
        )
    for extensao in extensoes_necessarias:
        comandos_sql.append(f'CREATE EXTENSION IF NOT EXISTS "{extensao}";')

    ambiente = _ambiente_conexao(instancia)

    inicio = time.monotonic()
    stdout_total, stderr_total = "", ""
    codigo_final = 0
    for comando in comandos_sql:
        cmd = [
            "psql",
            "--host", instancia.host,
            "--port", str(instancia.porta),
            "--username", instancia.usuario,
            "--dbname", instancia.banco,
            "-v", "ON_ERROR_STOP=1",
            "-c", comando,
        ]
        resultado = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, env=ambiente)
        stdout_total += resultado.stdout
        stderr_total += resultado.stderr
        if resultado.returncode != 0:
            codigo_final = resultado.returncode
            break
    duracao = time.monotonic() - inicio

    return ResultadoRestauracao(
        codigo_saida=codigo_final, stdout=stdout_total, stderr=stderr_total, duracao_s=duracao
    )


def executar_consulta(instancia: InstanciaTemporaria, consulta: str, timeout_s: int = 30):
    """Executa uma consulta somente-leitura no banco restaurado e retorna linhas.

    Usado pelos validadores (estrutura/conteúdo/negócio). Implementado via
    psycopg2 para permitir parsing estruturado do resultado.
    """
    import psycopg2

    parametros_conexao = dict(
        host=instancia.host,
        port=instancia.porta,
        user=instancia.usuario,
        password=instancia.senha,
        dbname=instancia.banco,
        connect_timeout=timeout_s,
    )
    if getattr(instancia, "imagem", "") == "neon":
        parametros_conexao["sslmode"] = "require"

    conexao = psycopg2.connect(**parametros_conexao)
    try:
        with conexao.cursor() as cursor:
            cursor.execute(consulta)
            colunas = [desc[0] for desc in cursor.description] if cursor.description else []
            linhas = cursor.fetchall() if cursor.description else []
        return colunas, linhas
    finally:
        conexao.close()
