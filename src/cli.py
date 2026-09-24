"""
Interface de linha de comando (seção 5.6 do TCC).

Comandos:
  gerar-base   -> gera base sintética + backup válido (C0) + manifesto assinado
  verificar    -> executa uma única tentativa (uma cópia, uma configuração)
  matriz       -> executa a matriz cenários x configurações x sementes
"""

from __future__ import annotations

import json
import os

import click

from . import generator, manifest as manifesto_mod
from .adapters import postgres_adapter
from .executor import (
    Configuracao,
    ConfiguracaoExecucao,
    EntradaCatalogo,
    PoliticaTemporal,
    executar_tentativa,
)
from .results import RegistradorCSV
from .scenarios import Cenario


def _obter_chave(nome_variavel_env: str) -> bytes:
    valor = os.environ.get(nome_variavel_env)
    if not valor:
        raise click.ClickException(
            f"Variável de ambiente {nome_variavel_env} não definida. "
            "A chave HMAC deve ficar fora do repositório de backups (seção 4.4)."
        )
    return valor.encode("utf-8")


@click.group()
def cli():
    """Verificador automatizado de integridade e recuperabilidade de backups PostgreSQL."""


@cli.command("gerar-base")
@click.option("--semente", type=int, required=True)
@click.option("--volume", type=int, default=1000, show_default=True)
@click.option("--saida", type=click.Path(), required=True, help="Diretório do repositório de backups")
@click.option("--dsn-origem", default=None, help="DSN do PostgreSQL de origem para gerar o backup real com pg_dump")
@click.option("--chave-env", default="TCC_HMAC_KEY", show_default=True)
@click.option("--versao-aplicacao", default="0.1.0", show_default=True)
def gerar_base(semente, volume, saida, dsn_origem, chave_env, versao_aplicacao):
    """Gera base sintética, popula o banco de origem (se DSN fornecido),
    cria o backup com pg_dump e assina o manifesto (cenário C0)."""
    os.makedirs(saida, exist_ok=True)
    base = generator.gerar_base(semente=semente, volume_pedidos=volume)
    id_copia = f"pedidos_seed{semente}_c0"
    caminho_backup = os.path.join(saida, f"{id_copia}.dump")
    caminho_manifesto = os.path.join(saida, f"{id_copia}.manifest.json")
    caminho_referencias = os.path.join(saida, f"{id_copia}.referencias.json")

    if dsn_origem:
        sql = generator.gerar_inserts(base)
        # Popula via psql (lista de argumentos, seção 5.4).
        import subprocess
        subprocess.run(
            ["psql", dsn_origem, "-v", "ON_ERROR_STOP=1"],
            input=sql, text=True, check=True,
        )
        postgres_adapter.criar_backup_customizado(dsn_origem, caminho_backup)
    else:
        click.echo(
            "Aviso: --dsn-origem não fornecido; gerando apenas SQL de referência "
            f"em {caminho_backup}.sql (sem backup binário real)."
        )
        with open(caminho_backup + ".sql", "w", encoding="utf-8") as f:
            f.write(generator.gerar_inserts(base))
        # Cria um arquivo placeholder para permitir testar o fluxo de manifesto/hash.
        with open(caminho_backup, "wb") as f:
            f.write(b"PLACEHOLDER_BACKUP_SEM_DSN_ORIGEM")

    chave = _obter_chave(chave_env)
    m = manifesto_mod.construir_manifesto(
        caminho_arquivo=caminho_backup,
        id_copia=id_copia,
        versao_banco="PostgreSQL 18",
        versao_aplicacao=versao_aplicacao,
    )
    documento_assinado = manifesto_mod.assinar_manifesto(m, chave)
    manifesto_mod.salvar_manifesto(documento_assinado, caminho_manifesto)

    with open(caminho_referencias, "w", encoding="utf-8") as f:
        json.dump(base.referencias_esperadas(), f, indent=2)

    click.echo(f"Backup:      {caminho_backup}")
    click.echo(f"Manifesto:   {caminho_manifesto}")
    click.echo(f"Referências: {caminho_referencias}")


@cli.command("verificar")
@click.option("--copia-id", required=True)
@click.option("--config", "configuracao", required=True,
              type=click.Choice([c.value for c in Configuracao]))
@click.option("--repositorio", required=True, type=click.Path(exists=True))
@click.option("--chave-env", default="TCC_HMAC_KEY", show_default=True)
@click.option("--idade-maxima-dias", default=30, show_default=True)
@click.option("--saida", default="./results", show_default=True)
@click.option("--cenario", default="C0", show_default=True, type=click.Choice([c.value for c in Cenario]))
@click.option("--semente", type=int, default=None, help="Semente experimental; inferida de seedN no ID quando omitida.")
@click.option("--dependencias", default="", help="Lista separada por vírgula de papéis a criar antes da restauração (cenário C5)")
@click.option("--imagem-postgres", default=None, help="Sobrescreve a imagem PostgreSQL do ambiente Docker")
@click.option("--provedor-ambiente", default="docker", type=click.Choice(["docker", "neon"]),
              help="docker (padrão, local) ou neon (nuvem, sem Docker — requer NEON_API_KEY/NEON_PROJECT_ID)")
def verificar(copia_id, configuracao, repositorio, chave_env, idade_maxima_dias, saida, cenario, semente, dependencias, imagem_postgres, provedor_ambiente):
    """Executa uma única tentativa de verificação."""
    caminho_backup = os.path.join(repositorio, f"{copia_id}.dump")
    caminho_manifesto = os.path.join(repositorio, f"{copia_id}.manifest.json")
    caminho_referencias = os.path.join(repositorio, f"{copia_id}.referencias.json")

    referencias = None
    if os.path.isfile(caminho_referencias):
        with open(caminho_referencias, "r", encoding="utf-8") as f:
            referencias = json.load(f)

    lista_dependencias = [d.strip() for d in dependencias.split(",") if d.strip()]

    entrada = EntradaCatalogo(
        id_copia=copia_id,
        caminho_backup=caminho_backup,
        caminho_manifesto=caminho_manifesto,
        localizacao_autorizada=repositorio,
        dependencias=lista_dependencias,
    )
    contexto = ConfiguracaoExecucao(
        chave_hmac=_obter_chave(chave_env),
        diretorio_trabalho="./tentativas",
        diretorio_saida_csv=saida,
        provedor_ambiente=provedor_ambiente,
    )
    registro = executar_tentativa(
        entrada=entrada,
        configuracao=Configuracao(configuracao),
        politica=PoliticaTemporal(idade_maxima_dias=idade_maxima_dias),
        contexto=contexto,
        referencias_esperadas=referencias,
        cenario=cenario,
        semente=semente,
        imagem_postgres_override=imagem_postgres,
    )
    RegistradorCSV(saida).gravar(registro)
    click.echo(f"Decisão: {registro.decisao} — {registro.motivo}")
    click.echo(f"id_tentativa: {registro.id_tentativa}")


@cli.command("matriz")
@click.option("--repositorio", required=True, type=click.Path(exists=True))
@click.option("--catalogo", required=True, type=click.Path(exists=True),
              help="JSON com a lista de cópias por cenário/semente a executar")
@click.option("--configs", multiple=True, default=[c.value for c in Configuracao])
@click.option("--chave-env", default="TCC_HMAC_KEY", show_default=True)
@click.option("--saida", default="./results", show_default=True)
def matriz(repositorio, catalogo, configs, chave_env, saida):
    """Executa a matriz cenários x configurações x sementes descrita no catálogo.

    Formato esperado do catálogo (JSON): lista de objetos
    {"id_copia": ..., "cenario": ..., "semente": ..., "dependencias": [...]}
    """
    with open(catalogo, "r", encoding="utf-8") as f:
        itens = json.load(f)

    contexto = ConfiguracaoExecucao(
        chave_hmac=_obter_chave(chave_env),
        diretorio_trabalho="./tentativas",
        diretorio_saida_csv=saida,
    )
    # Validar o catálogo completo antes de iniciar qualquer restauração.
    for item in itens:
        Cenario(item.get("cenario", "C0"))
    registrador = RegistradorCSV(saida)

    for item in itens:
        id_copia = item["id_copia"]
        cenario = item.get("cenario", "C0")
        semente = item.get("semente")
        dependencias = item.get("dependencias", [])

        caminho_backup = os.path.join(repositorio, f"{id_copia}.dump")
        caminho_manifesto = os.path.join(repositorio, f"{id_copia}.manifest.json")
        caminho_referencias = os.path.join(repositorio, f"{id_copia}.referencias.json")

        referencias = None
        if os.path.isfile(caminho_referencias):
            with open(caminho_referencias, "r", encoding="utf-8") as f:
                referencias = json.load(f)

        entrada = EntradaCatalogo(
            id_copia=id_copia,
            caminho_backup=caminho_backup,
            caminho_manifesto=caminho_manifesto,
            localizacao_autorizada=repositorio,
            dependencias=dependencias,
        )

        for config_str in configs:
            registro = executar_tentativa(
                entrada=entrada,
                configuracao=Configuracao(config_str),
                politica=PoliticaTemporal(),
                contexto=contexto,
                referencias_esperadas=referencias,
                cenario=cenario,
                semente=semente,
            )
            registrador.gravar(registro)
            click.echo(
                f"[{cenario}/{config_str}/seed{semente}] {registro.decisao} — {registro.motivo}"
            )


if __name__ == "__main__":
    cli()

