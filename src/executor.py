"""
Executor sequencial (seções 4.5, 4.6, 5.1, 5.3 e 5.7 do TCC).

Aplica, para uma tentativa (uma combinação cópia + configuração), as etapas
habilitadas pela configuração selecionada:

    A          : identidade + política temporal + sucesso informado pelo produtor
    B          : A + autenticação do manifesto (HMAC) + conferência de hash
    C_sem_func : B + preparação do ambiente + restauração (pg_restore --exit-on-error)
    C          : C_sem_func + validação de estrutura, conteúdo e regras de negócio

Um relógio monotônico mede cada etapa (seção 5.7). Uma reprovação pode
interromper as etapas seguintes (seção 4.5). O resultado final é aprovada,
reprovada ou inconclusiva (seção 4.6).
"""

from __future__ import annotations

import time
import re
import hashlib
import json
from pathlib import Path
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from . import manifest as manifesto_mod
from .adapters import docker_adapter, neon_adapter, file_adapter, postgres_adapter
from .results import RegistroTentativa
from .validators import (
    validar_estrutura,
    validar_conteudo,
    validar_regras_de_negocio,
)

VERSAO_CODIGO = "0.2.0"


class Configuracao(str, Enum):
    A = "A"
    B = "B"
    C_SEM_FUNC = "C_sem_func"
    C = "C"


@dataclass
class EntradaCatalogo:
    """Cadastro local de uma cópia autorizada (seção 5.2)."""
    id_copia: str
    caminho_backup: str
    caminho_manifesto: str
    localizacao_autorizada: str
    dependencias: List[str] = field(default_factory=list)
    versao_politica: str = "1.0"


@dataclass
class PoliticaTemporal:
    idade_maxima_dias: int = 30


@dataclass
class ConfiguracaoExecucao:
    chave_hmac: bytes
    diretorio_trabalho: str
    diretorio_saida_csv: str
    imagem_postgres: str = "postgres:18"
    timeout_disponibilidade_s: int = 60
    limite_cpu: str = "1.0"
    limite_memoria: str = "1g"
    provedor_ambiente: str = "docker"  # "docker" (padrão, local) ou "neon" (nuvem, sem Docker)


def _adaptador_ambiente(contexto: "ConfiguracaoExecucao"):
    """Seleciona o adaptador de ambiente temporário: Docker local (seção 5.5
    do TCC) ou Neon (desvio documentado para hospedagem sem Docker, ver
    src/adapters/neon_adapter.py). Ambos expõem a mesma interface
    (subir_postgres_temporario / derrubar_postgres_temporario) e reaproveitam
    a mesma exceção ContainerNaoDisponivel."""
    if contexto.provedor_ambiente == "neon":
        return neon_adapter
    return docker_adapter


class Decisao(str, Enum):
    APROVADA = "aprovada"
    REPROVADA = "reprovada"
    INCONCLUSIVA = "inconclusiva"


def executar_tentativa(
    entrada: EntradaCatalogo,
    configuracao: Configuracao,
    politica: PoliticaTemporal,
    contexto: ConfiguracaoExecucao,
    referencias_esperadas: Optional[Dict[str, Any]] = None,
    cenario: str = "C0",
    semente: Optional[int] = None,
    eh_repeticao_desempenho: bool = False,
    imagem_postgres_override: Optional[str] = None,
) -> RegistroTentativa:
    """Executa uma tentativa completa e retorna o registro pronto para CSV.

    `imagem_postgres_override` permite reproduzir C8 (impedimento do ambiente
    de restauração) passando uma imagem Docker inexistente.
    """
    if contexto.provedor_ambiente not in {"docker", "neon"}:
        raise ValueError("Provedor de restauração inválido.")
    seed_no_nome = re.search(r"(?:^|_)seed(\d+)(?:_|$)", entrada.id_copia)
    if semente is None:
        if not seed_no_nome:
            raise ValueError("Informe a semente utilizada para gerar a cópia.")
        semente = int(seed_no_nome.group(1))
    if seed_no_nome and semente != int(seed_no_nome.group(1)):
        raise ValueError("Semente informada diverge do identificador da cópia.")
    if semente < 0:
        raise ValueError("Semente inválida.")
    if politica.idade_maxima_dias < 1:
        raise ValueError("Idade máxima deve ser positiva.")
    raiz_autorizada = Path(entrada.localizacao_autorizada).resolve()
    for caminho in (entrada.caminho_backup, entrada.caminho_manifesto):
        if not Path(caminho).resolve().is_relative_to(raiz_autorizada):
            raise ValueError("Arquivo fora do repositório autorizado.")
    id_tentativa = str(uuid.uuid4())
    registro = RegistroTentativa(
        id_tentativa=id_tentativa,
        cenario=cenario,
        semente=semente,
        configuracao=configuracao.value,
        versao_codigo=VERSAO_CODIGO,
        provedor_ambiente=contexto.provedor_ambiente,
        sha256_referencias=(hashlib.sha256(json.dumps(referencias_esperadas, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if referencias_esperadas is not None else ""),
        id_copia=entrada.id_copia,
        eh_repeticao_desempenho=eh_repeticao_desempenho,
    )

    inicio_decisao = time.monotonic()
    diretorio_tentativa = f"{contexto.diretorio_trabalho}/{id_tentativa}"
    instancia = None
    manifesto: Optional[manifesto_mod.Manifesto] = None

    try:
        # --- Etapa 1: identidade e política temporal (todas as configs) ---
        t0 = time.monotonic()
        try:
            copia = file_adapter.copiar_para_area_protegida(
                entrada.caminho_backup, entrada.caminho_manifesto, diretorio_tentativa
            )
        except file_adapter.ArquivoNaoEncontrado as exc:
            registro.decisao = Decisao.REPROVADA.value
            registro.motivo = f"Arquivo não encontrado: {exc}"
            return registro
        registro.duracao_identidade_s = time.monotonic() - t0

        if configuracao == Configuracao.A:
            # A é a linha de base de existência, sem atestar validade ou restauração.
            registro.decisao = Decisao.APROVADA.value
            registro.motivo = "Configuração A: existência confirmada (sem autenticação nem restauração)"
            return registro

        # --- Etapa 2: autenticação do manifesto e conferência de hash (B+) ---
        t0 = time.monotonic()
        try:
            documento = manifesto_mod.carregar_documento_manifesto(copia.caminho_manifesto_protegido)
            manifesto = manifesto_mod.verificar_manifesto(documento, contexto.chave_hmac)
        except (manifesto_mod.ManifestoInvalido, manifesto_mod.AutenticacaoFalhou) as exc:
            registro.duracao_autenticacao_s = time.monotonic() - t0
            registro.decisao = Decisao.REPROVADA.value
            registro.motivo = f"Falha de autenticação do manifesto: {exc}"
            registro.registrar_evidencia("autenticacao_manifesto", False, "hmac válido", str(exc))
            return registro
        registro.duracao_autenticacao_s = time.monotonic() - t0
        registro.registrar_evidencia("autenticacao_manifesto", True, "hmac válido", "hmac válido")

        # Política de idade máxima e correspondência de identificador (seção 2.1/4.4).
        instante_captura = datetime.fromisoformat(manifesto.instante_captura)
        idade = datetime.now(timezone.utc) - instante_captura
        dentro_da_politica = timedelta(0) <= idade <= timedelta(days=politica.idade_maxima_dias)
        registro.registrar_evidencia(
            "politica_idade_maxima", dentro_da_politica,
            f"<= {politica.idade_maxima_dias} dias", f"{idade.days} dias",
        )
        id_confere = manifesto.id_copia == entrada.id_copia
        registro.registrar_evidencia(
            "identificador_autorizado", id_confere, entrada.id_copia, manifesto.id_copia
        )
        if not (dentro_da_politica and id_confere):
            registro.decisao = Decisao.REPROVADA.value
            registro.motivo = "Violação de política de idade máxima ou identificador (C6)"
            return registro

        t0 = time.monotonic()
        integro = manifesto_mod.verificar_integridade_arquivo(
            copia.caminho_protegido, manifesto
        )
        registro.duracao_hash_s = time.monotonic() - t0
        registro.registrar_evidencia(
            "integridade_hash", integro, manifesto.sha256, "confere" if integro else "não confere"
        )
        if not integro:
            registro.decisao = Decisao.REPROVADA.value
            registro.motivo = "Divergência de integridade (hash/tamanho não conferem)"
            return registro

        if configuracao == Configuracao.B:
            registro.decisao = Decisao.APROVADA.value
            registro.motivo = "Configuração B: autenticação e integridade aprovadas"
            return registro

        # --- Etapa 3: preparação do ambiente e restauração (C_sem_func, C) ---
        adaptador = _adaptador_ambiente(contexto)
        t0 = time.monotonic()
        try:
            instancia = adaptador.subir_postgres_temporario(
                id_tentativa=id_tentativa,
                imagem=imagem_postgres_override or contexto.imagem_postgres,
                limite_cpu=contexto.limite_cpu,
                limite_memoria=contexto.limite_memoria,
                timeout_disponibilidade_s=contexto.timeout_disponibilidade_s,
            )
        except docker_adapter.ContainerNaoDisponivel as exc:
            registro.duracao_preparacao_s = time.monotonic() - t0
            registro.decisao = Decisao.INCONCLUSIVA.value
            registro.motivo = f"Ambiente de restauração impedido de iniciar (C8): {exc}"
            registro.registrar_evidencia("preparacao_ambiente", False, "disponível", str(exc))
            return registro

        registro.duracao_preparacao_s = time.monotonic() - t0
        registro.registrar_evidencia("preparacao_ambiente", True, "disponível", contexto.provedor_ambiente)
        resultado_dependencias = postgres_adapter.preparar_dependencias(
            instancia, papeis_necessarios=entrada.dependencias
        )
        registro.duracao_preparacao_s = (registro.duracao_preparacao_s or 0) + resultado_dependencias.duracao_s
        dependencias_ok = resultado_dependencias.codigo_saida == 0
        registro.registrar_evidencia(
            "dependencias_declaradas", dependencias_ok,
            "código de saída 0", str(resultado_dependencias.codigo_saida),
            detalhe=resultado_dependencias.stderr[:500],
        )
        if not dependencias_ok:
            registro.decisao = Decisao.REPROVADA.value
            registro.motivo = "Falha de dependência declarada de recuperação (C5)"
            return registro

        t0 = time.monotonic()
        resultado_restauracao = postgres_adapter.restaurar(copia.caminho_protegido, instancia)
        registro.duracao_restauracao_s = resultado_restauracao.duracao_s
        restauracao_ok = resultado_restauracao.codigo_saida == 0
        registro.registrar_evidencia(
            "restauracao_pg_restore", restauracao_ok,
            "código de saída 0", str(resultado_restauracao.codigo_saida),
            detalhe=resultado_restauracao.stderr[:1000],
        )
        if not restauracao_ok:
            registro.decisao = Decisao.REPROVADA.value
            registro.motivo = "Falha na restauração nativa (pg_restore --exit-on-error)"
            return registro

        if configuracao == Configuracao.C_SEM_FUNC:
            registro.decisao = Decisao.APROVADA.value
            registro.motivo = "Configuração C_sem_func: restauração concluída sem erro"
            return registro

        # --- Etapa 4: validação de estrutura, conteúdo e negócio (C) ---
        if referencias_esperadas is None:
            registro.decisao = Decisao.INCONCLUSIVA.value
            registro.motivo = "Referências esperadas não fornecidas para validação funcional"
            return registro

        t0 = time.monotonic()
        resultado_estrutura = validar_estrutura(instancia)
        resultado_conteudo = validar_conteudo(instancia, referencias_esperadas)
        resultado_negocio = validar_regras_de_negocio(instancia, referencias_esperadas)
        registro.duracao_validacao_s = time.monotonic() - t0

        for parcial in (resultado_estrutura, resultado_conteudo, resultado_negocio):
            for ev in parcial.evidencias:
                registro.registrar_evidencia(
                    ev.teste, ev.aprovado, ev.valor_esperado, ev.valor_observado, ev.detalhe
                )

        tudo_aprovado = resultado_estrutura.aprovado and resultado_conteudo.aprovado and resultado_negocio.aprovado
        if tudo_aprovado:
            registro.decisao = Decisao.APROVADA.value
            registro.motivo = "Configuração C: estrutura, conteúdo e regras de negócio aprovados"
        else:
            registro.decisao = Decisao.REPROVADA.value
            motivos = []
            if not resultado_estrutura.aprovado:
                motivos.append("estrutura")
            if not resultado_conteudo.aprovado:
                motivos.append("conteúdo")
            if not resultado_negocio.aprovado:
                motivos.append("regra de negócio")
            registro.motivo = f"Falha funcional em: {', '.join(motivos)}"

        return registro

    except Exception as exc:
        # Falha de infraestrutura não comprova que o backup é defeituoso.
        registro.decisao = Decisao.INCONCLUSIVA.value
        registro.motivo = f"Execução interrompida ({type(exc).__name__}); verifique o ambiente e tente novamente."
        registro.registrar_evidencia("execucao_completa", False, "concluída", type(exc).__name__)
        return registro
    finally:
        t0 = time.monotonic()
        if instancia is not None:
            try:
                _adaptador_ambiente(contexto).derrubar_postgres_temporario(instancia.nome_container)
            except Exception as exc:
                registro.registrar_evidencia("limpeza_ambiente", False, "removido", f"{type(exc).__name__}: {instancia.nome_container}")
                registro.motivo += "; limpeza do ambiente pendente"
            else:
                registro.registrar_evidencia("limpeza_ambiente", True, "removido", "removido")
        try:
            file_adapter.limpar_diretorio_tentativa(diretorio_tentativa)
        except OSError as exc:
            # Falha de limpeza é registrada, não apaga a decisão já produzida (seção 5.3).
            registro.registrar_evidencia("limpeza", False, "diretório removido", str(exc))
        registro.duracao_limpeza_s = time.monotonic() - t0
        registro.tempo_decisao_s = time.monotonic() - inicio_decisao

