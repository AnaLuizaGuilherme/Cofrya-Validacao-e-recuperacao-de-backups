"""
Registro de resultados em CSV (seção 5.6 do TCC).

Dois arquivos ligados pelo identificador da tentativa:
  - resumo.csv:     uma linha por tentativa (decisão, tempos, cenário, config).
  - evidencias.csv: uma linha por verificação individual dentro da tentativa.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CAMPOS_RESUMO = [
    "id_tentativa", "cenario", "semente", "configuracao", "versao_codigo",
    "id_copia", "decisao", "motivo",
    "duracao_identidade_s", "duracao_autenticacao_s", "duracao_hash_s",
    "duracao_preparacao_s", "duracao_restauracao_s", "duracao_validacao_s",
    "duracao_limpeza_s", "tempo_decisao_s",
    "cpu_pct", "memoria_max_mb", "espaco_temp_mb",
    "eh_repeticao_desempenho",
]

CAMPOS_EVIDENCIA = ["id_tentativa", "teste", "aprovado", "valor_esperado", "valor_observado", "detalhe"]


@dataclass
class RegistroTentativa:
    id_tentativa: str
    cenario: str
    semente: int
    configuracao: str
    versao_codigo: str
    id_copia: str
    decisao: str = "inconclusiva"  # aprovada | reprovada | inconclusiva
    motivo: str = ""
    duracao_identidade_s: Optional[float] = None
    duracao_autenticacao_s: Optional[float] = None
    duracao_hash_s: Optional[float] = None
    duracao_preparacao_s: Optional[float] = None
    duracao_restauracao_s: Optional[float] = None
    duracao_validacao_s: Optional[float] = None
    duracao_limpeza_s: Optional[float] = None
    tempo_decisao_s: Optional[float] = None
    cpu_pct: Optional[float] = None
    memoria_max_mb: Optional[float] = None
    espaco_temp_mb: Optional[float] = None
    eh_repeticao_desempenho: bool = False
    evidencias: List[Dict[str, Any]] = field(default_factory=list)

    def registrar_evidencia(self, teste: str, aprovado: bool, esperado: Any, observado: Any, detalhe: str = "") -> None:
        self.evidencias.append({
            "id_tentativa": self.id_tentativa,
            "teste": teste,
            "aprovado": aprovado,
            "valor_esperado": esperado,
            "valor_observado": observado,
            "detalhe": detalhe,
        })


class RegistradorCSV:
    def __init__(self, diretorio_saida: str):
        self.diretorio_saida = diretorio_saida
        os.makedirs(diretorio_saida, exist_ok=True)
        self.caminho_resumo = os.path.join(diretorio_saida, "resumo.csv")
        self.caminho_evidencias = os.path.join(diretorio_saida, "evidencias.csv")
        self._garantir_cabecalhos()

    def _garantir_cabecalhos(self) -> None:
        if not os.path.isfile(self.caminho_resumo):
            with open(self.caminho_resumo, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=CAMPOS_RESUMO).writeheader()
        if not os.path.isfile(self.caminho_evidencias):
            with open(self.caminho_evidencias, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=CAMPOS_EVIDENCIA).writeheader()

    def gravar(self, registro: RegistroTentativa) -> None:
        linha_resumo = {campo: getattr(registro, campo, None) for campo in CAMPOS_RESUMO}
        # Dados ausentes são sinalizados, não substituídos por zero (seção 4.7).
        for chave, valor in linha_resumo.items():
            if valor is None:
                linha_resumo[chave] = "NA"

        with open(self.caminho_resumo, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CAMPOS_RESUMO).writerow(linha_resumo)

        if registro.evidencias:
            with open(self.caminho_evidencias, "a", newline="", encoding="utf-8") as f:
                escritor = csv.DictWriter(f, fieldnames=CAMPOS_EVIDENCIA)
                for ev in registro.evidencias:
                    escritor.writerow(ev)
