"""Cofrya: execute python -m streamlit run cofrya_app.py.

App unificado com login simples (usuário/senha, sem e-mail/Google) que
reúne as duas ferramentas do projeto:

  - Backups PostgreSQL: streamlit_app.renderizar_pagina — restauração
    completa via Docker ou Neon, cenários C0-C8, configurações A/B/C.
  - Arquivos genéricos: arquivos_app.pagina_proteger/pagina_verificar —
    CSV, JSON, SQLite e o índice de backups .dump, sem precisar de
    restauração completa.

Cada conta tem sua própria pasta de dados (dados/<usuario>/...), então o
que uma pessoa envia não aparece para outra.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from src import auth
import arquivos_app
import streamlit_app

RAIZ = Path(__file__).resolve().parent
CAMINHO_CONTAS = RAIZ / "dados" / "contas.db"


def pasta_do_usuario(username: str) -> Path:
    return RAIZ / "dados" / "usuarios" / username


def pagina_login_ou_registro() -> None:
    st.title("Cofrya")
    st.caption("Verificação de integridade e recuperabilidade de backups e arquivos de dados.")

    aba_entrar, aba_criar = st.tabs(["Entrar", "Criar conta"])

    with aba_entrar:
        with st.form("form_entrar"):
            username = st.text_input("Usuário", key="login_usuario")
            senha = st.text_input("Senha", type="password", key="login_senha")
            entrar = st.form_submit_button("Entrar", type="primary")
        if entrar:
            if auth.verificar_login(CAMINHO_CONTAS, username, senha):
                st.session_state["usuario_logado"] = username.strip()
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos.")

    with aba_criar:
        with st.form("form_criar_conta"):
            novo_usuario = st.text_input(
                "Escolha um usuário", key="registro_usuario",
                help="3 a 32 caracteres: letras, números, ponto, hífen ou sublinhado.",
            )
            nova_senha = st.text_input(
                "Escolha uma senha", type="password", key="registro_senha",
                help="Pelo menos 8 caracteres.",
            )
            confirmar_senha = st.text_input("Confirme a senha", type="password", key="registro_senha_confirmar")
            criar = st.form_submit_button("Criar conta", type="primary")
        if criar:
            if nova_senha != confirmar_senha:
                st.error("As senhas não coincidem.")
            else:
                try:
                    auth.criar_usuario(CAMINHO_CONTAS, novo_usuario, nova_senha)
                except auth.ErroAutenticacao as exc:
                    st.error(str(exc))
                else:
                    st.success("Conta criada! Vá para a aba \"Entrar\" para acessar.")


def pagina_minha_conta(username: str) -> None:
    st.header("Minha conta")
    st.write(f"Usuário: **{username}**")
    with st.form("form_trocar_senha"):
        senha_atual = st.text_input("Senha atual", type="password", key="conta_senha_atual")
        senha_nova = st.text_input("Nova senha", type="password", key="conta_senha_nova")
        confirmar = st.text_input("Confirme a nova senha", type="password", key="conta_senha_confirmar")
        trocar = st.form_submit_button("Trocar senha", type="primary")
    if trocar:
        if senha_nova != confirmar:
            st.error("As senhas não coincidem.")
        else:
            try:
                auth.trocar_senha(CAMINHO_CONTAS, username, senha_atual, senha_nova)
            except auth.ErroAutenticacao as exc:
                st.error(str(exc))
            else:
                st.success("Senha alterada.")


def main() -> None:
    st.set_page_config(page_title="Cofrya", page_icon="🛡️", layout="wide")

    if "usuario_logado" not in st.session_state:
        pagina_login_ou_registro()
        return

    username = st.session_state["usuario_logado"]
    pasta_usuario = pasta_do_usuario(username)

    with st.sidebar:
        st.write(f"Conectado como **{username}**")
        if st.button("Sair"):
            del st.session_state["usuario_logado"]
            st.rerun()

    st.title("Cofrya")
    st.caption("Um backup precisa fazer mais do que existir.")

    aba_pg, aba_arquivos, aba_conta = st.tabs(
        ["Backups PostgreSQL", "Arquivos (CSV / JSON / SQLite)", "Minha conta"]
    )

    with aba_pg:
        st.write(
            "Restauração completa em ambiente isolado (Docker local ou Neon na nuvem), "
            "com os cenários e configurações do protocolo original."
        )
        streamlit_app.renderizar_pagina(
            repositorio=pasta_usuario / "repositorio",
            saida=pasta_usuario / "results",
        )

    with aba_arquivos:
        st.write(
            "Verificação de integridade e leitura para CSV, JSON, SQLite e o índice de "
            "backups PostgreSQL (.dump) — sem restauração completa."
        )
        sub_proteger, sub_verificar = st.tabs(["Proteger", "Verificar"])
        with sub_proteger:
            arquivos_app.pagina_proteger(pasta_usuario / "arquivos")
        with sub_verificar:
            arquivos_app.pagina_verificar(pasta_usuario / "arquivos")

    with aba_conta:
        pagina_minha_conta(username)


if __name__ == "__main__":
    main()
