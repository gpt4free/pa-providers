"""
FreeGPT.tech WASM signer — generates secure payloads for API authentication.

Python port of the TypeScript freegpt-signer.cjs, using wasmtime to instantiate
the wasm_signer_bg.wasm binary with lightweight browser API mocks.

The WASM uses wasm-bindgen's externref convention:
  - Functions returning `anyref` return Python objects directly
  - Functions returning `i32` return externref table indices (object stored in table)
  - `generate_secure_payload` returns (result_idx, error_idx, error_flag) as i32s
  - Results are retrieved via `takeFromExternrefTable0(idx)`
"""

from __future__ import annotations

import json
import math
import random
import struct
from pathlib import Path
from typing import Any

try:
    import wasmtime
    HAS_WASMTIME = True
except ImportError:
    HAS_WASMTIME = False

# Default path to the WASM binary (same directory as this file)
# Use hardcoded path since __file__ may not be available in safe execution mode
WASM_PATH = r"C:\Users\heine\.g4f\workspace\pa-providers\wasm_signer_bg.wasm"

# Fixed canvas fingerprint data URL (same as TS implementation)
CANVAS_DATA_URL = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAYAAACM/rht"
    "AAABhGlDQ1BJQ0MgcHJvZmlsZQAAeJx9kT1Iw0AcxV/TSoUi"
)

# Browser API mock objects ---------------------------------------------------

class CanvasContextMock:
    """Mock CanvasRenderingContext2D"""
    def __init__(self):
        self.fill_style = ""
        self.font = "14px Arial"

    def fillRect(self, *args):
        pass

    def fillText(self, *args):
        pass


class CanvasMock:
    """Mock HTMLCanvasElement"""
    def __init__(self):
        self.width = 200
        self.height = 200

    def getContext(self, type_str: str):
        return CanvasContextMock()

    def toDataURL(self, type_str: str = "image/png"):
        return CANVAS_DATA_URL


class DocumentMock:
    """Mock document object"""
    def createElement(self, tag: str):
        if tag == "canvas":
            return CanvasMock()
        return _GenericMock()

    def __getattr__(self, name):
        return _GenericMock()


class _GenericMock:
    """Generic mock object that accepts any property access or method call.
    Stores properties in an internal dict so WASM-built objects survive."""
    def __init__(self):
        object.__setattr__(self, '_props', {})

    def setAttribute(self, *args):
        pass

    def appendChild(self, *args):
        pass

    def __setitem__(self, key, value):
        self._props[key] = value

    def __getitem__(self, key):
        return self._props.get(key, _GenericMock())

    def __setattr__(self, key, value):
        self._props[key] = value

    def __getattr__(self, name):
        # Only called when normal attribute lookup fails
        if name.startswith('_'):
            raise AttributeError(name)
        return self._props.get(name, _GenericMock())

    def __repr__(self):
        return f"_GenericMock({self._props!r})"

    def __str__(self):
        return str(self._props)

    def to_dict(self):
        """Recursively convert to a plain dict."""
        result = {}
        for k, v in self._props.items():
            if isinstance(v, _GenericMock):
                result[k] = v.to_dict()
            elif isinstance(v, dict):
                result[k] = v
            else:
                result[k] = v
        return result


class WindowMock:
    """Mock window object"""
    def __init__(self):
        self.document = DocumentMock()
        self.Object = dict
        self.Array = list
        self.String = str
        self.Number = float
        self.Boolean = bool
        self.Math = math
        self.Date = None  # placeholder
        self.JSON = json
        self.Error = Exception
        self.location = {
            "href": "https://freegpt.tech/",
            "protocol": "https:",
            "host": "freegpt.tech",
            "hostname": "freegpt.tech",
            "port": "",
            "pathname": "/",
            "search": "",
            "hash": "",
            "origin": "https://freegpt.tech",
        }
        self.navigator = {
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "platform": "Linux x86_64",
            "language": "en-US",
        }


# Sentinel objects for externref table
_UNDEFINED = object()
_NULL = object()
_TRUE = True
_FALSE = False


class FreeGPTSigner:
    """Python WASM signer for FreeGPT.tech secure payload generation."""

    def __init__(self):
        self.engine = None
        self.store = None
        self.instance = None
        self.memory = None
        self._wasm = None  # cached exports dict
        self._initialized = False
        self._window_mock = WindowMock()
        self._document_mock = DocumentMock()

    def init(self, wasm_path: str = WASM_PATH):
        """Initialize the WASM module with browser API mocks."""
        if not HAS_WASMTIME:
            raise ImportError("wasmtime is required for FreeGPT WASM signing")

        if not Path(wasm_path).exists():
            raise FileNotFoundError(f"WASM file not found: {wasm_path}")

        self.engine = wasmtime.Engine()

        with open(wasm_path, "rb") as f:
            wasm_bytes = f.read()

        module = wasmtime.Module(self.engine, wasm_bytes)
        self.store = wasmtime.Store(self.engine)
        linker = wasmtime.Linker(self.engine)

        # Register all import functions under module "./wasm_signer_bg.js"
        self._register_imports(linker)

        self.instance = linker.instantiate(self.store, module)
        exports = self.instance.exports(self.store)

        # Cache export references
        self._wasm = {
            "memory": exports["memory"],
            "generate_secure_payload": exports["generate_secure_payload"],
            "__wbindgen_malloc": exports["__wbindgen_malloc"],
            "__wbindgen_realloc": exports["__wbindgen_realloc"],
            "__wbindgen_exn_store": exports["__wbindgen_exn_store"],
            "__externref_table_alloc": exports["__externref_table_alloc"],
            "__wbindgen_externrefs": exports["__wbindgen_externrefs"],
            "__externref_table_dealloc": exports["__externref_table_dealloc"],
            "__wbindgen_start": exports["__wbindgen_start"],
        }
        self.memory = self._wasm["memory"]

        # Call __wbindgen_start to initialize the WASM module
        self._wasm["__wbindgen_start"](self.store)

        self._initialized = True
        return self

    # ─── Memory helpers ──────────────────────────────────────────────────

    def _write_to_memory(self, text: str) -> tuple[int, int]:
        """Write a string to WASM memory, return (ptr, length)."""
        encoded = text.encode("utf-8")
        length = len(encoded)
        ptr = self._wasm["__wbindgen_malloc"](self.store, length, 1)
        self.memory.write(self.store, encoded, ptr)
        return ptr, length

    def _read_string_from_memory(self, ptr: int, length: int) -> str:
        """Read a UTF-8 string from WASM memory."""
        raw = self.memory.read(self.store, ptr, ptr + length)
        return bytes(raw).decode("utf-8")

    def _set_i32_in_memory(self, ptr: int, value: int, offset: int = 0):
        """Write an i32 value to WASM memory at ptr + offset*4 (little-endian)."""
        addr = ptr + offset * 4
        self.memory.write(self.store, struct.pack("<i", value), addr)

    def _get_i32_from_memory(self, ptr: int, offset: int = 0) -> int:
        """Read an i32 value from WASM memory at ptr + offset*4 (little-endian)."""
        addr = ptr + offset * 4
        raw = self.memory.read(self.store, addr, addr + 4)
        return struct.unpack_from("<i", bytes(raw))[0]

    # ─── Externref table helpers ─────────────────────────────────────────

    def _add_to_externref_table(self, obj: Any) -> int:
        """Add an object to the externref table and return its index."""
        idx = self._wasm["__externref_table_alloc"](self.store)
        table = self._wasm["__wbindgen_externrefs"]
        table.set(self.store, idx, obj)
        return idx

    def _take_from_externref_table(self, idx: int) -> Any:
        """Get an object from the externref table and deallocate the slot."""
        table = self._wasm["__wbindgen_externrefs"]
        value = table.get(self.store, idx)
        self._wasm["__externref_table_dealloc"](self.store, idx)
        return value

    # ─── Import function implementations ─────────────────────────────────

    def _register_imports(self, linker: wasmtime.Linker):
        """Register all wasm-bindgen import functions with browser API mocks."""
        mod = "./wasm_signer_bg.js"

        # Type shortcuts
        i32 = wasmtime.ValType.i32()
        i64 = wasmtime.ValType.i64()
        f64 = wasmtime.ValType.f64()
        anyref = wasmtime.ValType.externref()

        def _ft(params, results):
            """Build a FuncType from lists of ValType."""
            return wasmtime.FuncType(params, results)

        def _def(name, params, results, func):
            """Register an import function with proper FuncType."""
            linker.define_func(mod, name, _ft(params, results), func)

        # __wbg_set_6be42768c690e380(anyref, anyref, anyref) -> ()
        def wbg_set(arg0, arg1, arg2):
            if arg0 is not None:
                try:
                    arg0[arg1] = arg2
                except Exception:
                    pass
        _def("__wbg_set_6be42768c690e380", [anyref, anyref, anyref], [], wbg_set)

        # __wbg_String_8564e559799eccda(i32, anyref) -> ()
        def wbg_String(arg0, arg1):
            ret = str(arg1) if arg1 is not None else "undefined"
            ptr, length = self._write_to_memory(ret)
            self._set_i32_in_memory(arg0, ptr, 0)
            self._set_i32_in_memory(arg0, length, 1)
        _def("__wbg_String_8564e559799eccda", [i32, anyref], [], wbg_String)

        # __wbg_instanceof_Window_23e677d2c6843922(anyref) -> (i32)
        def wbg_instanceof_Window(arg0):
            return 1 if arg0 is self._window_mock else 0
        _def("__wbg_instanceof_Window_23e677d2c6843922", [anyref], [i32], wbg_instanceof_Window)

        # __wbg_document_c0320cd4183c6d9b(anyref) -> (i32)
        def wbg_document(arg0):
            doc = getattr(arg0, "document", None) if arg0 is not None else None
            if doc is None:
                doc = self._document_mock
            return self._add_to_externref_table(doc)
        _def("__wbg_document_c0320cd4183c6d9b", [anyref], [i32], wbg_document)

        # __wbg_createElement_9b0aab265c549ded(anyref, i32, i32) -> (anyref)
        def wbg_createElement(arg0, arg1, arg2):
            tag = self._read_string_from_memory(arg1, arg2)
            doc = arg0 if arg0 is not None else self._document_mock
            try:
                return doc.createElement(tag)
            except Exception as e:
                self._store_exception(e)
                return None
        _def("__wbg_createElement_9b0aab265c549ded", [anyref, i32, i32], [anyref], wbg_createElement)

        # __wbg_set_height_b6548a01bdcb689a(anyref, i32) -> ()
        def wbg_set_height(arg0, arg1):
            if arg0 is not None:
                arg0.height = arg1
        _def("__wbg_set_height_b6548a01bdcb689a", [anyref, i32], [], wbg_set_height)

        # __wbg_getContext_f04bf8f22dcb2d53(anyref, i32, i32) -> (i32)
        def wbg_getContext(arg0, arg1, arg2):
            if arg0 is None or not hasattr(arg0, "getContext"):
                return 0
            type_str = self._read_string_from_memory(arg1, arg2)
            ctx = arg0.getContext(type_str)
            if ctx is None:
                return 0
            return self._add_to_externref_table(ctx)
        _def("__wbg_getContext_f04bf8f22dcb2d53", [anyref, i32, i32], [i32], wbg_getContext)

        # __wbg_toDataURL_bf99d85b39ce57cc(i32, anyref) -> ()
        def wbg_toDataURL(arg0, arg1):
            if arg1 is None or not hasattr(arg1, "toDataURL"):
                self._set_i32_in_memory(arg0, 0, 0)
                self._set_i32_in_memory(arg0, 0, 1)
                return
            ret = arg1.toDataURL()
            ptr, length = self._write_to_memory(ret)
            self._set_i32_in_memory(arg0, ptr, 0)
            self._set_i32_in_memory(arg0, length, 1)
        _def("__wbg_toDataURL_bf99d85b39ce57cc", [i32, anyref], [], wbg_toDataURL)

        # __wbg_set_width_c0fcaa2da53cd540(anyref, i32) -> ()
        def wbg_set_width(arg0, arg1):
            if arg0 is not None:
                arg0.width = arg1
        _def("__wbg_set_width_c0fcaa2da53cd540", [anyref, i32], [], wbg_set_width)

        # __wbg_instanceof_HtmlCanvasElement_26125339f936be50(anyref) -> (i32)
        def wbg_instanceof_HtmlCanvasElement(arg0):
            return 1 if (arg0 is not None and hasattr(arg0, "toDataURL")) else 0
        _def("__wbg_instanceof_HtmlCanvasElement_26125339f936be50", [anyref], [i32], wbg_instanceof_HtmlCanvasElement)

        # __wbg_instanceof_CanvasRenderingContext2d_08b9d193c22fa886(anyref) -> (i32)
        def wbg_instanceof_CanvasRenderingContext2d(arg0):
            return 1 if (arg0 is not None and hasattr(arg0, "fillRect")) else 0
        _def("__wbg_instanceof_CanvasRenderingContext2d_08b9d193c22fa886", [anyref], [i32], wbg_instanceof_CanvasRenderingContext2d)

        # __wbg_set_fillStyle_58417b6b548ae475(anyref, i32, i32) -> ()
        def wbg_set_fillStyle(arg0, arg1, arg2):
            if arg0 is not None:
                arg0.fill_style = self._read_string_from_memory(arg1, arg2)
        _def("__wbg_set_fillStyle_58417b6b548ae475", [anyref, i32, i32], [], wbg_set_fillStyle)

        # __wbg_set_font_b038797b3573ae5e(anyref, i32, i32) -> ()
        def wbg_set_font(arg0, arg1, arg2):
            if arg0 is not None:
                arg0.font = self._read_string_from_memory(arg1, arg2)
        _def("__wbg_set_font_b038797b3573ae5e", [anyref, i32, i32], [], wbg_set_font)

        # __wbg_fillRect_4e5596ca954226e7(anyref, f64, f64, f64, f64) -> ()
        def wbg_fillRect(arg0, arg1, arg2, arg3, arg4):
            if arg0 is not None and hasattr(arg0, "fillRect"):
                arg0.fillRect(arg1, arg2, arg3, arg4)
        _def("__wbg_fillRect_4e5596ca954226e7", [anyref, f64, f64, f64, f64], [], wbg_fillRect)

        # __wbg_fillText_b1722b6179692b85(anyref, i32, i32, f64, f64) -> ()
        def wbg_fillText(arg0, arg1, arg2, arg3, arg4):
            if arg0 is not None and hasattr(arg0, "fillText"):
                text = self._read_string_from_memory(arg1, arg2)
                arg0.fillText(text, arg3, arg4)
        _def("__wbg_fillText_b1722b6179692b85", [anyref, i32, i32, f64, f64], [], wbg_fillText)

        # __wbg_new_ab79df5bd7c26067() -> (anyref)
        def wbg_new():
            return _GenericMock()
        _def("__wbg_new_ab79df5bd7c26067", [], [anyref], wbg_new)

        # __wbg_static_accessor_GLOBAL_THIS_ad356e0db91c7913() -> (i32)
        def wbg_static_GLOBAL_THIS():
            return self._add_to_externref_table(self._window_mock)
        _def("__wbg_static_accessor_GLOBAL_THIS_ad356e0db91c7913", [], [i32], wbg_static_GLOBAL_THIS)

        # __wbg_static_accessor_SELF_f207c857566db248() -> (i32)
        def wbg_static_SELF():
            return self._add_to_externref_table(self._window_mock)
        _def("__wbg_static_accessor_SELF_f207c857566db248", [], [i32], wbg_static_SELF)

        # __wbg_static_accessor_GLOBAL_8adb955bd33fac2f() -> (i32)
        def wbg_static_GLOBAL():
            return self._add_to_externref_table(self._window_mock)
        _def("__wbg_static_accessor_GLOBAL_8adb955bd33fac2f", [], [i32], wbg_static_GLOBAL)

        # __wbg_static_accessor_WINDOW_bb9f1ba69d61b386() -> (i32)
        def wbg_static_WINDOW():
            return self._add_to_externref_table(self._window_mock)
        _def("__wbg_static_accessor_WINDOW_bb9f1ba69d61b386", [], [i32], wbg_static_WINDOW)

        # __wbg_random_5bb86cae65a45bf6() -> (f64)
        def wbg_random():
            return random.random()
        _def("__wbg_random_5bb86cae65a45bf6", [], [f64], wbg_random)

        # __wbg___wbindgen_throw_6ddd609b62940d55(i32, i32) -> ()
        def wbindgen_throw(arg0, arg1):
            msg = self._read_string_from_memory(arg0, arg1)
            raise RuntimeError(f"WASM error: {msg}")
        _def("__wbg___wbindgen_throw_6ddd609b62940d55", [i32, i32], [], wbindgen_throw)

        # __wbg_Error_83742b46f01ce22d(i32, i32) -> (anyref)
        def wbg_Error(arg0, arg1):
            msg = self._read_string_from_memory(arg0, arg1)
            return Exception(msg)
        _def("__wbg_Error_83742b46f01ce22d", [i32, i32], [anyref], wbg_Error)

        # __wbg___wbindgen_is_undefined_52709e72fb9f179c(anyref) -> (i32)
        def wbindgen_is_undefined(arg0):
            return 1 if arg0 is None else 0
        _def("__wbg___wbindgen_is_undefined_52709e72fb9f179c", [anyref], [i32], wbindgen_is_undefined)

        # __wbindgen_init_externref_table() -> ()
        def wbindgen_init_externref_table():
            pass
        _def("__wbindgen_init_externref_table", [], [], wbindgen_init_externref_table)

        # __wbindgen_cast_0000000000000001(f64) -> (anyref)
        def wbindgen_cast_1(arg0):
            return arg0
        _def("__wbindgen_cast_0000000000000001", [f64], [anyref], wbindgen_cast_1)

        # __wbindgen_cast_0000000000000002(i32, i32) -> (anyref)
        def wbindgen_cast_2(arg0, arg1):
            return self._read_string_from_memory(arg0, arg1)
        _def("__wbindgen_cast_0000000000000002", [i32, i32], [anyref], wbindgen_cast_2)

        # __wbindgen_cast_0000000000000003(i64) -> (anyref)
        def wbindgen_cast_3(arg0):
            return arg0
        _def("__wbindgen_cast_0000000000000003", [i64], [anyref], wbindgen_cast_3)

    def _store_exception(self, exc: Exception):
        """Store an exception in the WASM exception store (for handleError pattern)."""
        idx = self._add_to_externref_table(exc)
        self._wasm["__wbindgen_exn_store"](self.store, idx)

    # ─── Public API ──────────────────────────────────────────────────────

    def generate_secure_payload(
        self,
        uuid: str,
        timestamp: str,
        nonce: str,
        challenge: str,
        client_ip: str,
        difficulty: int,
    ) -> dict | str:
        """
        Generate a secure payload for FreeGPT API authentication.

        Returns the payload object (dict) or a JSON string.
        The payload contains: signature, fingerprint, client_ip, v, pow{seed_nonce, nonce, hash, difficulty}
        """
        if not self._initialized:
            raise RuntimeError("WASM not initialized. Call init() first.")

        # Write all strings to WASM memory
        ptr0, len0 = self._write_to_memory(uuid)
        ptr1, len1 = self._write_to_memory(timestamp)
        ptr2, len2 = self._write_to_memory(nonce)
        ptr3, len3 = self._write_to_memory(challenge)
        ptr4, len4 = self._write_to_memory(client_ip)

        # Call generate_secure_payload(ptr0, len0, ptr1, len1, ptr2, len2, ptr3, len3, ptr4, len4, difficulty)
        ret = self._wasm["generate_secure_payload"](
            self.store,
            ptr0, len0,
            ptr1, len1,
            ptr2, len2,
            ptr3, len3,
            ptr4, len4,
            difficulty,
        )

        # ret is (result_idx, error_idx, error_flag) - all i32
        result_idx, error_idx, error_flag = ret

        if error_flag:
            error_obj = self._take_from_externref_table(error_idx)
            raise RuntimeError(f"FreeGPT WASM error: {error_obj}")

        result = self._take_from_externref_table(result_idx)

        # The result should be a dict (JS object), a JSON string, or a _GenericMock
        if isinstance(result, _GenericMock):
            return result.to_dict()
        elif isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return result
        elif isinstance(result, dict):
            return result
        else:
            # Try to convert to dict
            return result


# Singleton instance for reuse across requests
_signer: FreeGPTSigner | None = None
_signer_init_lock = False


def get_signer() -> FreeGPTSigner:
    """Get or create the singleton signer instance."""
    global _signer
    if _signer is None:
        _signer = FreeGPTSigner()
        _signer.init(WASM_PATH)
    return _signer