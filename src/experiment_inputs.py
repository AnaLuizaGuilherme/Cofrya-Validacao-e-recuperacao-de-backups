"""Conferência dos dados de entrada, sem determinar o resultado do experimento."""
import json
import re

from .safe_files import validar_id
from .scenarios import Cenario


def id_do_backup_enviado(nome: str) -> str:
    if not isinstance(nome, str) or not nome.lower().endswith('.dump'):
        raise ValueError('O backup precisa ter um nome terminado em .dump.')
    return validar_id(nome[:-5])


def validar_rotulo_da_copia(copia_id: str, cenario: str, semente: int) -> None:
    """Confere apenas a convenção dos arquivos do laboratório, não seu conteúdo."""
    Cenario(cenario)
    nome = re.fullmatch(r'pedidos_seed(\d+)_c(\d+)', copia_id, re.IGNORECASE)
    if nome is None:
        return
    esperado = f'C{nome.group(2)}'
    Cenario(esperado)
    if cenario != esperado:
        raise ValueError(
            f'O ID {copia_id} identifica {esperado}, mas o cenário selecionado é {cenario}. '
            f'Envie os arquivos do cenário desejado e confira o ID da cópia.'
        )
    if int(nome.group(1)) != semente:
        raise ValueError(f'A semente de {copia_id} é {int(nome.group(1))}; confira o campo Semente.')


def validar_referencias(referencias: object) -> dict:
    if not isinstance(referencias, dict):
        raise ValueError('A configuração C exige o arquivo .referencias.json com um objeto JSON.')
    campos = ('n_clientes', 'n_produtos', 'n_pedidos', 'n_itens', 'n_pagamentos')
    invalidos = [nome for nome in campos
                 if type(referencias.get(nome)) is not int or referencias[nome] < 0]
    if invalidos:
        raise ValueError('Referências ausentes ou inválidas (inteiros não negativos): ' + ', '.join(invalidos) + '.')
    return referencias


def ler_referencias(conteudo: bytes) -> dict:
    if not conteudo:
        raise ValueError('Envie o arquivo .referencias.json no campo Referências para executar a configuração C.')
    if len(conteudo) > 5 * 1024 * 1024:
        raise ValueError('O arquivo de referências excede 5 MB.')
    try:
        referencias = json.loads(conteudo.decode('utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('O arquivo .referencias.json precisa ser um JSON válido.') from exc
    return validar_referencias(referencias)
