"""Generate deterministic synthetic PDF attachments for stress tests."""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

FIXTURES_DIR = Path(__file__).resolve().parent
TARGETS_KB = (5, 20, 50, 100)
MARKER_TEMPLATE = "ATTACH_MARKER_{size_kb}KB"


def marker_for_size(size_kb: int) -> str:
    return MARKER_TEMPLATE.format(size_kb=size_kb)


def pdf_path_for_size(size_kb: int) -> Path:
    return FIXTURES_DIR / f"attach_{size_kb}kb.pdf"


def _lorem_paragraph() -> str:
    return (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor "
        "incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis "
        "nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat."
    )


def _render_pdf(marker: str, repeat_count: int) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    pdf.multi_cell(pdf.epw, 5, f"Stress attachment marker: {marker}")
    pdf.ln(4)
    paragraph = _lorem_paragraph()
    for _ in range(repeat_count):
        if pdf.get_y() > 260:
            pdf.add_page()
        pdf.multi_cell(pdf.epw, 5, paragraph)
        pdf.ln(2)
    raw = pdf.output()
    if isinstance(raw, str):
        return raw.encode("latin-1")
    return bytes(raw)


def _repeat_for_target(marker: str, target_bytes: int) -> int:
    """Binary-search repeat count to approximate target PDF size."""
    lo, hi = 1, 1
    while len(_render_pdf(marker, hi)) < target_bytes:
        hi *= 2
        if hi > 200_000:
            break
    best = hi
    while lo <= hi:
        mid = (lo + hi) // 2
        size = len(_render_pdf(marker, mid))
        if size < target_bytes * 0.9:
            lo = mid + 1
        else:
            best = mid
            hi = mid - 1
    return best


def build_pdf(size_kb: int) -> Path:
    """Write a PDF targeting approximately ``size_kb`` kilobytes."""
    target_bytes = size_kb * 1024
    out_path = pdf_path_for_size(size_kb)
    marker = marker_for_size(size_kb)
    repeat_count = _repeat_for_target(marker, target_bytes)
    out_path.write_bytes(_render_pdf(marker, repeat_count))
    return out_path


def build_all(force: bool = False) -> dict[int, Path]:
    """Ensure all calibrated PDF fixtures exist."""
    paths: dict[int, Path] = {}
    if not force and all(pdf_path_for_size(size_kb).exists() for size_kb in TARGETS_KB):
        return {size_kb: pdf_path_for_size(size_kb) for size_kb in TARGETS_KB}
    for size_kb in TARGETS_KB:
        path = pdf_path_for_size(size_kb)
        if force or not path.exists():
            build_pdf(size_kb)
        paths[size_kb] = path
    return paths


if __name__ == "__main__":
    built = build_all(force=True)
    for size, path in built.items():
        print(f"{size} KB target -> {path.name} ({path.stat().st_size} bytes)")
