# Verificador Automatizado de Integridade e Recuperabilidade de Backups PostgreSQL

Implementação de referência da arquitetura descrita no TCC "Verificação Automatizada
da Integridade e da Recuperabilidade de Backups de Bancos de Dados".

Ferramenta local, linha de comando, execução sequencial, resultados em CSV.
Sem API, sem interface web, sem fila persistente — conforme o escopo do protótipo (seção 4.2/5).

## Componentes (mapeados às seções do TCC)

- `src/generator.py` — gerador de dados sintéticos determinísticos (seção 4.3): clientes,
  produtos, pedidos, itens e pagamentos, com a regra de negócio "total do pedido = soma dos itens".
- `src/manifest.py` — manifesto autenticado com HMAC-SHA-256 (seções 2.1 e 5.5).
- `src/adapters/file_adapter.py` — leitura somente-leitura do repositório de backups,
  cópia para área protegida antes da restauração (seção 5.3).
- `src/adapters/docker_adapter.py` — ambiente PostgreSQL temporário em contêiner,
  com limites de CPU/memória (seção 5.5).
- `src/adapters/postgres_adapter.py` — chamadas a `pg_dump`/`pg_restore` como lista de
  argumentos (nunca shell string), com `--exit-on-error` (seção 5.4).
- `src/scenarios.py` — cenários C0 a C8 com injeção de falhas controladas (seção 4.4, Quadro 3).
- `src/validators.py` — validação de estrutura, conteúdo e regras de negócio (funcional)
  do banco restaurado (seção 5.4).
- `src/executor.py` — executor sequencial que aplica as configurações A, B, C-sem-testes-funcionais
  e C, registra tempos, decisões e evidências (seções 4.5, 4.6, 5.1, 5.3).
- `src/results.py` — gravação dos resultados em CSV (resumo + evidências), seção 5.6.
- `src/cli.py` — interface de linha de comando.

## Configurações avaliadas (seção 4.5)

| Config | Etapas |
|---|---|
| A | Existência do arquivo + sucesso informado pelo produtor |
| B | A + autenticação do manifesto (HMAC) + conferência de hash (SHA-256) |
| C_sem_func | B + preparação do ambiente temporário + restauração via `pg_restore --exit-on-error` |
| C | C_sem_func + validação de estrutura, conteúdo e regras de negócio |

## Uso rápido

```bash
pip install -r requirements.txt

# 1. Gerar uma base sintética + backup válido de referência (C0)
python -m src.cli gerar-base --semente 1 --volume 1000 --saida ./repositorio

# 2. Rodar uma verificação com uma configuração específica
python -m src.cli verificar --copia-id pedidos_seed1_c0 --config C \
    --repositorio ./repositorio --catalogo ./config/catalog.json \
    --chave-env TCC_HMAC_KEY

# 3. Rodar a matriz completa (cenários x configurações x sementes)
python -m src.cli matriz --repositorio ./repositorio --catalogo ./config/catalog.json \
    --cenarios C0 C1 C2 C3 C4 C5 C6 C7 C8 --configs A B C_sem_func C \
    --sementes 1 2 3 --saida ./results
```

Resultados: `results/resumo.csv` (uma linha por tentativa) e `results/evidencias.csv`
(uma linha por verificação individual dentro de uma tentativa), ligados por `id_tentativa`.

## Modelo de ameaça (seção 4.4)

A chave HMAC e o catálogo autorizado (`config/catalog.json`) devem ficar fora do
repositório de backups. O ambiente de exemplo lê a chave de uma variável de ambiente
(`TCC_HMAC_KEY`), nunca de um arquivo dentro do repositório verificado.
