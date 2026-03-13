"""End-to-end encryption using PyNaCl (NaCl public-key authenticated encryption)."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from nacl.public import Box, PrivateKey, PublicKey
from nacl.utils import random as nacl_random


@dataclass(frozen=True)
class KeyPair:
    """A NaCl key pair encoded as base64 strings for portable storage."""

    public_key: str
    private_key: str


def generate_keypair() -> KeyPair:
    """Generate a new NaCl key pair.

    Returns:
        KeyPair with base64-encoded public and private keys.
    """
    sk = PrivateKey.generate()
    pk = sk.public_key
    return KeyPair(
        public_key=base64.b64encode(bytes(pk)).decode(),
        private_key=base64.b64encode(bytes(sk)).decode(),
    )


def encrypt(
    message: str,
    recipient_public_key: str,
    sender_private_key: str,
) -> str:
    """Encrypt a message for a recipient using authenticated encryption.

    Args:
        message: Plain text to encrypt.
        recipient_public_key: Base64-encoded recipient public key.
        sender_private_key: Base64-encoded sender private key.

    Returns:
        Base64-encoded encrypted bytes (includes nonce).
    """
    pk = PublicKey(base64.b64decode(recipient_public_key))
    sk = PrivateKey(base64.b64decode(sender_private_key))
    box = Box(sk, pk)
    encrypted = box.encrypt(message.encode("utf-8"))
    return base64.b64encode(encrypted).decode()


def decrypt(
    encrypted_b64: str,
    sender_public_key: str,
    recipient_private_key: str,
) -> str:
    """Decrypt a message from a sender using authenticated encryption.

    Args:
        encrypted_b64: Base64-encoded encrypted bytes (includes nonce).
        sender_public_key: Base64-encoded sender public key.
        recipient_private_key: Base64-encoded recipient private key.

    Returns:
        Decrypted plain text.
    """
    pk = PublicKey(base64.b64decode(sender_public_key))
    sk = PrivateKey(base64.b64decode(recipient_private_key))
    box = Box(sk, pk)
    decrypted = box.decrypt(base64.b64decode(encrypted_b64))
    return decrypted.decode("utf-8")
