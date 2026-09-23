"""Conexão direta, disponibilidade SQL e limpeza; sem credenciais externas."""
from types import SimpleNamespace

import psycopg2
import pytest

from src.adapters import neon_adapter, postgres_adapter


@pytest.fixture
def relogio(monkeypatch):
    agora = [0.0]
    monkeypatch.setattr(neon_adapter.time, 'monotonic', lambda: agora[0])
    def avancar(segundos):
        agora[0] += segundos
    monkeypatch.setattr(neon_adapter.time, 'sleep', avancar)
    return agora


def test_uri_solicita_branch_endpoint_banco_e_conexao_direta(monkeypatch):
    chamadas = []
    def get(url, **kwargs):
        chamadas.append((url, kwargs['params']))
        return SimpleNamespace(status_code=200, json=lambda: {'uri': 'postgresql://example'})
    monkeypatch.setattr(neon_adapter.requests, 'get', get)
    neon_adapter._obter_connection_uri('project', 'br-test', {}, 'cofrya_test', 'ep-test')
    assert chamadas == [(
        neon_adapter.BASE_URL + '/projects/project/connection_uri',
        {'branch_id': 'br-test', 'endpoint_id': 'ep-test', 'database_name': 'cofrya_test',
         'role_name': 'neondb_owner', 'pooled': 'false'},
    )]


def test_endpoint_seleciona_escrita_ativa_do_branch_correto(monkeypatch, relogio):
    chamadas = []
    def get(url, **kwargs):
        chamadas.append(url)
        return SimpleNamespace(status_code=200, json=lambda: {'endpoints': [
            {'id': 'ep-parent', 'branch_id': 'br-parent', 'type': 'read_write', 'current_state': 'active'},
            {'id': 'ep-readonly', 'branch_id': 'br-test', 'type': 'read_only', 'current_state': 'active'},
            {'id': 'ep-test', 'branch_id': 'br-test', 'type': 'read_write',
             'current_state': 'init' if len(chamadas) == 1 else 'active'},
        ]})
    monkeypatch.setattr(neon_adapter.requests, 'get', get)
    assert neon_adapter._aguardar_endpoint('project', 'br-test', {}, 10) == 'ep-test'
    assert len(chamadas) == 2


def test_banco_aguarda_disponibilidade_sql_sem_repetir_restauracao(monkeypatch, relogio):
    chamadas = []
    instancia = SimpleNamespace(banco='cofrya_test')
    def consultar(alvo, consulta, timeout_s):
        chamadas.append((alvo, consulta, timeout_s))
        if len(chamadas) == 1:
            raise psycopg2.OperationalError('database does not exist')
        if len(chamadas) == 2:
            raise psycopg2.InterfaceError('connection closed')
        return ['current_database'], [('cofrya_test',)]
    monkeypatch.setattr(neon_adapter, 'executar_consulta', consultar)
    neon_adapter._aguardar_banco(instancia, 10)
    assert len(chamadas) == 3
    assert all(alvo is instancia and consulta == 'SELECT current_database()' and 2 <= prazo <= 5
               for alvo, consulta, prazo in chamadas)


def test_banco_indisponivel_tem_prazo_e_nao_expoe_credenciais(monkeypatch, relogio):
    def consultar(*args, **kwargs):
        raise psycopg2.OperationalError('postgresql://user:segredo@host/db')
    monkeypatch.setattr(neon_adapter, 'executar_consulta', consultar)
    with pytest.raises(neon_adapter.ContainerNaoDisponivel, match='dentro do prazo') as erro:
        neon_adapter._aguardar_banco(SimpleNamespace(banco='cofrya_test'), 6)
    assert relogio[0] == 6
    assert 'segredo' not in str(erro.value)


def test_banco_sql_diferente_e_recusado(monkeypatch, relogio):
    monkeypatch.setattr(neon_adapter, 'executar_consulta', lambda *a, **k: (['current_database'], [('neondb',)]))
    with pytest.raises(neon_adapter.ContainerNaoDisponivel, match='diferente'):
        neon_adapter._aguardar_banco(SimpleNamespace(banco='cofrya_test'), 10)


@pytest.mark.parametrize('falha', ['pool', 'outro_endpoint', 'outro_banco', 'indisponivel', 'erro_sql'])
def test_preparacao_recusa_destino_errado_e_remove_branch(monkeypatch, relogio, falha):
    remocoes = []
    consultas = []
    monkeypatch.setattr(neon_adapter, '_obter_config', lambda: ('key', 'project'))
    monkeypatch.setattr(neon_adapter, '_aguardar_endpoint', lambda *args: 'ep-test')
    monkeypatch.setattr(neon_adapter.requests, 'post', lambda *a, **k: SimpleNamespace(
        status_code=201, json=lambda: {'branch': {'id': 'br-test'}}))
    monkeypatch.setattr(neon_adapter, '_remover_branch', lambda proj, branch, headers: remocoes.append(branch))
    def uri(proj, branch, headers, banco, endpoint_id):
        host = {'pool': 'ep-test-pooler.example', 'outro_endpoint': 'ep-parent.example'}.get(falha, 'ep-test.example')
        db = 'neondb' if falha == 'outro_banco' else banco
        return f'postgresql://neondb_owner:senha@{host}/{db}'
    monkeypatch.setattr(neon_adapter, '_obter_connection_uri', uri)
    def consultar(*args, **kwargs):
        consultas.append(args)
        if falha == 'erro_sql':
            raise psycopg2.ProgrammingError('erro interno')
        raise psycopg2.OperationalError('database does not exist')
    monkeypatch.setattr(neon_adapter, 'executar_consulta', consultar)
    with pytest.raises(neon_adapter.ContainerNaoDisponivel):
        neon_adapter.subir_postgres_temporario('test', timeout_disponibilidade_s=6)
    assert remocoes == ['br-test']
    assert bool(consultas) == (falha in {'indisponivel', 'erro_sql'})


def test_consulta_de_prontidao_fecha_conexao_e_usa_tls(monkeypatch):
    fechadas = []
    parametros = []
    comandos = []
    class Cursor:
        description = [('current_database',)]
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args): comandos.append(args)
        def fetchall(self): return [('cofrya_test',)]
    class Conexao:
        def set_session(self, **kwargs): assert kwargs == {'readonly': True}
        def cursor(self): return Cursor()
        def close(self): fechadas.append(True)
    def conectar(**kwargs):
        parametros.append(kwargs)
        return Conexao()
    monkeypatch.setattr(psycopg2, 'connect', conectar)
    instancia = SimpleNamespace(host='ep-test.example', porta=5432, usuario='user', senha='senha', banco='cofrya_test', imagem='neon')
    neon_adapter._aguardar_banco(instancia, 10)
    assert parametros[0]['dbname'] == 'cofrya_test'
    assert parametros[0]['sslmode'] == 'require'
    assert parametros[0]['connect_timeout'] == 5
    assert fechadas == [True]
    assert comandos[-1] == ('SELECT current_database()',)


def test_pg_restore_mantem_diagnostico_em_ingles_e_nao_repete(monkeypatch):
    chamadas = []
    monkeypatch.setenv('LC_ALL', 'pt_BR.UTF-8')
    def executar(cmd, **kwargs):
        chamadas.append((cmd, kwargs))
        return SimpleNamespace(returncode=1, stdout='', stderr='pg_restore: error: connection to server failed')
    monkeypatch.setattr(postgres_adapter.subprocess, 'run', executar)
    instancia = SimpleNamespace(host='ep-test.example', porta=5432, usuario='user', senha='senha', banco='cofrya_test', imagem='neon')
    resultado = postgres_adapter.restaurar('backup.dump', instancia)
    assert len(chamadas) == 1 and resultado.codigo_saida == 1
    assert chamadas[0][1]['env']['LC_ALL'] == 'C'
    assert chamadas[0][1]['env']['PGSSLMODE'] == 'require'
