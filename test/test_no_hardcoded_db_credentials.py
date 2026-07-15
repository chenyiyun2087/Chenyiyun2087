from scripts.ops.check_no_hardcoded_db_credentials import scan_json_source, scan_source


def test_scanner_rejects_literal_password_defaults():
    key = "CHENYIYUN_DB_" + "PASSWORD"
    source = f'import os\nos.environ.setdefault("{key}", "not-a-real-secret")\n'
    findings = scan_source(source)
    assert findings


def test_scanner_allows_environment_only_credentials():
    source = 'import os\nvalue = os.getenv("CHENYIYUN_DB_URL")\n'
    assert scan_source(source) == []


def test_scanner_rejects_literal_database_url_password():
    source = "DB = 'mysql+pymysql://user:secret@localhost:3306/example'"
    findings = scan_source(source, "example.py")
    assert any("URL contains a password" in value for value in findings)


def test_scanner_rejects_json_password_value():
    findings = scan_json_source('{"mysql": {"password": "not-a-real-secret"}}')
    assert any("literal password value" in value for value in findings)


def test_scanner_allows_json_environment_placeholder():
    assert scan_json_source('{"mysql": {"password": "${DB_PASSWORD}"}}') == []
