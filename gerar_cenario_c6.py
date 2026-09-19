"""C6: cópia antiga ou diferente da autorizada (seção 4.4, Quadro 3 do TCC)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import generator, manifest as manifesto_mod, scenarios
from src.adapters import postgres_adapter


def main():
    parser = argparse.ArgumentParser(description="Gera um backup do cenário C6.")
    parser.add_argument("--semente", type=int, required=True)
    parser.add_argument("--volume", type=int, default=1000)
    parser.add_argument("--dsn-origem", required=True, help='Ex.: banco separado "pedidos_c6"')
    parser.add_argument("--saida", required=True)
    parser.add_argument("--chave-env", default="TCC_HMAC_KEY")
    parser.add_argument("--versao-aplicacao", default="0.1.0")
    parser.add_argument("--dias-de-idade", type=int, default=400,
                         help="Quantos dias no passado colocar como instante_captura (política padrão é 30 dias)")
    args = parser.parse_args()

    chave = os.environ.get(args.chave_env)
    if not chave:
        print(f"Erro: defina a variável de ambiente {args.chave_env} antes de rodar.")
        sys.exit(1)
    chave = chave.encode("utf-8")

    os.makedirs(args.saida, exist_ok=True)

    base = generator.gerar_base(semente=args.semente, volume_pedidos=args.volume)
    sql = generator.gerar_inserts(base)
    subprocess.run(["psql", args.dsn_origem, "-v", "ON_ERROR_STOP=1"], input=sql, text=True, check=True)

    id_copia = f"pedidos_seed{args.semente}_c6"
    caminho_backup = os.path.join(args.saida, f"{id_copia}.dump")
    postgres_adapter.criar_backup_customizado(args.dsn_origem, caminho_backup)

    m = manifesto_mod.construir_manifesto(
        caminho_arquivo=caminho_backup, id_copia=id_copia,
        versao_banco="PostgreSQL 18", versao_aplicacao=args.versao_aplicacao,
    )

    # Envelhece o instante_captura ANTES de assinar (senão invalidaria o HMAC).
    resultado_injecao = scenarios.envelhecer_manifesto(asdict(m), dias=args.dias_de_idade)
    m.instante_captura = resultado_injecao.metadados["instante_captura_novo"]
    print(f"Falha injetada: {resultado_injecao.alteracao_aplicada}")

    documento_assinado = manifesto_mod.assinar_manifesto(m, chave)
    caminho_manifesto = os.path.join(args.saida, f"{id_copia}.manifest.json")
    manifesto_mod.salvar_manifesto(documento_assinado, caminho_manifesto)

    caminho_referencias = os.path.join(args.saida, f"{id_copia}.referencias.json")
    with open(caminho_referencias, "w", encoding="utf-8") as f:
        json.dump(base.referencias_esperadas(), f, indent=2)

    print(f"\nBackup:      {caminho_backup}")
    print(f"Manifesto:   {caminho_manifesto}")
    print(f"Referências: {caminho_referencias}")
    print(f"\nAgora rode:\n  python -m src.cli verificar --copia-id {id_copia} --config B "
          f"--repositorio {args.saida} --saida .\\results")


if __name__ == "__main__":
    main()
