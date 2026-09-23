from techcamp.shared.ids import uuid7


def test_uuid7_has_version_and_variant_bits() -> None:
    value = uuid7()

    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_uuid7_is_time_ordered() -> None:
    first = uuid7()
    second = uuid7()

    assert first.bytes[:6] <= second.bytes[:6]
    assert first != second
