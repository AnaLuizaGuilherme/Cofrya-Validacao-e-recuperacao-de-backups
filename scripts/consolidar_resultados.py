"""Reproduz a seleção do TCC sem escolher resultados pela decisão ou duração.

Entrada preservada: exportação do Cofrya 0.2.2, 40 registros.
Chave: cenário, semente, configuração, cópia, versão e provedor.
Conserva a primeira ocorrência cronológica com ID coerente com o cenário.
"""
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1] / "docs" / "resultados"
CONFIGS = ("A", "B", "C_sem_func", "C")
CENARIOS = tuple(f"C{i}" for i in range(8))
CHAVE = ("cenario", "semente", "configuracao", "id_copia", "versao_codigo", "provedor_ambiente")


def consolidar(linhas):
    vistos, retidos, auditoria = {}, [], []
    ordenadas = sorted(enumerate(linhas, 1), key=lambda x: (datetime.fromisoformat(x[1]["instante_inicio_utc"]), x[0]))
    for numero, linha in ordenadas:
        esperado = f"pedidos_seed{linha['semente']}_{linha['cenario'].lower()}"
        chave = tuple(linha[c] for c in CHAVE)
        if linha["cenario"] not in CENARIOS or linha["configuracao"] not in CONFIGS:
            motivo, mantida = "fora_do_protocolo", ""
        elif linha["id_copia"] != esperado:
            motivo, mantida = "identificacao_incompativel", ""
        elif chave in vistos:
            motivo, mantida = "repeticao", vistos[chave]
        else:
            motivo, mantida = "incluida", linha["id_tentativa"]
            vistos[chave] = mantida
            retidos.append(linha)
        auditoria.append({"linha_dados_original": numero, "id_tentativa": linha["id_tentativa"],
                          "cenario": linha["cenario"], "configuracao": linha["configuracao"],
                          "selecao": motivo, "id_mantido": mantida})
    return retidos, auditoria


def gravar_csv(nome, linhas, campos):
    with (RAIZ / nome).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(linhas)


def main():
    origem = RAIZ / "resumo_original_2026-09-24.csv"
    with origem.open(encoding="utf-8-sig", newline="") as f:
        leitor = csv.DictReader(f)
        campos = leitor.fieldnames
        linhas = list(leitor)
    retidos, auditoria = consolidar(linhas)
    gravar_csv("resumo_sem_repeticoes.csv", retidos, campos)
    gravar_csv("auditoria_selecao.csv", auditoria, list(auditoria[0]))
    mapa = {(x["cenario"], x["configuracao"]): x for x in retidos}
    matriz = [{"cenario": c, **{k: mapa.get((c,k), {}).get("decisao", "sem_registro") for k in CONFIGS}} for c in CENARIOS]
    gravar_csv("matriz_observada.csv", matriz, ["cenario", *CONFIGS])
    metricas = {
        "sha256_csv_original": hashlib.sha256(origem.read_bytes()).hexdigest(),
        "criterio": "primeira tentativa cronologica com ID compativel; sem selecionar por decisao ou tempo",
        "chave_deduplicacao": list(CHAVE), "n_original": len(linhas), "n_retido": len(retidos),
        "selecao": dict(Counter(x["selecao"] for x in auditoria)),
        "decisoes": dict(Counter(x["decisao"] for x in retidos)),
        "sem_registro": [{"cenario": c, "configuracao": k} for c in CENARIOS for k in CONFIGS if (c,k) not in mapa],
        "por_configuracao": {k: dict(Counter(x["decisao"] for x in retidos if x["configuracao"] == k)) for k in CONFIGS},
        "deteccao_conjunto_comum_C1_C6": {k: {"reprovadas": sum(mapa[(c,k)]["decisao"] == "reprovada" for c in CENARIOS[1:7]), "total": 6} for k in CONFIGS},
    }
    (RAIZ / "metricas.json").write_text(json.dumps(metricas, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(metricas, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
