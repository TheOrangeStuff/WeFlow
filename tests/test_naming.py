"""FR-26: Lightroom-style names for processed copies."""

from __future__ import annotations

from WeFlow.core import naming
from WeFlow.core.config import Profile

IMPORT = "Smith-Wedding_20261111-143000_DSC_0042.jpg"
CAPTURED = "2026-11-11T14:30:00"


def stem(**changes: object) -> str:
    event = Profile(name="Smith Wedding", folder="Smith-Wedding")
    for key, value in changes.items():
        setattr(event, key, value)
    return naming.output_stem(event, IMPORT, "DSC_0042", CAPTURED, 3)


def test_fr26_each_naming_pattern() -> None:
    assert stem() == "Smith-Wedding_20261111-143000_DSC_0042"  # import name, as before
    assert stem(file_naming="custom_seq") == "Smith-Wedding-0003"  # event name by default
    assert stem(file_naming="custom_seq", custom_name="Gala", start_number=100) == "Gala-0102"
    assert stem(file_naming="custom", custom_name="Gala Night") == "Gala-Night"
    assert stem(file_naming="date_filename") == "20261111-DSC_0042"
    assert stem(file_naming="filename") == "DSC_0042"
    assert stem(file_naming="filename_seq", start_number=0) == "DSC_0042-0002"


def test_fr26_names_are_safe_on_every_os() -> None:
    assert naming.clean('  A/B:C*?"<>|  d. ') == "ABC-d"
    assert naming.clean("CON") == "CON-"
    assert stem(file_naming="custom", custom_name="???") == "Smith-Wedding"  # falls back


def test_fr26_original_name_recovered_for_photos_imported_before() -> None:
    assert naming.original_stem(IMPORT) == "DSC_0042"
    assert naming.original_stem("Gala_20260101-000000_IMG_1_2.jpg") == "IMG_1_2"
    assert naming.original_stem("odd.jpg") == "odd"
    assert naming.original_stem(IMPORT, "DSC_9") == "DSC_9"


def test_fr26_collisions_get_a_number() -> None:
    taken = {"gala.jpg", "gala-2.jpg"}
    assert naming.unique_name("Gala", lambda n: n.casefold() in taken) == "Gala-3.jpg"
    assert naming.unique_name("Other", lambda n: n.casefold() in taken) == "Other.jpg"


def test_fr26_example_for_the_editor() -> None:
    event = Profile(name="Smith Wedding", file_naming="custom_seq")
    assert naming.example(event) == "Smith-Wedding-0001.jpg"
    assert naming.example(Profile(name="Gala")) == "Gala_20261111-143000_DSC_0001.jpg"
