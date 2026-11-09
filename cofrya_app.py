"""Cofrya: execute python -m streamlit run cofrya_app.py.

App unificado com login simples (usuário/senha, sem e-mail/Google) que
reúne as duas ferramentas do projeto:

  - Backups PostgreSQL: streamlit_app.renderizar_pagina — restauração
    completa via Docker ou Neon, cenários C0-C7, configurações A/B/C.
  - Arquivos genéricos: arquivos_app.pagina_proteger/pagina_verificar —
    CSV, JSON, SQLite e o índice de backups .dump, sem precisar de
    restauração completa.

Cada conta tem sua própria pasta de dados (dados/<usuario>/...), então o
que uma pessoa envia não aparece para outra.
"""
from __future__ import annotations

from pathlib import Path
from html import escape
import os

import streamlit as st

from src import auth, ui
import arquivos_app
import postgres_app as streamlit_app

RAIZ = Path(__file__).resolve().parent
DADOS = Path(os.environ.get("COFRYA_DATA_DIR", str(RAIZ / "dados"))).resolve()
CAMINHO_CONTAS = DADOS / "contas.db"


def pasta_do_usuario(username: str) -> Path:
    return DADOS / "usuarios" / auth.validar_username(username)


def pagina_login_ou_registro() -> None:
    ui.marca()
    esquerda, direita = st.columns([1.12, 1], gap="large")
    with esquerda:
        st.markdown("""
        <section class="cf-login-hero">
          <div class="cf-eyebrow">INTEGRIDADE. RESTAURAÇÃO. EVIDÊNCIAS.</div>
          <h1>Seu backup existe.<br><span>Mas ele funciona?</span></h1>
          <p>Confira os arquivos, teste a recuperação e entenda o que os dados revelam.</p>
          <div class="cf-login-point"><b>01</b><div><strong>Verifique com clareza</strong><p>Escolha o nível de análise adequado ao seu backup.</p></div></div>
          <div class="cf-login-point"><b>02</b><div><strong>Teste a recuperação</strong><p>Restaure em um ambiente temporário, separado da origem.</p></div></div>
          <div class="cf-login-point"><b>03</b><div><strong>Decida com evidências</strong><p>Consulte o motivo de cada resultado e exporte os registros.</p></div></div>
        </section>
        """, unsafe_allow_html=True)
    with direita, st.container(key="login-card"):
        st.subheader("Seu espaço de verificação")
        st.caption("Entre para acessar seus arquivos e resultados.")
        aba_entrar, aba_criar = st.tabs(["Entrar", "Criar conta"])

        with aba_entrar:
            with st.form("form_entrar"):
                username = st.text_input("Usuário", key="login_usuario")
                senha = st.text_input("Senha", type="password", key="login_senha")
                entrar = st.form_submit_button("Entrar", type="primary")
            if entrar:
                if auth.verificar_login(CAMINHO_CONTAS, username, senha):
                    usuario = username.strip()
                    st.session_state.clear()
                    st.session_state["usuario_logado"] = usuario
                    st.rerun()
                else:
                    st.error("Usuário ou senha incorretos. Após várias tentativas, aguarde 10 minutos.")

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
        st.caption("Cada conta acessa sua própria pasta. Exporte os resultados para preservá-los fora da hospedagem.")


def pagina_minha_conta(username: str) -> None:
    ui.cabecalho("PREFERÊNCIAS", "Minha conta", "Gerencie o acesso ao seu espaço de verificação.")
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
    st.set_page_config(page_title="Cofrya · Verificação de backups", page_icon=str(ui.SIMBOLO), layout="wide")
    ui.aplicar_estilo()

    if "usuario_logado" not in st.session_state:
        pagina_login_ou_registro()
        return

    username = st.session_state["usuario_logado"]
    pasta_usuario = pasta_do_usuario(username)
    with st.sidebar:
        ui.marca()
        st.markdown('<div class="cf-sidebar-label">ESPAÇO DE TRABALHO</div>', unsafe_allow_html=True)
        pagina = st.radio(
            "Navegação", ["Verificar backup", "Histórico", "Proteger arquivo", "Verificar arquivo", "Minha conta"],
            key="navegacao", label_visibility="collapsed",
        )
        st.markdown('<div class="cf-sidebar-note">Um backup precisa fazer mais do que existir.</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="cf-user">Conectada como <strong>{escape(username)}</strong></div>', unsafe_allow_html=True)
        if st.button("Sair", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    if pagina == "Verificar backup":
        streamlit_app.renderizar_pagina(
            repositorio=pasta_usuario / "repositorio", saida=pasta_usuario / "results",
        )
    elif pagina == "Histórico":
        streamlit_app.exibir_historico(pasta_usuario / "results")
    elif pagina == "Proteger arquivo":
        arquivos_app.pagina_proteger(pasta_usuario / "arquivos")
    elif pagina == "Verificar arquivo":
        arquivos_app.pagina_verificar(pasta_usuario / "arquivos")
    else:
        pagina_minha_conta(username)


if __name__ == "__main__":
    main()
