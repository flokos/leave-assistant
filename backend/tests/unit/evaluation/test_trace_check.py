from __future__ import annotations

from leave_assistant.evaluation.trace_check import check


def test_a_clean_trace_passes_and_leaks_are_found() -> None:
    clean = [
        {"tags": ["tenant:abc"], "userId": "0123456789abcdef", "input": "Είμαι η PERSON_0001 (EMAIL_0001)"},
    ]
    result = check(clean)
    assert result["tenant_tagged"] and result["user_id_opaque"]
    assert not (result["emails"] or result["phones"] or result["names"])
    leaky = [{"tags": [], "userId": "eleni.dimitriou@aegean-logistics.example", "input": "Ελένη, 6912345678"}]
    result = check(leaky)
    assert result["emails"] == ["eleni.dimitriou@aegean-logistics.example"]
    assert result["phones"] == ["6912345678"] and result["names"] == ["Ελένη"]
    assert not result["tenant_tagged"] and not result["user_id_opaque"]
