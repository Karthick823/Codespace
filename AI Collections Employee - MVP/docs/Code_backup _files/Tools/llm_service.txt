import math
import os
from typing import Literal

from google import genai
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MODEL_NAME = "gemini-flash-lite-latest"


class LLMServiceError(Exception):
    """Raised when the LLM service fails or returns invalid data."""


class PaymentReminder(BaseModel):
    """Validated structure for an AI-generated payment reminder."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    subject: str = Field(min_length=5, max_length=200)
    body: str = Field(min_length=20, max_length=5000)
    tone: Literal["Professional", "Friendly", "Firm"]
    recommended_action: str = Field(min_length=5, max_length=300)
    requires_human_review: bool


def generate_payment_reminder(
    invoice_number: str,
    customer_name: str,
    outstanding_amount: float,
    due_date: str,
    days_overdue: int,
    reminder_tone: str = "Professional",
) -> dict:
    """Generate a validated payment reminder. Never sends email."""

    # Validate invoice information.
    if not isinstance(invoice_number, str) or not invoice_number.strip():
        raise LLMServiceError("A valid invoice number is required.")

    if not isinstance(customer_name, str) or not customer_name.strip():
        raise LLMServiceError("A valid customer name is required.")

    if not isinstance(due_date, str) or not due_date.strip():
        raise LLMServiceError("A valid invoice due date is required.")

    if reminder_tone not in {"Professional", "Friendly", "Firm"}:
        raise LLMServiceError("Unsupported reminder tone.")

    try:
        outstanding_amount = float(outstanding_amount)
    except (TypeError, ValueError):
        raise LLMServiceError(
            "Outstanding amount must be a valid number."
        ) from None

    if not math.isfinite(outstanding_amount) or outstanding_amount <= 0:
        raise LLMServiceError(
            "Outstanding amount must be a finite number greater than zero."
        )

    try:
        days_overdue = int(days_overdue)
    except (TypeError, ValueError):
        raise LLMServiceError(
            "Days overdue must be a valid integer."
        ) from None

    if days_overdue <= 0:
        raise LLMServiceError("This invoice is not overdue.")

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise LLMServiceError(
            "GEMINI_API_KEY is missing. Configure your API key first."
        )

    prompt = f"""
You are an accounts receivable assistant.

Create a payment reminder using only these verified invoice facts.

Invoice number: {invoice_number}
Customer name: {customer_name}
Outstanding amount: INR {outstanding_amount:,.2f}
Due date: {due_date}
Days overdue: {days_overdue}
Requested tone: {reminder_tone}

Return a JSON object matching the supplied schema.

Rules:
1. Use the requested tone exactly.
2. Set requires_human_review to true.
3. Ask the customer to confirm the expected payment date.
4. Never invent previous reminders, promises, payment deadlines,
   penalties, contractual terms, or other facts.
5. Do not invent invoice information.
6. Do not send the email.
7. Recommend a sensible next action for the collections team.
8. Include the customer name and invoice number in the subject.
9. Keep the email concise and professional.
"""

    try:
        client = genai.Client(api_key=api_key)

        interaction = client.interactions.create(
            model=MODEL_NAME,
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": PaymentReminder.model_json_schema(),
            },
        )

        # Remove optional Markdown code fences around the JSON response.
        response_text = interaction.output_text
        response_text = response_text.strip()

        if response_text.startswith("```"):
            lines = response_text.splitlines()

            # Remove the opening Markdown fence.
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]

            # Remove the closing Markdown fence, if present.
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]

            response_text = "\n".join(lines).strip()

        # Parse JSON and validate every field using Pydantic.
        validated = PaymentReminder.model_validate_json(response_text)

        if not response_text:
            raise LLMServiceError(
                "The model returned an empty response."
            )

        # Parse JSON and validate its fields and data types.
        validated = PaymentReminder.model_validate_json(response_text)

        if validated.tone != reminder_tone:
            raise LLMServiceError(
                "The returned tone differs from the requested tone."
            )

        if validated.requires_human_review is not True:
            raise LLMServiceError(
                "The response did not require human review. "
                "The draft has been rejected."
            )

        return validated.model_dump()

    except LLMServiceError:
        raise

    except ValidationError as error:
        raise LLMServiceError(
            "The model response failed validation. "
            f"Details: {error}"
        ) from None

    except Exception as error:  # noqa: BLE001
        raise LLMServiceError(
            "The Gemini API request failed. "
            f"Error type: {type(error).__name__}. "
            f"Details: {error}"
        ) from None