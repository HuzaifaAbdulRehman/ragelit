import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest

from app.documents.chunking import chunk_sections
from app.documents.extraction import DocumentError, TextSection, extract_text
from app.documents.storage import stream_upload


def test_text_extraction_and_chunks_preserve_source(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_text("Alpha project.\n\n" + "Evidence " * 500, encoding="utf-8")
    sections = extract_text(source, "text/plain")
    chunks = chunk_sections(sections, UUID(int=1), max_chars=400, overlap=40)
    assert len(chunks) > 1
    assert all(0 < len(chunk.text) <= 400 for chunk in chunks)
    assert all(chunk.location.startswith("line ") for chunk in chunks)
    assert "Alpha project." in chunks[0].text
    assert [chunk.id for chunk in chunks] == [
        chunk.id
        for chunk in chunk_sections(sections, UUID(int=1), max_chars=400, overlap=40)
    ]


def test_chunks_do_not_mix_pdf_pages() -> None:
    chunks = chunk_sections(
        (TextSection("First", "page 1"), TextSection("Second", "page 2")), UUID(int=1)
    )
    assert [(chunk.text, chunk.location) for chunk in chunks] == [
        ("First", "page 1"),
        ("Second", "page 2"),
    ]


@pytest.mark.parametrize("body", [b"", b" \n\t", b"\xff\xfe\x00\x00"])
def test_empty_and_non_utf8_documents_are_rejected(tmp_path: Path, body: bytes) -> None:
    source = tmp_path / "empty.txt"
    source.write_bytes(body)
    with pytest.raises(DocumentError):
        extract_text(source, "text/plain")


def test_docx_extracts_paragraphs_without_expanding_archive(tmp_path: Path) -> None:
    source = tmp_path / "note.docx"
    with ZipFile(source, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p><w:r>'
            "<w:t>Project evidence</w:t></w:r></w:p></w:body></w:document>",
        )
        archive.writestr("../../escape.txt", "untrusted")
    sections = extract_text(
        source,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert sections == (TextSection("Project evidence", "paragraph 1"),)
    assert not (tmp_path.parent / "escape.txt").exists()


def test_malformed_docx_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "bad.docx"
    source.write_bytes(b"not a zip")
    with pytest.raises(DocumentError, match="invalid_document"):
        extract_text(
            source,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


def test_upload_size_limit_cleans_partial_file(tmp_path: Path) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield b"1234"
        yield b"5678"

    with pytest.raises(DocumentError, match="upload_too_large"):
        asyncio.run(
            stream_upload(
                body(),
                root=tmp_path,
                organization_id=uuid4(),
                version_id=uuid4(),
                max_bytes=5,
            )
        )
    assert list(tmp_path.rglob("*.upload")) == []


def test_stream_upload_hashes_and_uses_only_server_ids(tmp_path: Path) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield b"abc"

    organization_id, version_id = uuid4(), uuid4()
    upload = asyncio.run(
        stream_upload(
            body(),
            root=tmp_path,
            organization_id=organization_id,
            version_id=version_id,
            max_bytes=10,
        )
    )
    assert (
        upload.checksum
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert upload.byte_count == 3
    assert upload.path.read_bytes() == b"abc"
    assert upload.storage_key == f"{organization_id}/{version_id}.upload"
