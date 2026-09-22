"""
Contas de usuário simples (usuário + senha, sem e-mail nem login social) —
versão simplificada do sistema de contas do Cofrya 2.0
(backend/app/accounts.py), adaptada para rodar dentro do processo do
Streamlit, com um banco SQLite local por instância.

Senhas nunca são guardadas em texto puro: cada conta tem um sal aleatório
de 16 bytes e o hash é calculado com scrypt (hashlib.scrypt, biblioteca
padrão do Python, sem dependência extra).

LIMITAÇÃO IMPORTANTE: o arquivo SQLite fica no disco do servidor que roda
o Streamlit. Em serviços como o Streamlit Community Cloud, esse disco não
é garantidamente permanente — reinícios do contêiner (por inatividade,
atualização da plataforma, novo deploy) podem apagar as contas
cadastradas e os dados de cada usuário. Para persistência garantida, essa
tabela precisaria morar num banco externo (o projeto Neon já configurado
para o executor, por exemplo).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

CAMINHO_PADRAO = Path(__file__).resolve().parent.parent / "dados" / "contas.db"

PADRAO_USUARIO = re.compile(r"[a-z0-9_.-]{3,32}")


class NomeUsuarioInvalido(Exception):
    pass


class UsuarioJaExiste(Exception):
    pass


class CredenciaisInvalidas(Exception):
    pass


@contextmanager
def _conexao(caminho_db: Path):
    caminho_db.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(str(caminho_db))
    try:
        conexao.execute(
            """CREATE TABLE IF NOT EXISTS usuarios (
                usuario TEXT PRIMARY KEY,
                sal BLOB NOT NULL,
                hash_senha BLOB NOT NULL,
                criado_em TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        conexao.commit()
        yield conexao
    finally:
        conexao.close()


def _normalizar_usuario(usuario: str) -> str:
    usuario = usuario.strip().lower()
    if not PADRAO_USUARIO.fullmatch(usuario):
        raise NomeUsuarioInvalido(
            "Use de 3 a 32 caracteres: letras minúsculas, números, ponto, hífen ou sublinhado."
        )
    return usuario


def _hash_senha(senha: str, sal: bytes) -> bytes:
    return hashlib.scrypt(senha.encode("utf-8"), salt=sal, n=2**14, r=8, p=1, dklen=32)


def criar_conta(usuario: str, senha: str, caminho_db: Path = CAMINHO_PADRAO) -> str:
    """Cria uma conta nova. Retorna o nome de usuário normalizado (minúsculo,
    sem espaços nas pontas). Levanta NomeUsuarioInvalido, CredenciaisInvalidas
    (senha curta) ou UsuarioJaExiste."""
    usuario = _normalizar_usuario(usuario)
    if len(senha) < 8:
        raise CredenciaisInvalidas("A senha precisa ter pelo menos 8 caracteres.")

    sal = secrets.token_bytes(16)
    hash_senha = _hash_senha(senha, sal)

    with _conexao(caminho_db) as conexao:
        try:
            conexao.execute(
                "INSERT INTO usuarios (usuario, sal, hash_senha) VALUES (?, ?, ?)",
                (usuario, sal, hash_senha),
            )
            conexao.commit()
        except sqlite3.IntegrityError:
            raise UsuarioJaExiste(f"O usuário '{usuario}' já existe.")
    return usuario


def autenticar(usuario: str, senha: str, caminho_db: Path = CAMINHO_PADRAO) -> str:
    """Retorna o nome de usuário normalizado se as credenciais forem válidas.
    Levanta CredenciaisInvalidas em qualquer outro caso — a mensagem não
    diferencia 'usuário não existe' de 'senha errada', para não vazar quais
    usuários estão cadastrados."""
    usuario_normalizado = _normalizar_usuario(usuario)
    with _conexao(caminho_db) as conexao:
        linha = conexao.execute(
            "SELECT sal, hash_senha FROM usuarios WHERE usuario = ?", (usuario_normalizado,)
        ).fetchone()

    if not linha:
        # Ainda calcula um hash com um sal aleatório para não vazar, pelo
        # tempo de resposta, se o usuário existe ou não.
        _hash_senha(senha, secrets.token_bytes(16))
        raise CredenciaisInvalidas("Usuário ou senha inválidos.")

    sal, hash_esperado = linha
    hash_calculado = _hash_senha(senha, sal)
    if not secrets.compare_digest(hash_calculado, hash_esperado):
        raise CredenciaisInvalidas("Usuário ou senha inválidos.")

    return usuario_normalizado


def pasta_dados_usuario(usuario: str, raiz_dados: Path) -> Path:
    """Pasta isolada de um usuário para guardar backups/resultados/uploads.
    O nome de usuário já é validado no cadastro/login (regex restrita a
    minúsculas/números/./-/_), então é seguro usá-lo direto como nome de
    pasta sem risco de path traversal."""
    return raiz_dados / usuario
