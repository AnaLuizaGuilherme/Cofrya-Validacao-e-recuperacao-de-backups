"""Cofrya Arquivos: execute python -m streamlit run arquivos_app.py.

Ferramenta genérica de integridade e recuperabilidade de arquivos —
separada do verificador específico de backups PostgreSQL do TCC
(streamlit_app.py). Suporta CSV, JSON, SQLite (.db/.sqlite/.sqlite3) e
backups PostgreSQL (.dump/.backup).

Fluxo: a pessoa primeiro "protege" um arquivo (gera um manifesto assinado
com hash) e guarda os dois. Depois, quando quiser conferir se o arquivo
ainda está íntegro e utilizável, usa "verificar", enviando o arquivo e o
manifesto de volta.
"""
from __future__ import annotations

import io
import secrets
from pathlib import Path

import streamlit as st

from src import manifest as manifesto_mod
from src import tipos_arquivo as ta

RAIZ = Path(__file__).resolve().parent

NOMES_TIPO = {
    ".csv": "CSV",
    ".json": "JSON",
    ".db": "SQLite",
    ".sqlite": "SQLite",
    ".sqlite3": "SQLite",
    ".dump": "Backup PostgreSQL (pg_dump)",
    ".backup": "Backup PostgreSQL (pg_dump)",
}

DICA_ESTRUTURA = {
    ".csv": "Nomes das colunas do cabeçalho, separados por vírgula (ex.: nome,email,idade).",
    ".json": "Chaves esperadas no nível principal do JSON, separadas por vírgula.",
    ".db": "Nomes das tabelas esperadas, separados por vírgula.",
    ".sqlite": "Nomes das tabelas esperadas, separados por vírgula.",
    ".sqlite3": "Nomes das tabelas esperadas, separados por vírgula.",
    ".dump": "Nomes de tabelas que devem aparecer no índice do backup, separados por vírgula.",
    ".backup": "Nomes de tabelas que devem aparecer no índice do backup, separados por vírgula.",
}


def extensao_de(nome_arquivo: str) -> str | None:
    return ta.detectar_tipo(nome_arquivo)


def salvar_temp(pasta: Path, nome: str, conteudo: bytes) -> str:
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / nome
    caminho.write_bytes(conteudo)
    return str(caminho)


def pagina_proteger(pasta_base: Path):
    st.header("Proteger um arquivo")
    st.write(
        "Envie um arquivo (CSV, JSON, SQLite ou backup PostgreSQL) para gerar um "
        "**manifesto assinado** — um arquivo pequeno com o hash e uma assinatura "
        "(HMAC) que provam, mais tarde, se o arquivo original mudou."
    )

    arquivo = st.file_uploader(
        "Arquivo a proteger", type=["csv", "json", "db", "sqlite", "sqlite3", "dump", "backup"],
        key="proteger_arquivo",
    )
    if not arquivo:
        return

    extensao = extensao_de(arquivo.name)
    if extensao is None:
        st.error("Tipo de arquivo não suportado. Use CSV, JSON, SQLite ou um backup PostgreSQL (.dump/.backup).")
        return
    st.caption(f"Tipo detectado: **{NOMES_TIPO[extensao]}**")

    usar_chave_propria = st.checkbox("Já tenho uma chave secreta para usar (em vez de gerar uma nova)")
    chave_texto = ""
    if usar_chave_propria:
        chave_texto = st.text_input("Cole sua chave secreta", type="password", key="chave_propria")

    if st.button("Gerar manifesto", type="primary"):
        if usar_chave_propria and not chave_texto.strip():
            st.error("Cole a chave ou desmarque a opção para gerar uma nova.")
            return

        chave_texto_final = chave_texto.strip() if usar_chave_propria else secrets.token_urlsafe(24)
        chave_bytes = chave_texto_final.encode("utf-8")

        pasta_temp = pasta_base / "tmp_proteger"
        caminho_arquivo = salvar_temp(pasta_temp, arquivo.name, arquivo.getvalue())

        m = manifesto_mod.construir_manifesto(
            caminho_arquivo=caminho_arquivo,
            id_copia=arquivo.name,
            versao_banco="n/a",
            versao_aplicacao="cofrya-arquivos",
        )
        documento = manifesto_mod.assinar_manifesto(m, chave_bytes)

        import json as json_lib
        conteudo_manifesto = json_lib.dumps(documento, sort_keys=True, indent=2).encode("utf-8")

        st.success("Manifesto gerado.")
        if not usar_chave_propria:
            st.warning(
                "**Guarde esta chave agora — ela não aparece de novo.** Sem ela, não dá "
                "para verificar o arquivo depois."
            )
            st.code(chave_texto_final, language=None)

        st.download_button(
            "Baixar manifesto (.manifest.json)",
            data=conteudo_manifesto,
            file_name=f"{arquivo.name}.manifest.json",
            mime="application/json",
        )
        st.caption("Guarde o arquivo original, o manifesto baixado e a chave — os três juntos são o que permite verificar depois.")


def pagina_verificar(pasta_base: Path):
    st.header("Verificar um arquivo")
    st.write("Envie o arquivo, o manifesto gerado antes e a chave para conferir se está tudo certo.")

    col1, col2 = st.columns(2)
    arquivo = col1.file_uploader(
        "Arquivo a verificar", type=["csv", "json", "db", "sqlite", "sqlite3", "dump", "backup"],
        key="verificar_arquivo",
    )
    manifesto = col2.file_uploader("Manifesto (.manifest.json) — opcional", type=["json"], key="verificar_manifesto")

    chave_texto = st.text_input(
        "Chave secreta (necessária só se enviar um manifesto)", type="password", key="verificar_chave"
    )

    extensao = extensao_de(arquivo.name) if arquivo else None
    if arquivo and extensao is None:
        st.error("Tipo de arquivo não suportado. Use CSV, JSON, SQLite ou um backup PostgreSQL (.dump/.backup).")
        return

    estrutura_texto = ""
    if extensao:
        st.caption(f"Tipo detectado: **{NOMES_TIPO[extensao]}**")
        estrutura_texto = st.text_input(
            "Estrutura esperada (opcional)", help=DICA_ESTRUTURA[extensao], key="verificar_estrutura"
        )

    idade_maxima = st.number_input("Idade máxima aceitável do manifesto (dias)", min_value=1, value=30, key="verificar_idade")

    if st.button("Verificar", type="primary"):
        if not arquivo:
            st.error("Envie o arquivo a verificar.")
            return
        if manifesto and not chave_texto.strip():
            st.error("Envie a chave que assinou o manifesto, ou remova o manifesto para verificar só o conteúdo.")
            return

        pasta_temp = pasta_base / "tmp_verificar"
        caminho_arquivo = salvar_temp(pasta_temp, arquivo.name, arquivo.getvalue())
        caminho_manifesto = None
        if manifesto:
            caminho_manifesto = salvar_temp(pasta_temp, manifesto.name, manifesto.getvalue())

        estrutura_esperada = [x.strip() for x in estrutura_texto.split(",") if x.strip()] or None
        chave_bytes = chave_texto.strip().encode("utf-8") if chave_texto.strip() else None

        with st.spinner("Verificando…"):
            resultado = ta.verificar_arquivo_generico(
                caminho_arquivo=caminho_arquivo,
                caminho_manifesto=caminho_manifesto,
                chave_hmac=chave_bytes,
                extensao=extensao,
                estrutura_esperada=estrutura_esperada,
                idade_maxima_dias=int(idade_maxima),
            )

        if resultado.decisao == "aprovada":
            st.success("Aprovado — o arquivo está íntegro e utilizável.")
        else:
            st.error("Reprovado — encontramos um problema. Veja os detalhes abaixo.")

        import pandas as pd
        linhas = [
            {"teste": e.teste, "aprovado": e.aprovado, "esperado": e.valor_esperado,
             "observado": e.valor_observado, "detalhe": e.detalhe}
            for e in resultado.evidencias
        ]
        st.dataframe(pd.DataFrame(linhas).astype(str), use_container_width=True, hide_index=True)


def main():
    st.set_page_config(page_title="Cofrya Arquivos", page_icon="🗂️", layout="wide")
    st.title("Cofrya Arquivos")
    st.caption("Confira se seus arquivos de dados ainda estão íntegros e utilizáveis — CSV, JSON, SQLite e backups PostgreSQL.")

    aba_proteger, aba_verificar = st.tabs(["Proteger", "Verificar"])
    with aba_proteger:
        pagina_proteger(RAIZ)
    with aba_verificar:
        pagina_verificar(RAIZ)

    st.divider()
    st.caption(
        "Ferramenta de uso geral, sem restauração completa de bancos de dados. "
        "Para verificação avançada de backups PostgreSQL, com restauração em ambiente isolado, "
        "veja o Cofrya original (streamlit_app.py)."
    )


if __name__ == "__main__":
    main()
