from apiforge.generate import parse_files


def test_parse_files_whitelist():
    text = (
        '<file path="main.py">\nprint(1)\n</file>\n'
        '<file path="../../etc/passwd">\nhacked\n</file>\n'
        '<file path="README.md">\n# hi\n</file>'
    )
    files = parse_files(text)
    assert set(files) == {"main.py", "README.md"}
    assert files["main.py"] == "print(1)\n"
