"""
Adaptador de contêineres (seção 5.5 do TCC).

Cria e destrói uma instância PostgreSQL temporária por tentativa, via CLI do
Docker chamada com listas de argumentos (nunca montagem de comando de shell a
partir de entrada livre — seção 5.4). Limites explícitos de CPU e memória são
aplicados porque o Docker não os impõe por padrão.
"""

from __future__ import annotations

import secrets
import subprocess
import time
from dataclasses import dataclass
from typing import Optional


class ContainerNaoDisponivel(Exception):
    pass


@dataclass
class InstanciaTemporaria:
    nome_container: str
    host: str
    porta: int
    usuario: str
    senha: str
    banco: str
    imagem: str


def _executar(cmd: list, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def subir_postgres_temporario(
    id_tentativa: str,
    imagem: str = "postgres:18",
    limite_cpu: str = "1.0",
    limite_memoria: str = "1g",
    porta_host: Optional[int] = None,
    timeout_disponibilidade_s: int = 60,
) -> InstanciaTemporaria:
    """Sobe um contêiner PostgreSQL isolado e aguarda disponibilidade.

    Levanta ContainerNaoDisponivel se o contêiner não ficar pronto dentro do
    tempo limite — usado para simular/detectar o cenário C8 (ambiente de
    restauração impedido de iniciar).
    """
    nome_container = f"tcc-pg-{id_tentativa}"
    senha = secrets.token_urlsafe(16)
    usuario = "verificador"
    banco = "pedidos"
    porta_container = 5432

    cmd = [
        "docker", "run", "-d",
        "--name", nome_container,
        "--rm",
        "--cpus", limite_cpu,
        "--memory", limite_memoria,
        "-e", f"POSTGRES_USER={usuario}",
        "-e", f"POSTGRES_PASSWORD={senha}",
        "-e", f"POSTGRES_DB={banco}",
    ]
    if porta_host:
        cmd += ["-p", f"127.0.0.1:{porta_host}:{porta_container}"]
    else:
        cmd += ["-p", f"127.0.0.1::{porta_container}"]  # acesso somente local
    cmd.append(imagem)

    try:
        resultado = _executar(cmd, timeout=30)
        if resultado.returncode != 0:
            raise ContainerNaoDisponivel("Docker não conseguiu iniciar o ambiente temporário.")
        porta_publicada = porta_host or _descobrir_porta_publicada(nome_container, porta_container)
        if not _aguardar_disponibilidade(nome_container, usuario, banco, timeout_disponibilidade_s):
            raise ContainerNaoDisponivel("PostgreSQL não ficou disponível dentro do prazo.")
    except (OSError, subprocess.SubprocessError, ValueError, ContainerNaoDisponivel) as exc:
        try:
            derrubar_postgres_temporario(nome_container)
        except Exception:
            raise ContainerNaoDisponivel(f"Preparação interrompida; confira a limpeza de {nome_container}.") from exc
        raise ContainerNaoDisponivel(f"Preparação interrompida ({type(exc).__name__}).") from exc

    return InstanciaTemporaria(
        nome_container=nome_container,
        host="127.0.0.1",
        porta=porta_publicada,
        usuario=usuario,
        senha=senha,
        banco=banco,
        imagem=imagem,
    )


def _descobrir_porta_publicada(nome_container: str, porta_container: int) -> int:
    cmd = ["docker", "port", nome_container, str(porta_container)]
    resultado = _executar(cmd, timeout=10)
    if resultado.returncode != 0 or not resultado.stdout.strip():
        raise ContainerNaoDisponivel("Não foi possível determinar a porta publicada.")
    # saída típica: "0.0.0.0:32768"
    return int(resultado.stdout.strip().splitlines()[0].rsplit(":", 1)[-1])


def _aguardar_disponibilidade(
    nome_container: str, usuario: str, banco: str, timeout_s: int
) -> bool:
    prazo = time.time() + timeout_s
    while time.time() < prazo:
        resultado = _executar(
            ["docker", "exec", nome_container, "pg_isready", "-U", usuario, "-d", banco],
            timeout=10,
        )
        if resultado.returncode == 0:
            return True
        time.sleep(1)
    return False


def derrubar_postgres_temporario(nome_container: str) -> None:
    """Remove o contêiner da tentativa. Falha de limpeza é registrada, não
    silenciada e não deve apagar uma decisão já produzida (seção 5.3)."""
    resultado = _executar(["docker", "rm", "-f", nome_container], timeout=30)
    if resultado.returncode != 0 and "No such container" not in resultado.stderr:
        raise RuntimeError(f"Limpeza Docker pendente: {nome_container}")

