"""
Validadores do banco restaurado (seção 5.4 do TCC).

Três camadas, na ordem em que a configuração C as executa:
  1. Estrutura  — tabelas e objetos esperados existem.
  2. Conteúdo   — contagens e identificadores batem com a referência capturada
                  antes da inserção de qualquer falha.
  3. Negócio    — regras da aplicação (ex.: total do pedido = soma dos itens),
                  com representação decimal e arredondamento explícitos.

Cada divergência registra o teste, o valor esperado e o valor observado
(seção 5.4), para compor as evidências da tentativa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from .adapters.docker_adapter import InstanciaTemporaria
from .adapters.postgres_adapter import executar_consulta

DUAS_CASAS = Decimal("0.01")

TABELAS_ESPERADAS = ["clientes", "produtos", "pedidos", "itens_pedido", "pagamentos"]


@dataclass
class Evidencia:
    teste: str
    aprovado: bool
    valor_esperado: Any
    valor_observado: Any
    detalhe: str = ""


@dataclass
class ResultadoValidacao:
    aprovado: bool
    evidencias: List[Evidencia] = field(default_factory=list)

    def adicionar(self, evidencia: Evidencia) -> None:
        self.evidencias.append(evidencia)
        if not evidencia.aprovado:
            self.aprovado = False


def validar_estrutura(instancia: InstanciaTemporaria) -> ResultadoValidacao:
    resultado = ResultadoValidacao(aprovado=True)
    colunas, linhas = executar_consulta(
        instancia,
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';",
    )
    tabelas_presentes = {linha[0] for linha in linhas}

    for tabela in TABELAS_ESPERADAS:
        presente = tabela in tabelas_presentes
        resultado.adicionar(
            Evidencia(
                teste=f"tabela_presente:{tabela}",
                aprovado=presente,
                valor_esperado=True,
                valor_observado=presente,
                detalhe="Verificação de estrutura (seção 5.4)",
            )
        )
    return resultado


def validar_conteudo(
    instancia: InstanciaTemporaria, referencias_esperadas: Dict[str, Any]
) -> ResultadoValidacao:
    resultado = ResultadoValidacao(aprovado=True)

    contagens_esperadas = {
        "clientes": referencias_esperadas["n_clientes"],
        "produtos": referencias_esperadas["n_produtos"],
        "pedidos": referencias_esperadas["n_pedidos"],
        "itens_pedido": referencias_esperadas["n_itens"],
        "pagamentos": referencias_esperadas["n_pagamentos"],
    }

    for tabela, esperado in contagens_esperadas.items():
        try:
            _, linhas = executar_consulta(instancia, f"SELECT COUNT(*) FROM {tabela};")
            observado = linhas[0][0]
        except Exception as exc:  # tabela pode não existir (ligado a C3)
            observado = None
            resultado.adicionar(
                Evidencia(
                    teste=f"contagem:{tabela}",
                    aprovado=False,
                    valor_esperado=esperado,
                    valor_observado=f"erro: {exc}",
                )
            )
            continue

        resultado.adicionar(
            Evidencia(
                teste=f"contagem:{tabela}",
                aprovado=(observado == esperado),
                valor_esperado=esperado,
                valor_observado=observado,
            )
        )

    return resultado


def validar_regras_de_negocio(
    instancia: InstanciaTemporaria, referencias_esperadas: Dict[str, Any]
) -> ResultadoValidacao:
    """Teste de totalização: total_declarado do pedido == soma dos itens.

    Executado em consulta somente-leitura; não modifica dados (seção 5.4:
    "testes que modifiquem dados utilizarão transações revertidas").
    """
    resultado = ResultadoValidacao(aprovado=True)

    consulta = """
        SELECT p.id, p.total_declarado,
               COALESCE(SUM(i.quantidade * i.preco_unitario), 0) AS soma_itens
        FROM pedidos p
        LEFT JOIN itens_pedido i ON i.pedido_id = p.id
        GROUP BY p.id, p.total_declarado
        ORDER BY p.id;
    """
    try:
        _, linhas = executar_consulta(instancia, consulta)
    except Exception as exc:
        resultado.adicionar(
            Evidencia(
                teste="totalizacao_pedidos",
                aprovado=False,
                valor_esperado="consulta executável",
                valor_observado=f"erro: {exc}",
            )
        )
        return resultado

    divergencias = 0
    for id_pedido, total_declarado, soma_itens in linhas:
        total_declarado = Decimal(total_declarado).quantize(DUAS_CASAS, rounding=ROUND_HALF_UP)
        soma_itens = Decimal(soma_itens).quantize(DUAS_CASAS, rounding=ROUND_HALF_UP)
        aprovado = total_declarado == soma_itens
        if not aprovado:
            divergencias += 1
            resultado.adicionar(
                Evidencia(
                    teste=f"totalizacao_pedido:{id_pedido}",
                    aprovado=False,
                    valor_esperado=str(soma_itens),
                    valor_observado=str(total_declarado),
                    detalhe="total_declarado difere da soma dos itens",
                )
            )

    if divergencias == 0:
        resultado.adicionar(
            Evidencia(
                teste="totalizacao_pedidos",
                aprovado=True,
                valor_esperado=0,
                valor_observado=0,
                detalhe="Nenhuma divergência entre total declarado e soma dos itens",
            )
        )

    return resultado


def executar_validacao_funcional_completa(
    instancia: InstanciaTemporaria, referencias_esperadas: Dict[str, Any]
) -> ResultadoValidacao:
    """Agrega estrutura + conteúdo + negócio (usado pela configuração C)."""
    final = ResultadoValidacao(aprovado=True)
    for parcial in (
        validar_estrutura(instancia),
        validar_conteudo(instancia, referencias_esperadas),
        validar_regras_de_negocio(instancia, referencias_esperadas),
    ):
        final.evidencias.extend(parcial.evidencias)
        if not parcial.aprovado:
            final.aprovado = False
    return final
