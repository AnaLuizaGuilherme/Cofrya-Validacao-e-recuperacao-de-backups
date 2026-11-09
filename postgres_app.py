"""Cofrya: execute python -m streamlit run streamlit_app.py.

Reutiliza src/ e mostra somente resultados reais do executor ou CSVs locais.
Caminhos relativos são resolvidos a partir da pasta deste arquivo.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import tempfile
from src.safe_files import validar_id, salvar_uploads
from src.experiment_inputs import id_do_backup_enviado, validar_rotulo_da_copia, ler_referencias

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from src.scenarios import Cenario
from src.executor import (
    Configuracao, ConfiguracaoExecucao, EntradaCatalogo,
    PoliticaTemporal, executar_tentativa,
)
from src.results import RegistradorCSV
from src import ui

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
    salvar_uploads(pasta_destino, copia_id, dump, manifest, referencias)


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
    st.subheader("Resultado da verificação")
    titulos = {
        "aprovada": "Aprovada nas verificações realizadas",
        "reprovada": "Uma verificação identificou um problema",
        "inconclusiva": "Não foi possível concluir a verificação",
    }
    ui.resultado(registro.decisao, titulos.get(registro.decisao, "Resultado"), registro.motivo)
    ui.escopo(registro.configuracao)
    st.caption(f"Cópia: {registro.id_copia} · {ui.NOMES_CONFIG[registro.configuracao]}")
    if registro.decisao == "reprovada":
        st.caption("Consulte o teste que falhou antes de usar esta cópia. As etapas posteriores podem não ter sido executadas.")
    if not salvo:
        st.warning("A tentativa foi executada, mas não foi salva no histórico CSV.")
    ui.detalhe_evidencias(registro.evidencias)
    with st.expander("Identificação e contexto da tentativa"):
        st.write(f"ID: {registro.id_tentativa}")
        st.write(f"Cenário: {registro.cenario} · Semente: {registro.semente} · Versão: {registro.versao_codigo}")
        if registro.tempo_decisao_s is not None:
            st.write(f"Tempo até a decisão: {registro.tempo_decisao_s:.3f} s")
    st.caption("O histórico reúne as tentativas salvas e permite baixar o resumo e as evidências.")


def executar_formulario(repositorio, saida, copia_id, configuracao, cenario,
                       semente, idade, dependencias_texto, imagem, chave_env, provedor_ambiente,
                       uploads=None, usar_nome_backup=False):
    try:
        if usar_nome_backup:
            if uploads is None or uploads[0] is None:
                raise ValueError('Envie o backup .dump para identificar a cópia.')
            copia_id = id_do_backup_enviado(uploads[0].name)
        validar_id(copia_id)
        validar_rotulo_da_copia(copia_id, cenario, int(semente))
        referencias = None
        if configuracao == 'C' and uploads is not None:
            referencias = ler_referencias(uploads[2].getvalue() if uploads[2] is not None else b'')
    except ValueError as exc:
        st.error(str(exc))
        return
    chave_env = "TCC_HMAC_KEY"
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
        if configuracao == 'C' and uploads is None:
            referencia = repositorio / f"{copia_id}.referencias.json"
            if not referencia.is_file():
                raise ValueError(f'A configuração C exige {referencia.name} na pasta da cópia.')
            referencias = ler_referencias(referencia.read_bytes())
        if uploads is not None:
            salvar_arquivos_enviados(repositorio, copia_id, *uploads)
        entrada = EntradaCatalogo(
            id_copia=copia_id,
            caminho_backup=str(repositorio / f"{copia_id}.dump"),
            caminho_manifesto=str(repositorio / f"{copia_id}.manifest.json"),
            localizacao_autorizada=str(repositorio), dependencias=dependencias,
        )
        contexto = ConfiguracaoExecucao(
            chave_hmac=chave, diretorio_trabalho=str(saida.parent / "tentativas"),
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
    ui.cabecalho("ACOMPANHAMENTO", "Histórico de verificações", "Entenda as decisões, consulte as evidências e exporte seus resultados.")
    resumo = ler_csv(saida / "resumo.csv", {"id_tentativa", "cenario", "configuracao", "decisao"})
    if resumo.empty:
        ui.estado_vazio("Seu histórico começa com uma verificação", "Escolha Verificar backup no menu. As tentativas salvas aparecerão aqui, com a decisão e suas evidências.")
        return
    metricas = st.columns(4)
    metricas[0].metric("Tentativas registradas", len(resumo))
    for coluna, decisao, titulo in zip(metricas[1:], ("aprovada", "reprovada", "inconclusiva"), ("Aprovadas", "Reprovadas", "Inconclusivas")):
        coluna.metric(titulo, int((resumo["decisao"] == decisao).sum()))
    st.caption("A aprovação se refere ao nível escolhido. A e B não testam a restauração.")
    colunas = st.columns(3)
    filtrado = resumo.copy()
    for coluna, campo, rotulo in zip(colunas, ("cenario", "configuracao", "decisao"), ("Cenários", "Níveis de verificação", "Decisões")):
        selecao = coluna.multiselect(rotulo, sorted(resumo[campo].unique()), key=f"filtro_{campo}", placeholder="Todos")
        if selecao:
            filtrado = filtrado[filtrado[campo].isin(selecao)]
    st.caption(f"{len(filtrado)} de {len(resumo)} tentativas • Os indicadores acima mostram todo o histórico.")
    if filtrado.empty:
        ui.estado_vazio("Nenhum resultado para estes filtros", "Remova um dos filtros para consultar outras tentativas.")
        return
    preferidas = ["instante_inicio_utc", "id_copia", "cenario", "configuracao", "decisao", "motivo"]
    cols = [c for c in preferidas if c in filtrado]
    st.dataframe(filtrado[cols].rename(columns={
        "instante_inicio_utc": "Início (UTC)", "id_copia": "Cópia", "cenario": "Cenário",
        "configuracao": "Nível", "decisao": "Decisão", "motivo": "Motivo",
    }), use_container_width=True, hide_index=True)
    st.download_button("Baixar resumo filtrado", filtrado.to_csv(index=False).encode("utf-8-sig"),
                       "resumo.csv", "text/csv", key="download_resumo")
    st.subheader("Detalhes de uma tentativa")
    def rotulo_tentativa(identificador):
        linha = filtrado[filtrado["id_tentativa"] == identificador].iloc[0]
        return f"{linha.get('id_copia', 'Cópia')} · {linha['configuracao']} · {linha['decisao']} · {identificador}"
    escolhido = st.selectbox("Selecione uma tentativa", filtrado["id_tentativa"].unique(),
                             key="tentativa_selecionada", format_func=rotulo_tentativa)
    linha = filtrado[filtrado["id_tentativa"] == escolhido].iloc[0]
    ui.resultado(linha["decisao"], linha["decisao"].capitalize(), linha.get("motivo", "Motivo não registrado."))
    if linha["configuracao"] in ui.ESCOPO_CONFIG:
        ui.escopo(linha["configuracao"])
    evidencias = ler_csv(saida / "evidencias.csv", {"id_tentativa", "teste", "aprovado"})
    if evidencias.empty:
        st.info("Esta exportação não contém evidências individuais disponíveis.")
    else:
        detalhes = evidencias[evidencias["id_tentativa"] == escolhido]
        ui.detalhe_evidencias(detalhes.to_dict("records"))
        if not detalhes.empty:
            st.download_button("Baixar evidências da tentativa", detalhes.to_csv(index=False).encode("utf-8-sig"),
                               "evidencias.csv", "text/csv", key="download_evidencias")
    with st.expander("Dados técnicos e comparação por cenário"):
        st.dataframe(filtrado, use_container_width=True, hide_index=True)
        contagens = filtrado.groupby(["cenario", "configuracao", "decisao"]).size().rename("tentativas").reset_index()
        st.dataframe(contagens, use_container_width=True, hide_index=True)
        st.caption("Contagens observadas. Todas as tentativas são mantidas; esta tela não faz a consolidação científica.")
    st.button("Atualizar histórico", key="atualizar")


def renderizar_pagina(repositorio: Path, saida: Path) -> None:
    """Fluxo orientado por tarefa; os valores enviados ao executor permanecem iguais."""
    ui.cabecalho("BACKUPS POSTGRESQL", "Verificar um backup", "Escolha o nível de análise, envie os arquivos e acompanhe o resultado com evidências.")
    with st.container(key="etapa-nivel"):
        ui.etapa("01", "Escolha o nível de verificação", "Cada nível responde a uma pergunta diferente sobre o seu backup.")
        configuracao = st.selectbox("Nível de verificação", list(DESCRICOES), index=3,
                                   key="configuracao", format_func=ui.NOMES_CONFIG.get)
        ui.escopo(configuracao)
        with st.expander("Comparar os quatro níveis"):
            for nome, descricao in DESCRICOES.items():
                st.write(f"**{ui.NOMES_CONFIG[nome]}:** {descricao}")

    with st.container(key="etapa-arquivos"):
        ui.etapa("02", "Adicione os arquivos", "Use os arquivos correspondentes à mesma cópia de backup.")
        modo_arquivos = st.radio("Origem dos arquivos", ["Enviar agora pelo navegador", "Já estão na pasta do repositório"],
                                key="modo_arquivos", horizontal=True)
        arquivo_dump = arquivo_manifest = arquivo_referencias = None
        usar_nome_backup = False
        copia_id = ""
        if modo_arquivos == "Enviar agora pelo navegador":
            col1, col2 = st.columns(2)
            arquivo_dump = col1.file_uploader("Backup · .dump", type=["dump"], key="upload_dump",
                                              help="Cópia do PostgreSQL em formato customizado, produzida pelo pg_dump.")
            arquivo_manifest = col2.file_uploader("Manifesto · .manifest.json", type=["json"], key="upload_manifest",
                                                  help="Metadados e assinatura HMAC usados para conferir a cópia.")
            if configuracao == "C":
                arquivo_referencias = st.file_uploader("Referências · .referencias.json", type=["json"], key="upload_referencias",
                                                       help="Valores esperados da base correta, necessários para a validação funcional.")
            usar_nome_backup = st.checkbox("Usar o nome do backup como ID da cópia", value=True, key="usar_nome_backup",
                                           help="Exemplo: pedidos_seed1_c3.dump → pedidos_seed1_c3.")
            if usar_nome_backup and arquivo_dump:
                st.caption(f"Cópia identificada: {Path(arquivo_dump.name).stem}")
            if not usar_nome_backup:
                copia_id = st.text_input("ID da cópia", "pedidos_seed1_c0", key="copia_id")
            st.caption("Arquivos e resultados ficam na pasta da sua conta no servidor. Baixe os registros para preservá-los.")
        else:
            copia_id = st.text_input("ID da cópia", "pedidos_seed1_c0", key="copia_id")
            st.caption("Informe o nome-base dos arquivos já enviados para a sua conta, sem a extensão.")

    with st.container(key="etapa-revisao"):
        ui.etapa("03", "Revise e execute", "Os parâmetros do experimento ficam disponíveis abaixo quando você precisar deles.")
        with st.expander("Contexto experimental · cenário e semente"):
            col1, col2 = st.columns(2)
            cenario = col1.selectbox("Cenário", [c.value for c in Cenario], key="cenario")
            semente = col2.number_input("Semente", min_value=0, value=1, step=1, key="semente")
            st.caption("O cenário identifica uma condição já preparada nos arquivos. Selecioná-lo não injeta uma falha. A semente identifica a geração dos dados.")
        with st.expander("Política e ambiente de restauração"):
            idade = st.number_input("Idade máxima do manifesto (dias)", min_value=1, value=30, step=1, key="idade")
            provedor_ambiente = st.selectbox("Ambiente de restauração", ["docker", "neon"],
                index=1 if obter_valor("NEON_API_KEY") and obter_valor("NEON_PROJECT_ID") else 0,
                key="provedor_ambiente", format_func=lambda x: {"docker": "Docker · ambiente local", "neon": "Neon · PostgreSQL na nuvem"}[x])
            dependencias = st.text_input("Papéis necessários, separados por vírgula", key="dependencias")
            imagem = st.text_input("Imagem PostgreSQL (opcional, só para Docker)", key="imagem")
            st.caption("A restauração exige pg_restore; psql também é necessário quando há papéis declarados. Neon usa as credenciais configuradas no servidor.")
        st.caption(f"{ui.NOMES_CONFIG[configuracao]} · Cenário {cenario} · Semente {semente} · Limite: {idade} dias")
        if configuracao in ("C", "C_sem_func"):
            st.info("A restauração pode levar alguns minutos. Mantenha esta página aberta até a conclusão.")
        rodar = st.button("Executar verificação", type="primary", key="executar_backup")
    if rodar:
        st.session_state.pop("ultima_tentativa", None)
        if modo_arquivos == "Enviar agora pelo navegador":
            if not arquivo_dump or not arquivo_manifest:
                st.error("Adicione o backup .dump e o manifesto .manifest.json na etapa 02 para continuar.")
            elif configuracao == "C" and not arquivo_referencias:
                st.error("A validação funcional precisa do arquivo .referencias.json. Adicione-o na etapa 02.")
            else:
                executar_formulario(repositorio, saida, copia_id.strip(), configuracao, cenario,
                    semente, idade, dependencias, imagem, "TCC_HMAC_KEY", provedor_ambiente,
                    uploads=(arquivo_dump, arquivo_manifest, arquivo_referencias), usar_nome_backup=usar_nome_backup)
        else:
            executar_formulario(repositorio, saida, copia_id.strip(), configuracao, cenario,
                semente, idade, dependencias, imagem, "TCC_HMAC_KEY", provedor_ambiente)
    if "ultima_tentativa" in st.session_state:
        exibir_registro(*st.session_state["ultima_tentativa"])


def main():
    from cofrya_app import main as app_com_login
    app_com_login()


if __name__ == "__main__":
    main()
