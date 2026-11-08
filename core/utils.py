import os
import sys
import traceback

def get_resource_path(relative_path):
    """Get the absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

def resource_path(relative_path):
    """
    Returns the absolute path to a resource file, handling PyInstaller's _MEIPASS
    """
    return get_resource_path(relative_path)

def is_frozen():
    """Returns whether the application is running as a PyInstaller bundle."""
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')

def _name_of(obj):
    """Returns the name of an object or function."""
    if hasattr(obj, '__name__'):
        return obj.__name__
    return str(obj)

def _log_err(e=None, message=""):
    """
    Logs an error, including traceback.
    """
    if message:
        print(f"ERROR: {message}", file=sys.stderr)
    if e:
        print(f"EXCEPTION: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)


def load_json_file(path, default=None):
    """v4.109★ 读 json 文件的公共实现: 不存在 / 损坏 / 读不了, 一律返回 default。

    以前 core/ 与 tools/ 下有 6 份各自写的 _load_json(实现大同小异但默认值处理、
    是否先判存在、null 怎么处理各不相同), 改一处漏五处。现在统一到这里,
    各处保留自己的包装函数(签名不动) → 调用点零改动。
    """
    import io as _io
    import json as _json
    if not path:
        return default
    try:
        if not os.path.exists(path):
            return default
        with _io.open(path, encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return default
