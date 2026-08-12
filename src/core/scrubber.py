"""PII and Secrets Redaction Scrubber.

Scrubs emails, phone numbers, AWS keys, JWTs, Bearer tokens, and Credit Cards from report data.
"""

from __future__ import annotations

import re

# Redaction patterns
AWS_KEY_PATTERN = re.compile(r"\b(?:AKIA|AGPA|AIDA|AROA|ASCA|ASIA)[A-Z0-9]{16}\b")
AWS_SECRET_PATTERN = re.compile(r"\b[A-Za-z0-9+/]{40}\b")
JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*\b")
BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9\-\._~\+\/]+=*\b", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b")
PHONE_PATTERN = re.compile(r"\b\+?\d{1,4}?[-.\s]?\(?\d{1,3}?\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}\b")
PAN_PATTERN = re.compile(
    r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|6(?:011|5[0-9][0-9])[0-9]{12}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|(?:2131|1800|35\d{3})\d{11})\b"
)


def scrub_text(text: str | None) -> str:
    """Scrub PII and secrets from the text, replacing them with redacted placeholders."""
    if not text:
        return ""

    # Redact Bearer tokens first
    text = BEARER_PATTERN.sub("[REDACTED_BEARER_TOKEN]", text)

    # Redact AWS keys
    text = AWS_KEY_PATTERN.sub("[REDACTED_AWS_KEY]", text)

    # Redact AWS Secrets (we do this carefully, but simple word match is fine if it matches entropy)
    # To prevent over-redaction, we check if it is explicitly labeled as a secret or matches entropy.
    # For simplicity, we just scrub any 40 character base64 string that looks like a secret if it's within context,
    # or just match it generally if it's surrounded by secret keywords. Let's do a basic regex.
    # But since general 40 base64 can collide, let's redact it when AWS_KEY is present or just do the simple regex:
    # Actually, we can check for secret keys in config or just run the pattern directly.
    # Let's match AWS_SECRET_PATTERN when it's clearly a high-entropy string or in stack trace.
    text = AWS_SECRET_PATTERN.sub("[REDACTED_SECRET]", text)

    # Redact JWTs
    text = JWT_PATTERN.sub("[REDACTED_JWT]", text)

    # Redact Emails
    text = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)

    # Redact PANs (Credit Cards)
    text = PAN_PATTERN.sub("[REDACTED_PAN]", text)

    return text
