from pathlib import Path

from triconvey_agent.backend.api import (
    _extract_triconvey_explicit_path_values,
    _read_triconvey_reference_payload,
    _resolve_explicit_triconvey_paths,
)


def test_extract_triconvey_explicit_path_values_supports_smokeball_pdf_path() -> None:
    payload = {
        "Folders": [
            {
                "Name": "Searches",
                "Files": [
                    {
                        "PDF Path": r"C:\Program Files\Smokeball\dataAu\mattermanagement\files2\encoded\instrument.pdf",
                    }
                ],
            }
        ]
    }

    assert _extract_triconvey_explicit_path_values(payload) == [
        r"C:\Program Files\Smokeball\dataAu\mattermanagement\files2\encoded\instrument.pdf"
    ]


def test_read_triconvey_reference_payload_accepts_explicit_pdf_paths(tmp_path: Path) -> None:
    reference_path = tmp_path / "triconvey-drop.json"
    reference_path.write_text(
        '{"Folders":[{"Files":[{"PDF Path":"C:\\\\Program Files\\\\Smokeball\\\\cached.pdf"}]}]}',
        encoding="utf-8",
    )

    payload = _read_triconvey_reference_payload(reference_path)

    assert payload is not None
    assert payload["Folders"][0]["Files"][0]["PDF Path"].endswith("cached.pdf")


def test_resolve_explicit_triconvey_paths_supports_nested_pdf_path(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Instrument Search.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    payload = {
        "Folders": [
            {
                "Files": [
                    {
                        "PDF Path": str(pdf_path),
                    }
                ]
            }
        ]
    }

    resolved = _resolve_explicit_triconvey_paths(payload)

    assert resolved == [pdf_path]
