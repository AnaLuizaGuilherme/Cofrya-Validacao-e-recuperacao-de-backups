# Compatibilidade de caminhos do Cofrya

O código canônico está na raiz do repositório. Consulte [README principal](../README.md) para instalação, execução e limites do experimento.

Os comandos abaixo continuam funcionando nesta pasta:

```bash
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
# ou: python -m streamlit run cofrya_app.py
```

Todos os pontos de entrada web abrem a aplicação unificada com cadastro/login. O pacote `src` redireciona para a implementação da raiz, evitando manter duas versões divergentes do executor e dos resultados.
