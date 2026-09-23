"""Reproduz a troca de cenário com ID antigo e a ausência de referências.

Os arquivos destes testes exercitam upload, HMAC e hash. Não são dumps reais
e não são submetidos a uma restauração PostgreSQL.
"""
import csv
import io
import json
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

import postgres_app
from src import manifest
from src.experiment_inputs import id_do_backup_enviado, validar_rotulo_da_copia, ler_referencias


def upload(nome, conteudo):
    arquivo = io.BytesIO(conteudo)
    arquivo.name = nome
    return arquivo


@pytest.fixture
def formulario(tmp_path, monkeypatch):
    tela = SimpleNamespace(erros=[], avisos=[], session_state={})
    tela.error = tela.erros.append
    tela.warning = tela.avisos.append
    tela.spinner = lambda *a: nullcontext()
    monkeypatch.setattr(postgres_app, 'st', tela)
    monkeypatch.setattr(postgres_app, 'obter_chave', lambda *a: b'chave-teste')
    monkeypatch.setattr(postgres_app, 'obter_valor', lambda *a: 'configurada')
    monkeypatch.setattr(postgres_app, 'trava_execucao', lambda: threading.Lock())
    monkeypatch.setattr(postgres_app.shutil, 'which', lambda nome: '/bin/'+nome)
    def arquivos(cenario, adulterar_hmac=False):
        copia_id = f'pedidos_seed1_{cenario.lower()}'
        original = tmp_path / (copia_id+'.dump')
        original.write_bytes(b'fixture de upload e integridade, sem restauracao')
        m = manifest.construir_manifesto(str(original), copia_id, '17', 'test')
        dados = original.read_bytes()
        if cenario == 'C1':
            dados = dados[:10]
        if cenario == 'C2':
            dados = bytes([dados[0] ^ 1])+dados[1:]
        assinado = manifest.assinar_manifesto(m, b'chave-teste')
        if adulterar_hmac:
            assinado['hmac'] = '0'*64
        return (upload(copia_id+'.dump', dados),
                upload(copia_id+'.manifest.json', json.dumps(assinado).encode()), None)
    def executar(cenario, configuracao='B', copia_id='pedidos_seed1_c0',
                 usar_nome=True, enviados=None):
        postgres_app.executar_formulario(
            tmp_path/'repo', tmp_path/'results', copia_id, configuracao, cenario,
            1, 30, '', '', 'TCC_HMAC_KEY', 'neon',
            uploads=enviados if enviados is not None else arquivos(cenario), usar_nome_backup=usar_nome,
        )
    return tela, arquivos, executar, tmp_path


@pytest.mark.parametrize('cenario,decisao', [('C0','aprovada'), ('C1','reprovada'), ('C2','reprovada'), ('C3','aprovada'), ('C4','aprovada')])
def test_upload_b_usa_id_do_arquivo_e_chega_ao_hash(formulario,cenario,decisao):
    tela,arquivos,executar,pasta=formulario
    executar(cenario)
    assert not tela.erros
    registro,salvo=tela.session_state['ultima_tentativa']
    assert salvo and registro.decisao==decisao
    assert registro.id_copia==f'pedidos_seed1_{cenario.lower()}'
    assert any(e['teste']=='identificador_autorizado' and e['aprovado'] for e in registro.evidencias)
    assert any(e['teste']=='integridade_hash' for e in registro.evidencias)
    if cenario in {'C1','C2'}:
        assert registro.motivo.startswith('Divergência de integridade')
    with (pasta/'results/resumo.csv').open() as f:
        linhas=list(csv.DictReader(f))
    assert len(linhas)==1 and linhas[0]['id_copia']==registro.id_copia


def test_upload_id_antigo_e_bloqueado_em_modo_manual(formulario):
    tela,arquivos,executar,pasta=formulario
    executar('C3', usar_nome=False)
    assert 'identifica C0' in tela.erros[0]
    assert 'ultima_tentativa' not in tela.session_state
    assert not (pasta/'repo').exists()
    assert not (pasta/'results').exists()


def test_rotulo_errado_nao_grava_tentativa_ate_mesmo_em_a(formulario):
    tela,arquivos,executar,pasta=formulario
    executar('C4', configuracao='A', enviados=arquivos('C3'))
    assert 'selecionado é C4' in tela.erros[0]
    assert 'ultima_tentativa' not in tela.session_state
    assert not (pasta/'repo').exists()


def test_upload_automatico_nao_ignora_hmac_invalido(formulario):
    tela,arquivos,executar,pasta=formulario
    executar('C3', enviados=arquivos('C3', adulterar_hmac=True))
    registro,_=tela.session_state['ultima_tentativa']
    assert registro.decisao=='reprovada' and 'autenticação' in registro.motivo


def test_upload_automatico_nao_reescreve_id_do_manifesto(formulario):
    tela,arquivos,executar,pasta=formulario
    dump,_,_=arquivos('C3')
    _,outro_manifesto,_=arquivos('C4')
    executar('C3', enviados=(dump,outro_manifesto,None))
    registro,_=tela.session_state['ultima_tentativa']
    assert registro.decisao=='reprovada' and 'ID da cópia divergente' in registro.motivo
    assert 'pedidos_seed1_c3' in registro.motivo and 'pedidos_seed1_c4' in registro.motivo


@pytest.mark.parametrize('conteudo', [None, b'', b'null', b'{}', b'[]', b'{invalido'])
def test_c_exige_referencias_antes_de_gravar_upload_ou_executar(formulario,monkeypatch,conteudo):
    tela,arquivos,executar,pasta=formulario
    monkeypatch.setattr(postgres_app,'executar_tentativa',lambda **k:pytest.fail('Não deve iniciar C sem referências'))
    dump,assinado,_=arquivos('C0')
    referencia=upload('pedidos_seed1_c0.referencias.json',conteudo) if conteudo is not None else None
    executar('C0',configuracao='C',enviados=(dump,assinado,referencia))
    assert tela.erros
    assert not (pasta/'repo').exists()
    assert 'ultima_tentativa' not in tela.session_state


@pytest.mark.parametrize('nome', ['../copia.dump', '/tmp/copia.dump', r'..\copia.dump', 'copia.sql', 'copia (1).dump'])
def test_nome_do_upload_nao_pode_virar_caminho_ou_id_ambiguo(nome):
    with pytest.raises(ValueError):
        id_do_backup_enviado(nome)


def test_semente_rotulo_e_referencias_validos():
    validar_rotulo_da_copia('pedidos_seed1_c3','C3',1)
    with pytest.raises(ValueError,match='semente'):
        validar_rotulo_da_copia('pedidos_seed1_c3','C3',2)
    contagens={k:0 for k in ['n_clientes','n_produtos','n_pedidos','n_itens','n_pagamentos']}
    assert ler_referencias(json.dumps(contagens).encode())==contagens
    contagens['n_pedidos']=False
    with pytest.raises(ValueError,match='n_pedidos'):
        ler_referencias(json.dumps(contagens).encode())
