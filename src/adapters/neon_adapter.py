"""
Adaptador Neon utilizado nos ensaios em nuvem do Cofrya.

Cria um branch por tentativa e um banco vazio dentro dele; solicita uma
conexão direta ao endpoint de escrita e confirma o banco via SQL antes de
restaurar. Solicita a remoção do branch ao terminar.

Requer NEON_API_KEY e NEON_PROJECT_ID no ambiente ou em st.secrets.
A rede e o provedor integram o domínio de confiança. Branches herdam o
estado do pai, inclusive papéis; o banco novo evita herdar tabelas nos
resultados da restauração. Os limites locais de CPU/memória do adaptador
Docker não se aplicam ao Neon. Use projeto exclusivo de laboratório.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Optional
from urllib.parse import urlparse, unquote

import requests

from .docker_adapter import ContainerNaoDisponivel, InstanciaTemporaria
from .postgres_adapter import executar_consulta

BASE_URL = "https://console.neon.tech/api/v2"


def _obter_config() -> tuple:
    chave = os.environ.get("NEON_API_KEY")
    project_id = os.environ.get("NEON_PROJECT_ID")

    # Se estiver rodando dentro do Streamlit e as env vars não estiverem
    # definidas, tenta st.secrets (forma padrão de guardar segredos no
    # Streamlit Cloud).
    if not chave or not project_id:
        try:
            import streamlit as st
            chave = chave or st.secrets.get("NEON_API_KEY")
            project_id = project_id or st.secrets.get("NEON_PROJECT_ID")
        except Exception:
            pass

    if not chave or not project_id:
        raise ContainerNaoDisponivel(
            "NEON_API_KEY e/ou NEON_PROJECT_ID não definidas (variável de "
            "ambiente ou st.secrets)."
        )
    return chave, project_id


def _cabecalhos(chave: str) -> dict:
    return {"Authorization": f"Bearer {chave}", "Content-Type": "application/json"}


def subir_postgres_temporario(
    id_tentativa: str,
    timeout_disponibilidade_s: int = 60,
    **_ignorados,  # aceita e ignora imagem/limite_cpu/limite_memoria (não se aplicam ao Neon)
) -> InstanciaTemporaria:
    """Cria um branch efêmero do Neon e retorna seus dados de conexão.

    Levanta ContainerNaoDisponivel se o ambiente não puder ser preparado.
    Esse impedimento não demonstra defeito no backup.
    """
    chave, project_id = _obter_config()
    cabecalhos = _cabecalhos(chave)

    nome_branch = f"tentativa-{id_tentativa}"[:63]

    branch_id = None
    try:
        resposta = requests.post(
            f"{BASE_URL}/projects/{project_id}/branches", headers=cabecalhos,
            json={"branch": {"name": nome_branch}, "endpoints": [{"type": "read_write"}]},
            timeout=30,
        )
        if resposta.status_code >= 300:
            raise ContainerNaoDisponivel(f"Falha ao criar branch Neon (HTTP {resposta.status_code}).")
        branch_id = resposta.json()["branch"]["id"]
        endpoint_id = _aguardar_endpoint(project_id, branch_id, cabecalhos, timeout_disponibilidade_s)
        if not endpoint_id:
            raise ContainerNaoDisponivel("O ambiente Neon não ficou pronto dentro do prazo.")
        # O branch herda o banco pai. Restaurar em um banco novo impede que
        # tabelas herdadas sejam confundidas com dados recuperados do backup.
        banco = "cofrya_" + uuid.uuid4().hex
        resposta_db = requests.post(
            f"{BASE_URL}/projects/{project_id}/branches/{branch_id}/databases",
            headers=cabecalhos,
            json={"database": {"name": banco, "owner_name": "neondb_owner"}}, timeout=30,
        )
        if resposta_db.status_code >= 300:
            raise ContainerNaoDisponivel(f"Falha ao criar banco temporário (HTTP {resposta_db.status_code}).")
        uri = _obter_connection_uri(project_id, branch_id, cabecalhos, banco, endpoint_id)
        p = urlparse(uri)
        if p.scheme not in {"postgres", "postgresql"} or not p.hostname or not p.username or not p.password:
            raise ValueError("URI de conexão incompleta")
        # Recusa um URI de outro endpoint/banco ou com pool. Não altera o URI
        # para apontar ao banco pai, nem restaura em um destino herdado.
        if p.hostname.split(".")[0] != endpoint_id or unquote(p.path[1:]) != banco:
            raise ContainerNaoDisponivel("A conexão Neon não aponta diretamente ao banco temporário solicitado")
        instancia = InstanciaTemporaria(
            nome_container=branch_id, host=p.hostname, porta=p.port or 5432,
            usuario=unquote(p.username), senha=unquote(p.password), banco=banco, imagem="neon",
        )
        _aguardar_banco(instancia, timeout_disponibilidade_s)
        return instancia
    except (requests.RequestException, ValueError, KeyError, ContainerNaoDisponivel) as exc:
        limpeza = ""
        if branch_id:
            try:
                _remover_branch(project_id, branch_id, cabecalhos)
            except RuntimeError:
                limpeza = f" Remoção pendente do branch {branch_id}; confira o console Neon."
        detalhe = str(exc) if isinstance(exc, ContainerNaoDisponivel) else type(exc).__name__
        raise ContainerNaoDisponivel(f"Preparação Neon interrompida: {detalhe}.{limpeza}") from exc


def _aguardar_endpoint(project_id: str, branch_id: str, cabecalhos: dict, timeout_s: int) -> Optional[str]:
    """Retorna o endpoint de escrita ativo do branch recém-criado."""
    prazo = time.monotonic() + timeout_s
    while time.monotonic() < prazo:
        r = requests.get(
            f"{BASE_URL}/projects/{project_id}/branches/{branch_id}/endpoints",
            headers=cabecalhos, timeout=15,
        )
        if r.status_code < 300:
            lista = r.json().get("endpoints", [])
            for endpoint in lista:
                if (endpoint.get("branch_id") == branch_id
                        and endpoint.get("type") == "read_write"
                        and endpoint.get("current_state") == "active"):
                    return endpoint["id"]
        time.sleep(2)
    return None


def _obter_connection_uri(
    project_id: str, branch_id: str, cabecalhos: dict, banco: str, endpoint_id: str,
) -> str:
    r = requests.get(
        f"{BASE_URL}/projects/{project_id}/connection_uri",
        headers=cabecalhos,
        params={"branch_id": branch_id, "endpoint_id": endpoint_id,
                "database_name": banco, "role_name": "neondb_owner", "pooled": "false"},
        timeout=15,
    )
    if r.status_code >= 300:
        raise ContainerNaoDisponivel(f"Falha ao obter conexão Neon (HTTP {r.status_code}).")
    return r.json()["uri"]


def _aguardar_banco(instancia: InstanciaTemporaria, timeout_s: int) -> None:
    """Espera a conexão SQL real, pois endpoint ativo não garante banco pronto.

    Repete somente uma consulta de leitura. pg_restore nunca é repetido num
    banco parcialmente restaurado. A consulta fecha cada conexão ao terminar.
    """
    import psycopg2

    prazo = time.monotonic() + timeout_s
    while (restante := prazo - time.monotonic()) >= 2:
        try:
            _, linhas = executar_consulta(
                instancia, "SELECT current_database()", timeout_s=min(5, int(restante)),
            )
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            time.sleep(min(2, max(0, prazo - time.monotonic())))
            continue
        except psycopg2.Error as exc:
            raise ContainerNaoDisponivel(
                f"A confirmação SQL do banco temporário falhou ({type(exc).__name__})"
            ) from exc
        if linhas != [(instancia.banco,)]:
            raise ContainerNaoDisponivel("A conexão SQL Neon retornou um banco diferente do solicitado")
        return
    raise ContainerNaoDisponivel(
        "O banco temporário Neon não ficou acessível por conexão direta dentro do prazo"
    )


def derrubar_postgres_temporario(branch_id: str) -> None:
    """Remove o branch da tentativa. Mesma semântica de derrubar_postgres_temporario
    do docker_adapter: falha de limpeza é possível e não deve travar o restante
    do fluxo (seção 5.3 do TCC)."""
    chave, project_id = _obter_config()
    _remover_branch(project_id, branch_id, _cabecalhos(chave))


def _remover_branch(project_id: str, branch_id: str, cabecalhos: dict) -> None:
    try:
        resposta = requests.delete(
            f"{BASE_URL}/projects/{project_id}/branches/{branch_id}",
            headers=cabecalhos, timeout=30,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Limpeza Neon pendente: {branch_id}") from exc
    if resposta.status_code >= 300 and resposta.status_code != 404:
        raise RuntimeError(f"Limpeza Neon pendente: {branch_id} (HTTP {resposta.status_code})")
