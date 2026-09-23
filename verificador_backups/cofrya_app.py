"""Entrada compatível: usa o Cofrya unificado da raiz."""
import sys
from pathlib import Path
RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from cofrya_app import main
if __name__ == "__main__":
    main()
