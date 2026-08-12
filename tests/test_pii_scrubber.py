from src.core.scrubber import scrub_text


def test_scrub_aws_keys():
    text = "Error: AWS key AKIAIOSFODNN7EXAMPLE and secret wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY leaked!"
    scrubbed = scrub_text(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in scrubbed
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in scrubbed
    assert "[REDACTED_AWS_KEY]" in scrubbed
    assert "[REDACTED_SECRET]" in scrubbed


def test_scrub_jwt_bearer():
    # Test Bearer token scrubbing
    text1 = "Authorization: Bearer my-custom-secret-token"
    scrubbed1 = scrub_text(text1)
    assert "Bearer" not in scrubbed1
    assert "[REDACTED_BEARER_TOKEN]" in scrubbed1

    # Test standalone JWT scrubbing
    text2 = "token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    scrubbed2 = scrub_text(text2)
    assert "eyJhb" not in scrubbed2
    assert "[REDACTED_JWT]" in scrubbed2


def test_scrub_pii():
    text = "User email is john.doe@example.com and card number is 4111111111111111"
    scrubbed = scrub_text(text)
    assert "john.doe@example.com" not in scrubbed
    assert "4111111111111111" not in scrubbed
    assert "[REDACTED_EMAIL]" in scrubbed
    assert "[REDACTED_PAN]" in scrubbed
