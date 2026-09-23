# Cofrya

Um backup precisa fazer mais do que existir.

Protótipo em Python e Streamlit para verificar integridade, restauração e regras de negócio em backups PostgreSQL. Inclui cadastro/login, histórico por conta e uma ferramenta complementar de leitura de CSV, JSON, SQLite e índices de arquivos `pg_dump`.

## Executar

Python 3.10 ou superior; Python 3.12 utilizado nos testes.

```bash
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

`streamlit_app.py` e `cofrya_app.py` abrem a mesma aplicação com login. Os três caminhos antigos dentro de `verificador_backups/` continuam funcionando. A implementação canônica fica na raiz; `verificador_backups/src/__init__.py` apenas redireciona imports legados para `src/`.

| Arquivo/pasta | Responsabilidade |
| --- | --- |
| `cofrya_app.py` | Cadastro, login, troca de senha e navegação |
| `postgres_app.py` | Interface PostgreSQL, upload, histórico e exportação |
| `arquivos_app.py` | Manifestos e verificação de arquivos genéricos |
| `src/executor.py` | Etapas, decisões, métricas e limpeza |
| `src/manifest.py` | HMAC-SHA-256 e SHA-256 do backup |
| `src/validators.py` | Estrutura, contagens e totalização de pedidos |
| `src/adapters/` | Arquivos, PostgreSQL, Docker e Neon |
| `src/results.py` | CSVs de resumo e evidências |
| `src/auth.py` | Contas SQLite, senhas com PBKDF2 e limite de tentativas |
| `src/safe_files.py` | Validação de destinos e limites dos uploads |

## Publicar no Streamlit Community Cloud

1. Selecione este repositório e a branch `main`.
2. Use **`streamlit_app.py`** como arquivo principal. Se o app já estiver apontando para um dos caminhos em `verificador_backups/`, o redirecionamento é mantido.
3. Em Settings → Secrets, configure os valores reais, sem publicá-los no GitHub:

```toml
TCC_HMAC_KEY = "chave usada para assinar os backups do laboratório"
NEON_API_KEY = "chave da API Neon"
NEON_PROJECT_ID = "id-do-projeto-neon"
```

4. Crie sua conta na página inicial. A e B não usam Neon. C e C_sem_func exigem `pg_restore` e um ambiente de restauração.
5. Para Neon, use um **projeto exclusivo do laboratório**, com o papel `neondb_owner`. O app seleciona Neon inicialmente quando suas duas configurações estão disponíveis. `packages.txt` instala `postgresql-client`; a versão de `pg_restore` deve ser compatível com a versão que gerou o dump. Um dump de versão mais recente pode exigir um cliente mais recente.

O modo Neon cria um branch por tentativa e um banco novo dentro dele, evitando restaurar sobre tabelas herdadas do banco pai. Ao terminar, solicita a remoção do branch. Falhas de remoção são registradas e precisam ser verificadas no console Neon. A criação e remoção usam a [API oficial de branches](https://api-docs.neon.tech/reference/createprojectbranch) e a [API de bancos](https://api-docs.neon.tech/reference/createprojectbranchdatabase).

Neon é uma alternativa de demonstração em nuvem. Não equivale ao ambiente Docker do protocolo experimental: rede, provedor, versão do servidor e recursos são diferentes. Não misture os tempos dos dois ambientes na mesma comparação sem controlar essas diferenças.

## Contas e persistência

Cada usuário possui `dados/usuarios/<usuario>/repositorio`, `results` e arquivos temporários próprios. Sair limpa os dados da sessão, inclusive o último resultado e chaves mostradas na interface. O login bloqueia uma conta por até 10 minutos após oito tentativas incorretas na janela.

Contas e arquivos são locais ao servidor. **No Community Cloud, reinícios/reimplantações podem apagar esses dados.** Baixe os CSVs; para persistência garantida, hospede em volume persistente ou implemente armazenamento externo. `COFRYA_DATA_DIR` permite indicar um volume persistente onde o host oferecer essa opção. Não versionar `dados/`, `.env` nem `.streamlit/secrets.toml`.

Os resultados antigos em `results/` são preservados como evidência histórica. Não são atribuídos automaticamente a novas contas. Não foram produzidos novos resultados científicos por esta correção.

## Configurações do experimento

| Configuração | O que uma aprovação significa |
| --- | --- |
| A | Backup e manifesto existem; não autentica nem restaura |
| B | Manifesto autenticado; identificador, idade, tamanho e hash conferem |
| C_sem_func | B e restauração concluída com `pg_restore --exit-on-error` |
| C | C_sem_func e validações de estrutura, contagens e regra de totalização |

Selecionar C0–C8 apenas rotula uma tentativa. As falhas devem ser preparadas separadamente. A interface não injeta falhas ao selecionar um cenário. C8 por imagem inexistente aplica-se somente a Docker; para Neon, uma indisponibilidade precisa ser controlada no próprio provedor.

A chave HMAC e as referências esperadas pertencem ao domínio confiável. As referências devem ser capturadas **antes** da injeção de falhas e preservadas pelo pesquisador. O hash das referências registrado no CSV permite identificar a entrada utilizada, mas **não autentica a origem das referências**. O upload é destinado ao laboratório controlado: receber referências de um adversário não estabelece uma referência confiável. Uma aprovação C também não certifica todas as regras de uma aplicação arbitrária; os validadores atuais usam o esquema sintético de pedidos.

Arquivos genéricos têm escopo separado: sem manifesto, verifica-se leitura/estrutura, sem atestar integridade em relação ao original. `pg_restore --list` avalia o índice, não restaura os dados. A ferramenta distingue resultado reprovado de inconclusivo quando um requisito não está disponível.

## Linha de comando

```bash
# Defina TCC_HMAC_KEY no ambiente, fora do repositório de backups.
# Use exclusivamente um banco de origem de laboratório.
python -m src.cli gerar-base --semente 1 --volume 1000 --saida ./repositorio --dsn-origem "postgresql://..."
python -m src.cli verificar --copia-id pedidos_seed1_c0 --config C --repositorio ./repositorio --cenario C0 --semente 1 --saida ./results
python -m src.cli matriz --repositorio ./repositorio --catalogo ./config/catalog.json --configs A --configs B --configs C_sem_func --configs C --saida ./results
```

Sem `--dsn-origem`, o gerador cria SQL e um **placeholder**, não um backup restaurável. Use um `pg_dump` real nos ensaios C/C_sem_func. O catálogo da matriz é preparado pelo pesquisador; sementes e cenários vêm desse arquivo. As configurações são opções repetidas. Semente omitida em uma tentativa pode ser inferida de `seedN` no identificador; divergências são recusadas.

O executor local requer Docker, `pg_restore`, `psql` quando há dependências declaradas e um cliente compatível com o servidor. O Docker publica a porta somente em `127.0.0.1`. Execute dumps de laboratório confiáveis: restauração de SQL não constitui uma sandbox de execução de código hostil.

## Resultados e métricas

`resumo.csv` e `evidencias.csv` são ligados por `id_tentativa`. Novos registros incluem versão `0.2.0`, instante UTC, provedor e hash das referências. Preparação inclui inicialização do ambiente e dependências. O tempo total inclui limpeza. CPU, memória e espaço permanecem `NA` enquanto não houver instrumentação; não são zeros nem medições.

Registros anteriores à correção não devem fundamentar a decomposição de tempos: a inicialização não era somada à preparação. O exemplo legado também possui semente declarada `0` e ID `seed1`; reconcilie com os arquivos de origem ou repita o ensaio. O programa **não altera essa evidência retroativamente**. Ao acrescentar registros a CSVs legados compatíveis, apenas estende o cabeçalho; metadados antigos desconhecidos ficam `NA`. Escrita CSV é sequencial por processo; use uma instância por pasta de resultados.

## Testes

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
```

A suíte verifica gerador, manifesto, isolamento de uploads, autenticação, troca de contas, metadados, temporização, falhas de infraestrutura, limpeza e o fluxo B real pela interface Streamlit. Docker/Neon e o relógio são substituídos nos testes específicos do executor. Esses testes não são execuções da matriz experimental nem certificam uma restauração real na hospedagem.

O workflow `Testes Cofrya` executa a suíte em pushes para `main` e pull requests. A integração Codacy descrita nas instruções do repositório não estava disponível durante esta correção; para ativá-la, reconecte/reinicie seu servidor MCP e confira Settings → Copilot → Enable MCP servers no GitHub, ou contate o suporte Codacy.
