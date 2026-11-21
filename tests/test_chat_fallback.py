from time_report_agent.chat_agent import _format_candidates_fallback


def test_format_candidates_fallback() -> None:
    payload = {
        "ok": True,
        "period": "2026-11",
        "candidates": [
            {
                "employee_id": "1",
                "first_name": "Synthetic",
                "phone": "***4567",
                "missing_hours": 8,
                "sms_text": "Hej Synthetic!",
            }
        ],
    }
    import json

    text = _format_candidates_fallback(json.dumps(payload))
    assert "employee_id=1" in text
    assert "Hej Synthetic!" in text
