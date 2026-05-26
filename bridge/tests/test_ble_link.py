from buddy_bridge.ble_link import chunk_payload, BLE_CHUNK, BleLink


def test_small_payload_is_single_chunk():
    p = b'{"evt":"status"}\n'
    assert chunk_payload(p) == [p]


def test_exactly_chunk_size_is_single_chunk():
    p = b"x" * BLE_CHUNK
    assert chunk_payload(p) == [p]


def test_large_payload_is_split():
    p = b"a" * (BLE_CHUNK * 2 + 10)
    chunks = chunk_payload(p)
    assert len(chunks) == 3
    assert all(len(c) <= BLE_CHUNK for c in chunks)
    assert b"".join(chunks) == p  # lossless: reassembly reproduces the payload


def test_split_preserves_newline_terminator():
    # A realistic oversized ask ending in a newline must keep the newline in
    # the final chunk so the firmware's newline reassembly completes.
    p = b'{"evt":"ask","questions":[' + b"x" * 400 + b"]}\n"
    chunks = chunk_payload(p)
    assert b"".join(chunks) == p
    assert chunks[-1].endswith(b"\n")


def test_on_connect_callback_stored():
    called = []
    link = BleLink(on_connect=lambda: called.append(True))
    link._fire_connect()
    assert called == [True]


def test_on_connect_optional():
    link = BleLink()
    link._fire_connect()  # must not raise when no callback set
