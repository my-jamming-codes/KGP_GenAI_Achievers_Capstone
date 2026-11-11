"""Load documents from disk and split them into overlapping chunks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".rst", ".py", ".java", ".json", ".yaml", ".yml", ".html", ".csv"}


@dataclass(frozen=True)
class Chunk:
    text: str
    source: str
    position: int  # chunk number within its source


def read_document(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF support needs: pip install 'ragkit[pdf]'") from exc
        return "\n\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8", errors="replace")


def iter_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    wanted = TEXT_SUFFIXES | {".pdf"}
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in wanted)


def split_text(text: str, size: int = 800, overlap: int = 120) -> list[str]:
    """Greedy paragraph packing up to `size` characters, with character overlap.

    Paragraphs longer than `size` are hard-split. Sizes are in characters, which
    is a cheap proxy for tokens (roughly 4 chars per token for English).
    """
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    pieces: list[str] = []
    for para in (p.strip() for p in text.split("\n\n")):
        if not para:
            continue
        while len(para) > size:
            pieces.append(para[:size])
            para = para[size - overlap :]
        pieces.append(para)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > size:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{piece}" if tail else piece
            if len(current) > size:  # tail + piece overflowed; drop the tail
                current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def chunk_path(root: Path, size: int = 800, overlap: int = 120) -> list[Chunk]:
    chunks: list[Chunk] = []
    for file in iter_files(root):
        for i, text in enumerate(split_text(read_document(file), size, overlap)):
            chunks.append(Chunk(text=text, source=str(file), position=i))
    return chunks
