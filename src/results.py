"""
Registro de resultados em CSV (seção 5.6 do TCC).

Dois arquivos ligados pelo identificador da tentativa:
  - resumo.csv:     uma linha por tentativa (decisão, tempos, cenário, config).
  - evidencias.csv: uma linha por verificação individual dentro da tentativa.
"""

from __future__ import annotations

import csv
import os
import threading
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CAMPOS_RESUMO = [
    "id_tentativa", "cenario", "semente", "configuracao", "versao_codigo",
    "id_copia", "decisao", "motivo",
    "duracao_identidade_s", "duracao_autenticacao_s", "duracao_hash_s",
    "duracao_preparacao_s", "duracao_restauracao_s", "duracao_validacao_s",
    "duracao_limpeza_s", "tempo_decisao_s",
    "cpu_pct", "memoria_max_mb", "espaco_temp_mb",
    "eh_repeticao_desempenho", "instante_inicio_utc", "provedor_ambiente", "sha256_referencias",
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
    instante_inicio_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    provedor_ambiente: str = ""
    sha256_referencias: str = ""
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


_TRAVA_CSV = threading.RLock()


class RegistradorCSV:
    def __init__(self, diretorio_saida):
        self.diretorio_saida = os.fspath(diretorio_saida)
        os.makedirs(self.diretorio_saida, exist_ok=True)
        self.caminho_resumo = os.path.join(self.diretorio_saida, "resumo.csv")
        self.caminho_evidencias = os.path.join(self.diretorio_saida, "evidencias.csv")

    def _ler_existente(self, caminho, campos):
        if not os.path.exists(caminho) or os.path.getsize(caminho) == 0:
            return None
        with open(caminho, newline="", encoding="utf-8-sig") as f:
            leitor = csv.DictReader(f)
            header = leitor.fieldnames or []
            if len(header) != len(set(header)) or "id_tentativa" not in header or not set(header) <= set(campos):
                raise ValueError("Cabeçalho CSV incompatível; preserve o arquivo e escolha outra pasta.")
            if header == campos:
                return False
            linhas = list(leitor)
            if any(None in linha for linha in linhas):
                raise ValueError("CSV com linhas incompatíveis; nenhuma alteração realizada.")
            return linhas

    def _preparar(self, caminho, campos, linhas):
        if linhas is False:
            return
        temporario = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", dir=self.diretorio_saida, delete=False) as f:
                temporario = f.name
                escritor = csv.DictWriter(f, fieldnames=campos)
                escritor.writeheader()
                for linha in linhas or []:
                    escritor.writerow({campo: linha.get(campo, "NA") for campo in campos})
            os.replace(temporario, caminho)
        finally:
            if temporario and os.path.exists(temporario):
                os.unlink(temporario)

    def gravar(self, registro: RegistroTentativa) -> None:
        with _TRAVA_CSV:
            # Validar ambos antes de migrar cabeçalhos; não inventar metadados antigos.
            resumo = self._ler_existente(self.caminho_resumo, CAMPOS_RESUMO)
            evidencias = self._ler_existente(self.caminho_evidencias, CAMPOS_EVIDENCIA)
            self._preparar(self.caminho_resumo, CAMPOS_RESUMO, resumo)
            self._preparar(self.caminho_evidencias, CAMPOS_EVIDENCIA, evidencias)
            if registro.evidencias:
                with open(self.caminho_evidencias, "a", newline="", encoding="utf-8") as f:
                    csv.DictWriter(f, fieldnames=CAMPOS_EVIDENCIA).writerows(registro.evidencias)
            linha = {campo: getattr(registro, campo, None) for campo in CAMPOS_RESUMO}
            with open(self.caminho_resumo, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=CAMPOS_RESUMO).writerow(
                    {campo: "NA" if valor is None else valor for campo, valor in linha.items()}
                )
