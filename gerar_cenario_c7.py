"""C7: arquivo e manifesto adulterados sem chave válida (seção 4.4, Quadro 3 do TCC).

Reaproveita um backup já existente (ex.: pedidos_seed1_c0) copiando-o para um
novo identificador e corrompendo a cópia do manifesto, sem ter acesso à
chave HMAC correta — simula um agente que só consegue mexer no repositório,
não no domínio confiável (seção 4.4).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import scenarios


def main():
    parser = argparse.ArgumentParser(description="Gera um backup do cenário C7 a partir de um C0 existente.")
    parser.add_argument("--copia-base", required=True, help="id_copia de um backup válido já existente, ex.: pedidos_seed1_c0")
    parser.add_argument("--repositorio", required=True)
    parser.add_argument("--semente", type=int, required=True)
    args = parser.parse_args()

    id_novo = f"pedidos_seed{args.semente}_c7"
    origem_backup = os.path.join(args.repositorio, f"{args.copia_base}.dump")
    origem_manifesto = os.path.join(args.repositorio, f"{args.copia_base}.manifest.json")
    destino_backup = os.path.join(args.repositorio, f"{id_novo}.dump")
    destino_manifesto = os.path.join(args.repositorio, f"{id_novo}.manifest.json")

    if not os.path.isfile(origem_backup) or not os.path.isfile(origem_manifesto):
        print(f"Erro: não achei {origem_backup} ou {origem_manifesto}. Gere um C0 primeiro.")
        sys.exit(1)

    shutil.copy2(origem_backup, destino_backup)
    shutil.copy2(origem_manifesto, destino_manifesto)

    resultado = scenarios.adulterar_manifesto_sem_chave(destino_manifesto)
    print(f"Falha injetada: {resultado.alteracao_aplicada}")

    print(f"\nBackup:    {destino_backup}")
    print(f"Manifesto: {destino_manifesto} (adulterado)")
    print(f"\nAgora rode:\n  python -m src.cli verificar --copia-id {id_novo} --config B "
          f"--repositorio {args.repositorio} --saida .\\results")


if __name__ == "__main__":
    main()
