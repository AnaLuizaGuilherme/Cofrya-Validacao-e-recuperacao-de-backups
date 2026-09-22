"""
Adaptador de arquivos (seção 5.3 do TCC).

Responsável por: acessar o repositório de backups somente para leitura, copiar
o arquivo submetido a exame para uma área protegida contra alterações do
agente que controla o repositório, e disponibilizar esses mesmos bytes para
a conferência de integridade e para a restauração.
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass


class ArquivoNaoEncontrado(Exception):
    pass


@dataclass
class CopiaProtegida:
    caminho_original: str
    caminho_protegido: str
    caminho_manifesto_protegido: str


def copiar_para_area_protegida(
    caminho_backup: str,
    caminho_manifesto: str,
    diretorio_tentativa: str,
) -> CopiaProtegida:
    """Copia o arquivo de backup e seu manifesto para o diretório da tentativa.

    A cópia é feita uma única vez por tentativa: todas as etapas subsequentes
    (hash, restauração) leem esses bytes protegidos, nunca o arquivo original
    do repositório, evitando condição de corrida com um agente que altere o
    repositório durante a execução.
    """
    if not os.path.isfile(caminho_backup):
        raise ArquivoNaoEncontrado(f"Backup não encontrado: {caminho_backup}")
    if not os.path.isfile(caminho_manifesto):
        raise ArquivoNaoEncontrado(f"Manifesto não encontrado: {caminho_manifesto}")

    os.makedirs(diretorio_tentativa, exist_ok=True)

    nome_backup = os.path.basename(caminho_backup)
    nome_manifesto = os.path.basename(caminho_manifesto)
    destino_backup = os.path.join(diretorio_tentativa, nome_backup)
    destino_manifesto = os.path.join(diretorio_tentativa, nome_manifesto)

    shutil.copy2(caminho_backup, destino_backup)
    shutil.copy2(caminho_manifesto, destino_manifesto)

    # Somente leitura após a cópia: reduz o risco de a própria execução do
    # verificador alterar acidentalmente os bytes sob exame.
    os.chmod(destino_backup, stat.S_IRUSR | stat.S_IRGRP)
    os.chmod(destino_manifesto, stat.S_IRUSR | stat.S_IRGRP)

    return CopiaProtegida(
        caminho_original=caminho_backup,
        caminho_protegido=destino_backup,
        caminho_manifesto_protegido=destino_manifesto,
    )


def limpar_diretorio_tentativa(diretorio_tentativa: str) -> None:
    """Remove somente os recursos da tentativa (seção 5.3: limpeza restrita)."""
    if os.path.isdir(diretorio_tentativa):
        # Restaura permissão de escrita antes de remover (arquivos foram
        # marcados somente-leitura acima).
        for raiz, _dirs, arquivos in os.walk(diretorio_tentativa):
            for nome in arquivos:
                caminho = os.path.join(raiz, nome)
                try:
                    os.chmod(caminho, stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
        shutil.rmtree(diretorio_tentativa, ignore_errors=False)
