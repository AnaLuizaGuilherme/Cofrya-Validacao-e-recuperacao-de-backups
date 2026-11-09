"""Apresentação do Cofrya. Não participa das decisões do verificador."""
from __future__ import annotations

import base64
from functools import lru_cache
from html import escape
from pathlib import Path

import streamlit as st

RAIZ = Path(__file__).resolve().parents[1]
SIMBOLO = RAIZ / "assets" / "brand" / "cofrya-simbolo.png"

NOMES_CONFIG = {
    "A": "A · Presença dos arquivos",
    "B": "B · Autenticidade e integridade",
    "C_sem_func": "C_sem_func · Restauração",
    "C": "C · Restauração e validação funcional",
}
ESCOPO_CONFIG = {
    "A": "Confirma a presença do backup e do manifesto. Não verifica os bytes nem restaura o banco.",
    "B": "Confere assinatura, identificação, idade, tamanho e hash. Não restaura o banco.",
    "C_sem_func": "Inclui as verificações de B e restaura em um banco temporário. Não valida as regras dos dados.",
    "C": "Inclui as verificações de B, restaura o banco e compara estrutura, conteúdo e regras de negócio.",
}


@lru_cache(maxsize=1)
def simbolo_base64() -> str:
    return base64.b64encode(SIMBOLO.read_bytes()).decode("ascii")


def aplicar_estilo() -> None:
    st.markdown((RAIZ / "assets" / "cofrya.css").read_text(encoding="utf-8"), unsafe_allow_html=True)


def marca() -> None:
    st.markdown(
        f'<div class="cf-brand"><img src="data:image/png;base64,{simbolo_base64()}" '
        'alt="Símbolo Cofrya"><span>Cofrya<span class="cf-brand-dot">.</span></span></div>',
        unsafe_allow_html=True,
    )


def cabecalho(categoria: str, titulo: str, descricao: str) -> None:
    st.markdown(f'<div class="cf-eyebrow">{escape(categoria)}</div>', unsafe_allow_html=True)
    st.title(titulo)
    st.markdown(f'<p class="cf-lead">{escape(descricao)}</p>', unsafe_allow_html=True)


def etapa(numero: str, titulo: str, descricao: str = "") -> None:
    st.markdown(
        f'<div class="cf-step"><span class="cf-step-number">{escape(numero)}</span>'
        f'<div><h3>{escape(titulo)}</h3><p>{escape(descricao)}</p></div></div>',
        unsafe_allow_html=True,
    )


def escopo(configuracao: str) -> None:
    st.markdown(
        '<div class="cf-scope"><span>O QUE ESTE NÍVEL VERIFICA</span>'
        f'<p>{escape(ESCOPO_CONFIG[configuracao])}</p></div>', unsafe_allow_html=True,
    )


def estado_vazio(titulo: str, descricao: str) -> None:
    st.markdown(
        f'<div class="cf-empty"><span class="cf-empty-mark" aria-hidden="true">○</span>'
        f'<h3>{escape(titulo)}</h3><p>{escape(descricao)}</p></div>', unsafe_allow_html=True,
    )


def resultado(decisao: str, titulo: str, descricao: str) -> None:
    classe = {"aprovada": "success", "reprovada": "error"}.get(decisao, "warning")
    sinal = {"aprovada": "✓", "reprovada": "×"}.get(decisao, "!")
    st.markdown(
        f'<section class="cf-result cf-result-{classe}" role="status">'
        f'<span class="cf-result-sign" aria-hidden="true">{sinal}</span><div>'
        f'<h2>{escape(titulo)}</h2><p>{escape(descricao)}</p></div></section>',
        unsafe_allow_html=True,
    )


def detalhe_evidencias(linhas: list[dict]) -> None:
    """Resumo legível, preservando as evidências completas para inspeção."""
    import pandas as pd
    if not linhas:
        st.caption("Esta tentativa não registrou testes individuais. Consulte o escopo e o motivo da decisão.")
        return
    df = pd.DataFrame(linhas)
    if "aprovado" in df:
        resultado_txt = df["aprovado"].astype(str).str.lower()
        falhas = df[resultado_txt == "false"]
        if not falhas.empty:
            st.subheader("O que precisa de atenção")
            colunas = [c for c in ["teste", "valor_esperado", "valor_observado", "esperado", "observado", "detalhe"] if c in falhas]
            st.dataframe(falhas[colunas].rename(columns={
                "teste": "Verificação", "valor_esperado": "Esperado",
                "valor_observado": "Encontrado", "esperado": "Esperado", "observado": "Encontrado", "detalhe": "Detalhe",
            }).astype(str), use_container_width=True, hide_index=True)
    with st.expander(f"Ver todas as evidências ({len(df)})"):
        st.dataframe(df.astype(str), use_container_width=True, hide_index=True)
