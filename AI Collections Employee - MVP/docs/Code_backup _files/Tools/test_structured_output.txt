import json

from llm_service import (
    LLMServiceError,
    generate_payment_reminder,
)


def main():
    try:
        result = generate_payment_reminder(
            invoice_number="INV-1001",
            customer_name="ABC Technologies",
            outstanding_amount=15000.00,
            due_date="10 Sept 2026",
            days_overdue=38,
            reminder_tone="Professional",
        )

        print("Structured AI output generated successfully.")
        print(json.dumps(result, indent=4, ensure_ascii=False))

        required_fields = {
            "subject",
            "body",
            "tone",
            "recommended_action",
            "requires_human_review",
        }

        assert set(result.keys()) == required_fields
        assert result["tone"] == "Professional"
        assert result["requires_human_review"] is True
        assert len(result["subject"]) >= 5
        assert len(result["body"]) >= 20

        print("\nAll validation checks passed.")

    except LLMServiceError as error:
        print("\nAI service error:")
        print(error)

    except AssertionError:
        print("\nThe returned structure failed a validation check.")


if __name__ == "__main__":
    main()