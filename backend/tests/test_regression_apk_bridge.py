import hashlib
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
spec=importlib.util.spec_from_file_location('regression_bridge',Path(__file__).parents[2]/'tools/adb_bridge.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)

class APKTests(unittest.IsolatedAsyncioTestCase):
    async def test_hash_and_manifest_mismatch_never_install(self):
        instance=bridge.Bridge('adb','http://localhost:8000','phone','token');instance.command=AsyncMock()
        content=b'fake-apk-for-transport-test'
        with patch.object(bridge.shutil,'which',return_value='aapt'),patch.object(bridge.urllib.request,'urlopen',return_value=io.BytesIO(content)):
            with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):
                await instance.dispatch('app/install',{'package':'com.test.app','download_url':'http://server/apk','sha256':'a'*64})
        instance.command.assert_not_awaited()
        proc=SimpleNamespace(returncode=0,communicate=AsyncMock(return_value=(b"package: name='com.wrong.app' versionCode='1' versionName='1.0'",b'')))
        with patch.object(bridge.shutil,'which',return_value='aapt'),patch.object(bridge.urllib.request,'urlopen',return_value=io.BytesIO(content)),patch.object(bridge.asyncio,'create_subprocess_exec',new=AsyncMock(return_value=proc)):
            with self.assertRaisesRegex(ValueError,'package does not match'):
                await instance.dispatch('app/install',{'package':'com.test.app','download_url':'http://server/apk','sha256':hashlib.sha256(content).hexdigest()})
        instance.command.assert_not_awaited()

    async def test_reset_requires_success_ack_and_diagnostics_are_bounded(self):
        instance=bridge.Bridge('adb','http://localhost:8000','phone','token');instance.shell=AsyncMock(return_value='Failed')
        with self.assertRaises(RuntimeError):await instance.dispatch('app/reset',{'package':'com.test.app'})
        instance.shell=AsyncMock(side_effect=['x'*300000,'y'*60000,'model'])
        result=await instance.dispatch('diagnostics',{})
        self.assertEqual(200000,len(result['logcat']));self.assertEqual(50000,len(result['last_anr']))
