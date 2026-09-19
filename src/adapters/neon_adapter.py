"""
Adaptador Neon (seção 5.5 do TCC, adaptado — ver nota de desvio abaixo).

Alternativa ao adaptador Docker (docker_adapter.py) para ambientes sem
Docker disponível, como o Streamlit Community Cloud. Usa a API do Neon
(https://neon.tech) para criar um branch de banco de dados isolado por
tentativa — o equivalente, neste contexto, a subir e derrubar um contêiner
PostgreSQL temporário. O branch é removido ao final da tentativa, do mesmo
jeito que o contêiner Docker é destruído.

Requer duas variáveis de ambiente (ou st.secrets, se rodando no Streamlit):
  NEON_API_KEY     - chave de API pessoal do Neon (console.neon.tech -> Account -> API keys)
  NEON_PROJECT_ID  - id do projeto Neon já criado (contém o branch base "main")

NOTA DE DESVIO DA ARQUITETURA DO TCC: a seção 5.5 descreve isolamento via
contêineres Docker locais, com limites explícitos de CPU/memória e sem
dependência de rede externa. Usar o Neon troca esse isolamento local por um
provedor de nuvem terceiro — o modelo de ameaça muda (a rede e o provedor
passam a fazer parte da superfície de confiança) e os limites de CPU/memória
deixam de ser configuráveis pelo verificador. Esta via é recomendada apenas
para demonstração pública hospedada, não como substituição do protocolo
experimental descrito no TCC.
"""

from __future__ import annotations

import os
import time
from typing import Optional
from urllib.parse import urlparse

import requests

from .docker_adapter import ContainerNaoDisponivel, InstanciaTemporaria

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

    Levanta ContainerNaoDisponivel nos mesmos casos em que o adaptador
    Docker levantaria — permitindo reproduzir o cenário C8 (ambiente de
    restauração impedido de iniciar) também nesta via, por exemplo com
    NEON_PROJECT_ID inválido.
    """
    chave, project_id = _obter_config()
    cabecalhos = _cabecalhos(chave)

    nome_branch = f"tentativa-{id_tentativa}"[:63]

    resposta = requests.post(
        f"{BASE_URL}/projects/{project_id}/branches",
        headers=cabecalhos,
        json={"branch": {"name": nome_branch}, "endpoints": [{"type": "read_write"}]},
        timeout=30,
    )
    if resposta.status_code >= 300:
        raise ContainerNaoDisponivel(f"Falha ao criar branch Neon: {resposta.text}")

    dados = resposta.json()
    branch_id = dados["branch"]["id"]

    endpoint_pronto = _aguardar_endpoint(project_id, branch_id, cabecalhos, timeout_disponibilidade_s)
    if not endpoint_pronto:
        _remover_branch(project_id, branch_id, cabecalhos)
        raise ContainerNaoDisponivel(
            f"Branch Neon não ficou pronto em {timeout_disponibilidade_s}s "
            f"(cenário compatível com C8)."
        )

    uri = _obter_connection_uri(project_id, branch_id, cabecalhos)
    p = urlparse(uri)

    return InstanciaTemporaria(
        nome_container=branch_id,  # reaproveita o campo para guardar o id do branch Neon
        host=p.hostname,
        porta=p.port or 5432,
        usuario=p.username,
        senha=p.password,
        banco=(p.path.lstrip("/").split("?")[0] or "neondb"),
        imagem="neon",  # sinaliza para o postgres_adapter usar sslmode=require
    )


def _aguardar_endpoint(project_id: str, branch_id: str, cabecalhos: dict, timeout_s: int) -> bool:
    prazo = time.time() + timeout_s
    while time.time() < prazo:
        r = requests.get(
            f"{BASE_URL}/projects/{project_id}/branches/{branch_id}/endpoints",
            headers=cabecalhos, timeout=15,
        )
        if r.status_code < 300:
            lista = r.json().get("endpoints", [])
            if lista and lista[0].get("current_state") == "active":
                return True
        time.sleep(2)
    return False


def _obter_connection_uri(project_id: str, branch_id: str, cabecalhos: dict) -> str:
    r = requests.get(
        f"{BASE_URL}/projects/{project_id}/connection_uri",
        headers=cabecalhos,
        params={"branch_id": branch_id, "database_name": "neondb", "role_name": "neondb_owner"},
        timeout=15,
    )
    if r.status_code >= 300:
        raise ContainerNaoDisponivel(f"Falha ao obter connection_uri do Neon: {r.text}")
    return r.json()["uri"]


def derrubar_postgres_temporario(branch_id: str) -> None:
    """Remove o branch da tentativa. Mesma semântica de derrubar_postgres_temporario
    do docker_adapter: falha de limpeza é possível e não deve travar o restante
    do fluxo (seção 5.3 do TCC)."""
    try:
        chave, project_id = _obter_config()
    except ContainerNaoDisponivel:
        return
    _remover_branch(project_id, branch_id, _cabecalhos(chave))


def _remover_branch(project_id: str, branch_id: str, cabecalhos: dict) -> None:
    try:
        requests.delete(
            f"{BASE_URL}/projects/{project_id}/branches/{branch_id}",
            headers=cabecalhos, timeout=30,
        )
    except requests.RequestException:
        pass  # falha de limpeza registrada pelo chamador, não interrompe o fluxo
