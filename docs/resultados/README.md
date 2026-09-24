# Resultados do TCC

A fonte é a exportação `resumo (2).csv` fornecida pela autora, preservada no material suplementar entregue à autora, byte a byte, como `resumo_original_2026-09-24.csv`. SHA-256: `ccc6c9fef7fb8b6f9a394358afb47930528ae757be57e54645419abb24d40d49`.

Coloque o CSV original do material suplementar em `docs/resultados/resumo_original_2026-09-24.csv` e execute `python scripts/consolidar_resultados.py` na raiz para reproduzir os arquivos derivados. Nenhum dado foi coletado novamente para completar a matriz.

Seleção: ordenar por `instante_inicio_utc`, desempatar pela ordem original, excluir IDs incompatíveis com `pedidos_seed{semente}_{cenario}` e manter a primeira tentativa por cenário, semente, configuração, ID, versão e provedor. Decisão e duração não influenciam a escolha.

- 40 registros originais; 31 retidos, 7 repetições e 2 identificações incompatíveis.
- Linhas de dados excluídas por repetição: 5, 22, 25, 26, 27, 28 e 29.
- Linhas excluídas por identificação incompatível: 23 e 24 (`Oii`). A numeração exclui o cabeçalho.
- Não há registro de C7/C_sem_func. `sem_registro` não equivale a `inconclusiva`.
- 16 aprovações e 15 reprovações na amostra selecionada. Todas as linhas registram versão 0.2.2 e provedor Neon, embora A/B não criem banco.
- Uma semente, um volume, uma tentativa retida por célula. Sem inferência estatística de população, sem média de repetições, sem comparação de desempenho Docker/Neon.
- CPU, memória e espaço são `NA`. Os tempos totais incluem a limpeza.

A auditoria registra o UUID preservado para cada repetição. O CSV bruto e os resultados legados em `results/` não foram reescritos. As imagens ilustram a interface; métricas foram calculadas somente a partir dos dados selecionados.

## Disponibilidade dos arquivos

Este diretório publica somente `matriz_observada.csv` e `metricas.json`, sem UUIDs nem horários individuais. O CSV original, os 31 registros selecionados e a auditoria detalhada são entregues à autora como material suplementar do TCC. O script gera esses arquivos localmente; eles estão excluídos do versionamento público.
