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
import time
import threading
from contextlib import closing
from pathlib import Path

PADRAO_USUARIO = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
ITERACOES_PBKDF2 = 200_000
TAMANHO_MINIMO_SENHA = 8
MAX_TENTATIVAS = 8
JANELA_TENTATIVAS_S = 600
_TRAVA_LOGIN = threading.Lock()


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
    conexao.execute("CREATE TABLE IF NOT EXISTS tentativas_login (username TEXT PRIMARY KEY, falhas INTEGER NOT NULL, inicio REAL NOT NULL)")
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
    if not TAMANHO_MINIMO_SENHA <= len(senha) <= 1024:
        raise ErroAutenticacao(f"A senha precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres.")

    with closing(_conectar(caminho_db)) as conexao:
        existente = conexao.execute(
            "SELECT 1 FROM usuarios WHERE username = ?", (username,)
        ).fetchone()
        if existente:
            raise ErroAutenticacao("Esse nome de usuário já existe.")
        salt = secrets.token_bytes(16)
        hash_senha = _hash_senha(senha, salt)
        try:
            conexao.execute(
                "INSERT INTO usuarios (username, salt, hash) VALUES (?, ?, ?)",
                (username, salt.hex(), hash_senha),
            )
        except sqlite3.IntegrityError as exc:
            raise ErroAutenticacao("Esse nome de usuário já existe.") from exc
        conexao.commit()


def verificar_login(caminho_db: Path, username: str, senha: str) -> bool:
    username = username.strip()
    if len(username) > 32 or len(senha) > 1024:
        return False
    with _TRAVA_LOGIN, closing(_conectar(caminho_db)) as conexao:
        agora = time.time()
        tentativa = conexao.execute("SELECT falhas, inicio FROM tentativas_login WHERE username = ?", (username,)).fetchone()
        if tentativa and agora - tentativa[1] < JANELA_TENTATIVAS_S and tentativa[0] >= MAX_TENTATIVAS:
            return False
        linha = conexao.execute("SELECT salt, hash FROM usuarios WHERE username = ?", (username,)).fetchone()
        salt = bytes.fromhex(linha[0]) if linha else bytes(16)
        calculado = _hash_senha(senha, salt)
        valido = bool(linha and secrets.compare_digest(calculado, linha[1]))
        if valido:
            conexao.execute("DELETE FROM tentativas_login WHERE username = ?", (username,))
        else:
            falhas, inicio = tentativa if tentativa and agora - tentativa[1] < JANELA_TENTATIVAS_S else (0, agora)
            conexao.execute("INSERT OR REPLACE INTO tentativas_login VALUES (?, ?, ?)", (username, falhas + 1, inicio))
            conexao.execute("DELETE FROM tentativas_login WHERE inicio < ?", (agora - JANELA_TENTATIVAS_S,))
        conexao.commit()
        return valido


def usuario_existe(caminho_db: Path, username: str) -> bool:
    with closing(_conectar(caminho_db)) as conexao:
        linha = conexao.execute(
            "SELECT 1 FROM usuarios WHERE username = ?", (username.strip(),)
        ).fetchone()
    return linha is not None


def trocar_senha(caminho_db: Path, username: str, senha_atual: str, senha_nova: str) -> None:
    if not verificar_login(caminho_db, username, senha_atual):
        raise ErroAutenticacao("Senha atual incorreta.")
    if not TAMANHO_MINIMO_SENHA <= len(senha_nova) <= 1024:
        raise ErroAutenticacao(f"A nova senha precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres.")
    salt = secrets.token_bytes(16)
    hash_senha = _hash_senha(senha_nova, salt)
    with closing(_conectar(caminho_db)) as conexao:
        conexao.execute(
            "UPDATE usuarios SET salt = ?, hash = ? WHERE username = ?",
            (salt.hex(), hash_senha, username.strip()),
        )
        conexao.commit()

