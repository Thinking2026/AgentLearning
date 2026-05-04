from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def read_text(path: str | Path, encoding: str = "utf-8") -> str:
    """读取文本文件内容。

    Args:
        path: 文件路径
        encoding: 文件编码，默认 UTF-8

    Returns:
        文件内容字符串
    """
    return Path(path).read_text(encoding=encoding)


def write_text(path: str | Path, content: str, encoding: str = "utf-8") -> int:
    """将文本写入文件，自动创建父目录。

    Args:
        path: 文件路径
        content: 要写入的文本内容
        encoding: 文件编码，默认 UTF-8

    Returns:
        写入的字节数
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding=encoding)
    return len(content.encode(encoding))


def append_text(path: str | Path, content: str, encoding: str = "utf-8") -> int:
    """将文本追加到文件末尾，自动创建父目录。

    Args:
        path: 文件路径
        content: 要追加的文本内容
        encoding: 文件编码，默认 UTF-8

    Returns:
        追加的字节数
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding=encoding) as f:
        f.write(content)
    return len(content.encode(encoding))


def read_lines(path: str | Path, encoding: str = "utf-8", skip_empty: bool = True) -> list[str]:
    """按行读取文件，返回行列表。

    Args:
        path: 文件路径
        encoding: 文件编码，默认 UTF-8
        skip_empty: 是否跳过空行，默认 True

    Returns:
        行字符串列表（已去除首尾空白）
    """
    lines = Path(path).read_text(encoding=encoding).splitlines()
    stripped = [line.strip() for line in lines]
    if skip_empty:
        return [line for line in stripped if line]
    return stripped


def read_json(path: str | Path, encoding: str = "utf-8") -> Any:
    """读取并解析 JSON 文件。

    Args:
        path: 文件路径
        encoding: 文件编码，默认 UTF-8

    Returns:
        解析后的 Python 对象
    """
    return json.loads(Path(path).read_text(encoding=encoding))


def write_json(path: str | Path, data: Any, indent: int = 2, encoding: str = "utf-8") -> None:
    """将 Python 对象序列化为 JSON 并写入文件，自动创建父目录。

    Args:
        path: 文件路径
        data: 要序列化的 Python 对象
        indent: JSON 缩进空格数，默认 2
        encoding: 文件编码，默认 UTF-8
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding=encoding)


def ensure_dir(path: str | Path) -> Path:
    """确保目录存在，不存在则创建（含父目录）。

    Args:
        path: 目录路径

    Returns:
        Path 对象
    """
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def exists(path: str | Path) -> bool:
    """检查路径是否存在（文件或目录）。

    Args:
        path: 文件或目录路径

    Returns:
        存在返回 True，否则返回 False
    """
    return Path(path).exists()


def file_size(path: str | Path) -> int:
    """获取文件大小（字节数）。

    Args:
        path: 文件路径

    Returns:
        文件大小（字节）
    """
    return Path(path).stat().st_size


def list_dir(path: str | Path) -> list[dict[str, Any]]:
    """列出目录下的所有条目。

    Args:
        path: 目录路径

    Returns:
        条目列表，每项包含 name、type（'file' 或 'directory'）以及文件的 size（字节）
    """
    entries = []
    for entry in sorted(Path(path).iterdir()):
        if entry.is_dir():
            entries.append({"name": entry.name, "type": "directory"})
        else:
            entries.append({"name": entry.name, "type": "file", "size": entry.stat().st_size})
    return entries


def glob_files(directory: str | Path, pattern: str) -> list[Path]:
    """在目录中查找匹配 glob 模式的文件。

    Args:
        directory: 搜索根目录
        pattern: glob 模式，如 '**/*.py'、'*.json'

    Returns:
        匹配的 Path 列表，按路径排序
    """
    return sorted(Path(directory).glob(pattern))


def copy_file(src: str | Path, dst: str | Path) -> Path:
    """复制文件，自动创建目标父目录。

    Args:
        src: 源文件路径
        dst: 目标文件路径

    Returns:
        目标文件的 Path 对象
    """
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    return Path(shutil.copy2(src, dst_path))


def move_file(src: str | Path, dst: str | Path) -> Path:
    """移动（重命名）文件，自动创建目标父目录。

    Args:
        src: 源文件路径
        dst: 目标文件路径

    Returns:
        目标文件的 Path 对象
    """
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    return Path(shutil.move(str(src), dst_path))


def delete_file(path: str | Path) -> None:
    """删除文件。文件不存在时静默忽略。

    Args:
        path: 文件路径
    """
    Path(path).unlink(missing_ok=True)
