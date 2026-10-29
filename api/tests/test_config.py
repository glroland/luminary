from app.config import redact_uri


def test_redact_uri_strips_credentials():
    assert redact_uri("smb://user:secret@server/share/photos") == "smb://***@server/share/photos"


def test_redact_uri_leaves_credential_free_uris_untouched():
    assert redact_uri("/data/photos") == "/data/photos"
    assert redact_uri("s3://my-bucket/photos") == "s3://my-bucket/photos"
    assert redact_uri("https://host/photos") == "https://host/photos"
