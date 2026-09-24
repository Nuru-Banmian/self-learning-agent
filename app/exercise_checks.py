"""Conservative prerequisite checks for generated Python/Redis exercises.

This is not a Python executor or a proof of the expected output. Unsupported
key expressions are left unpublished rather than guessed to be isolated.
"""

import ast
import re
from collections.abc import Sequence


class ExerciseError(ValueError):
    pass


REDIS_INSTRUCTIONS = (
    "仅当用户目标涉及Python操作Redis时应用以下要求，不能因此改变用户学习主题。"
    "Python Redis练习必须自包含且可重复运行：各节点用不同的固定专用键，"
    "例如KEY='learning:cache:node2'，同一脚本的Redis键参数全部直接使用KEY；"
    "不要用user:1等业务键或动态拼键。import redis后用r=redis.Redis(...)初始化，"
    "在模块顶层、任何演示调用前执行r.delete(KEY)，不能仅在更新函数内删键。"
    "各脚本完整初始化自己的模拟数据库，不继承前一脚本变量。"
    "第一节点给一条完整服务安装、启动、redis-cli ping返回PONG的路径，"
    "并给Python虚拟环境和redis库安装命令。若未指定操作系统，明确选用Ubuntu终端，"
    "给apt安装redis-server与python3-venv、service启动命令；"
    "Windows读者先安装并进入Ubuntu/WSL，"
    "不能把安装redis Python库说成安装Redis服务。"
    "每节点用状态表核对：清理后首次读、第二次读、更新后读、再次运行，"
    "输出必须与实际分支和数据一致；状态表可简写在验证段。"
)


def redis_instructions(context: str) -> str:
    return REDIS_INSTRUCTIONS if re.search(r"\bredis\b", context, re.I) else ""


def redis_initializer(call: ast.Call) -> bool:
    return ast.unparse(call.func) in ("redis.Redis", "redis.Redis.from_url")


def check_exercises(
    exercises: Sequence[str],
    goals: Sequence[str] = (),
    criteria: Sequence[str] = (),
) -> None:
    scripts = [
        (index, code)
        for index, exercise in enumerate(exercises, 1)
        for code in re.findall(r"```(?:python|py)\s*\n(.*?)```", exercise, re.S)
        if re.search(r"\b(?:import redis|from redis import)\b", code)
    ]
    if not scripts:
        return
    # Commands must appear before the first script needs the service. These
    # checks detect omissions, not whether installation on a given OS succeeds.
    preparation = "\n".join(exercises[: scripts[0][0]])
    native = re.search(r"(?:apt(?:-get)?|brew) install[^\n`]*redis", preparation)
    start = re.search(
        r"(?:service redis-server start|systemctl start redis|"
        r"brew services start redis|redis-server --daemonize yes)",
        preparation,
    )
    container = re.search(r"docker run[^\n`]*\bredis(?::[\w.-]+)?", preparation)
    if (
        not ((native and start) or container)
        or not re.search(r"redis-cli(?:[^\n`]* )?ping", preparation)
        or not re.search(r"(?:pip|pip3) install[^\n`]*\bredis\b", preparation)
    ):
        raise ExerciseError(
            "练习准备不完整：需提供 Redis 服务安装、启动、PING 检查"
            "及 Python 依赖安装命令。"
        )
    if (
        re.search(r"apt(?:-get)? install", preparation)
        and re.search(r"python3? -m venv", preparation)
        and not re.search(
            r"apt(?:-get)? install[^\n`]*\bpython3(?:\.\d+)?-venv\b", preparation
        )
    ):
        raise ExerciseError("练习使用 Ubuntu 虚拟环境，但缺少 python3-venv 安装步骤。")
    owners: dict[str, int] = {}
    for index, code in scripts:
        try:
            tree = ast.parse(code)
        except SyntaxError as error:
            raise ExerciseError(f"节点 {index} 的 Python 练习语法不完整。") from error
        constants = {
            item.targets[0].id: item.value.value
            for item in tree.body
            if isinstance(item, ast.Assign)
            and len(item.targets) == 1
            and isinstance(item.targets[0], ast.Name)
            and isinstance(item.value, ast.Constant)
            and isinstance(item.value.value, str)
        }
        clients = {
            item.targets[0].id
            for item in tree.body
            if isinstance(item, ast.Assign)
            and len(item.targets) == 1
            and isinstance(item.targets[0], ast.Name)
            and isinstance(item.value, ast.Call)
            and redis_initializer(item.value)
        }
        if not clients:
            raise ExerciseError(
                f"节点 {index} 的 Redis 客户端初始化无法核对，请使用独立完整脚本。"
            )
        uses: list[tuple[ast.Call, str]] = []
        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id in clients
            ):
                continue
            if call.func.attr in ("ping", "close"):
                continue
            if call.func.attr not in (
                "get",
                "set",
                "setex",
                "ttl",
                "expire",
                "delete",
                "exists",
                "hset",
                "hget",
                "hgetall",
                "hdel",
                "lpush",
                "rpush",
                "lpop",
                "rpop",
                "lrange",
                "llen",
            ):
                raise ExerciseError(f"节点 {index} 的 Redis 操作无法核对专用键范围。")
            if call.func.attr in ("delete", "exists") and (
                len(call.args) != 1 or call.keywords
            ):
                raise ExerciseError(
                    f"节点 {index} 的多键操作无法核对，请逐个操作专用键。"
                )
            arg = call.args[0] if call.args else None
            key = (
                constants.get(arg.id)
                if isinstance(arg, ast.Name)
                else arg.value
                if isinstance(arg, ast.Constant)
                else None
            )
            if not isinstance(key, str) or not key.startswith("learning:"):
                raise ExerciseError(
                    f"节点 {index} 需使用可核对的 learning: 专用固定键，"
                    "避免业务键及动态键。"
                )
            if key in owners and owners[key] != index:
                raise ExerciseError(
                    f"节点 {index} 与节点 {owners[key]} 复用了缓存键，"
                    "预期输出可能受前序状态影响。"
                )
            owners[key] = index
            uses.append((call, key))
        reset: set[str] = set()
        # A reset in a function or conditional does not establish initial state.
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.Import, ast.ImportFrom)):
                continue
            calls = [item for item in ast.walk(statement) if isinstance(item, ast.Call)]
            for call in calls:
                if (
                    isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in clients
                    and call.func.attr == "ping"
                ):
                    continue
                entry = next(
                    (key for candidate, key in uses if candidate is call), None
                )
                if (
                    entry
                    and isinstance(statement, ast.Expr)
                    and statement.value is call
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "delete"
                ):
                    reset.add(entry)
                elif (
                    isinstance(statement, ast.Assign)
                    and call is statement.value
                    and redis_initializer(call)
                ):
                    continue
                elif not {key for _, key in uses} <= reset:
                    raise ExerciseError(
                        f"节点 {index} 缺少演示调用前的顶层专用键重置，"
                        "重复运行的首次未命中无法保证。"
                    )
        if not {key for _, key in uses} <= reset:
            raise ExerciseError(f"节点 {index} 缺少专用键操作或独立初始化。")
        claims_update = bool(
            re.search(
                r"更新|修改底层|新值|updated|new value",
                " ".join(
                    (
                        goals[index - 1] if goals else "",
                        criteria[index - 1] if criteria else "",
                    )
                ),
                re.I,
            )
        )
        if claims_update:
            readers: dict[str, set[tuple[str, str, str]]] = {}
            for item in tree.body:
                if not isinstance(item, ast.FunctionDef):
                    continue
                db_refs: set[tuple[str, str]] = set()
                for position, statement in enumerate(item.body):
                    if not (
                        isinstance(statement, ast.Assign)
                        and len(statement.targets) == 1
                        and isinstance(statement.targets[0], ast.Name)
                        and isinstance(statement.value, ast.Subscript)
                        and isinstance(statement.value.value, ast.Name)
                    ):
                        continue
                    value_name = statement.targets[0].id
                    following = item.body[position + 1 :]
                    write_position = next(
                        (
                            offset
                            for offset, later in enumerate(following)
                            if any(
                                isinstance(call, ast.Call)
                                and isinstance(call.func, ast.Attribute)
                                and isinstance(call.func.value, ast.Name)
                                and call.func.value.id in clients
                                and call.func.attr in ("set", "setex")
                                and len(call.args) > 1
                                and isinstance(call.args[-1], ast.Name)
                                and call.args[-1].id == value_name
                                for call in ast.walk(later)
                            )
                        ),
                        None,
                    )
                    if write_position is None or any(
                        isinstance(later, ast.Assign)
                        and any(
                            isinstance(target, ast.Name) and target.id == value_name
                            for target in later.targets
                        )
                        for later in following[:write_position]
                    ):
                        continue
                    if any(
                        isinstance(ret, ast.Return)
                        and isinstance(ret.value, ast.Name)
                        and ret.value.id == value_name
                        for ret in ast.walk(item)
                    ):
                        db_refs.add(
                            (
                                statement.value.value.id,
                                ast.unparse(statement.value.slice),
                            )
                        )
                cache_keys = {
                    key
                    for call, key in uses
                    if isinstance(call.func, ast.Attribute)
                    and call.func.attr == "get"
                    and any(call is nested for nested in ast.walk(item))
                }
                readers[item.name] = {
                    (db_name, db_index, key)
                    for db_name, db_index in db_refs
                    for key in cache_keys
                }
            actions = tree.body
            updates = {
                pos: {
                    (target.value.id, ast.unparse(target.slice))
                    for target in (
                        item.targets if isinstance(item, ast.Assign) else [item.target]
                    )
                    if isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                }
                for pos, item in enumerate(actions)
                if isinstance(item, (ast.Assign, ast.AugAssign))
            }

            def executed_calls(statement: ast.stmt) -> list[ast.Call]:
                if not isinstance(statement, (ast.Expr, ast.Assign, ast.AnnAssign)):
                    return []
                if any(
                    isinstance(node, (ast.IfExp, ast.BoolOp, ast.Lambda))
                    for node in ast.walk(statement)
                ):
                    return []
                return [
                    node for node in ast.walk(statement) if isinstance(node, ast.Call)
                ]

            def reads(
                statement: ast.stmt,
                db: tuple[str, str],
                key: str,
                readers: dict[str, set[tuple[str, str, str]]] = readers,
            ) -> bool:
                return any(
                    isinstance(call.func, ast.Name)
                    and (db[0], db[1], key) in readers.get(call.func.id, set())
                    for call in executed_calls(statement)
                )

            def invalidates(
                statement: ast.stmt,
                key: str,
                uses: list[tuple[ast.Call, str]] = uses,
            ) -> bool:
                return any(
                    call.func.attr == "delete" and (call, key) in uses
                    for call in executed_calls(statement)
                    if isinstance(call.func, ast.Attribute)
                )

            if not any(
                any(reads(item, db, key) for item in actions[:update])
                and any(
                    invalidates(actions[delete], key)
                    and any(reads(item, db, key) for item in actions[delete + 1 :])
                    for delete in range(update + 1, len(actions))
                )
                for update, changed in updates.items()
                for db in changed
                for key in {entry[2] for values in readers.values() for entry in values}
            ):
                raise ExerciseError(
                    f"节点 {index} 声称更新后读取新值，但脚本未展示旧值读取、"
                    "底层数据更新、缓存失效及新值读回的完整顺序。"
                )
