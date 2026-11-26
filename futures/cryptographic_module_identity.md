# Future: Cryptographic Module Identity

> Private key storage in the CH32V003/CH32V203 identity flash region, enabling authenticated bus messages, secure config delivery, and hardware attestation.

## Context

The 256-byte identity region (0x3F00-0x3FFF) currently stores a UUID (16 bytes) for module identification. The same region has room for a 32-byte Ed25519 private key, with the public key derived and shared during discovery.

## Capability

| Feature | UUID Only (V1) | With Private Key (Future) |
|---|---|---|
| Module identification | Yes | Yes |
| Spoofed module detection | No | Yes — messages are signed |
| Secure config push | No | Yes — config encrypted to module's public key |
| Hardware attestation | No | Yes — module proves physical identity |
| Game anti-cheat | No | Yes — CAN node swap detected |
| Vehicle bus security | No | Yes — namespace events authenticated |

## Implementation Path

1. **Flash station generates Ed25519 keypair** during initial programming
2. **Private key written to identity region** (0x3F10, 32 bytes) alongside UUID
3. **Public key registered in bus storage** as part of module discovery
4. **Bus message signature field** — optional 8-byte truncated Ed25519 signature in bus messages (fits within 256-byte message limit)
5. **Config encryption** — bus storage encrypts config binary with module's public key before push

## Design Principle Alignment

- DP-CIO-2 (not brittle): Identity region designed once, extended later without layout changes
- DP-CIO-11 (graceful degradation): Signature verification is optional — unsigned messages still work on buses that don't require auth
- DP-CIO-23 (bus storage): Public key registry lives in bus storage alongside config and namespace registry

## Flash Layout (unchanged from V1)

```
0x3F00: UUID (16 bytes)
0x3F10: Private key (32 bytes) — Ed25519, written once by flash station
0x3F30: Public key (32 bytes) — derived, cached for fast access
0x3F50: Board type (1 byte)
0x3F51: Hardware revision (1 byte)
0x3F52: Flash station timestamp (4 bytes)
0x3F56 - 0x3FFF: Reserved (170 bytes)
```

## Not V1

This is documented for future implementation. The flash layout supports it today. The firmware `get_module_id()` abstraction is in place. When the time comes, it's a firmware update + protocol extension, not an architecture change.

## Origin

David's observation: "When I saw the capability to store some bytes in flash, I immediately thought, that's for storing a private key." Professional biometrics instinct applied to maker hardware identity.
