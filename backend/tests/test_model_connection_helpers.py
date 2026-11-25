"""Tests for shared model credential encryption helpers."""

import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from core.llm_crypto import decrypt_secret, encrypt_secret
from core.model_adapters import SUPPORTED_MODEL_ADAPTERS


class ModelCredentialCryptoTests(unittest.TestCase):
    def test_encrypt_decrypt_round_trip_with_generated_installation_key(self):
        with tempfile.TemporaryDirectory() as data_dir, patch.dict(
            os.environ, {"SAB_DATA_DIR": data_dir}, clear=False
        ):
            os.environ.pop("SAB_LLM_ENCRYPTION_KEY", None)
            encrypted = encrypt_secret("test-secret")
            self.assertNotEqual(encrypted, "test-secret")
            self.assertEqual(decrypt_secret(encrypted), "test-secret")
            key_path = os.path.join(data_dir, ".llm_encryption_key")
            self.assertTrue(os.path.isfile(key_path))
            self.assertEqual(len(Path(key_path).read_bytes().strip()), 44)

    def test_empty_credentials_remain_empty(self):
        self.assertEqual(encrypt_secret(""), "")
        self.assertEqual(decrypt_secret(""), "")

    def test_configured_encryption_key_is_used(self):
        key = Fernet.generate_key().decode("ascii")
        with tempfile.TemporaryDirectory() as data_dir, patch.dict(
            os.environ,
            {"SAB_DATA_DIR": data_dir, "SAB_LLM_ENCRYPTION_KEY": key},
            clear=False,
        ):
            encrypted = encrypt_secret("configured-key-secret")
            self.assertEqual(decrypt_secret(encrypted), "configured-key-secret")
            self.assertFalse(os.path.exists(os.path.join(data_dir, ".llm_encryption_key")))


class ModelAdapterRegistryTests(unittest.TestCase):
    def test_supported_adapters_are_unique_and_nonempty(self):
        self.assertTrue(SUPPORTED_MODEL_ADAPTERS)
        self.assertEqual(len(SUPPORTED_MODEL_ADAPTERS), len(set(SUPPORTED_MODEL_ADAPTERS)))
        self.assertIn("openai-compatible", SUPPORTED_MODEL_ADAPTERS)
        self.assertIn("bedrock", SUPPORTED_MODEL_ADAPTERS)


if __name__ == "__main__":
    unittest.main()
