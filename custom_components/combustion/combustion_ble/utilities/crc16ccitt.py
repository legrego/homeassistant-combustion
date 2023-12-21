def crc16ccitt(data):
    """Calculate the CRC-16-CCITT checksum of a byte array.
    :param data: The input data as a byte array.
    :return: The calculated CRC as an integer.
    """
    poly = 0x1021
    crc = 0xFFFF

    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (crc << 1) ^ poly if (crc & 0x8000) else crc << 1
            crc &= 0xFFFF  # Truncate to 16 bits

    return crc
