# Cofrya

Verificador de backups lógicos PostgreSQL em **Python**, com interface **Streamlit**, CLI e execução de restaurações temporárias em **Neon** ou **Docker**. O núcleo combina manifesto HMAC-SHA-256, integridade do arquivo, `pg_restore` e consultas SQL sobre os dados recuperados.

**Implementação:** `0.2.3` · **Python:** 3.10+ · **CI:** Python 3.12

[Aplicação](https://cofrya.streamlit.app) · [Guia técnico](docs/TCC_Cofrya.md) · [Resultados experimentais](docs/resultados/README.md) · [Código](https://github.com/AnaLuizaGuilherme/Cofrya-Validacao-e-recuperacao-de-backups)

## Executar localmente

```bash
git clone https://github.com/AnaLuizaGuilherme/Cofrya-Validacao-e-recuperacao-de-backups.git
cd Cofrya-Validacao-e-recuperacao-de-backups
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

No PowerShell, substitua a ativação por `.venv\Scripts\Activate.ps1`. Instale os clientes PostgreSQL no host e confira `pg_restore --version`. A geração de dumps exige também `pg_dump` e `psql`; a preparação de papéis na restauração utiliza `psql`.

Para o modo Docker, mantenha o daemon disponível. Para o modo Neon, configure um projeto exclusivo de laboratório e as credenciais abaixo. A e B não criam banco temporário.

## Configuração

A interface lê a chave HMAC e as credenciais Neon do ambiente ou de `.streamlit/secrets.toml`. Na CLI, `TCC_HMAC_KEY` deve estar no **ambiente**. Use valores reais somente na sua configuração privada:

```toml
TCC_HMAC_KEY = "substitua-pela-chave-que-assinou-os-artefatos"
NEON_API_KEY = "substitua-pela-chave-da-api-neon"
NEON_PROJECT_ID = "substitua-pelo-id-do-projeto"
```

| Parâmetro | Comportamento |
| --- | --- |
| `TCC_HMAC_KEY` | Chave compartilhada para autenticar o manifesto |
| `NEON_API_KEY`, `NEON_PROJECT_ID` | Criação e remoção dos recursos temporários no Neon |
| `COFRYA_DATA_DIR` | Variável de ambiente; diretório de contas e arquivos, padrão `./dados` |
| `--provedor-ambiente docker\|neon` | Opção de `src.cli verificar`; padrão `docker` |
| `--idade-maxima-dias` | Política temporal de `verificar`; padrão 30 |

No Streamlit Community Cloud, selecione `main` e `streamlit_app.py`, e preencha **Settings → Secrets**. [`packages.txt`](packages.txt) instala o cliente PostgreSQL sem fixar sua versão; confira a compatibilidade com os dumps.

## Contrato de verificação

Cada cópia usa `<id>.dump` e `<id>.manifest.json`. A configuração C exige também `<id>.referencias.json`, obtido do estado correto da base antes de introduzir falhas.

| Configuração | Etapas | O que uma aprovação demonstra |
| --- | --- | --- |
| `A` | Existência e cópia de trabalho | Dump e manifesto encontrados |
| `B` | A + HMAC + ID/idade + tamanho/SHA-256 | Autenticidade do manifesto e integridade do arquivo |
| `C_sem_func` | B + ambiente temporário + `pg_restore` | Restauração encerrada sem erro |
| `C` | C_sem_func + consultas SQL | Tabelas, contagens e totalização dos pedidos aprovadas |

O executor retorna `RegistroTentativa` com decisão `aprovada`, `reprovada` ou `inconclusiva`, evidências e tempos por etapa. A persistência é explícita: `RegistradorCSV.gravar(registro)` escreve `resumo.csv` e `evidencias.csv`.

A validação funcional é específica das tabelas `clientes`, `produtos`, `pedidos`, `itens_pedido` e `pagamentos`. Ela não compara todos os registros nem valida regras de outros domínios. Veja os [contratos de entrada](docs/TCC_Cofrya.md#contratos-de-entrada), o [fluxo de decisões](docs/TCC_Cofrya.md#executor-e-decisões) e a [consulta SQL](docs/TCC_Cofrya.md#validação-funcional-e-sql).

## CLI e API Python

Exemplo Bash, com artefatos previamente gerados em `./repositorio`:

```bash
export TCC_HMAC_KEY='substitua-pela-chave-que-assinou-os-artefatos'

python -m src.cli verificar \
  --copia-id pedidos_seed1_c0 --config B \
  --repositorio ./repositorio --cenario C0 --semente 1 \
  --saida ./results

python -m src.cli verificar --help
```

Para C no Neon, configure as credenciais, forneça as referências e use `--config C --provedor-ambiente neon`. Uma decisão `reprovada` não gera automaticamente código de saída não zero na CLI; integrações devem avaliar a decisão retornada pela API ou registrada no CSV.

- [Exemplo completo pela API Python](docs/TCC_Cofrya.md#api-python).
- [Geração de C0–C7 e execução da matriz](docs/TCC_Cofrya.md#cli-e-geração-de-cenários).
- [Ciclo de vida dos adaptadores Neon/Docker](docs/TCC_Cofrya.md#adaptadores-de-infraestrutura).

Sem `--dsn-origem`, `gerar-base` produz SQL e um placeholder, **não um dump restaurável**. A geração com DSN popula o banco indicado e requer uma base vazia de laboratório. Selecionar o cenário na verificação apenas rotula a tentativa; não injeta falhas. O guia distingue o protocolo experimental do comportamento atual dos geradores C3 e C7.

## Pacote completo de cenários C0–C7

[Baixar Cofrya_Cenarios_C0_C7.zip](tests/fixtures/Cofrya_Cenarios_C0_C7.zip?raw=true)

Um único ZIP com **24 arquivos de entrada** (dump, manifesto e referências por cenário), instruções para o Streamlit, matriz esperada das **32 combinações** entre C0–C7 e A/B/C_sem_func/C, proveniência e inventário SHA-256. Extraia o arquivo e envie os três arquivos da mesma pasta em **Backups PostgreSQL**. Use semente 1, idade máxima de 30 dias e a chave de laboratório configurada fora do repositório.

Os artefatos são os pacotes históricos de 23–24/09/2026, preservados sem alteração. Após 30 dias, a idade pode mudar o resultado dos cenários que deveriam passar nessa etapa. C5 exige que `papel_leitura_restrita` esteja ausente no destino. Consulte o `README.md` interno antes dos ensaios.

A matriz do ZIP descreve expectativas; a unificação não representa uma nova execução experimental. **C7/C_sem_func permanece sem registro na coleta do TCC.**

SHA-256 do ZIP: `965606bffbc92c212487d2b087d91df54280bd5883d98510b72432f31ab926af`.

## Organização do código

| Arquivo ou diretório | Responsabilidade |
| --- | --- |
| [`streamlit_app.py`](streamlit_app.py), [`cofrya_app.py`](cofrya_app.py) | Inicialização, autenticação e navegação |
| [`postgres_app.py`](postgres_app.py), [`arquivos_app.py`](arquivos_app.py) | Formulários, uploads e resultados |
| [`src/cli.py`](src/cli.py) | Comandos Click |
| [`src/executor.py`](src/executor.py) | Orquestração, decisões e limpeza |
| [`src/manifest.py`](src/manifest.py), [`src/validators.py`](src/validators.py) | HMAC, hash e verificações SQL |
| [`src/adapters/`](src/adapters/) | Arquivos, subprocessos PostgreSQL, Docker e Neon |
| [`src/results.py`](src/results.py) | Modelos de resultado e CSVs |
| [`src/auth.py`](src/auth.py), [`src/safe_files.py`](src/safe_files.py) | Contas, caminhos e limites de upload |
| [`scripts/consolidar_resultados.py`](scripts/consolidar_resultados.py) | Seleção e agregação da amostra experimental |
| [`tests/`](tests/) | Testes unitários, regressões e interface |

A interface e a CLI chamam o executor diretamente. Não há API HTTP própria, fila persistente ou worker distribuído. `verificador_backups/` mantém entradas antigas de compatibilidade.

## Testes

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
```

A [CI](.github/workflows/tests.yml) executa a suíte em pushes para `main` e pull requests. A revisão 0.2.3 foi validada com **80 testes**. Os testes de infraestrutura usam substitutos controlados; não representam novas restaurações no Neon. O [guia de testes](docs/TCC_Cofrya.md#testes-e-manutenção) relaciona os arquivos de teste às responsabilidades verificadas.

## Operação e limites

- **Neon:** branch temporário por tentativa, banco novo `cofrya_<uuid>`, conexão direta com `pooled=false` e confirmação SQL do destino. Ao terminar, o código solicita a exclusão do branch. Papéis podem ser herdados do branch pai; isso importa para C5.
- **Persistência:** contas em SQLite e arquivos por usuário no disco do processo. O Neon de restauração não guarda contas ou histórico. Um volume efêmero exige exportar os dados que precisam ser preservados.
- **Concorrência:** trava de execução e locks dos CSVs limitados ao processo. Não há coordenação entre múltiplos processos nem transação atômica entre os dois CSVs.
- **Segurança:** senhas derivadas por PBKDF2-HMAC-SHA-256; uploads com validação de caminhos e tamanho. Use dumps sintéticos confiáveis: o banco temporário não é uma sandbox para SQL hostil.
- **Arquivos complementares:** CSV, JSON, SQLite e `.dump` possuem verificações de leitura e integridade. Para `.dump`, esse módulo lê apenas o índice com `pg_restore --list`; não executa restauração ou validação funcional.

Consulte [persistência e concorrência](docs/TCC_Cofrya.md#persistência-e-concorrência) e [diagnóstico](docs/TCC_Cofrya.md#diagnóstico) antes de integrar o executor a outro serviço.

## Evidência experimental

Os ensaios do TCC utilizaram **Neon**, versão **0.2.2**, semente 1 e mil pedidos. A consolidação preservou 31 de 40 registros: 16 aprovações, 15 reprovações e nenhuma inconclusão. **C7/C_sem_func está sem registro**, sem substituição pelo valor esperado. Sete repetições e dois IDs incompatíveis foram excluídos por critérios independentes de decisão e duração.

Em C1–C6, a detecção observada foi **A: 0/6 → B: 3/6 → C_sem_func: 4/6 → C: 6/6**. São contagens descritivas dos cenários preparados, com uma observação selecionada por combinação, não estimativas de eficácia geral. Os testes automatizados não alteram esses resultados históricos.

[Protocolo, matriz e limitações](docs/TCC_Cofrya.md#dados-experimentais) · [Matriz observada](docs/resultados/matriz_observada.csv) · [Métricas](docs/resultados/metricas.json) · [Seleção auditável](docs/resultados/README.md)

## Interface

Capturas fornecidas pela autora em 23/09/2026, selecionadas sem chaves em texto aberto. Ilustram a interface; as contagens experimentais vêm dos CSVs consolidados.

![C4 em C: restauração concluída e regra de negócio reprovada](docs/imagens/cofrya-falha-funcional-c4.png)

<details>
<summary>Upload de backup, manifesto e referências</summary>

![Formulário de verificação PostgreSQL](docs/imagens/cofrya-verificacao-postgresql.png)

</details>

<details>
<summary>Login e cadastro</summary>

![Login do Cofrya](docs/imagens/cofrya-login.png)

![Cadastro do Cofrya](docs/imagens/cofrya-cadastro.png)

</details>

<details>
<summary>Proteção e leitura de arquivos</summary>

![Geração de manifesto com chave mascarada](docs/imagens/cofrya-proteger-arquivo.png)

![Índice PostgreSQL legível com aviso sobre o escopo da verificação](docs/imagens/cofrya-leitura-indice.png)

</details>
