from pathlib import Path


def test_streamlit_app_compiles():
    source = Path(__file__).parents[1].joinpath("app.py").read_text(encoding="utf-8")
    compile(source, "app.py", "exec")
