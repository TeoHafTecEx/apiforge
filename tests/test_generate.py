from apiforge.generate import parse_files


def test_parse_files_whitelist():
    text = (
        '<file path="service.py">\nprint(1)\n</file>\n'
        '<file path="../../etc/passwd">\nhacked\n</file>\n'
        '<file path="README.md">\n# hi\n</file>'
    )
    files = parse_files(text)
    assert set(files) == {"service.py", "README.md"}
    assert files["service.py"] == "print(1)\n"
