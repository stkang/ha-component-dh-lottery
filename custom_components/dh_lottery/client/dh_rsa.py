import os

class RSAKey:
    """Python implementation of RSA encryption matching JavaScript jsbn library"""

    def __init__(self):
        self.n = None  # modulus
        self.e = 0     # public exponent

    def set_public(self, N_hex: str, E_hex: str):
        """Set the public key fields N and e from hex strings"""
        if N_hex and E_hex and len(N_hex) > 0 and len(E_hex) > 0:
            self.n = int(N_hex, 16)
            self.e = int(E_hex, 16)
        else:
            raise ValueError("Invalid RSA public key")

    def do_public(self, x: int) -> int:
        """Perform raw public operation on x: return x^e (mod n)"""
        return pow(x, self.e, self.n)

    def encrypt(self, text: str) -> str:
        """Return the PKCS#1 RSA encryption of text as an even-length hex string"""
        m = pkcs1pad2(text, (self.n.bit_length() + 7) >> 3)
        if m is None:
            return None
        c = self.do_public(m)
        if c is None:
            return None
        h = hex(c)[2:]  # Remove '0x' prefix
        # Make sure it's even length
        if (len(h) & 1) == 0:
            return h
        else:
            return "0" + h


def pkcs1pad2(s: str, n: int) -> int:
    """
    PKCS#1 (type 2, random) pad input string s to n bytes, and return a bigint
    This matches the JavaScript implementation in rsa.js
    """
    if n < len(s) + 11:
        raise ValueError("Message too long for RSA")

    ba = [0] * n
    i = len(s) - 1
    n_idx = n

    # Encode the string using UTF-8
    while i >= 0 and n_idx > 0:
        c = ord(s[i])
        i -= 1

        if c < 128:  # Single byte
            n_idx -= 1
            ba[n_idx] = c
        elif c > 127 and c < 2048:  # Two bytes
            n_idx -= 1
            ba[n_idx] = (c & 63) | 128
            n_idx -= 1
            ba[n_idx] = (c >> 6) | 192
        else:  # Three bytes
            n_idx -= 1
            ba[n_idx] = (c & 63) | 128
            n_idx -= 1
            ba[n_idx] = ((c >> 6) & 63) | 128
            n_idx -= 1
            ba[n_idx] = (c >> 12) | 224

    # Add 0x00 separator
    n_idx -= 1
    ba[n_idx] = 0

    # Fill with random non-zero bytes
    while n_idx > 2:
        x = 0
        while x == 0:
            x = os.urandom(1)[0]
        n_idx -= 1
        ba[n_idx] = x

    # Add PKCS#1 type 2 header
    ba[1] = 2
    ba[0] = 0

    # Convert byte array to integer (big-endian)
    result = 0
    for byte_val in ba:
        result = (result << 8) | byte_val

    return result