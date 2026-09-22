"""
Script auxiliar: gera um backup do cenário C4 (regra de negócio violada,
seção 4.4 / Quadro 3 do TCC).

Diferente do comando `gerar-base` do CLI (que só produz cópias válidas, C0),
este script usa diretamente `src.scenarios.violar_regra_de_negocio` para
alterar o total_declarado de um pedido ANTES da exportação, preservando
esquema, identificadores e contagens — exatamente como o cenário exige.

Uso:
    python gerar_cenario_c4.py --semente 1 --volume 1000 \
        --dsn-origem "postgresql://postgres:SENHA@localhost:5432/pedidos_c4" \
        --saida .\repositorio

Observação: usa um banco de origem SEPARADO do banco usado para C0
(ex.: "pedidos_c4" em vez de "pedidos"), para não misturar dados corretos
com dados alterados no mesmo banco. Crie-o antes com:
    psql -U postgres -c "CREATE DATABASE pedidos_c4;"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import generator, manifest as manifesto_mod, scenarios
from src.adapters import postgres_adapter


def main():
    parser = argparse.ArgumentParser(description="Gera um backup do cenário C4.")
    parser.add_argument("--semente", type=int, required=True)
    parser.add_argument("--volume", type=int, default=1000)
    parser.add_argument("--dsn-origem", required=True,
                         help='Ex.: "postgresql://postgres:SENHA@localhost:5432/pedidos_c4"')
    parser.add_argument("--saida", required=True, help="Diretório do repositório de backups")
    parser.add_argument("--chave-env", default="TCC_HMAC_KEY")
    parser.add_argument("--versao-aplicacao", default="0.1.0")
    args = parser.parse_args()

    chave = os.environ.get(args.chave_env)
    if not chave:
        print(f"Erro: defina a variável de ambiente {args.chave_env} antes de rodar.")
        sys.exit(1)
    chave = chave.encode("utf-8")

    os.makedirs(args.saida, exist_ok=True)

    # 1. Gera a base sintética válida.
    base = generator.gerar_base(semente=args.semente, volume_pedidos=args.volume)

    # 2. Injeta a falha do cenário C4 (regra de negócio violada).
    id_pedido_alvo = base.pedidos[0].id
    resultado_injecao = scenarios.violar_regra_de_negocio(base, id_pedido=id_pedido_alvo)
    base_alterada = resultado_injecao.metadados["base_alterada"]
    print(f"Falha injetada: {resultado_injecao.alteracao_aplicada}")
    print(f"  total original:   {resultado_injecao.metadados['total_original']}")
    print(f"  total adulterado: {resultado_injecao.metadados['total_adulterado']}")

    # 3. Popula o banco de origem com a base JÁ ALTERADA.
    sql = generator.gerar_inserts(base_alterada)
    subprocess.run(
        ["psql", args.dsn_origem, "-v", "ON_ERROR_STOP=1"],
        input=sql, text=True, check=True,
    )

    # 4. Gera o backup a partir do banco alterado.
    id_copia = f"pedidos_seed{args.semente}_c4"
    caminho_backup = os.path.join(args.saida, f"{id_copia}.dump")
    postgres_adapter.criar_backup_customizado(args.dsn_origem, caminho_backup)

    # 5. Assina o manifesto normalmente — o manifesto é autêntico e íntegro;
    #    é isso que caracteriza C4 ("arquivo pode ser produzido corretamente
    #    e receber um manifesto autêntico", seção 4.4).
    m = manifesto_mod.construir_manifesto(
        caminho_arquivo=caminho_backup,
        id_copia=id_copia,
        versao_banco="PostgreSQL 18",
        versao_aplicacao=args.versao_aplicacao,
    )
    documento_assinado = manifesto_mod.assinar_manifesto(m, chave)
    caminho_manifesto = os.path.join(args.saida, f"{id_copia}.manifest.json")
    manifesto_mod.salvar_manifesto(documento_assinado, caminho_manifesto)

    # 6. As referências esperadas usam a base ORIGINAL (correta) — contagens
    #    não mudam em C4, então isso não atrapalha os testes de estrutura e
    #    conteúdo. O teste de negócio compara o banco restaurado com ele
    #    mesmo (total_declarado vs. soma dos itens), então a falha aparece
    #    de qualquer forma.
    caminho_referencias = os.path.join(args.saida, f"{id_copia}.referencias.json")
    with open(caminho_referencias, "w", encoding="utf-8") as f:
        json.dump(base.referencias_esperadas(), f, indent=2)

    print(f"\nBackup:      {caminho_backup}")
    print(f"Manifesto:   {caminho_manifesto}")
    print(f"Referências: {caminho_referencias}")
    print(f"\nAgora rode:\n  python -m src.cli verificar --copia-id {id_copia} --config C "
          f"--repositorio {args.saida} --saida .\\results")


if __name__ == "__main__":
    main()
