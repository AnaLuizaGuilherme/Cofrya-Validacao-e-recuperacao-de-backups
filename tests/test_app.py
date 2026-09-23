from pathlib import Path
import csv
import shutil

import pytest
from streamlit.testing.v1 import AppTest
from src import auth, manifest
from src.results import RegistroTentativa

RAIZ=Path(__file__).resolve().parents[1]


@pytest.fixture
def contas(tmp_path,monkeypatch):
    import cofrya_app
    monkeypatch.setattr(cofrya_app,'DADOS',tmp_path)
    monkeypatch.setattr(cofrya_app,'CAMINHO_CONTAS',tmp_path/'contas.db')
    return cofrya_app


@pytest.mark.parametrize('entrada',['streamlit_app.py','cofrya_app.py','verificador_backups/streamlit_app.py','verificador_backups/cofrya_app.py','verificador_backups/arquivos_app.py'])
def test_entradas_abrem_com_login(entrada,contas):
    app=AppTest.from_file(str(RAIZ/entrada)).run()
    assert not app.exception
    assert app.text_input(key='login_usuario')
    assert 'usuario_logado' not in app.session_state


def clicar(app,label):
    return next(b for b in app.button if b.label==label).click().run()


def test_cadastro_login_logout_limpa_dados_entre_contas(contas):
    app=AppTest.from_file(str(RAIZ/'streamlit_app.py')).run()
    app.text_input(key='registro_usuario').set_value('alice')
    app.text_input(key='registro_senha').set_value('SenhaTeste1')
    app.text_input(key='registro_senha_confirmar').set_value('SenhaTeste1')
    clicar(app,'Criar conta')
    assert not app.exception
    app.text_input(key='login_usuario').set_value('alice')
    app.text_input(key='login_senha').set_value('SenhaTeste1')
    clicar(app,'Entrar')
    assert not app.exception
    assert app.session_state['usuario_logado']=='alice'
    app.session_state['ultima_tentativa']=(RegistroTentativa('privado','C0',1,'A','test','copia'),True)
    app.session_state['manifesto_gerado']=(b'{}','privado.json','chave-privada')
    clicar(app,'Sair')
    assert not app.exception
    assert 'usuario_logado' not in app.session_state
    assert 'ultima_tentativa' not in app.session_state
    assert 'manifesto_gerado' not in app.session_state
    auth.criar_usuario(contas.CAMINHO_CONTAS,'bob','SenhaTeste2')
    app.text_input(key='login_usuario').set_value('bob')
    app.text_input(key='login_senha').set_value('SenhaTeste2')
    clicar(app,'Entrar')
    assert not app.exception
    assert app.session_state['usuario_logado']=='bob'
    assert 'ultima_tentativa' not in app.session_state


def test_fluxo_b_real_pela_interface_grava_resultado_uma_vez(contas,monkeypatch):
    monkeypatch.setenv('TCC_HMAC_KEY','chave-teste')
    auth.criar_usuario(contas.CAMINHO_CONTAS,'alice','SenhaTeste1')
    repo=contas.pasta_do_usuario('alice')/'repositorio';repo.mkdir(parents=True)
    backup=repo/'pedidos_seed1_c0.dump';backup.write_bytes(b'fixture de hash, sem restauracao')
    m=manifest.construir_manifesto(str(backup),'pedidos_seed1_c0','18','test')
    manifest.salvar_manifesto(manifest.assinar_manifesto(m,b'chave-teste'),str(repo/'pedidos_seed1_c0.manifest.json'))
    app=AppTest.from_file(str(RAIZ/'streamlit_app.py')).run()
    app.text_input(key='login_usuario').set_value('alice')
    app.text_input(key='login_senha').set_value('SenhaTeste1')
    clicar(app,'Entrar')
    app.radio(key='modo_arquivos').set_value('Já estão na pasta do repositório')
    app.selectbox(key='configuracao').set_value('B').run()
    clicar(app,'Executar verificação')
    assert not app.exception
    assert not app.error
    registro,salvo=app.session_state['ultima_tentativa']
    assert salvo and registro.decisao=='aprovada'
    assert registro.semente==1 and registro.versao_codigo=='0.2.0'
    app.run()
    with (contas.pasta_do_usuario('alice')/'results/resumo.csv').open() as f: linhas=list(csv.DictReader(f))
    assert len(linhas)==1
