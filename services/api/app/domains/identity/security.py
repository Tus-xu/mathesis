"""Credential hashing and local password policy."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type


class PasswordPolicyError(ValueError):
    def __init__(self, error_key: str, message: str):
        super().__init__(message)
        self.error_key = error_key
        self.message = message


@dataclass(frozen=True)
class PasswordCheck:
    valid: bool
    replacement_hash: str | None = None


class PasswordHasher:
    min_length = 15
    max_length = 128

    def __init__(self, blocklist_path: Path | None = None):
        path = blocklist_path or Path(__file__).with_name("password_blocklist.txt")
        self.blocklist = {
            self.normalize(line.strip()).casefold()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        self._hasher = Argon2PasswordHasher(
            time_cost=2,
            memory_cost=19_456,
            parallelism=1,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )

    @staticmethod
    def normalize(password: str) -> str:
        return unicodedata.normalize("NFC", password)

    def validate(self, password: str) -> str:
        normalized = self.normalize(password)
        if not self.min_length <= len(normalized) <= self.max_length:
            raise PasswordPolicyError(
                "AUTH_PASSWORD_POLICY",
                f"密码长度必须为 {self.min_length}–{self.max_length} 个字符",
            )
        if normalized.casefold() in self.blocklist:
            raise PasswordPolicyError("AUTH_PASSWORD_BLOCKED", "该密码过于常见，请更换")
        return normalized

    def hash(self, password: str) -> str:
        return self._hasher.hash(self.validate(password))

    def verify_and_rehash(self, password: str, encoded: str) -> PasswordCheck:
        normalized = self.normalize(password)
        try:
            valid = self._hasher.verify(encoded, normalized)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return PasswordCheck(valid=False)
        replacement = self._hasher.hash(normalized) if self._hasher.check_needs_rehash(encoded) else None
        return PasswordCheck(valid=bool(valid), replacement_hash=replacement)
