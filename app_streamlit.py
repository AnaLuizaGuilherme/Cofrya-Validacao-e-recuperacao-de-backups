"""
Painel Streamlit para o verificador de backups (mencionado como trabalho
futuro na seção 5.6 do TCC: "uma interface Streamlit... poderá ser
desenvolvida posteriormente").

Roda por cima do código já existente em src/ — não duplica lógica nenhuma,
só chama executar_tentativa() e lê os CSVs gerados pelo RegistradorCSV.

Uso:
    streamlit run app_streamlit.py
"""

from __future__ import annotations

import json
import os

import pandas as pd
import streamlit as st

from src.executor import (
    Configuracao,
    ConfiguracaoExecucao,
    EntradaCatalogo,
    PoliticaTemporal,
    executar_tentativa,
)
from src.results import RegistradorCSV

st.set_page_config(page_title="Verificador de Backups PostgreSQL", layout="wide")
st.title("Verificador de Integridade e Recuperabilidade de Backups")
st.caption("Painel sobre o protótipo descrito no TCC — não substitui o CLI, só facilita a visualização.")

# --- Barra lateral: configuração da execução -------------------------------

with st.sidebar:
    st.header("Nova verificação")

    repositorio = st.text_input("Repositório de backups", value="./repositorio")
    saida = st.text_input("Pasta de resultados (CSV)", value="./results")
    copia_id = st.text_input("ID da cópia", value="pedidos_seed1_c0",
                              help="Nome base dos arquivos .dump/.manifest.json/.referencias.json")
    configuracao = st.selectbox("Configuração", [c.value for c in Configuracao], index=3)
    cenario = st.selectbox(
        "Cenário (só para rotular o resultado)",
        ["C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"],
    )
    semente = st.number_input("Semente", min_value=0, value=1, step=1)
    idade_maxima_dias = st.number_input("Idade máxima da política (dias)", min_value=1, value=30, step=1)
    dependencias_texto = st.text_input(
        "Dependências (papéis), separadas por vírgula", value="",
        help="Deixe em branco para simular C5 (dependência ausente).",
    )
    imagem_postgres = st.text_input(
        "Imagem Docker (opcional)", value="",
        help="Use algo como postgres:versao-que-nao-existe para simular C8.",
    )
    chave_env = st.text_input("Variável de ambiente com a chave HMAC", value="TCC_HMAC_KEY")

    rodar = st.button("Rodar verificação", type="primary", use_container_width=True)

# --- Execução ----------------------------------------------------------------

if rodar:
    chave = os.environ.get(chave_env)
    if not chave:
        st.error(
            f"Variável de ambiente {chave_env} não definida nesta sessão do Streamlit. "
            f"Defina-a no terminal ANTES de rodar `streamlit run app_streamlit.py`."
        )
    else:
        caminho_backup = os.path.join(repositorio, f"{copia_id}.dump")
        caminho_manifesto = os.path.join(repositorio, f"{copia_id}.manifest.json")
        caminho_referencias = os.path.join(repositorio, f"{copia_id}.referencias.json")

        referencias = None
        if os.path.isfile(caminho_referencias):
            with open(caminho_referencias, "r", encoding="utf-8") as f:
                referencias = json.load(f)

        dependencias = [d.strip() for d in dependencias_texto.split(",") if d.strip()]

        entrada = EntradaCatalogo(
            id_copia=copia_id,
            caminho_backup=caminho_backup,
            caminho_manifesto=caminho_manifesto,
            localizacao_autorizada=repositorio,
            dependencias=dependencias,
        )
        contexto = ConfiguracaoExecucao(
            chave_hmac=chave.encode("utf-8"),
            diretorio_trabalho="./tentativas",
            diretorio_saida_csv=saida,
        )

        with st.spinner("Executando a tentativa (pode demorar se envolver Docker/restauração)..."):
            registro = executar_tentativa(
                entrada=entrada,
                configuracao=Configuracao(configuracao),
                politica=PoliticaTemporal(idade_maxima_dias=idade_maxima_dias),
                contexto=contexto,
                referencias_esperadas=referencias,
                cenario=cenario,
                semente=int(semente),
                imagem_postgres_override=imagem_postgres or None,
            )
            RegistradorCSV(saida).gravar(registro)

        cor = {"aprovada": "success", "reprovada": "error", "inconclusiva": "warning"}[registro.decisao]
        getattr(st, cor)(f"**Decisão: {registro.decisao}** — {registro.motivo}")

        if registro.evidencias:
            st.subheader("Evidências desta tentativa")
            st.dataframe(pd.DataFrame(registro.evidencias), use_container_width=True, hide_index=True)

# --- Histórico de resultados -------------------------------------------------

st.divider()
st.header("Histórico de execuções")

caminho_resumo = os.path.join(saida, "resumo.csv")
caminho_evidencias = os.path.join(saida, "evidencias.csv")

if os.path.isfile(caminho_resumo):
    df_resumo = pd.read_csv(caminho_resumo)

    col1, col2, col3 = st.columns(3)
    with col1:
        filtro_cenario = st.multiselect("Filtrar por cenário", sorted(df_resumo["cenario"].unique()))
    with col2:
        filtro_config = st.multiselect("Filtrar por configuração", sorted(df_resumo["configuracao"].unique()))
    with col3:
        filtro_decisao = st.multiselect("Filtrar por decisão", sorted(df_resumo["decisao"].unique()))

    df_filtrado = df_resumo.copy()
    if filtro_cenario:
        df_filtrado = df_filtrado[df_filtrado["cenario"].isin(filtro_cenario)]
    if filtro_config:
        df_filtrado = df_filtrado[df_filtrado["configuracao"].isin(filtro_config)]
    if filtro_decisao:
        df_filtrado = df_filtrado[df_filtrado["decisao"].isin(filtro_decisao)]

    st.dataframe(df_filtrado, use_container_width=True, hide_index=True)

    st.subheader("Aprovações por cenário x configuração")
    if not df_filtrado.empty:
        tabela_cruzada = pd.crosstab(df_filtrado["cenario"], df_filtrado["configuracao"], df_filtrado["decisao"],
                                      aggfunc=lambda x: x.mode().iat[0] if not x.mode().empty else "NA")
        st.dataframe(tabela_cruzada, use_container_width=True)

    if os.path.isfile(caminho_evidencias):
        st.subheader("Ver evidências de uma tentativa específica")
        id_escolhido = st.selectbox("id_tentativa", df_resumo["id_tentativa"].unique())
        df_evidencias = pd.read_csv(caminho_evidencias)
        st.dataframe(
            df_evidencias[df_evidencias["id_tentativa"] == id_escolhido],
            use_container_width=True, hide_index=True,
        )
else:
    st.info(f"Nenhum resultado encontrado ainda em `{caminho_resumo}`. Rode uma verificação na barra lateral.")
