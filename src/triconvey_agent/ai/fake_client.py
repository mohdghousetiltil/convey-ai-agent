from __future__ import annotations

import json
import re

from triconvey_agent.ai.client import AIResult


class FakeAIClient:
    def complete(self, prompt: str) -> AIResult:
        match = re.search(r"Field key:\s*(.+)", prompt)
        field_key = match.group(1).strip() if match else "unknown"

        if field_key == "new_appliance_details":
            payload = {
                "field_key": field_key,
                "suggested_value": {
                    "raw_text": "2025",
                    "item": None,
                    "installed_year": "2025",
                    "cost": None,
                },
                "confidence": 0.35,
                "reason": "Only a year is present in the evidence. No appliance item or cost is supported.",
                "requires_review": True,
            }
        elif field_key == "permit_required_for_water_tank":
            payload = {
                "field_key": field_key,
                "suggested_value": False,
                "confidence": 0.55,
                "reason": "Evidence snippet explicitly shows 'No'.",
                "requires_review": False,
            }
        else:
            payload = {
                "field_key": field_key,
                "suggested_value": None,
                "confidence": 0.25,
                "reason": "Stub AI review result. Evidence appears incomplete.",
                "requires_review": True,
            }
        return AIResult(raw_text=json.dumps(payload))
