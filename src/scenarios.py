"""
Cenários de falha C0 a C8 (seção 4.4, Quadro 3 do TCC).

Cada função de injeção documenta a alteração principal e retorna metadados
sobre o que foi alterado, para permitir atribuir os efeitos observados na
análise (seção 4.4: "cada cenário terá uma alteração principal documentada").
"""

from __future__ import annotations

import copy
import json
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional

from . import manifest as manifesto_mod


class Cenario(str, Enum):
    C0 = "C0"  # cópia válida e dentro da política
    C1 = "C1"  # arquivo truncado após a criação
    C2 = "C2"  # alteração de bytes após a criação
    C3 = "C3"  # exportação com tabela necessária omitida
    C4 = "C4"  # regra de negócio violada antes da exportação
    C5 = "C5"  # papel ou permissão requerida indisponível
    C6 = "C6"  # cópia antiga ou diferente da autorizada
    C7 = "C7"  # arquivo e manifesto adulterados sem chave válida
    C8 = "C8"  # ambiente de restauração impedido de iniciar


DESCRICAO_CENARIO = {
    Cenario.C0: "Cópia válida e dentro da política",
    Cenario.C1: "Arquivo truncado após a criação",
    Cenario.C2: "Alteração de bytes após a criação",
    Cenario.C3: "Exportação com tabela necessária omitida",
    Cenario.C4: "Regra de negócio violada antes da exportação",
    Cenario.C5: "Papel ou permissão requerida indisponível",
    Cenario.C6: "Cópia antiga ou diferente da autorizada",
    Cenario.C7: "Arquivo e manifesto adulterados sem chave válida",
    Cenario.C8: "Ambiente de restauração impedido de iniciar",
}

EVIDENCIA_ESPERADA = {
    Cenario.C0: "Aprovação com dados e operações corretos",
    Cenario.C1: "Alteração dos bytes ou falha de restauração",
    Cenario.C2: "Divergência de integridade",
    Cenario.C3: "Divergência do esquema ou conteúdo esperado",
    Cenario.C4: "Falha funcional apesar de arquivo íntegro",
    Cenario.C5: "Falha da dependência declarada de recuperação",
    Cenario.C6: "Violação de idade máxima ou identificador",
    Cenario.C7: "Rejeição da autenticação",
    Cenario.C8: "Inconclusivo nas configurações que dependem da restauração",
}


@dataclass
class ResultadoInjecao:
    cenario: Cenario
    alteracao_aplicada: str
    metadados: Dict[str, Any]


def truncar_arquivo(caminho: str, proporcao_mantida: float = 0.7) -> ResultadoInjecao:
    """C1: trunca o arquivo de backup após a criação (fora do manifesto)."""
    tamanho_original = os.path.getsize(caminho)
    novo_tamanho = int(tamanho_original * proporcao_mantida)
    with open(caminho, "r+b") as f:
        f.truncate(novo_tamanho)
    return ResultadoInjecao(
        cenario=Cenario.C1,
        alteracao_aplicada=f"Truncado de {tamanho_original} para {novo_tamanho} bytes",
        metadados={"tamanho_original": tamanho_original, "tamanho_novo": novo_tamanho},
    )


def adulterar_bytes(caminho: str, offset: Optional[int] = None) -> ResultadoInjecao:
    """C2: altera bytes no meio do arquivo, preservando o tamanho."""
    tamanho = os.path.getsize(caminho)
    if offset is None:
        offset = tamanho // 2
    with open(caminho, "r+b") as f:
        f.seek(offset)
        atual = f.read(1)
        novo_byte = bytes([(atual[0] + 1) % 256]) if atual else b"\x00"
        f.seek(offset)
        f.write(novo_byte)
    return ResultadoInjecao(
        cenario=Cenario.C2,
        alteracao_aplicada=f"Byte alterado no offset {offset}",
        metadados={"offset": offset},
    )


def omitir_tabela_na_exportacao(sql_insercao: str, tabela_omitida: str) -> ResultadoInjecao:
    """C3: remove do script de geração todas as instruções relativas a uma tabela,
    simulando uma exportação parcial (a ser aplicado ANTES de gerar o backup)."""
    linhas = sql_insercao.splitlines()
    padrao = f"INTO {tabela_omitida} "
    filtradas = [linha for linha in linhas if padrao not in linha]
    ddl_sem_tabela = "\n".join(filtradas)
    return ResultadoInjecao(
        cenario=Cenario.C3,
        alteracao_aplicada=f"Tabela '{tabela_omitida}' omitida da exportação",
        metadados={"sql_resultante": ddl_sem_tabela, "tabela_omitida": tabela_omitida},
    )


def violar_regra_de_negocio(base, id_pedido: Optional[int] = None) -> ResultadoInjecao:
    """C4: altera o total_declarado de um pedido para divergir da soma dos itens,
    SEM alterar esquema, identificadores ou contagens (aplicado antes da exportação)."""
    base = copy.deepcopy(base)
    if id_pedido is None:
        id_pedido = base.pedidos[0].id
    pedido = next(p for p in base.pedidos if p.id == id_pedido)
    total_original = pedido.total_declarado
    pedido.total_declarado = (pedido.total_declarado + Decimal("999.99"))
    return ResultadoInjecao(
        cenario=Cenario.C4,
        alteracao_aplicada=f"total_declarado do pedido {id_pedido} alterado sem refletir os itens",
        metadados={
            "id_pedido": id_pedido,
            "total_original": str(total_original),
            "total_adulterado": str(pedido.total_declarado),
            "base_alterada": base,
        },
    )


def declarar_dependencia_ausente(dependencias_cadastradas: list, dependencia_removida: str) -> ResultadoInjecao:
    """C5: remove deliberadamente uma dependência (papel/extensão) da lista
    fornecida ao adaptador de preparação — a ausência não deve ser contornada."""
    restantes = [d for d in dependencias_cadastradas if d != dependencia_removida]
    return ResultadoInjecao(
        cenario=Cenario.C5,
        alteracao_aplicada=f"Dependência '{dependencia_removida}' removida da preparação",
        metadados={"dependencias_restantes": restantes, "dependencia_removida": dependencia_removida},
    )


def envelhecer_manifesto(documento_manifesto: dict, dias: int = 400) -> ResultadoInjecao:
    """C6: define instante_captura no passado, além da política de idade máxima.

    Nota: esta função opera sobre o manifesto ANTES de assiná-lo, pois alterar
    um manifesto já assinado invalidaria o HMAC (o que corresponderia a C7,
    não a C6). Para simular C6 de forma realista, gere e assine o manifesto
    com instante_captura já no passado.
    """
    doc = copy.deepcopy(documento_manifesto)
    instante_antigo = datetime.now(timezone.utc) - timedelta(days=dias)
    doc["instante_captura"] = instante_antigo.isoformat()
    return ResultadoInjecao(
        cenario=Cenario.C6,
        alteracao_aplicada=f"instante_captura definido para {dias} dias atrás",
        metadados={"instante_captura_novo": doc["instante_captura"]},
    )


def adulterar_manifesto_sem_chave(caminho_manifesto: str) -> ResultadoInjecao:
    """C7: modifica o manifesto (ex.: sha256) sem recalcular o HMAC com a chave
    correta — simula um agente sem acesso à chave de autenticação."""
    with open(caminho_manifesto, "r", encoding="utf-8") as f:
        doc = json.load(f)
    doc["sha256"] = "0" * 64
    doc["hmac"] = "adulterado_sem_chave_valida"
    with open(caminho_manifesto, "w", encoding="utf-8") as f:
        json.dump(doc, f, sort_keys=True, indent=2)
    return ResultadoInjecao(
        cenario=Cenario.C7,
        alteracao_aplicada="sha256 e hmac substituídos sem a chave correta",
        metadados={},
    )


def impedir_ambiente_restauracao() -> ResultadoInjecao:
    """C8: simula impedimento do laboratório (ex.: imagem Docker inexistente
    ou indisponibilidade de recursos). A aplicação prática é passar uma
    imagem inválida ao adaptador Docker para esta tentativa."""
    return ResultadoInjecao(
        cenario=Cenario.C8,
        alteracao_aplicada="Ambiente de restauração configurado para falhar ao iniciar",
        metadados={"imagem_invalida": "postgres:versao-inexistente-para-teste"},
    )
