"""
Manifesto autenticado do backup (seções 2.1 e 5.5 do TCC).

O manifesto usa representação JSON determinística (chaves ordenadas, sem espaços
supérfluos) para que o HMAC seja reproduzível. A chave de autenticação NUNCA deve
ser lida do repositório de backups verificado; aqui ela vem de variável de ambiente
ou de um arquivo fora do repositório (domínio confiável, seção 4.4).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

FORMATO_MANIFESTO = "1.0"


class ManifestoInvalido(Exception):
    pass


class AutenticacaoFalhou(Exception):
    pass


@dataclass
class Manifesto:
    formato: str
    id_copia: str
    instante_captura: str  # ISO 8601 UTC
    tamanho_bytes: int
    sha256: str
    versao_banco: str
    versao_aplicacao: str

    def payload_canonico(self) -> bytes:
        """Serialização determinística usada como entrada do HMAC.

        `sort_keys=True` e separadores compactos garantem que o mesmo conteúdo
        lógico sempre produza os mesmos bytes, independentemente da ordem em que
        os campos foram inseridos no dicionário de origem.
        """
        dados = asdict(self)
        return json.dumps(dados, sort_keys=True, separators=(",", ":")).encode("utf-8")


def calcular_sha256(caminho_arquivo: str, tamanho_bloco: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with open(caminho_arquivo, "rb") as f:
        while True:
            bloco = f.read(tamanho_bloco)
            if not bloco:
                break
            hasher.update(bloco)
    return hasher.hexdigest()


def construir_manifesto(
    caminho_arquivo: str,
    id_copia: str,
    versao_banco: str,
    versao_aplicacao: str,
    instante_captura: Optional[str] = None,
) -> Manifesto:
    tamanho = os.path.getsize(caminho_arquivo)
    sha256 = calcular_sha256(caminho_arquivo)
    if instante_captura is None:
        instante_captura = datetime.now(timezone.utc).isoformat()
    return Manifesto(
        formato=FORMATO_MANIFESTO,
        id_copia=id_copia,
        instante_captura=instante_captura,
        tamanho_bytes=tamanho,
        sha256=sha256,
        versao_banco=versao_banco,
        versao_aplicacao=versao_aplicacao,
    )


def assinar_manifesto(manifesto: Manifesto, chave: bytes) -> Dict[str, Any]:
    """Produz o objeto persistido: manifesto + campo de autenticação separado.

    O campo `hmac` nunca entra no payload que ele mesmo autentica (evita
    dependência circular na verificação).
    """
    payload = manifesto.payload_canonico()
    assinatura = hmac.new(chave, payload, hashlib.sha256).hexdigest()
    documento = asdict(manifesto)
    documento["hmac"] = assinatura
    return documento


def carregar_documento_manifesto(caminho_json: str) -> Dict[str, Any]:
    with open(caminho_json, "r", encoding="utf-8") as f:
        try:
            documento = json.load(f)
        except json.JSONDecodeError as exc:
            raise ManifestoInvalido(f"JSON inválido em {caminho_json}: {exc}") from exc

    campos_obrigatorios = {
        "formato", "id_copia", "instante_captura", "tamanho_bytes",
        "sha256", "versao_banco", "versao_aplicacao", "hmac",
    }
    faltantes = campos_obrigatorios - set(documento.keys())
    if faltantes:
        raise ManifestoInvalido(f"Campos ausentes no manifesto: {sorted(faltantes)}")

    # Rejeita campos duplicados: json.load já colapsa duplicatas silenciosamente
    # em um dict; a defesa real está em validar contra um schema estrito de chaves
    # conhecidas, o que já é feito acima (qualquer chave extra é ignorada, nunca
    # aceita como se fosse dado íntegro).
    extras = set(documento.keys()) - campos_obrigatorios
    if extras:
        raise ManifestoInvalido(f"Campos não reconhecidos no manifesto: {sorted(extras)}")

    return documento


def verificar_manifesto(documento: Dict[str, Any], chave: bytes) -> Manifesto:
    """Reconstrói o manifesto a partir do documento e verifica o HMAC.

    Levanta AutenticacaoFalhou se a assinatura não corresponder — isto cobre
    tanto adulteração do conteúdo quanto substituição do próprio campo hmac
    (cenário C7 do TCC).
    """
    assinatura_recebida = documento.get("hmac", "")
    campos_manifesto = {
        k: v for k, v in documento.items() if k != "hmac"
    }
    try:
        manifesto = Manifesto(**campos_manifesto)
    except TypeError as exc:
        raise ManifestoInvalido(f"Estrutura do manifesto inválida: {exc}") from exc

    payload = manifesto.payload_canonico()
    assinatura_esperada = hmac.new(chave, payload, hashlib.sha256).hexdigest()

    # hmac.compare_digest evita vazamento de tempo na comparação (seção 5.5,
    # conforme documentação do módulo hmac do Python).
    if not hmac.compare_digest(assinatura_esperada, assinatura_recebida):
        raise AutenticacaoFalhou("HMAC do manifesto não confere: possível adulteração.")

    return manifesto


def verificar_integridade_arquivo(caminho_arquivo: str, manifesto: Manifesto) -> bool:
    """Confere tamanho e hash do arquivo contra o manifesto já autenticado."""
    tamanho_atual = os.path.getsize(caminho_arquivo)
    if tamanho_atual != manifesto.tamanho_bytes:
        return False
    hash_atual = calcular_sha256(caminho_arquivo)
    return hmac.compare_digest(hash_atual, manifesto.sha256)


def salvar_manifesto(documento: Dict[str, Any], caminho_saida: str) -> None:
    with open(caminho_saida, "w", encoding="utf-8") as f:
        json.dump(documento, f, sort_keys=True, indent=2)
