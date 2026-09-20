# 注意事项

- 每次改动完成后，都必须创建一个对应的 Git commit，以便后续追踪和回滚。
- 每次改动后，都必须编写或更新相关测试，并在交付给用户前，确保所有测试和验证全部通过。

## 测试

- 测试框架：pytest，用例放在 `tests/` 目录。
- 依赖清单见 `requirements.txt`（openpyxl 读 Excel，scipy 做卡方检验，matplotlib 出图）。
- 解释器刻意装在项目**外**，避免归档目录被虚拟环境（约 300 MB）撑大：

      %USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe -m pytest

- 交付前必须让全部测试通过，测试输出里不得出现 failed 或 error。
