"""Restrict generated private material on POSIX and Windows.

Windows uses a protected DACL, not chmod's read-only flag. New directories also
pass these access rules to their children. Only the current process user and
LocalSystem receive access; no administrator privileges or external tool is used.
"""

import ctypes
from functools import lru_cache
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from typing import Any

TOKEN_QUERY = 0x0008
TOKEN_USER_CLASS = 1
ERROR_INSUFFICIENT_BUFFER = 122
SE_FILE_OBJECT = 1
DACL_SECURITY_INFORMATION = 0x00000004
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
SDDL_REVISION_1 = 1

DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("attributes", DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("user", _SidAndAttributes)]


def _bind(library: Any, name: str, arguments: list, result: Any) -> Any:
    function = getattr(library, name)
    function.argtypes = arguments
    function.restype = result
    return function


@lru_cache(maxsize=1)
def _windows_api() -> Any:
    """Load and bind native functions only when Windows permissions are needed."""
    load = getattr(ctypes, "WinDLL")
    kernel = load("kernel32", use_last_error=True)
    security = load("advapi32", use_last_error=True)
    pointer = ctypes.c_void_p
    pointer_pointer = ctypes.POINTER(pointer)

    return SimpleNamespace(
        current_process=_bind(kernel, "GetCurrentProcess", [], HANDLE),
        close_handle=_bind(kernel, "CloseHandle", [HANDLE], BOOL),
        local_free=_bind(kernel, "LocalFree", [pointer], pointer),
        open_token=_bind(
            security, "OpenProcessToken", [HANDLE, DWORD, ctypes.POINTER(HANDLE)], BOOL
        ),
        token_information=_bind(
            security,
            "GetTokenInformation",
            [HANDLE, ctypes.c_int, pointer, DWORD, ctypes.POINTER(DWORD)],
            BOOL,
        ),
        sid_to_string=_bind(
            security, "ConvertSidToStringSidW", [pointer, pointer_pointer], BOOL
        ),
        descriptor_from_string=_bind(
            security,
            "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            [ctypes.c_wchar_p, DWORD, pointer_pointer, ctypes.POINTER(DWORD)],
            BOOL,
        ),
        descriptor_dacl=_bind(
            security,
            "GetSecurityDescriptorDacl",
            [pointer, ctypes.POINTER(BOOL), pointer_pointer, ctypes.POINTER(BOOL)],
            BOOL,
        ),
        set_named_security=_bind(
            security,
            "SetNamedSecurityInfoW",
            [ctypes.c_wchar_p, ctypes.c_int, DWORD, pointer, pointer, pointer, pointer],
            DWORD,
        ),
    )


def _last_error() -> int:
    return getattr(ctypes, "get_last_error")()


def _windows_failure(operation: str, code: int = 0) -> OSError:
    return OSError(
        code or _last_error(), f"Could not restrict private storage: {operation}"
    )


def _current_user_sid(api: Any) -> str:
    token = HANDLE()
    if not api.open_token(api.current_process(), TOKEN_QUERY, ctypes.byref(token)):
        raise _windows_failure("open current process token")

    try:
        required = DWORD()
        succeeded = api.token_information(
            token, TOKEN_USER_CLASS, None, 0, ctypes.byref(required)
        )
        if (
            succeeded
            or _last_error() != ERROR_INSUFFICIENT_BUFFER
            or required.value < ctypes.sizeof(_TokenUser)
        ):
            raise _windows_failure("read current user token size")

        buffer = ctypes.create_string_buffer(required.value)
        if not api.token_information(
            token, TOKEN_USER_CLASS, buffer, required.value, ctypes.byref(required)
        ):
            raise _windows_failure("read current user token")

        token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        if not token_user.user.sid:
            raise _windows_failure("read current user SID")

        sid_text = ctypes.c_void_p()
        if not api.sid_to_string(token_user.user.sid, ctypes.byref(sid_text)):
            raise _windows_failure("convert current user SID")

        try:
            if not sid_text.value:
                raise _windows_failure("read current user SID")
            return ctypes.wstring_at(sid_text.value)
        finally:
            api.local_free(sid_text)
    finally:
        api.close_handle(token)


def _private_sddl(user_sid: str, *, directory: bool) -> str:
    if re.fullmatch(r"S-\d+(?:-\d+)+", user_sid) is None:
        raise ValueError("Windows returned an invalid current-user SID")

    inheritance = "OICI" if directory else ""
    user_access = f"(A;{inheritance};FA;;;{user_sid})"
    system_access = f"(A;{inheritance};FA;;;SY)"
    return "D:P" + user_access + system_access


def _restrict_windows(path: Path, *, directory: bool) -> None:
    api = _windows_api()
    descriptor = ctypes.c_void_p()
    sddl = _private_sddl(_current_user_sid(api), directory=directory)
    if not api.descriptor_from_string(
        sddl, SDDL_REVISION_1, ctypes.byref(descriptor), None
    ):
        raise _windows_failure("construct private access rules")

    try:
        present = BOOL()
        defaulted = BOOL()
        dacl = ctypes.c_void_p()
        if not api.descriptor_dacl(
            descriptor,
            ctypes.byref(present),
            ctypes.byref(dacl),
            ctypes.byref(defaulted),
        ):
            raise _windows_failure("read private access rules")
        if not present.value or not dacl.value:
            raise OSError("Refusing to apply a missing or unrestricted Windows DACL")

        status = api.set_named_security(
            str(path.absolute()),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
        if status:
            raise _windows_failure("apply private access rules", status)
    finally:
        api.local_free(descriptor)


def restrict_private(path: Path, *, directory: bool) -> None:
    """Restrict an existing file/directory before writing secrets beneath it.

    Fail rather than silently leave a Windows volume without enforceable ACLs.
    This function never changes ownership, disables a read-only flag, or follows
    a final path component that is a symbolic link.
    """
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Private storage must not be a symbolic link")
    if not path.exists():
        raise FileNotFoundError(path)
    if path.is_dir() != directory:
        raise ValueError("Private storage path does not match its expected type")

    if sys.platform == "win32":
        _restrict_windows(path, directory=directory)
    else:
        os.chmod(path, 0o700 if directory else 0o600)
