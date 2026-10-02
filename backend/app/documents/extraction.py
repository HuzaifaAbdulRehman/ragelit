import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from app.core.problems import ProblemException

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_TEXT = 4_000_000


class DocumentError(ProblemException):
    def __init__(self, code: str, status: int = 422) -> None:
        details = {
            "resource_not_found": "The requested resource was not found.",
            "upload_too_large": "The document exceeds the upload limit.",
            "empty_document": "The document contains no supported text.",
            "unsupported_document_type": "Upload TXT, Markdown, DOCX, or a text PDF.",
            "invalid_filename": "Use a filename without directory components.",
            "invalid_document": "The document could not be read.",
            "extraction_limit": "The extracted document exceeds processing limits.",
        }
        super().__init__(
            status=status,
            code=code,
            title="Document request failed",
            detail=details.get(code, "The document operation could not be completed."),
        )


@dataclass(frozen=True, slots=True)
class TextSection:
    text: str
    location: str


def _extract_pdf(path: Path) -> list[TextSection]:
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    environment = {
        key: os.environ[key]
        for key in ("SystemRoot", "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP")
        if key in os.environ
    }
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(Path(__file__).with_name("pdf_worker.py")),
                str(path.resolve()),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
            env=environment,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as error:
        raise DocumentError("extraction_limit") from error
    except OSError as error:
        raise DocumentError("extraction_unavailable", 503) from error
    if result.returncode != 0:
        raise DocumentError("extraction_limit")
    payload = json.loads(result.stdout)
    if "error" in payload:
        raise DocumentError(payload["error"])
    return [
        TextSection(section["text"], section["location"])
        for section in payload["sections"]
    ]


def extract_text(path: Path, media_type: str) -> tuple[TextSection, ...]:
    sections: list[TextSection] = []
    try:
        if media_type in ("text/plain", "text/markdown"):
            if path.stat().st_size > MAX_TEXT:
                raise DocumentError("extraction_limit")
            content = path.read_text(encoding="utf-8-sig")
            if "\x00" in content:
                raise DocumentError("invalid_document")
            lines = content.splitlines()
            start = 1
            paragraph: list[str] = []
            for number, line in enumerate(lines, start=1):
                if line.strip():
                    if not paragraph:
                        start = number
                    paragraph.append(line)
                elif paragraph:
                    sections.append(TextSection("\n".join(paragraph), f"line {start}"))
                    paragraph = []
            if paragraph:
                sections.append(TextSection("\n".join(paragraph), f"line {start}"))
        elif media_type == DOCX:
            with ZipFile(path) as archive:
                if (
                    sum(item.file_size for item in archive.infolist())
                    > 64 * 1024 * 1024
                ):
                    raise DocumentError("extraction_limit")
                info = archive.getinfo("word/document.xml")
                if info.file_size > 8 * 1024 * 1024:
                    raise DocumentError("extraction_limit")
                root = ElementTree.fromstring(archive.read(info))
            namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            for number, paragraph_node in enumerate(
                root.iter(f"{namespace}p"), start=1
            ):
                text = "".join(
                    node.text or "" for node in paragraph_node.iter(f"{namespace}t")
                )
                if text.strip():
                    sections.append(TextSection(text, f"paragraph {number}"))
        elif media_type == "application/pdf":
            sections = _extract_pdf(path)
        else:
            raise DocumentError("unsupported_document_type", 415)
    except (
        UnicodeError,
        BadZipFile,
        KeyError,
        ElementTree.ParseError,
        ValueError,
    ) as error:
        raise DocumentError("invalid_document") from error
    if sum(len(section.text) for section in sections) > MAX_TEXT:
        raise DocumentError("extraction_limit")
    if not sections:
        raise DocumentError("empty_document")
    return tuple(sections)
