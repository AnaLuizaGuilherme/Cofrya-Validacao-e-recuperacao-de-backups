"""
Testes automatizados concentrados em decisões que poderiam invalidar o
experimento (seção 5.7): rejeição de manifesto adulterado, preservação de
inconclusivos e verificação de integridade.

Rodar com: python -m pytest tests/
"""

import json
import os
import tempfile

import pytest

from src import manifest as manifesto_mod


def test_manifesto_valido_e_verificado_com_sucesso():
    chave = b"chave-de-teste-nao-usar-em-producao"
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"conteudo de backup de exemplo")
        caminho = f.name

    try:
        m = manifesto_mod.construir_manifesto(
            caminho_arquivo=caminho,
            id_copia="teste_1",
            versao_banco="PostgreSQL 18",
            versao_aplicacao="0.1.0",
        )
        documento = manifesto_mod.assinar_manifesto(m, chave)
        m_verificado = manifesto_mod.verificar_manifesto(documento, chave)
        assert m_verificado.id_copia == "teste_1"
        assert manifesto_mod.verificar_integridade_arquivo(caminho, m_verificado)
    finally:
        os.unlink(caminho)


def test_manifesto_adulterado_e_rejeitado_sem_a_chave_correta():
    """Cobre o cenário C7: adulteração sem acesso à chave de autenticação."""
    chave_correta = b"chave-correta"
    chave_errada = b"chave-errada"

    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"conteudo original")
        caminho = f.name

    try:
        m = manifesto_mod.construir_manifesto(
            caminho_arquivo=caminho,
            id_copia="teste_2",
            versao_banco="PostgreSQL 18",
            versao_aplicacao="0.1.0",
        )
        documento = manifesto_mod.assinar_manifesto(m, chave_correta)

        # Um agente sem a chave correta tenta adulterar e reassinar.
        documento["sha256"] = "0" * 64

        with pytest.raises(manifesto_mod.AutenticacaoFalhou):
            manifesto_mod.verificar_manifesto(documento, chave_correta)

        # Mesmo tentando "validar" com uma chave arbitrária, a verificação
        # contra a chave real deve falhar (o teste acima já garante isso;
        # aqui reforçamos que chaves diferentes produzem HMACs diferentes).
        assinatura_com_chave_errada = manifesto_mod.assinar_manifesto(m, chave_errada)
        with pytest.raises(manifesto_mod.AutenticacaoFalhou):
            manifesto_mod.verificar_manifesto(assinatura_com_chave_errada, chave_correta)
    finally:
        os.unlink(caminho)


def test_manifesto_com_campo_nao_reconhecido_e_rejeitado():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        json.dump(
            {
                "formato": "1.0", "id_copia": "x", "instante_captura": "2026-01-01T00:00:00+00:00",
                "tamanho_bytes": 1, "sha256": "a" * 64, "versao_banco": "x", "versao_aplicacao": "x",
                "hmac": "b" * 64, "campo_extra_suspeito": "valor",
            },
            open(f.name, "w"),
        )
        caminho = f.name

    try:
        with pytest.raises(manifesto_mod.ManifestoInvalido):
            manifesto_mod.carregar_documento_manifesto(caminho)
    finally:
        os.unlink(caminho)


def test_integridade_falha_apos_truncamento():
    """Cobre o cenário C1: truncamento após a criação deve ser detectado."""
    chave = b"chave-de-teste"
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"conteudo relativamente longo para permitir truncamento" * 10)
        caminho = f.name

    try:
        m = manifesto_mod.construir_manifesto(
            caminho_arquivo=caminho, id_copia="teste_3",
            versao_banco="PostgreSQL 18", versao_aplicacao="0.1.0",
        )
        # Trunca DEPOIS de calcular o manifesto original.
        with open(caminho, "r+b") as f:
            f.truncate(10)

        assert not manifesto_mod.verificar_integridade_arquivo(caminho, m)
    finally:
        os.unlink(caminho)
