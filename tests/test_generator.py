from decimal import Decimal

from src import generator


def test_geracao_e_deterministica_pela_semente():
    base_a = generator.gerar_base(semente=42, volume_pedidos=50)
    base_b = generator.gerar_base(semente=42, volume_pedidos=50)
    assert base_a.referencias_esperadas() == base_b.referencias_esperadas()


def test_sementes_diferentes_produzem_bases_diferentes():
    base_a = generator.gerar_base(semente=1, volume_pedidos=50)
    base_b = generator.gerar_base(semente=2, volume_pedidos=50)
    assert base_a.referencias_esperadas() != base_b.referencias_esperadas()


def test_total_declarado_bate_com_soma_dos_itens_na_base_valida():
    base = generator.gerar_base(semente=7, volume_pedidos=30)
    for pedido in base.pedidos:
        assert pedido.total_declarado == pedido.total_calculado()


def test_violacao_de_regra_de_negocio_c4_nao_altera_esquema_nem_contagens():
    from src.scenarios import violar_regra_de_negocio

    base = generator.gerar_base(semente=9, volume_pedidos=20)
    referencias_antes = base.referencias_esperadas()

    resultado = violar_regra_de_negocio(base, id_pedido=base.pedidos[0].id)
    base_alterada = resultado.metadados["base_alterada"]
    referencias_depois = base_alterada.referencias_esperadas()

    # Contagens preservadas (C4: "preservará o esquema, os identificadores e
    # as contagens e violará uma regra").
    assert referencias_depois["n_pedidos"] == referencias_antes["n_pedidos"]
    assert referencias_depois["n_itens"] == referencias_antes["n_itens"]

    pedido_alterado = next(p for p in base_alterada.pedidos if p.id == base.pedidos[0].id)
    assert pedido_alterado.total_declarado != pedido_alterado.total_calculado()
