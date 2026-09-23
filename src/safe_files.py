"""Validação de uploads antes de qualquer escrita no disco."""
from pathlib import Path
import os
import re
import tempfile


def validar_id(copia_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", copia_id):
        raise ValueError("ID inválido: use letras, números, ponto, hífen ou sublinhado, sem pastas.")
    return copia_id


def salvar_uploads(pasta: Path, copia_id: str, dump, manifest, referencias=None):
    validar_id(copia_id)
    pasta = Path(pasta).resolve()
    arquivos = [(".dump", dump, 200), (".manifest.json", manifest, 5),
                (".referencias.json", referencias, 5)]
    conteudos = []
    for sufixo, upload, limite_mb in arquivos:
        destino = pasta / (copia_id + sufixo)
        if destino.is_symlink() or destino.resolve().parent != pasta:
            raise ValueError("Destino de upload inválido.")
        dados = upload.getvalue() if upload is not None else None
        if sufixo != ".referencias.json" and not dados:
            raise ValueError("Envie um backup e um manifesto não vazios.")
        if dados is not None and len(dados) > limite_mb * 1024 * 1024:
            raise ValueError(f"O arquivo {sufixo} excede {limite_mb} MB.")
        conteudos.append((destino, dados))
    pasta.mkdir(parents=True, exist_ok=True)
    # Chamador mantém a trava durante upload, leitura e execução.
    for destino, dados in conteudos:
        if dados is None:
            destino.unlink(missing_ok=True)  # nunca reutilizar referências de outro upload
            continue
        temporario = None
        try:
            with tempfile.NamedTemporaryFile(dir=pasta, delete=False) as f:
                temporario = Path(f.name)
                f.write(dados)
            os.replace(temporario, destino)
        finally:
            if temporario is not None:
                temporario.unlink(missing_ok=True)
