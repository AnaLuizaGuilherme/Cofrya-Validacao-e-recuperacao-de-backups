"""Limites do protocolo e seleção independente da decisão experimental."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from src.cli import cli
from src.experiment_inputs import validar_rotulo_da_copia
from src.scenarios import Cenario
from scripts.consolidar_resultados import consolidar


def test_protocolo_e_catalogos_consistentes():
    raiz = Path(__file__).resolve().parents[1]
    assert [c.value for c in Cenario] == [f'C{i}' for i in range(8)]
    for nome in ('config/catalog.example.json', 'verificador_backups/config/catalog.example.json'):
        itens = json.loads((raiz / nome).read_text())
        assert {x['cenario'] for x in itens} == {c.value for c in Cenario}
        assert next(x for x in itens if x['cenario'] == 'C5')['dependencias'] == []


def test_cenario_fora_do_protocolo_recusado(tmp_path):
    fora = f'C{len(Cenario)}'
    with pytest.raises(ValueError):
        validar_rotulo_da_copia('pedidos_seed1_'+fora.lower(), fora, 1)
    resultado = CliRunner().invoke(cli, ['verificar', '--copia-id', 'pedidos_seed1_c0',
        '--config', 'A', '--repositorio', str(tmp_path), '--cenario', fora])
    assert resultado.exit_code == 2


def test_selecao_preserva_primeira_tentativa_independente_da_decisao():
    base = dict(cenario='C0', semente='1', configuracao='B', id_copia='pedidos_seed1_c0',
                versao_codigo='0.2.2', provedor_ambiente='neon')
    primeira = dict(base, id_tentativa='primeira', instante_inicio_utc='2026-09-23T20:00:00+00:00', decisao='reprovada')
    segunda = dict(base, id_tentativa='segunda', instante_inicio_utc='2026-09-23T20:01:00+00:00', decisao='aprovada')
    erro = dict(primeira, id_tentativa='entrada_errada', id_copia='Oii')
    retidas, auditoria = consolidar([segunda, erro, primeira])
    assert retidas == [primeira]
    assert {a['selecao'] for a in auditoria} == {'incluida', 'repeticao', 'identificacao_incompativel'}
