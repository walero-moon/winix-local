"""Private-file permissions, including Windows DACL behavior when available."""

import ctypes
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from winix_local import private_storage as storage


USER_SID = "S-1-5-21-101-202-303-1001"


class FakeWindowsApi:
    """Exercise pointer/handle ownership without loading Windows libraries."""

    def __init__(self):
        self.sid_buffer = ctypes.create_unicode_buffer(USER_SID)
        self.current_process = Mock(return_value=0x100000001)
        self.close_handle = Mock(return_value=1)
        self.local_free = Mock(return_value=None)
        self.open_token = Mock(side_effect=self._open_token)
        self.token_information = Mock(side_effect=self._token_information)
        self.sid_to_string = Mock(side_effect=self._sid_to_string)
        self.descriptor_from_string = Mock(side_effect=self._descriptor_from_string)
        self.descriptor_dacl = Mock(side_effect=self._descriptor_dacl)
        self.set_named_security = Mock(return_value=0)

    def _open_token(self, process, access, output):
        ctypes.cast(output, ctypes.POINTER(storage.HANDLE)).contents.value = 0x200000002
        return 1

    def _token_information(self, token, kind, buffer, length, required):
        ctypes.cast(required, ctypes.POINTER(storage.DWORD)).contents.value = (
            ctypes.sizeof(storage._TokenUser)
        )
        if buffer is None:
            return 0
        ctypes.cast(buffer, ctypes.POINTER(storage._TokenUser)).contents.user.sid = (
            0x300000003
        )
        return 1

    def _sid_to_string(self, sid, output):
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p)).contents.value = (
            ctypes.addressof(self.sid_buffer)
        )
        return 1

    def _descriptor_from_string(self, text, revision, output, size):
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p)).contents.value = (
            0x400000004
        )
        return 1

    def _descriptor_dacl(self, descriptor, present, dacl, defaulted):
        ctypes.cast(present, ctypes.POINTER(storage.BOOL)).contents.value = 1
        ctypes.cast(dacl, ctypes.POINTER(ctypes.c_void_p)).contents.value = 0x500000005
        return 1


class PrivateStorageTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "POSIX mode test")
    def test_posix_files_and_directories_have_owner_only_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "private"
            directory.mkdir()
            file = directory / "secret.key"
            file.write_text("fixture")

            storage.restrict_private(directory, directory=True)
            storage.restrict_private(file, directory=False)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            file.write_text("still writable")
            file.unlink()

    def test_windows_dispatch_does_not_use_posix_chmod(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with (
                patch("winix_local.private_storage.sys.platform", "win32"),
                patch("winix_local.private_storage._restrict_windows") as restrict,
                patch("winix_local.private_storage.os.chmod") as chmod,
            ):
                storage.restrict_private(directory, directory=True)

            restrict.assert_called_once_with(directory, directory=True)
            chmod.assert_not_called()

    def test_current_user_and_system_are_the_only_dacl_grants(self):
        for directory, flags in ((True, "OICI"), (False, "")):
            with self.subTest(directory=directory):
                self.assertEqual(
                    storage._private_sddl(USER_SID, directory=directory),
                    f"D:P(A;{flags};FA;;;{USER_SID})(A;{flags};FA;;;SY)",
                )
        with self.assertRaises(ValueError):
            storage._private_sddl("bad;WD", directory=True)

    def test_native_calls_keep_handles_and_descriptors_alive_until_applied(self):
        api = FakeWindowsApi()
        with (
            patch("winix_local.private_storage._windows_api", return_value=api),
            patch("winix_local.private_storage._last_error", return_value=122),
        ):
            storage._restrict_windows(Path("fixture key"), directory=False)

        self.assertEqual(api.close_handle.call_args.args[0].value, 0x200000002)
        self.assertEqual(api.local_free.call_count, 2)
        call = api.set_named_security.call_args.args
        self.assertEqual(call[1], storage.SE_FILE_OBJECT)
        self.assertEqual(
            call[2],
            storage.DACL_SECURITY_INFORMATION
            | storage.PROTECTED_DACL_SECURITY_INFORMATION,
        )
        self.assertIsNone(call[3])
        self.assertIsNone(call[4])
        self.assertEqual(call[5].value, 0x500000005)
        self.assertIsNone(call[6])
        self.assertEqual(
            api.descriptor_from_string.call_args.args[0],
            f"D:P(A;;FA;;;{USER_SID})(A;;FA;;;SY)",
        )

    def test_native_error_closes_token_and_never_applies_unrestricted_acl(self):
        api = FakeWindowsApi()
        api.token_information.side_effect = None
        api.token_information.return_value = 0
        with (
            patch("winix_local.private_storage._windows_api", return_value=api),
            patch("winix_local.private_storage._last_error", return_value=5),
            self.assertRaises(OSError),
        ):
            storage._restrict_windows(Path("fixture"), directory=True)
        api.close_handle.assert_called_once()
        api.set_named_security.assert_not_called()

        api = FakeWindowsApi()
        api.descriptor_dacl.side_effect = None
        api.descriptor_dacl.return_value = 1
        with (
            patch("winix_local.private_storage._windows_api", return_value=api),
            patch("winix_local.private_storage._last_error", return_value=122),
            self.assertRaisesRegex(OSError, "unrestricted"),
        ):
            storage._restrict_windows(Path("fixture"), directory=True)
        api.set_named_security.assert_not_called()
        self.assertEqual(api.local_free.call_count, 2)

    def test_native_apply_error_is_not_silently_accepted(self):
        api = FakeWindowsApi()
        api.set_named_security.return_value = 5
        with (
            patch("winix_local.private_storage._windows_api", return_value=api),
            patch("winix_local.private_storage._last_error", return_value=122),
            self.assertRaisesRegex(OSError, "apply private"),
        ):
            storage._restrict_windows(Path("fixture"), directory=True)
        self.assertEqual(api.local_free.call_count, 2)

    def test_missing_and_wrong_type_paths_fail_without_permission_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaises(FileNotFoundError):
                storage.restrict_private(directory / "missing", directory=False)
            with self.assertRaises(ValueError):
                storage.restrict_private(directory, directory=False)


@unittest.skipUnless(sys.platform == "win32", "requires native Windows security APIs")
class WindowsPrivateStorageTests(unittest.TestCase):
    def read_dacl(self, path):
        load = getattr(ctypes, "WinDLL")
        api = storage._windows_api()
        security = load("advapi32", use_last_error=True)
        pointer = ctypes.c_void_p
        get_security = storage._bind(
            security,
            "GetNamedSecurityInfoW",
            [
                ctypes.c_wchar_p,
                ctypes.c_int,
                storage.DWORD,
                pointer,
                pointer,
                pointer,
                pointer,
                ctypes.POINTER(pointer),
            ],
            storage.DWORD,
        )
        stringify = storage._bind(
            security,
            "ConvertSecurityDescriptorToStringSecurityDescriptorW",
            [
                pointer,
                storage.DWORD,
                storage.DWORD,
                ctypes.POINTER(pointer),
                ctypes.POINTER(storage.DWORD),
            ],
            storage.BOOL,
        )
        descriptor = pointer()
        result = get_security(
            str(path),
            storage.SE_FILE_OBJECT,
            storage.DACL_SECURITY_INFORMATION,
            None,
            None,
            None,
            None,
            ctypes.byref(descriptor),
        )
        self.assertEqual(result, 0)
        text = pointer()
        try:
            self.assertTrue(
                stringify(
                    descriptor,
                    1,
                    storage.DACL_SECURITY_INFORMATION,
                    ctypes.byref(text),
                    None,
                )
            )
            try:
                return ctypes.wstring_at(text.value)
            finally:
                api.local_free(text)
        finally:
            api.local_free(descriptor)

    def assert_private_grants(self, sddl, user_sid):
        accounts = {entry.split(";")[-1] for entry in re.findall(r"\(([^()]*)\)", sddl)}
        accounts = {"S-1-5-18" if account == "SY" else account for account in accounts}
        self.assertEqual(accounts, {user_sid, "S-1-5-18"})
        self.assertTrue(
            all(entry.startswith("A;") for entry in re.findall(r"\(([^()]*)\)", sddl))
        )

    def test_native_directory_inherits_private_access_and_files_remain_writable(self):
        api = storage._windows_api()
        sid = storage._current_user_sid(api)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "private Unicode \u00e9"
            directory.mkdir()
            storage.restrict_private(directory, directory=True)
            descriptor = self.read_dacl(directory)
            self.assertTrue(descriptor.startswith("D:P"), descriptor)
            self.assertIn("OICI", descriptor)
            self.assert_private_grants(descriptor, sid)

            file = directory / "private key.txt"
            file.write_text("fixture")
            self.assert_private_grants(self.read_dacl(file), sid)
            storage.restrict_private(file, directory=False)
            self.assertTrue(self.read_dacl(file).startswith("D:P"))
            file.write_text("still writable")
            self.assertEqual(file.read_text(), "still writable")
            file.unlink()
            directory.rmdir()


if __name__ == "__main__":
    unittest.main()
