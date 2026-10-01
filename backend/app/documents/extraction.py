from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
from pypdf.errors import PdfReadError

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
            reader = PdfReader(path, strict=True)
            if reader.is_encrypted or len(reader.pages) > 1000:
                raise DocumentError("invalid_document")
            total = 0
            for number, page in enumerate(reader.pages, start=1):
                contents = page.get_contents()
                if contents is not None and len(contents.get_data()) > 16 * 1024 * 1024:
                    raise DocumentError("extraction_limit")
                text = page.extract_text() or ""
                total += len(text)
                if total > MAX_TEXT:
                    raise DocumentError("extraction_limit")
                if text.strip():
                    sections.append(TextSection(text, f"page {number}"))
        else:
            raise DocumentError("unsupported_document_type", 415)
    except (
        UnicodeError,
        BadZipFile,
        KeyError,
        ElementTree.ParseError,
        PdfReadError,
        ValueError,
    ) as error:
        raise DocumentError("invalid_document") from error
    if sum(len(section.text) for section in sections) > MAX_TEXT:
        raise DocumentError("extraction_limit")
    if not sections:
        raise DocumentError("empty_document")
    return tuple(sections)
