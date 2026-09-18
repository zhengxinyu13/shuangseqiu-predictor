# 注意事项

- 每次改动完成后，都必须创建一个对应的 Git commit，以便后续追踪和回滚。
- 每次改动后，都必须编写或更新相关测试，并在交付给用户前，确保所有测试和验证全部通过。

## 测试

- 测试框架：pytest，用例放在 `tests/` 目录。
- 运行全部测试：`.venv\Scripts\python.exe -m pytest`
- 交付前必须让全部测试通过，测试输出里不得出现 failed 或 error。
- 依赖清单见 `requirements.txt`（openpyxl 用于读 Excel，pytest 用于测试）。
