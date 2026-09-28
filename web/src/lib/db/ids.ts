/**
 * UUIDv7 (RFC 9562 section 5.7) with the same byte layout as the server's
 * `shared/ids.uuid7`: a 48-bit big-endian Unix millisecond timestamp, the
 * 4-bit version `0111`, 12 random `rand_a` bits, the 2-bit variant `10` and 62
 * random `rand_b` bits. Byte-for-byte identical layout matters because the
 * server and this client generate ids for the same rows: an id made offline
 * has to sort among the ids made online.
 *
 * Implemented here rather than taken from a dependency: the browser's
 * `crypto.randomUUID()` is version 4 (no time, no order), and nothing else in
 * the bundle already provides version 7.
 */
export function uuidv7(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)

  const unixMs = Date.now()
  // The timestamp is 48 bits, so it is written big-endian across bytes 0-5.
  // Division (not shifts) because `<< 32` and beyond overflow a 32-bit int.
  bytes[0] = Math.floor(unixMs / 2 ** 40) & 0xff
  bytes[1] = Math.floor(unixMs / 2 ** 32) & 0xff
  bytes[2] = Math.floor(unixMs / 2 ** 24) & 0xff
  bytes[3] = Math.floor(unixMs / 2 ** 16) & 0xff
  bytes[4] = Math.floor(unixMs / 2 ** 8) & 0xff
  bytes[5] = unixMs & 0xff
  // version 0111 in the high nibble of byte 6, then 4 random rand_a bits;
  // byte 7 keeps rand_a's remaining 8 random bits.
  bytes[6] = 0x70 | (bytes[6] & 0x0f)
  // variant 10 in the high 2 bits of byte 8, then 6 random rand_b bits;
  // bytes 9-15 keep rand_b's remaining 56 random bits.
  bytes[8] = 0x80 | (bytes[8] & 0x3f)

  return formatUuid(bytes)
}

function formatUuid(bytes: Uint8Array): string {
  let hex = ''
  for (const byte of bytes) hex += byte.toString(16).padStart(2, '0')
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20, 32),
  ].join('-')
}
