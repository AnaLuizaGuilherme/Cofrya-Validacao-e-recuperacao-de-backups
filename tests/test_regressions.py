"""Regressões de isolamento, decisões e métricas; sem acesso a serviços externos."""
import csv
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from src import auth, executor, manifest
from src.safe_files import salvar_uploads
from src.results import RegistroTentativa, RegistradorCSV, CAMPOS_RESUMO
from src.tipos_arquivo import verificar_arquivo_generico, verificar_json
from src.adapters import neon_adapter


@pytest.mark.parametrize('id_copia', ['../fora', '../../bob/copia', '/tmp/copia', r'..\fora'])
def test_upload_nao_escreve_com_id_invalido(tmp_path, id_copia):
    with pytest.raises(ValueError):
        salvar_uploads(tmp_path/'repositorio', id_copia, io.BytesIO(b'backup'), io.BytesIO(b'{}'))
    assert not list(tmp_path.iterdir())


def test_upload_rejeita_symlink_e_remove_referencia_antiga(tmp_path):
    repo = tmp_path/'repo';repo.mkdir()
    (repo/'copia.dump').symlink_to(tmp_path/'fora')
    with pytest.raises(ValueError):
        salvar_uploads(repo, 'copia', io.BytesIO(b'backup'), io.BytesIO(b'{}'))
    assert not (tmp_path/'fora').exists()
    (repo/'copia.dump').unlink()
    (repo/'copia.referencias.json').write_text('{}')
    salvar_uploads(repo, 'copia', io.BytesIO(b'backup'), io.BytesIO(b'{}'))
    assert not (repo/'copia.referencias.json').exists()


def test_csv_vazio_nao_aprova_e_manifesto_sem_chave_nao_e_ignorado(tmp_path):
    p=tmp_path/'arquivo.csv';p.write_text('')
    assert verificar_arquivo_generico(str(p),None,None,'.csv').decisao=='reprovada'
    p.write_text('nome\nAna\n')
    r=verificar_arquivo_generico(str(p),'manifest.json',None,'.csv')
    assert r.decisao=='inconclusiva'
    r=verificar_arquivo_generico(str(p),None,None,'.csv')
    assert r.decisao=='aprovada'
    assert not any(e.teste=='integridade_hash' for e in r.evidencias)


def test_json_verifica_chaves_em_todos_os_registros(tmp_path):
    p=tmp_path/'arquivo.json';p.write_text('[{"id":1},{"outro":2}]')
    assert verificar_json(str(p),['id']).decisao=='reprovada'


def test_manifesto_rejeita_chaves_duplicadas(tmp_path):
    p=tmp_path/'manifest.json';p.write_text('{"hmac":"a","hmac":"b"}')
    with pytest.raises(manifest.ManifestoInvalido,match='duplicados'):
        manifest.carregar_documento_manifesto(str(p))


def test_auth_cadastro_login_troca_e_limite(tmp_path,monkeypatch):
    db=tmp_path/'contas.db'
    auth.criar_usuario(db,'alice','SenhaTeste1')
    assert auth.verificar_login(db,'alice','SenhaTeste1')
    auth.trocar_senha(db,'alice','SenhaTeste1','SenhaTeste2')
    assert not auth.verificar_login(db,'alice','SenhaTeste1')
    assert auth.verificar_login(db,'alice','SenhaTeste2')
    for _ in range(auth.MAX_TENTATIVAS):
        assert not auth.verificar_login(db,'alice','errada')
    assert not auth.verificar_login(db,'alice','SenhaTeste2')
    agora=auth.time.time()
    monkeypatch.setattr(auth.time,'time',lambda:agora+auth.JANELA_TENTATIVAS_S+1)
    assert auth.verificar_login(db,'alice','SenhaTeste2')
    assert b'SenhaTeste' not in db.read_bytes()


@pytest.fixture
def laboratorio(tmp_path):
    repo=tmp_path/'repositorio';repo.mkdir()
    backup=repo/'pedidos_seed1_c0.dump';backup.write_bytes(b'fixture apenas de integridade; nao e dump PostgreSQL')
    m=manifest.construir_manifesto(str(backup),'pedidos_seed1_c0','18','test')
    caminho=repo/'pedidos_seed1_c0.manifest.json'
    manifest.salvar_manifesto(manifest.assinar_manifesto(m,b'key'),str(caminho))
    entrada=executor.EntradaCatalogo('pedidos_seed1_c0',str(backup),str(caminho),str(repo))
    contexto=executor.ConfiguracaoExecucao(b'key',str(tmp_path/'tentativas'),str(tmp_path/'results'))
    return entrada,contexto


def test_semente_inferida_e_divergencia_bloqueada(laboratorio):
    entrada,contexto=laboratorio
    r=executor.executar_tentativa(entrada,executor.Configuracao.B,executor.PoliticaTemporal(),contexto)
    assert r.semente==1 and r.decisao=='aprovada'
    with pytest.raises(ValueError,match='Semente'):
        executor.executar_tentativa(entrada,executor.Configuracao.B,executor.PoliticaTemporal(),contexto,semente=0)


def test_preparacao_inclui_inicializacao_e_limpeza_nao_apaga_resultado(laboratorio,monkeypatch):
    entrada,contexto=laboratorio
    relogio=[0.0]
    monkeypatch.setattr(executor.time,'monotonic',lambda:relogio[0])
    def subir(**kwargs):
        relogio[0]+=20
        return SimpleNamespace(nome_container='test')
    def dependencias(*args,**kwargs):
        relogio[0]+=2
        return SimpleNamespace(duracao_s=2,codigo_saida=0,stderr='')
    def restaurar(*args):
        relogio[0]+=3
        return SimpleNamespace(duracao_s=3,codigo_saida=0,stderr='')
    def limpar(*args):
        raise RuntimeError('cleanup test')
    monkeypatch.setattr(executor.docker_adapter,'subir_postgres_temporario',subir)
    monkeypatch.setattr(executor.postgres_adapter,'preparar_dependencias',dependencias)
    monkeypatch.setattr(executor.postgres_adapter,'restaurar',restaurar)
    monkeypatch.setattr(executor.docker_adapter,'derrubar_postgres_temporario',limpar)
    r=executor.executar_tentativa(entrada,executor.Configuracao.C_SEM_FUNC,executor.PoliticaTemporal(),contexto)
    assert r.duracao_preparacao_s==22
    assert r.tempo_decisao_s==25
    assert r.decisao=='aprovada'
    assert any(e['teste']=='limpeza_ambiente' and not e['aprovado'] for e in r.evidencias)
    assert not list(Path(contexto.diretorio_trabalho).iterdir())


def test_ambiente_indisponivel_e_inconclusivo(laboratorio,monkeypatch):
    entrada,contexto=laboratorio
    def falhar(**kwargs): raise executor.docker_adapter.ContainerNaoDisponivel('indisponivel')
    monkeypatch.setattr(executor.docker_adapter,'subir_postgres_temporario',falhar)
    r=executor.executar_tentativa(entrada,executor.Configuracao.C,executor.PoliticaTemporal(),contexto)
    assert r.decisao=='inconclusiva'


def test_neon_remove_branch_se_preparacao_falha(monkeypatch):
    monkeypatch.setattr(neon_adapter,'_obter_config',lambda:('key','project'))
    monkeypatch.setattr(neon_adapter,'_aguardar_endpoint',lambda *args:'ep-test')
    criacoes=[];remocoes=[]
    def post(url,**kwargs):
        criacoes.append(kwargs['json'])
        if url.endswith('/branches'):
            return SimpleNamespace(status_code=201,json=lambda:{'branch':{'id':'br-test'}})
        return SimpleNamespace(status_code=500)
    monkeypatch.setattr(neon_adapter.requests,'post',post)
    monkeypatch.setattr(neon_adapter,'_remover_branch',lambda *args:remocoes.append(args[1]))
    with pytest.raises(neon_adapter.ContainerNaoDisponivel): neon_adapter.subir_postgres_temporario('test')
    assert remocoes==['br-test']
    assert criacoes[1]['database']['name'].startswith('cofrya_')


def test_neon_retorna_banco_novo_e_credenciais_decodificadas(monkeypatch):
    monkeypatch.setattr(neon_adapter,'_obter_config',lambda:('key','project'))
    monkeypatch.setattr(neon_adapter,'_aguardar_endpoint',lambda *args:'ep-test')
    monkeypatch.setattr(neon_adapter.requests,'post',lambda *a,**k:SimpleNamespace(status_code=201,json=lambda:{'branch':{'id':'br-test'}}))
    nomes=[];confirmacoes=[]
    def uri(proj,branch,headers,banco,endpoint_id):
        assert branch=='br-test' and endpoint_id=='ep-test'
        nomes.append(banco)
        return 'postgresql://neondb_owner:senha%40teste@ep-test.example/'+banco+'?sslmode=require'
    monkeypatch.setattr(neon_adapter,'_obter_connection_uri',uri)
    monkeypatch.setattr(neon_adapter,'_aguardar_banco',lambda instancia,prazo:confirmacoes.append(instancia.banco))
    r=neon_adapter.subir_postgres_temporario('test')
    assert r.banco==nomes[0] and r.banco!='neondb'
    assert r.senha=='senha@teste'
    assert confirmacoes==nomes


def test_csv_preserva_dados_antigos_ao_adicionar_metadados(tmp_path):
    anterior='id_tentativa,cenario,semente,decisao\nold,C0,0,aprovada\n'
    (tmp_path/'resumo.csv').write_text(anterior)
    registro=RegistroTentativa('new','C0',1,'A','0.2.0','pedidos_seed1_c0')
    RegistradorCSV(tmp_path).gravar(registro)
    with (tmp_path/'resumo.csv').open() as f: linhas=list(csv.DictReader(f))
    assert linhas[0]['semente']=='0' and linhas[0]['instante_inicio_utc']=='NA'
    assert linhas[1]['semente']=='1' and linhas[1]['instante_inicio_utc']!='NA'
    (tmp_path/'resumo.csv').write_text('coluna_desconhecida\nx\n')
    with pytest.raises(ValueError): RegistradorCSV(tmp_path).gravar(registro)
    assert (tmp_path/'resumo.csv').read_text()=='coluna_desconhecida\nx\n'


def test_imports_do_diretorio_compatibilidade():
    raiz=Path(__file__).resolve().parents[1]
    proc=subprocess.run([sys.executable,'-c','import src.executor, src.results, src.auth; print(src.executor.VERSAO_CODIGO)'],cwd=raiz/'verificador_backups',capture_output=True,text=True)
    assert proc.returncode==0,proc.stderr
    assert proc.stdout.strip()=='0.2.1'


@pytest.mark.parametrize('configuracao', [executor.Configuracao.C_SEM_FUNC, executor.Configuracao.C])
@pytest.mark.parametrize('erro,decisao', [
    ('pg_restore: error: connection to server at "ep-test-pooler.example" (127.0.0.1), port 5432 failed: ERROR:  database "cofrya_test" does not exist\n', 'inconclusiva'),
    ('pg_restore: error: could not execute query: ERROR: relation "pagamentos" does not exist\n', 'reprovada'),
    ('pg_restore: error: could not read from input file: end of file\n', 'reprovada'),
])
def test_falha_restauracao_distingue_conexao_de_backup(laboratorio,monkeypatch,configuracao,erro,decisao):
    entrada,contexto=laboratorio
    remocoes=[]
    monkeypatch.setattr(executor.docker_adapter,'subir_postgres_temporario',lambda **kwargs:SimpleNamespace(nome_container='test'))
    monkeypatch.setattr(executor.docker_adapter,'derrubar_postgres_temporario',lambda nome:remocoes.append(nome))
    monkeypatch.setattr(executor.postgres_adapter,'preparar_dependencias',lambda *a,**k:SimpleNamespace(duracao_s=0,codigo_saida=0,stderr=''))
    restauracoes=[]
    def restaurar(*args):
        restauracoes.append(args)
        return SimpleNamespace(duracao_s=1,codigo_saida=1,stderr=erro)
    monkeypatch.setattr(executor.postgres_adapter,'restaurar',restaurar)
    def validar_indevidamente(*args):
        pytest.fail('Não deve executar validação funcional após falha de restauração')
    monkeypatch.setattr(executor,'validar_estrutura',validar_indevidamente)
    r=executor.executar_tentativa(entrada,configuracao,executor.PoliticaTemporal(),contexto)
    assert r.decisao==decisao
    assert len(restauracoes)==1 and remocoes==['test']
    assert any(e['teste']=='restauracao_pg_restore' and e['detalhe']==erro for e in r.evidencias)


def test_conexao_indisponivel_ao_preparar_dependencias_e_inconclusiva(laboratorio,monkeypatch):
    entrada,contexto=laboratorio
    monkeypatch.setattr(executor.docker_adapter,'subir_postgres_temporario',lambda **kwargs:SimpleNamespace(nome_container='test'))
    monkeypatch.setattr(executor.docker_adapter,'derrubar_postgres_temporario',lambda nome:None)
    monkeypatch.setattr(executor.postgres_adapter,'preparar_dependencias',lambda *a,**k:SimpleNamespace(duracao_s=1,codigo_saida=2,stderr='psql: error: connection to server at "example" failed: timeout expired'))
    monkeypatch.setattr(executor.postgres_adapter,'restaurar',lambda *args:pytest.fail('Não deve restaurar sem conexão'))
    r=executor.executar_tentativa(entrada,executor.Configuracao.C_SEM_FUNC,executor.PoliticaTemporal(),contexto)
    assert r.decisao=='inconclusiva' and 'conexão' in r.motivo
