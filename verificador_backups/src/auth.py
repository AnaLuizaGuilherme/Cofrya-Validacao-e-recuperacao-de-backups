"""
Autenticação simples por usuário e senha (sem e-mail, sem login social),
usando SQLite local.

Trade-off deliberado, mais simples que o Cofrya 2.0 (que tem confirmação
por e-mail e login com Google): aqui a base de contas fica em um arquivo
SQLite no disco do processo que roda o Streamlit. Em serviços com disco
efêmero (como o Streamlit Community Cloud), as contas podem ser perdidas se
o contêiner for reiniciado ou reimplantado — aceitável para uso pessoal ou
demonstração; para produção com contas permanentes, trocar por um banco
externo persistente (por exemplo, o mesmo projeto Neon já usado para as
restaurações, guardando as contas num branch fixo em vez de um efêmero).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

PADRAO_USUARIO = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
ITERACOES_PBKDF2 = 200_000
TAMANHO_MINIMO_SENHA = 8


class ErroAutenticacao(Exception):
    pass


def _conectar(caminho_db: Path) -> sqlite3.Connection:
    caminho_db.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(caminho_db)
    conexao.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            username TEXT PRIMARY KEY,
            salt TEXT NOT NULL,
            hash TEXT NOT NULL,
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    return conexao


def _hash_senha(senha: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, ITERACOES_PBKDF2).hex()


def validar_username(username: str) -> str:
    username = username.strip()
    if not PADRAO_USUARIO.fullmatch(username):
        raise ErroAutenticacao(
            "Use de 3 a 32 caracteres: letras, números, ponto, hífen ou sublinhado."
        )
    return username


def criar_usuario(caminho_db: Path, username: str, senha: str) -> None:
    username = validar_username(username)
    if len(senha) < TAMANHO_MINIMO_SENHA:
        raise ErroAutenticacao(f"A senha precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres.")

    with closing(_conectar(caminho_db)) as conexao:
        existente = conexao.execute(
            "SELECT 1 FROM usuarios WHERE username = ?", (username,)
        ).fetchone()
        if existente:
            raise ErroAutenticacao("Esse nome de usuário já existe.")
        salt = secrets.token_bytes(16)
        hash_senha = _hash_senha(senha, salt)
        conexao.execute(
            "INSERT INTO usuarios (username, salt, hash) VALUES (?, ?, ?)",
            (username, salt.hex(), hash_senha),
        )
        conexao.commit()


def verificar_login(caminho_db: Path, username: str, senha: str) -> bool:
    username = username.strip()
    with closing(_conectar(caminho_db)) as conexao:
        linha = conexao.execute(
            "SELECT salt, hash FROM usuarios WHERE username = ?", (username,)
        ).fetchone()
    if not linha:
        return False
    salt_hex, hash_esperado = linha
    hash_calculado = _hash_senha(senha, bytes.fromhex(salt_hex))
    return secrets.compare_digest(hash_calculado, hash_esperado)


def usuario_existe(caminho_db: Path, username: str) -> bool:
    with closing(_conectar(caminho_db)) as conexao:
        linha = conexao.execute(
            "SELECT 1 FROM usuarios WHERE username = ?", (username.strip(),)
        ).fetchone()
    return linha is not None


def trocar_senha(caminho_db: Path, username: str, senha_atual: str, senha_nova: str) -> None:
    if not verificar_login(caminho_db, username, senha_atual):
        raise ErroAutenticacao("Senha atual incorreta.")
    if len(senha_nova) < TAMANHO_MINIMO_SENHA:
        raise ErroAutenticacao(f"A nova senha precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres.")
    salt = secrets.token_bytes(16)
    hash_senha = _hash_senha(senha_nova, salt)
    with closing(_conectar(caminho_db)) as conexao:
        conexao.execute(
            "UPDATE usuarios SET salt = ?, hash = ? WHERE username = ?",
            (salt.hex(), hash_senha, username.strip()),
        )
        conexao.commit()
