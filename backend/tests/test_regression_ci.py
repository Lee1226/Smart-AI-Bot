import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('regression_ci',Path(__file__).parents[2]/'tools/regression_ci.py')
ci=importlib.util.module_from_spec(spec);spec.loader.exec_module(ci)

class CITests(unittest.TestCase):
    def test_gate_exit_codes_and_fail_on_archive_error(self):
        for gate,code in [('passed',0),('failed',1)]:
            with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'SAB_API_KEY':'test-secret'}):
                def rpc(base,token,path,body=None):
                    return json.dumps({'gate':gate,'release_allowed':gate=='passed'}).encode() if path.endswith('/gate') else b'artifact'
                with patch.object(ci,'request',side_effect=rpc):
                    self.assertEqual(code,ci.main(['--plan-id','p','--output',folder]))
                self.assertTrue((Path(folder)/'junit.xml').exists())
                self.assertTrue((Path(folder)/'evidence.zip').exists())
        with patch.dict('os.environ',{'SAB_API_KEY':'test-secret'}),patch.object(ci,'request',side_effect=OSError('offline')):
            self.assertEqual(2,ci.main(['--plan-id','p']))

    def test_timeout_does_not_release_or_silently_cancel(self):
        with patch.dict('os.environ',{'SAB_API_KEY':'test-secret'}),patch.object(ci,'request',return_value=b'{"gate":"waiting","release_allowed":false}') as rpc,patch.object(ci.time,'monotonic',side_effect=[0,2]):
            self.assertEqual(2,ci.main(['--plan-id','p','--timeout','1']))
            self.assertEqual(1,rpc.call_count)
