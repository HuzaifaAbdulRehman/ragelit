"""Run PDF parsing with process-level resource limits."""

import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path

MAX_TEXT = 4_000_000
MAX_DECODED = 16 * 1024 * 1024


def limit_process(max_bytes: int = 256 * 1024 * 1024) -> None:
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))
        return

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64),
            ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD),
            ("min_working_set", ctypes.c_size_t),
            ("max_working_set", ctypes.c_size_t),
            ("active_processes", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class IOCounters(ctypes.Structure):
        _fields_ = [(f"counter_{index}", ctypes.c_uint64) for index in range(6)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits),
            ("io", IOCounters),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t),
            ("peak_job_memory", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    limits.process_memory = max_bytes
    if not kernel.SetInformationJobObject(
        job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
    ):
        error = ctypes.get_last_error()
        kernel.CloseHandle(job)
        raise ctypes.WinError(error)
    if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        error = ctypes.get_last_error()
        kernel.CloseHandle(job)
        raise ctypes.WinError(error)
    # The child owns this handle until process exit, which releases it.


def parse_pdf(path: Path) -> list[dict[str, str]]:
    from pypdf import PdfReader, apply_configuration

    sections: list[dict[str, str]] = []
    decoded = text_count = 0
    with apply_configuration(
        disable_legacy_handling=True,
        maximum_declared_stream_length=MAX_DECODED,
        array_based_stream_maximum_output_length=MAX_DECODED,
        zlib_maximum_output_length=MAX_DECODED,
        lzw_maximum_output_length=MAX_DECODED,
        run_length_maximum_output_length=MAX_DECODED,
        image_maximum_buffer_size=MAX_DECODED,
        page_tree_maximum_entries=1000,
    ):
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted or len(reader.pages) > 1000:
            raise ValueError("invalid_document")
        for number, page in enumerate(reader.pages, start=1):
            remaining = MAX_DECODED - decoded
            if remaining <= 0:
                raise ValueError("extraction_limit")
            with apply_configuration(
                array_based_stream_maximum_output_length=remaining,
                zlib_maximum_output_length=remaining,
                lzw_maximum_output_length=remaining,
                run_length_maximum_output_length=remaining,
            ):
                contents = page.get_contents()
                if contents is not None:
                    decoded += len(contents.get_data())
                    if decoded > MAX_DECODED:
                        raise ValueError("extraction_limit")
                text = page.extract_text() or ""
            text_count += len(text)
            if text_count > MAX_TEXT:
                raise ValueError("extraction_limit")
            if text.strip():
                sections.append({"text": text, "location": f"page {number}"})
    return sections


def main() -> None:
    try:
        limit_process()
    except OSError:
        result: dict[str, object] = {"error": "extraction_unavailable"}
    else:
        from pypdf.errors import LimitReachedError

        try:
            sections = parse_pdf(Path(sys.argv[1]))
            result = {"sections": sections} if sections else {"error": "empty_document"}
        except (MemoryError, LimitReachedError):
            result = {"error": "extraction_limit"}
        except ValueError as error:
            code = (
                "extraction_limit"
                if str(error) == "extraction_limit"
                else "invalid_document"
            )
            result = {"error": code}
        except Exception:
            result = {"error": "invalid_document"}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode("ascii"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
