# tests 包标记：使 ``from tests.conftest import ...`` 在 ``pytest`` 与
# ``python -m pytest`` 两种调用方式下均可导入（CI 用前者，不含 cwd 的 sys.path）。
