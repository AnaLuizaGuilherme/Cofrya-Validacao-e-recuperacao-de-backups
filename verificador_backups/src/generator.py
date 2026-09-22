"""
Gerador de bases sintéticas (seção 4.3 do TCC).

Produz uma aplicação experimental de gestão de pedidos: clientes, produtos,
pedidos, itens e pagamentos, com chaves estrangeiras e a regra de negócio
"total declarado do pedido = soma dos itens" (usada no teste funcional C4).

A geração é determinística por semente: a mesma semente sempre produz a
mesma base, permitindo capturar referências de correção independentes do
arquivo de backup antes da inserção de qualquer falha.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import List

DUAS_CASAS = Decimal("0.01")


@dataclass
class Cliente:
    id: int
    nome: str
    email: str


@dataclass
class Produto:
    id: int
    nome: str
    preco: Decimal


@dataclass
class ItemPedido:
    id: int
    pedido_id: int
    produto_id: int
    quantidade: int
    preco_unitario: Decimal

    @property
    def subtotal(self) -> Decimal:
        return (self.preco_unitario * self.quantidade).quantize(DUAS_CASAS, rounding=ROUND_HALF_UP)


@dataclass
class Pedido:
    id: int
    cliente_id: int
    total_declarado: Decimal
    itens: List[ItemPedido] = field(default_factory=list)

    def total_calculado(self) -> Decimal:
        soma = sum((item.subtotal for item in self.itens), Decimal("0.00"))
        return soma.quantize(DUAS_CASAS, rounding=ROUND_HALF_UP)


@dataclass
class Pagamento:
    id: int
    pedido_id: int
    valor: Decimal
    metodo: str


@dataclass
class BaseSintetica:
    semente: int
    clientes: List[Cliente]
    produtos: List[Produto]
    pedidos: List[Pedido]
    pagamentos: List[Pagamento]

    def referencias_esperadas(self) -> dict:
        """Estado esperado, capturado ANTES de qualquer falha injetada.

        Mantido separado do arquivo de backup em si (seção 2.1: "as referências
        para avaliação funcional serão definidas de maneira independente do
        arquivo submetido ao teste").
        """
        return {
            "n_clientes": len(self.clientes),
            "n_produtos": len(self.produtos),
            "n_pedidos": len(self.pedidos),
            "n_itens": sum(len(p.itens) for p in self.pedidos),
            "n_pagamentos": len(self.pagamentos),
            "totais_por_pedido": {
                p.id: str(p.total_calculado()) for p in self.pedidos
            },
        }


def gerar_base(semente: int, volume_pedidos: int = 1000) -> BaseSintetica:
    rng = random.Random(semente)

    n_clientes = max(10, volume_pedidos // 5)
    clientes = [
        Cliente(id=i, nome=f"Cliente {i}", email=f"cliente{i}@exemplo.test")
        for i in range(1, n_clientes + 1)
    ]

    n_produtos = 50
    produtos = [
        Produto(
            id=i,
            nome=f"Produto {i}",
            preco=Decimal(rng.randrange(500, 50000)) / Decimal(100),
        )
        for i in range(1, n_produtos + 1)
    ]

    pedidos: List[Pedido] = []
    item_id_seq = 1
    for pedido_id in range(1, volume_pedidos + 1):
        cliente = rng.choice(clientes)
        n_itens = rng.randint(1, 5)
        itens = []
        for _ in range(n_itens):
            produto = rng.choice(produtos)
            quantidade = rng.randint(1, 4)
            item = ItemPedido(
                id=item_id_seq,
                pedido_id=pedido_id,
                produto_id=produto.id,
                quantidade=quantidade,
                preco_unitario=produto.preco,
            )
            itens.append(item)
            item_id_seq += 1

        total = sum((it.subtotal for it in itens), Decimal("0.00")).quantize(
            DUAS_CASAS, rounding=ROUND_HALF_UP
        )
        pedidos.append(
            Pedido(id=pedido_id, cliente_id=cliente.id, total_declarado=total, itens=itens)
        )

    pagamentos = [
        Pagamento(
            id=p.id,
            pedido_id=p.id,
            valor=p.total_declarado,
            metodo=rng.choice(["cartao", "pix", "boleto"]),
        )
        for p in pedidos
    ]

    return BaseSintetica(
        semente=semente,
        clientes=clientes,
        produtos=produtos,
        pedidos=pedidos,
        pagamentos=pagamentos,
    )


# --- DDL mínima da aplicação experimental (usada para popular o PostgreSQL) ---

DDL_ESQUEMA = """
CREATE TABLE IF NOT EXISTS clientes (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    email TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS produtos (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    preco NUMERIC(10,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS pedidos (
    id INTEGER PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES clientes(id),
    total_declarado NUMERIC(12,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS itens_pedido (
    id INTEGER PRIMARY KEY,
    pedido_id INTEGER NOT NULL REFERENCES pedidos(id),
    produto_id INTEGER NOT NULL REFERENCES produtos(id),
    quantidade INTEGER NOT NULL CHECK (quantidade > 0),
    preco_unitario NUMERIC(10,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS pagamentos (
    id INTEGER PRIMARY KEY,
    pedido_id INTEGER NOT NULL REFERENCES pedidos(id),
    valor NUMERIC(12,2) NOT NULL,
    metodo TEXT NOT NULL
);
"""


def gerar_inserts(base: BaseSintetica) -> str:
    """Gera SQL de inserção determinístico para popular o banco de origem."""
    linhas = [DDL_ESQUEMA]

    for c in base.clientes:
        linhas.append(
            f"INSERT INTO clientes (id, nome, email) VALUES "
            f"({c.id}, '{c.nome}', '{c.email}');"
        )
    for p in base.produtos:
        linhas.append(
            f"INSERT INTO produtos (id, nome, preco) VALUES "
            f"({p.id}, '{p.nome}', {p.preco});"
        )
    for pedido in base.pedidos:
        linhas.append(
            f"INSERT INTO pedidos (id, cliente_id, total_declarado) VALUES "
            f"({pedido.id}, {pedido.cliente_id}, {pedido.total_declarado});"
        )
        for item in pedido.itens:
            linhas.append(
                "INSERT INTO itens_pedido (id, pedido_id, produto_id, quantidade, preco_unitario) "
                f"VALUES ({item.id}, {item.pedido_id}, {item.produto_id}, "
                f"{item.quantidade}, {item.preco_unitario});"
            )
    for pag in base.pagamentos:
        linhas.append(
            f"INSERT INTO pagamentos (id, pedido_id, valor, metodo) VALUES "
            f"({pag.id}, {pag.pedido_id}, {pag.valor}, '{pag.metodo}');"
        )

    return "\n".join(linhas)
