"""Cofrya: execute python -m streamlit run streamlit_app.py.

Reutiliza src/ e mostra somente resultados reais do executor ou CSVs locais.
Caminhos relativos são resolvidos a partir da pasta deste arquivo.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from src.executor import (
    Configuracao, ConfiguracaoExecucao, EntradaCatalogo,
    PoliticaTemporal, executar_tentativa,
)
from src.results import RegistradorCSV

RAIZ = Path(__file__).resolve().parent
DESCRICOES = {
    "A": "Confere a existência dos arquivos, sem autenticação ou restauração.",
    "B": "Autentica o manifesto e confere identificador, idade, tamanho e hash.",
    "C_sem_func": "Executa B e tenta restaurar em um PostgreSQL temporário.",
    "C": "Executa a restauração e valida estrutura, conteúdo e regras de negócio.",
}


@st.cache_resource
def trava_execucao():
    """Mantém as tentativas sequenciais entre sessões deste processo."""
    return threading.Lock()


def caminho_local(valor: str) -> Path:
    caminho = Path(valor.strip()).expanduser()
    return (caminho if caminho.is_absolute() else RAIZ / caminho).resolve()


def obter_valor(nome: str) -> str:
    """Lê uma variável de ambiente ou, se ausente, os Secrets do Streamlit
    (forma padrão de guardar segredos no Streamlit Community Cloud)."""
    valor = os.environ.get(nome)
    if not valor:
        try:
            valor = st.secrets.get(nome, "")
        except (StreamlitSecretNotFoundError, FileNotFoundError):
            valor = ""
    return valor or ""


def obter_chave(nome: str) -> bytes:
    return obter_valor(nome).encode("utf-8")


def salvar_arquivos_enviados(pasta_destino: Path, copia_id: str, dump, manifest, referencias) -> None:
    """Grava os arquivos enviados pelo navegador com o nome que o
    verificador espera ({copia_id}.dump / .manifest.json / .referencias.json),
    dentro da pasta de repositório já configurada — não precisa mexer no
    GitHub nem numa pasta local para testar um backup pontual."""
    pasta_destino.mkdir(parents=True, exist_ok=True)
    (pasta_destino / f"{copia_id}.dump").write_bytes(dump.getvalue())
    (pasta_destino / f"{copia_id}.manifest.json").write_bytes(manifest.getvalue())
    if referencias is not None:
        (pasta_destino / f"{copia_id}.referencias.json").write_bytes(referencias.getvalue())


def ler_csv(caminho: Path, obrigatorias: set[str]) -> pd.DataFrame:
    if not caminho.is_file():
        return pd.DataFrame()
    try:
        dados = pd.read_csv(caminho, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    except (OSError, UnicodeError, pd.errors.ParserError):
        st.warning(f"Não foi possível ler {caminho.name}. Verifique o arquivo de resultados.")
        return pd.DataFrame()
    faltantes = obrigatorias - set(dados.columns)
    if faltantes:
        st.warning(f"{caminho.name}: faltam as colunas {', '.join(sorted(faltantes))}.")
        return pd.DataFrame()
    return dados


def exibir_registro(registro, salvo: bool):
    apresentar = {"aprovada": st.success, "reprovada": st.error}.get(registro.decisao, st.warning)
    apresentar(f"{registro.decisao.capitalize()} — {registro.motivo}")
    st.caption(f"Tentativa: {registro.id_tentativa} · Configuração: {registro.configuracao}")
    if not salvo:
        st.warning("A tentativa foi executada, mas não foi salva no histórico CSV.")
    if registro.evidencias:
        st.dataframe(pd.DataFrame(registro.evidencias).astype(str), use_container_width=True, hide_index=True)


def executar_formulario(repositorio, saida, copia_id, configuracao, cenario,
                       semente, idade, dependencias_texto, imagem, chave_env, provedor_ambiente):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", copia_id):
        st.error("Informe um ID de cópia sem pastas, usando letras, números, ponto, hífen ou sublinhado.")
        return
    chave = obter_chave(chave_env)
    if configuracao != "A" and not chave:
        st.error(f"Configure {chave_env} no ambiente ou nos Secrets do Streamlit, usando a chave que assinou o manifesto.")
        return
    dependencias = [x.strip() for x in dependencias_texto.split(",") if x.strip()]
    if any(not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", x) for x in dependencias):
        st.error("Use nomes de papéis com letras minúsculas, números e sublinhado, começando por letra ou sublinhado.")
        return
    if configuracao in ("C", "C_sem_func"):
        # pg_restore (e psql, se houver papéis) são necessários nos dois
        # ambientes — é o próprio processo do Streamlit que os invoca,
        # apontando para o Docker local ou para o branch Neon remoto.
        necessarios = ["pg_restore"] + (["psql"] if dependencias else [])
        faltantes = [nome for nome in necessarios if shutil.which(nome) is None]
        if faltantes:
            st.error("A restauração exige estas ferramentas no servidor: " + ", ".join(faltantes) + ".")
            return

        if provedor_ambiente == "docker":
            if shutil.which("docker") is None:
                st.error("A restauração exige o Docker instalado no servidor.")
                return
            try:
                status = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
                if status.returncode:
                    st.error("O Docker não está disponível. Inicie o serviço e confira as permissões do servidor.")
                    return
            except (OSError, subprocess.TimeoutExpired):
                st.error("Não foi possível acessar o Docker no servidor.")
                return
        else:  # provedor_ambiente == "neon"
            if not obter_valor("NEON_API_KEY") or not obter_valor("NEON_PROJECT_ID"):
                st.error(
                    "Configure NEON_API_KEY e NEON_PROJECT_ID (variável de ambiente ou "
                    "Secrets do Streamlit) para restaurar sem Docker."
                )
                return
    trava = trava_execucao()
    if not trava.acquire(blocking=False):
        st.warning("Uma verificação já está em andamento. Aguarde a conclusão e tente novamente.")
        return
    try:
        referencia = repositorio / f"{copia_id}.referencias.json"
        referencias = None
        if configuracao == "C" and referencia.is_file():
            referencias = json.loads(referencia.read_text(encoding="utf-8"))
            if not isinstance(referencias, dict):
                raise ValueError("O arquivo de referências precisa conter um objeto JSON.")
        entrada = EntradaCatalogo(
            id_copia=copia_id,
            caminho_backup=str(repositorio / f"{copia_id}.dump"),
            caminho_manifesto=str(repositorio / f"{copia_id}.manifest.json"),
            localizacao_autorizada=str(repositorio), dependencias=dependencias,
        )
        contexto = ConfiguracaoExecucao(
            chave_hmac=chave, diretorio_trabalho=str(RAIZ / "tentativas"),
            diretorio_saida_csv=str(saida), provedor_ambiente=provedor_ambiente,
        )
        with st.spinner("Verificando o backup…"):
            registro = executar_tentativa(
                entrada=entrada, configuracao=Configuracao(configuracao),
                politica=PoliticaTemporal(idade_maxima_dias=int(idade)), contexto=contexto,
                referencias_esperadas=referencias, cenario=cenario, semente=int(semente),
                imagem_postgres_override=imagem.strip() or None,
            )
        st.session_state["ultima_tentativa"] = (registro, False)
        try:
            RegistradorCSV(saida).gravar(registro)
        except (OSError, ValueError) as exc:
            st.error(f"Não foi possível salvar o resultado: {exc}")
        else:
            st.session_state["ultima_tentativa"] = (registro, True)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        st.error(f"A verificação não foi concluída: {exc}")
    finally:
        trava.release()


def exibir_historico(saida: Path):
    st.header("Histórico de verificações")
    st.button("Atualizar histórico", key="atualizar")
    resumo = ler_csv(saida / "resumo.csv", {"id_tentativa", "cenario", "configuracao", "decisao"})
    if resumo.empty:
        st.info("Ainda não há resultados disponíveis nesta pasta. Execute uma verificação ou indique uma pasta com CSVs existentes.")
        return
    colunas = st.columns(3)
    filtrado = resumo.copy()
    for coluna, campo, rotulo in zip(colunas, ("cenario", "configuracao", "decisao"),
                                    ("Cenários", "Configurações", "Decisões")):
        selecao = coluna.multiselect(rotulo, sorted(resumo[campo].unique()), key=f"filtro_{campo}")
        if selecao:
            filtrado = filtrado[filtrado[campo].isin(selecao)]
    metricas = st.columns(4)
    metricas[0].metric("Tentativas", len(filtrado))
    for coluna, decisao in zip(metricas[1:], ("aprovada", "reprovada", "inconclusiva")):
        coluna.metric(decisao.capitalize() + "s", int((filtrado["decisao"] == decisao).sum()))
    st.dataframe(filtrado, use_container_width=True, hide_index=True)
    st.download_button("Baixar resumo filtrado", filtrado.to_csv(index=False).encode("utf-8-sig"),
                       "resumo.csv", "text/csv", key="download_resumo")
    if filtrado.empty:
        st.info("Nenhuma tentativa corresponde aos filtros selecionados.")
        return
    st.subheader("Decisões por cenário e configuração")
    contagens = filtrado.groupby(["cenario", "configuracao", "decisao"]).size().rename("tentativas").reset_index()
    st.dataframe(contagens, use_container_width=True, hide_index=True)
    st.caption("Contagens observadas. Aprovação nas configurações A e B não comprova restauração.")
    evidencias = ler_csv(saida / "evidencias.csv", {"id_tentativa", "teste", "aprovado"})
    st.subheader("Evidências de uma tentativa")
    escolhido = st.selectbox("ID da tentativa", filtrado["id_tentativa"].unique(), key="tentativa_selecionada")
    if evidencias.empty:
        st.info("Não há evidências individuais disponíveis.")
        return
    detalhes = evidencias[evidencias["id_tentativa"] == escolhido]
    st.dataframe(detalhes, use_container_width=True, hide_index=True)
    st.download_button("Baixar evidências da tentativa", detalhes.to_csv(index=False).encode("utf-8-sig"),
                       "evidencias.csv", "text/csv", key="download_evidencias")


def main():
    st.set_page_config(page_title="Cofrya | Verificação de backups", page_icon="🛡️", layout="wide")
    st.title("Cofrya")
    st.caption("Um backup precisa fazer mais do que existir.")
    st.write("Verifique a integridade, teste a recuperação e consulte as evidências dos seus backups PostgreSQL.")
    with st.sidebar:
        st.header("Arquivos do laboratório")
        repositorio = caminho_local(st.text_input("Pasta dos backups", "./repositorio", key="repositorio"))
        saida = caminho_local(st.text_input("Pasta dos resultados", "./results", key="saida"))
        st.caption("As pastas são do computador ou servidor que executa o Streamlit.")
        with st.expander("Requisitos de execução"):
            st.write(
                "A: arquivos da cópia. B: chave HMAC. C_sem_func e C: pg_restore "
                "(psql quando houver papéis declarados), mais Docker **ou** "
                "NEON_API_KEY/NEON_PROJECT_ID, conforme o ambiente escolhido. "
                "C também usa as referências esperadas."
            )
    verificacao, historico = st.tabs(["Nova verificação", "Resultados"])
    with verificacao:
        with st.form("verificar_backup"):
            copia_id = st.text_input("ID da cópia", "pedidos_seed1_c0", key="copia_id")
            st.caption("Nome base dos arquivos .dump, .manifest.json e .referencias.json.")

            st.subheader("Arquivos do backup")
            modo_arquivos = st.radio(
                "De onde vêm os arquivos?",
                ["Enviar agora pelo navegador", "Já estão na pasta do repositório"],
                key="modo_arquivos",
                horizontal=True,
            )
            arquivo_dump = arquivo_manifest = arquivo_referencias = None
            if modo_arquivos == "Enviar agora pelo navegador":
                arquivo_dump = st.file_uploader("Backup (.dump)", key="upload_dump")
                arquivo_manifest = st.file_uploader("Manifesto (.manifest.json)", key="upload_manifest")
                arquivo_referencias = st.file_uploader(
                    "Referências (.referencias.json) — só necessário para a configuração C",
                    key="upload_referencias",
                )
                st.caption(
                    "Os arquivos são salvos com o ID da cópia acima e ficam só nesta sessão do "
                    "servidor — não é preciso subir nada no GitHub."
                )

            col1, col2 = st.columns(2)
            configuracao = col1.selectbox("Configuração", list(DESCRICOES), index=3, key="configuracao")
            cenario = col2.selectbox("Cenário", [f"C{i}" for i in range(9)], key="cenario")
            st.caption("O cenário é um rótulo experimental; selecioná-lo não injeta uma falha no backup.")
            col1, col2 = st.columns(2)
            semente = col1.number_input("Semente", min_value=0, value=1, step=1, key="semente")
            idade = col2.number_input("Idade máxima (dias)", min_value=1, value=30, step=1, key="idade")
            with st.expander("Parâmetros adicionais"):
                provedor_ambiente = st.selectbox(
                    "Ambiente de restauração", ["docker", "neon"], key="provedor_ambiente",
                    help="docker: contêiner local (seção 5.5 do TCC). neon: banco efêmero na "
                         "nuvem via API, sem precisar de Docker — útil para hospedar este painel.",
                )
                dependencias = st.text_input("Papéis necessários, separados por vírgula", key="dependencias")
                imagem = st.text_input("Imagem PostgreSQL (opcional, só para o ambiente docker)", key="imagem")
                chave_env = st.text_input("Nome da variável com a chave HMAC", "TCC_HMAC_KEY", key="chave_env")
            rodar = st.form_submit_button("Executar verificação", type="primary")
        with st.expander("O que cada configuração avalia"):
            for nome, descricao in DESCRICOES.items():
                st.write(f"**{nome}:** {descricao}")
        if rodar:
            st.session_state.pop("ultima_tentativa", None)
            if modo_arquivos == "Enviar agora pelo navegador":
                if not arquivo_dump or not arquivo_manifest:
                    st.error("Envie pelo menos o arquivo .dump e o .manifest.json antes de executar.")
                else:
                    try:
                        salvar_arquivos_enviados(
                            repositorio, copia_id.strip(), arquivo_dump, arquivo_manifest, arquivo_referencias
                        )
                    except OSError as exc:
                        st.error(f"Não foi possível salvar os arquivos enviados: {exc}")
                    else:
                        executar_formulario(repositorio, saida, copia_id.strip(), configuracao, cenario,
                                           semente, idade, dependencias, imagem, chave_env.strip(), provedor_ambiente)
            else:
                executar_formulario(repositorio, saida, copia_id.strip(), configuracao, cenario,
                                   semente, idade, dependencias, imagem, chave_env.strip(), provedor_ambiente)
        if "ultima_tentativa" in st.session_state:
            exibir_registro(*st.session_state["ultima_tentativa"])
    with historico:
        exibir_historico(saida)


if __name__ == "__main__":
    main()
