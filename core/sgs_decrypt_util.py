import gzip
import io
import json
import os
import sys
import zipfile
from pathlib import Path

# NOTE: This utility requires the 'wasmtime' Python package.
# Install with: pip install wasmtime

def _looks_like_zip(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04"

def _looks_like_gzip(data: bytes) -> bool:
    return data[:2] == b"\x1f\x8b"

def _looks_like_json(data: bytes) -> bool:
    head = data.lstrip()[:1]
    return head in (b"{", b"[")

def _load_wasm(wasm_path: Path):
    try:
        from wasmtime import Store, Module, Instance, Memory, MemoryType, Limits
    except Exception as exc:
        raise RuntimeError(
            "wasmtime is required. Install with: python -m pip install wasmtime"
        ) from exc

    store = Store()
    module = Module.from_file(store.engine, str(wasm_path))
    memory = Memory(store, MemoryType(Limits(256, 256)))
    instance = Instance(store, module, [memory])
    exports = instance.exports(store)

    ctx = {
        "store": store,
        "memory": memory,
        "ofb": exports["a"],
        "func_d": exports["d"],
        "func_e": exports["e"],
        "func_f": exports["f"],
    }
    ctx["func_e"](store, 0, 0, 256)
    ctx["func_f"](store, 0, 0, 256)
    ctx["func_d"](store, 0, 0, 128)
    return ctx

def _ofb_decrypt(data: bytes, ctx) -> bytes:
    import ctypes
    store = ctx["store"]
    memory = ctx["memory"]
    ofb = ctx["ofb"]
    func_d = ctx["func_d"]
    block_ptr = 1 << 14
    ptr = memory.data_ptr(store)
    base_addr = ctypes.addressof(ptr.contents)
    byte_len = memory.data_len(store)
    array_type = ctypes.c_ubyte * byte_len
    mem_view = array_type.from_address(base_addr)
    orig_len = len(data)
    pad = (16 - (orig_len % 16)) % 16
    if pad == 0:
        pad = 16
    padded = data + b"\x00" * pad
    mem_view[block_ptr : block_ptr + len(padded)] = padded
    ofb(store, block_ptr, len(padded))
    func_d(store, 0, 0, 128)
    out = bytes(mem_view[block_ptr : block_ptr + len(padded)])[:orig_len]
    return out

def _extract_zip(data: bytes, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(out_dir)
    return out_dir

def decrypt_sgs_file(
    input_path: Path,
    wasm_path: Path,
    output_path: Path = None,
    extract_zip: bool = False,
    no_gunzip: bool = False,
) -> Path:
    """Decrypts an .sgs file and optionally extracts a zip archive.

    Args:
        input_path: Path to the .sgs file.
        wasm_path: Path to the resc WebAssembly file.
        output_path: Optional output file path. If None, it will be derived from input_path.
        extract_zip: If True, and the final output is a ZIP, extract it to a directory.
        no_gunzip: If True, do not auto-gunzip after decrypt.

    Returns:
        The path to the final output file or directory.

    Raises:
        FileNotFoundError: If input_path or wasm_path does not exist.
        RuntimeError: If wasmtime is not installed or decryption fails.
    """

    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")
    if not wasm_path.exists():
        raise FileNotFoundError(f"WASM not found: {wasm_path}")

    raw = input_path.read_bytes()

    # If already readable
    if _looks_like_zip(raw) or _looks_like_gzip(raw) or _looks_like_json(raw):
        candidate = raw
    else:
        ctx = _load_wasm(wasm_path)
        dec1 = _ofb_decrypt(raw, ctx)
        if _looks_like_zip(dec1) or _looks_like_gzip(dec1) or _looks_like_json(dec1):
            candidate = dec1
        else:
            dec2 = _ofb_decrypt(dec1, ctx)
            candidate = dec2

    # Optionally gunzip
    final = candidate
    gunzipped = False
    if _looks_like_gzip(final) and not no_gunzip:
        final = gzip.decompress(final)
        gunzipped = True

    # Determine output path
    if output_path:
        out_path = Path(output_path)
    else:
        suffix = ".unzipped" if gunzipped else ".dec"
        out_path = input_path.with_suffix(input_path.suffix + suffix)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(final)

    # Optional zip extraction
    if _looks_like_zip(final) and extract_zip:
        extract_dir = out_path.with_suffix(out_path.suffix + "_extracted")
        _extract_zip(final, extract_dir)
        return extract_dir
    
    return out_path
